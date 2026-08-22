import io
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")
MAP_STATE_FILE = Path("radar_map_state.json")
SCRIPT_FILE = OUTPUT_DIR / "pagasa_radar_map.js"
PH_TZ = timezone(timedelta(hours=8))
TARGET_LAT, TARGET_LON = 14.5794, 121.0359
WEST, SOUTH = 115.969111093, 3.80912641587
EAST, NORTH = 129.511990464, 22.322581275


def extract_assignment(text, name):
    m = re.search(rf"(?:const|let|var)?\s*{re.escape(name)}\s*=\s*", text, re.I)
    if not m: return None
    start = m.end()
    while start < len(text) and text[start].isspace(): start += 1
    opener = text[start:start+1]
    if opener not in "[{": return text[start:start+500]
    closer = "]" if opener == "[" else "}"
    depth = 0; quote = None; escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            if escaped: escaped = False
            elif c == "\\": escaped = True
            elif c == quote: quote = None
            continue
        if c in "'\"`": quote = c
        elif c == opener: depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0: return text[start:i+1]
    return text[start:start+2000]


def extract_radar_config(text):
    result = {}
    for name in ["radarBoundaries", "images", "radarImages", "products"]:
        value = extract_assignment(text, name)
        if value is not None: result[name] = value
    return result


def lonlat_to_pixel(lon, lat, width, height):
    return ((lon-WEST)/(EAST-WEST)*width, (NORTH-lat)/(NORTH-SOUTH)*height)


def analyze_radar(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size
    tx, ty = lonlat_to_pixel(TARGET_LON, TARGET_LAT, width, height)
    lat_radius = 25 / 111.0
    lon_radius = 25 / (111.0 * math.cos(math.radians(TARGET_LAT)))
    x0, y0 = lonlat_to_pixel(TARGET_LON-lon_radius, TARGET_LAT+lat_radius, width, height)
    x1, y1 = lonlat_to_pixel(TARGET_LON+lon_radius, TARGET_LAT-lat_radius, width, height)
    x0, x1 = max(0,int(min(x0,x1))), min(width,int(max(x0,x1)))
    y0, y1 = max(0,int(min(y0,y1))), min(height,int(max(y0,y1)))

    pixels = image.load()
    total = 0
    alpha_counts = Counter()
    rgba_counts = Counter()
    nonwhite = 0
    nontransparent = 0
    strong_candidates = []

    for y in range(y0,y1):
        for x in range(x0,x1):
            rgba = pixels[x,y]
            r,g,b,a = rgba
            total += 1
            alpha_counts[a] += 1
            rgba_counts[rgba] += 1
            if a > 10: nontransparent += 1
            if a > 10 and not (r > 245 and g > 245 and b > 245):
                nonwhite += 1
                # Bright saturated colors are retained for palette calibration.
                if max(r,g,b)-min(r,g,b) >= 25:
                    strong_candidates.append(rgba)

    target_rgba = pixels[min(width-1,max(0,round(tx))), min(height-1,max(0,round(ty)))]
    top_colors = [[list(c), n] for c,n in rgba_counts.most_common(30)]
    top_colored = [[list(c), n] for c,n in Counter(strong_candidates).most_common(30)]

    annotated=image.copy(); draw=ImageDraw.Draw(annotated)
    radius_px=max(4,int(2.5*width/(EAST-WEST)))
    draw.ellipse((tx-radius_px,ty-radius_px,tx+radius_px,ty+radius_px),outline=(255,0,0,255),width=3)
    draw.line((tx-radius_px*2,ty,tx+radius_px*2,ty),fill=(255,0,0,255),width=2)
    draw.line((tx,ty-radius_px*2,tx,ty+radius_px*2),fill=(255,0,0,255),width=2)
    annotated.save(OUTPUT_DIR/"mandaluyong_radar_localized.png")

    return {
        "image_size":[width,height],
        "target":{"lat":TARGET_LAT,"lon":TARGET_LON},
        "target_pixel":{"x":round(tx,2),"y":round(ty,2)},
        "analysis_box_pixels":[x0,y0,x1,y1],
        "analysis_radius_km":25,
        "analyzed_pixels":total,
        "nontransparent_pixels":nontransparent,
        "nontransparent_fraction":round(nontransparent/total,5) if total else 0,
        "nonwhite_colored_pixels":nonwhite,
        "nonwhite_colored_fraction":round(nonwhite/total,5) if total else 0,
        "target_rgba":list(target_rgba),
        "top_rgba":top_colors,
        "top_colored_rgba":top_colored,
        "alpha_distribution":[[a,n] for a,n in alpha_counts.most_common(20)],
        "note":"Diagnostic palette/alpha analysis only. No dBZ or alert threshold is assigned yet."
    }


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    captured=[]; frame_states=[]; script_urls=[]; script_result={}; latest_image_bytes=None
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); page=browser.new_page()
        def on_response(response):
            nonlocal latest_image_bytes
            url=response.url
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200 or url in captured: return
            try:
                body=response.body(); latest_image_bytes=body
                m=re.search(r"ph_hybrid_mosaic_(\d{14})",url); ts=m.group(1) if m else None
                path=OUTPUT_DIR/(f"radar_{ts}.png" if ts else "radar_latest.png"); path.write_bytes(body); captured.append(url)
                print("RADAR IMAGE:",url); print("SAVED:",path,"BYTES:",len(body))
            except Exception as e: print("RADAR CAPTURE ERROR:",e)
        page.on("response",on_response)
        page.goto(RADAR_PAGE,wait_until="networkidle",timeout=60000); page.wait_for_timeout(10000)
        for i,frame in enumerate(page.frames):
            try:
                state=frame.evaluate("""() => ({url:location.href,title:document.title,libraries:{openlayers:!!window.ol},canvases:[...document.querySelectorAll('canvas')].map((e,i)=>({index:i,width:e.width,height:e.height})),scripts:[...document.querySelectorAll('script[src]')].map(e=>e.src).filter(x=>/radar|map|meteopilipinas/i.test(x))})""")
                state["frame_index"]=i; frame_states.append(state); script_urls.extend(state.get("scripts",[]))
            except Exception as e: frame_states.append({"frame_index":i,"url":frame.url,"error":str(e)})
        for url in list(dict.fromkeys(script_urls)):
            if "/app/radar/map.js" not in url: continue
            try:
                response=page.request.get(url,timeout=60000); text=response.text(); SCRIPT_FILE.write_text(text,encoding="utf-8")
                script_result={"url":url,"status":response.status,"bytes":len(text),"config":extract_radar_config(text),"saved_file":str(SCRIPT_FILE)}
            except Exception as e: script_result={"url":url,"error":str(e)}
            break
        page.screenshot(path=str(OUTPUT_DIR/"pagasa_radar_page.png"),full_page=True); browser.close()

    if not latest_image_bytes: raise RuntimeError("No PAGASA hybrid radar image was captured.")
    analysis=analyze_radar(latest_image_bytes)
    result={"frames":frame_states,"map_script":script_result,"captured_images":captured,"mandaluyong_analysis":analysis}
    MAP_STATE_FILE.write_text(json.dumps(result,indent=2),encoding="utf-8")
    images=sorted(OUTPUT_DIR.glob("radar_*.png"))
    state={"success":True,"checked_at":datetime.now(PH_TZ).isoformat(),"source":RADAR_PAGE,"product":"PAGASA Radar Mosaic Hybrid Reflectivity","image_url":captured[-1],"image_file":str(images[-1]) if images else None,"localized_image":str(OUTPUT_DIR/"mandaluyong_radar_localized.png"),"map_state_file":str(MAP_STATE_FILE)}
    STATE_FILE.write_text(json.dumps(state,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))

if __name__ == "__main__": main()
