import io, json, math, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter, deque
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

RADAR_PAGE='https://www.pagasa.dost.gov.ph/radar'
OUTPUT_DIR=Path('radar_data'); STATE_FILE=Path('radar_state.json'); MAP_STATE_FILE=Path('radar_map_state.json')
SCRIPT_FILE=OUTPUT_DIR/'pagasa_radar_map.js'; PREVIOUS_IMAGE=OUTPUT_DIR/'previous_radar.png'; PH_TZ=timezone(timedelta(hours=8))
TARGET_LAT,TARGET_LON=14.5794,121.0359
WEST,SOUTH=115.969111093,3.80912641587; EAST,NORTH=129.511990464,22.322581275
COLOR_CLASS_RANGES={'cyan':lambda r,g,b:b>180 and g>180 and r<190,'blue':lambda r,g,b:b>180 and g<190 and r<160,'green':lambda r,g,b:g>180 and r<190 and b<130,'yellow':lambda r,g,b:r>200 and g>190 and b<130,'orange':lambda r,g,b:r>180 and 70<g<200 and b<100,'red':lambda r,g,b:r>150 and g<100 and b<80,'purple':lambda r,g,b:r>100 and b>100 and g<150}

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
def pixel_to_lonlat(x,y,w,h):return WEST+x/w*(EAST-WEST),NORTH-y/h*(NORTH-SOUTH)
def classify(r,g,b):
    for name,fn in COLOR_CLASS_RANGES.items():
        if fn(r,g,b):return name
    return None

def analyze(img_bytes,draw_target=True):
    im=Image.open(io.BytesIO(img_bytes)).convert('RGBA'); w,h=im.size; tx,ty=lonlat_to_pixel(TARGET_LON,TARGET_LAT,w,h); p=im.load(); counts=Counter(); pixels_by_class={k:[] for k in COLOR_CLASS_RANGES}
    for y in range(h):
        for x in range(w):
            r,g,b,a=p[x,y]
            if a<=10:continue
            cls=classify(r,g,b)
            if cls:counts[cls]+=1;pixels_by_class[cls].append((x,y))
    components=[]
    for cls,pts in pixels_by_class.items():
        pts_set=set(pts);seen=set()
        for seed in list(pts_set):
            if seed in seen:continue
            q=deque([seed]);seen.add(seed);comp=[]
            while q:
                x,y=q.popleft();comp.append((x,y))
                for dx in(-1,0,1):
                    for dy in(-1,0,1):
                        if dx or dy:
                            n=(x+dx,y+dy)
                            if n in pts_set and n not in seen:seen.add(n);q.append(n)
            if len(comp)>=8:
                cx=sum(x for x,y in comp)/len(comp);cy=sum(y for x,y in comp)/len(comp);lon,lat=pixel_to_lonlat(cx,cy,w,h)
                dist=111*math.sqrt((lat-TARGET_LAT)**2+(math.cos(math.radians(TARGET_LAT))*(lon-TARGET_LON))**2)
                components.append({'class':cls,'pixels':len(comp),'centroid_pixel':[round(cx,1),round(cy,1)],'centroid_lonlat':[round(lon,4),round(lat,4)],'distance_km':round(dist,1)})
    if draw_target:
        out=im.copy();d=ImageDraw.Draw(out);r=max(4,int(2.5*w/(EAST-WEST)));d.ellipse((tx-r,ty-r,tx+r,ty+r),outline=(255,0,0,255),width=3);d.line((tx-r*2,ty,tx+r*2,ty),fill=(255,0,0,255),width=2);d.line((tx,ty-r*2,tx,ty+r*2),fill=(255,0,0,255),width=2);out.save(OUTPUT_DIR/'mandaluyong_radar_localized.png')
    return {'image_size':[w,h],'target':{'lat':TARGET_LAT,'lon':TARGET_LON},'target_pixel':{'x':round(tx,2),'y':round(ty,2)},'color_class_counts':dict(counts),'components_count':len(components),'components':components,'note':'Color classes are diagnostic only. They are not dBZ or PAGASA rainfall warning thresholds.'}

def track(previous,current):
    matches=[];used=set()
    for a in previous['components']:
        best=None
        for i,b in enumerate(current['components']):
            if i in used or a['class']!=b['class']:continue
            d=math.hypot(b['centroid_pixel'][0]-a['centroid_pixel'][0],b['centroid_pixel'][1]-a['centroid_pixel'][1])
            if d<=90 and(best is None or d<best[0]):best=(d,i,b)
        if best:
            d,i,b=best;used.add(i);lon1,lat1=a['centroid_lonlat'];lon2,lat2=b['centroid_lonlat'];mid=(lat1+lat2)/2
            km=111*math.sqrt((lat2-lat1)**2+(math.cos(math.radians(mid))*(lon2-lon1))**2);bearing=(math.degrees(math.atan2((lon2-lon1)*math.cos(math.radians(mid)),lat2-lat1))+360)%360
            olddist=111*math.sqrt((lat1-TARGET_LAT)**2+(math.cos(math.radians(TARGET_LAT))*(lon1-TARGET_LON))**2)
            matches.append({'class':a['class'],'pixels_previous':a['pixels'],'pixels_current':b['pixels'],'from_lonlat':[lon1,lat1],'to_lonlat':[lon2,lat2],'pixel_displacement':round(d,1),'movement_km':round(km,1),'bearing_deg':round(bearing,1),'previous_distance_km':round(olddist,1),'distance_to_mandaluyong_km':b['distance_km'],'approaching_mandaluyong':b['distance_km']<olddist-2})
    return matches

def main():
    OUTPUT_DIR.mkdir(exist_ok=True);frames=[];captured=[];script_result={};radar_frames=[]
    previous_timestamp=None
    if PREVIOUS_IMAGE.exists() and STATE_FILE.exists():
        try:
            state=json.loads(STATE_FILE.read_text(encoding='utf-8'));previous_timestamp=state.get('image_timestamp');body=PREVIOUS_IMAGE.read_bytes()
            if previous_timestamp:radar_frames.append({'timestamp':previous_timestamp,'path':str(PREVIOUS_IMAGE),'bytes':len(body),'analysis':analyze(body,False),'source':'previous_run'})
        except Exception:pass
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        def on_response(resp):
            u=resp.url
            if '/radar/timeline/mosaic-hybrid/' in u and resp.status==200 and u not in captured:
                body=resp.body();m=re.search(r'ph_hybrid_mosaic_(\d{14})',u);ts=m.group(1) if m else str(len(captured))
                if ts==previous_timestamp:return
                captured.append(u);path=OUTPUT_DIR/f'radar_{ts}.png';path.write_bytes(body);radar_frames.append({'url':u,'timestamp':ts,'path':str(path),'bytes':len(body),'analysis':analyze(body,False),'source':'current_run'})
        page.on('response',on_response);page.goto(RADAR_PAGE,wait_until='networkidle',timeout=60000);page.wait_for_timeout(10000)
        for i,f in enumerate(page.frames):
            try:frames.append({'frame_index':i,'url':f.url,'ol':f.evaluate('() => !!window.ol'),'scripts':f.evaluate("() => [...document.querySelectorAll('script[src]')].map(x=>x.src).filter(x=>x.includes('/app/radar/map.js'))")})
            except Exception as e:frames.append({'frame_index':i,'error':str(e)})
        urls=list(dict.fromkeys([u for f in frames for u in f.get('scripts',[])]))
        if urls:
            try:r=page.request.get(urls[0]);t=r.text();SCRIPT_FILE.write_text(t,encoding='utf-8');script_result={'url':urls[0],'status':r.status,'bytes':len(t),'radarBoundaries':extract_assignment(t,'radarBoundaries'),'images':extract_assignment(t,'images')}
            except Exception as e:script_result={'url':urls[0],'error':str(e)}
        page.screenshot(path=str(OUTPUT_DIR/'pagasa_radar_page.png'),full_page=True);browser.close()
    current_frames=[f for f in radar_frames if f.get('source')=='current_run']
    if not current_frames:
        # Do not fail the workflow when PAGASA returns no radar image. Preserve the prior state.
        result={'frames':frames,'map_script':script_result,'captured_images':captured,'radar_frames':radar_frames,'radar_tracking':{'status':'no_current_frame','message':'PAGASA did not return a new hybrid radar image during this run.'}}
        MAP_STATE_FILE.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2));return
    current=current_frames[-1];radar_frames=([radar_frames[0]] if radar_frames and radar_frames[0].get('source')=='previous_run' else [])+[current]
    if len(radar_frames)==2 and radar_frames[0]['timestamp']!=radar_frames[1]['timestamp']:tracking={'status':'ok','previous_timestamp':radar_frames[0]['timestamp'],'current_timestamp':radar_frames[1]['timestamp'],'matches':track(radar_frames[0]['analysis'],radar_frames[1]['analysis'])}
    else:tracking={'status':'insufficient_frames','message':'Need a prior distinct PAGASA radar frame. The current frame has been persisted for the next run.'}
    PREVIOUS_IMAGE.write_bytes(Path(current['path']).read_bytes())
    result={'frames':frames,'map_script':script_result,'captured_images':captured,'radar_frames':radar_frames,'radar_tracking':tracking};MAP_STATE_FILE.write_text(json.dumps(result,indent=2),encoding='utf-8');STATE_FILE.write_text(json.dumps({'success':True,'checked_at':datetime.now(PH_TZ).isoformat(),'image_url':current['url'],'image_timestamp':current['timestamp'],'localized_image':str(OUTPUT_DIR/'mandaluyong_radar_localized.png'),'tracking_status':tracking['status']},indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
