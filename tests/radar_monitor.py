import io, json, math, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

RADAR_PAGE='https://www.pagasa.dost.gov.ph/radar'
OUTPUT_DIR=Path('radar_data'); STATE_FILE=Path('radar_state.json'); MAP_STATE_FILE=Path('radar_map_state.json')
SCRIPT_FILE=OUTPUT_DIR/'pagasa_radar_map.js'; PH_TZ=timezone(timedelta(hours=8))
TARGET_LAT,TARGET_LON=14.5794,121.0359
WEST,SOUTH=115.969111093,3.80912641587; EAST,NORTH=129.511990464,22.322581275

def extract_assignment(text,name):
    m=re.search(rf'(?:const|let|var)?\s*{re.escape(name)}\s*=\s*',text,re.I)
    if not m:return None
    s=m.end()
    while s<len(text) and text[s].isspace():s+=1
    o=text[s:s+1]
    if o not in '[{':return text[s:s+500]
    c=']' if o=='[' else '}'; d=0;q=None;e=False
    for i in range(s,len(text)):
        x=text[i]
        if q:
            if e:e=False
            elif x=='\\':e=True
            elif x==q:q=None
            continue
        if x in "'\"`":q=x
        elif x==o:d+=1
        elif x==c:
            d-=1
            if d==0:return text[s:i+1]
    return text[s:s+2000]

def lonlat_to_pixel(lon,lat,w,h):return ((lon-WEST)/(EAST-WEST)*w,(NORTH-lat)/(NORTH-SOUTH)*h)

def analyze(img_bytes):
    im=Image.open(io.BytesIO(img_bytes)).convert('RGBA'); w,h=im.size; tx,ty=lonlat_to_pixel(TARGET_LON,TARGET_LAT,w,h)
    p=im.load(); full=Counter(); alpha=Counter(); colored=[]
    for y in range(h):
        for x in range(w):
            c=p[x,y]; full[c]+=1; alpha[c[3]]+=1
            if c[3]>10 and max(c[:3])-min(c[:3])>=25: colored.append((x,y,c))
    # Geographic bbox of all visible/colored pixels.
    if colored:
        xs=[a[0] for a in colored]; ys=[a[1] for a in colored]
        lon0=WEST+min(xs)/w*(EAST-WEST); lon1=WEST+max(xs)/w*(EAST-WEST)
        lat1=NORTH-min(ys)/h*(NORTH-SOUTH); lat0=NORTH-max(ys)/h*(NORTH-SOUTH)
        visible_bbox={'west':lon0,'south':lat0,'east':lon1,'north':lat1}
    else: visible_bbox=None
    # Nearest colored pixel to Mandaluyong, if any.
    nearest=None
    if colored:
        nearest=min(colored,key=lambda a:(a[0]-tx)**2+(a[1]-ty)**2)
        nx,ny,nc=nearest; nearest={'pixel':[nx,ny],'rgba':list(nc),'distance_pixels':round(math.hypot(nx-tx,ny-ty),2),'lon':WEST+nx/w*(EAST-WEST),'lat':NORTH-ny/h*(NORTH-SOUTH)}
    out=im.copy(); d=ImageDraw.Draw(out); r=max(4,int(2.5*w/(EAST-WEST)))
    d.ellipse((tx-r,ty-r,tx+r,ty+r),outline=(255,0,0,255),width=3); d.line((tx-r*2,ty,tx+r*2,ty),fill=(255,0,0,255),width=2); d.line((tx,ty-r*2,tx,ty+r*2),fill=(255,0,0,255),width=2)
    out.save(OUTPUT_DIR/'mandaluyong_radar_localized.png')
    return {'image_size':[w,h],'target':{'lat':TARGET_LAT,'lon':TARGET_LON},'target_pixel':{'x':round(tx,2),'y':round(ty,2)},'visible_colored_pixels':len(colored),'visible_colored_bbox':visible_bbox,'nearest_colored_pixel':nearest,'top_rgba':[[list(c),n] for c,n in full.most_common(30)],'alpha_distribution':[[a,n] for a,n in alpha.most_common(20)]}

def main():
    OUTPUT_DIR.mkdir(exist_ok=True); captured=[]; latest=None; script_result={}; frame_states=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); page=browser.new_page()
        def on_response(resp):
            nonlocal latest
            u=resp.url
            if '/radar/timeline/mosaic-hybrid/' in u and resp.status==200 and u not in captured:
                latest=resp.body(); captured.append(u); m=re.search(r'ph_hybrid_mosaic_(\d{14})',u); ts=m.group(1) if m else 'latest'; (OUTPUT_DIR/f'radar_{ts}.png').write_bytes(latest)
        page.on('response',on_response)
        page.goto(RADAR_PAGE,wait_until='networkidle',timeout=60000); page.wait_for_timeout(10000)
        # The raw radar image is the authoritative diagnostic target. We therefore
        # analyze the complete PNG rather than repeatedly attempting to access the
        # page's private OpenLayers map variable.
        for i,f in enumerate(page.frames):
            try: frame_states.append({'frame_index':i,'url':f.url,'ol':f.evaluate('() => !!window.ol'),'scripts':f.evaluate("() => [...document.querySelectorAll('script[src]')].map(x=>x.src).filter(x=>x.includes('/app/radar/map.js'))")})
            except Exception as e: frame_states.append({'frame_index':i,'error':str(e)})
        urls=[u for f in frame_states for u in f.get('scripts',[])]; urls=list(dict.fromkeys(urls))
        if urls:
            try:
                r=page.request.get(urls[0]); t=r.text(); SCRIPT_FILE.write_text(t,encoding='utf-8'); script_result={'url':urls[0],'status':r.status,'bytes':len(t),'radarBoundaries':extract_assignment(t,'radarBoundaries'),'images':extract_assignment(t,'images')}
            except Exception as e:script_result={'url':urls[0],'error':str(e)}
        page.screenshot(path=str(OUTPUT_DIR/'pagasa_radar_page.png'),full_page=True); browser.close()
    if latest is None:raise RuntimeError('No PAGASA hybrid radar image captured')
    result={'frames':frame_states,'map_script':script_result,'captured_images':captured,'full_image_analysis':analyze(latest)}
    MAP_STATE_FILE.write_text(json.dumps(result,indent=2),encoding='utf-8'); STATE_FILE.write_text(json.dumps({'success':True,'checked_at':datetime.now(PH_TZ).isoformat(),'image_url':captured[-1],'localized_image':str(OUTPUT_DIR/'mandaluyong_radar_localized.png')},indent=2),encoding='utf-8'); print(json.dumps(result,indent=2))
if __name__=='__main__':main()
