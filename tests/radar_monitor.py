import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")
MAP_STATE_FILE = Path("radar_map_state.json")
SCRIPT_FILE = Path("radar_data/pagasa_radar_map.js")
PH_TZ = timezone(timedelta(hours=8))


def inspect_frame(frame):
    return frame.evaluate("""
    () => {
      const out = {url: location.href, title: document.title, libraries:{}, canvases:[], map_elements:[], scripts:[]};
      for (const [n,k] of [['openlayers','ol'],['leaflet','L'],['mapboxgl','mapboxgl'],['maplibregl','maplibregl']]) {
        try { out.libraries[n] = !!window[k]; } catch(e) { out.libraries[n] = false; }
      }
      document.querySelectorAll('canvas').forEach((e,i)=>{
        const r=e.getBoundingClientRect(); out.canvases.push({index:i,width:e.width,height:e.height,css_width:r.width,css_height:r.height});
      });
      document.querySelectorAll('[class*="map"],[class*="ol-"]').forEach((e,i)=>{
        if(i>=100)return; const r=e.getBoundingClientRect();
        out.map_elements.push({index:i,tag:e.tagName,id:e.id||'',class:typeof e.className==='string'?e.className:'',width:r.width,height:r.height});
      });
      document.querySelectorAll('script[src]').forEach(e=>{if(/radar|map|meteopilipinas/i.test(e.src))out.scripts.push(e.src)});
      return out;
    }
    """)


def inspect_map_script(text):
    patterns = {
        "new_ol_Map": r"new\s+ol\.Map",
        "ol_View": r"ol\.View",
        "projection": r"projection",
        "extent": r"extent",
        "center": r"center",
        "zoom": r"zoom",
        "mosaic": r"mosaic",
        "hybrid": r"hybrid",
        "ImageStatic": r"ImageStatic",
        "ImageLayer": r"ImageLayer",
        "XYZ": r"XYZ",
        "TileLayer": r"TileLayer",
    }
    counts = {k: len(re.findall(v,text,re.I)) for k,v in patterns.items()}

    contexts = {}
    terms = ["new ol.Map", "ol.View", "projection", "extent", "ImageStatic", "mosaic-hybrid"]
    for term in terms:
        matches = list(re.finditer(re.escape(term), text, re.I))
        contexts[term] = [text[max(0,m.start()-500):min(len(text),m.end()+1200)] for m in matches[:5]]

    # Extract likely geographic numeric arrays and radar URL fragments for later inspection.
    urls = sorted(set(re.findall(r'https?[^\"\']+', text)))
    radar_urls = [u for u in urls if re.search(r'radar|mosaic|hybrid|meteopilipinas',u,re.I)]

    return {"bytes":len(text),"pattern_counts":counts,"contexts":contexts,"radar_urls":radar_urls}


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    captured=[]; frame_states=[]; script_urls=[]; script_result={}

    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=p.chromium.launch_persistent_context if False else None
        page=browser.new_page()

        def on_response(response):
            url=response.url
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200 or url in captured:
                return
            try:
                body=response.body()
                m=re.search(r"ph_hybrid_mosaic_(\d{14})",url)
                ts=m.group(1) if m else None
                path=OUTPUT_DIR/(f"radar_{ts}.png" if ts else "radar_latest.png")
                path.write_bytes(body); captured.append(url)
                print("RADAR IMAGE:",url)
                print("SAVED:",path,"BYTES:",len(body))
            except Exception as e: print("RADAR CAPTURE ERROR:",e)

        page.on("response",on_response)
        print("OPENING",RADAR_PAGE)
        page.goto(RADAR_PAGE,wait_until="networkidle",timeout=60000)
        page.wait_for_timeout(10000)

        for i,frame in enumerate(page.frames):
            try:
                s=inspect_frame(frame); s["frame_index"]=i; frame_states.append(s); script_urls += s.get("scripts",[])
            except Exception as e: frame_states.append({"frame_index":i,"url":frame.url,"error":str(e)})
        script_urls=list(dict.fromkeys(script_urls))

        for url in script_urls:
            if "/app/radar/map.js" not in url: continue
            try:
                r=page.request.get(url,timeout=60000); text=r.text(); SCRIPT_FILE.write_text(text,encoding="utf-8")
                script_result={"url":url,"status":r.status,**inspect_map_script(text),"saved_file":str(SCRIPT_FILE)}
                print("MAP SCRIPT STATUS:",r.status,"BYTES:",len(text))
            except Exception as e: script_result={"url":url,"error":str(e)}
            break

        page.screenshot(path=str(OUTPUT_DIR/"pagasa_radar_page.png"),full_page=True)
        browser.close()

    MAP_STATE_FILE.write_text(json.dumps({"frames":frame_states,"map_script":script_result},indent=2),encoding="utf-8")
    images=sorted(OUTPUT_DIR.glob("radar_*.png"))
    state={"success":bool(captured),"checked_at":datetime.now(PH_TZ).isoformat(),"source":RADAR_PAGE,"product":"PAGASA Radar Mosaic Hybrid Reflectivity","image_url":captured[-1] if captured else None,"image_file":str(images[-1]) if images else None,"map_state_file":str(MAP_STATE_FILE),"map_script_file":str(SCRIPT_FILE)}
    STATE_FILE.write_text(json.dumps(state,indent=2),encoding="utf-8")
    print(json.dumps(state,indent=2))
    if not captured: raise RuntimeError("No PAGASA hybrid radar image was captured.")

if __name__ == "__main__": main()
