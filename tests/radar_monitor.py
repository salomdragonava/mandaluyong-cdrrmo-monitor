import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")
MAP_STATE_FILE = Path("radar_map_state.json")
SCRIPT_FILE = OUTPUT_DIR / "pagasa_radar_map.js"
PH_TZ = timezone(timedelta(hours=8))


def extract_assignment(text, name):
    # Capture a JS object/array assignment for the named variable, even when
    # the value is nested across multiple lines. This is diagnostic only.
    m = re.search(rf"(?:const|let|var)?\s*{re.escape(name)}\s*=\s*", text, re.I)
    if not m:
        return None
    start = m.end()
    while start < len(text) and text[start].isspace():
        start += 1
    opener = text[start:start+1]
    if opener not in "[{":
        return text[start:start+500]
    closer = "]" if opener == "[" else "}"
    depth = 0
    quote = None
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
            continue
        if c in "'\"`":
            quote = c
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return text[start:start+2000]


def extract_radar_config(text):
    result = {}
    for name in ["radarBoundaries", "images", "radarImages", "products"]:
        value = extract_assignment(text, name)
        if value is not None:
            result[name] = value

    # Also capture every occurrence around radarBoundaries, including when it
    # is assigned indirectly from another configuration object.
    contexts = []
    for m in re.finditer(r"radarBoundaries", text, re.I):
        contexts.append(text[max(0, m.start()-1000):min(len(text), m.end()+2500)])
    result["radarBoundaries_contexts"] = contexts[:10]
    return result


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    captured = []
    frame_states = []
    script_urls = []
    script_result = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_response(response):
            url = response.url
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200 or url in captured:
                return
            try:
                body = response.body()
                m = re.search(r"ph_hybrid_mosaic_(\d{14})", url)
                ts = m.group(1) if m else None
                path = OUTPUT_DIR / (f"radar_{ts}.png" if ts else "radar_latest.png")
                path.write_bytes(body)
                captured.append(url)
                print("RADAR IMAGE:", url)
                print("SAVED:", path, "BYTES:", len(body))
            except Exception as e:
                print("RADAR CAPTURE ERROR:", e)

        page.on("response", on_response)
        print("OPENING", RADAR_PAGE)
        page.goto(RADAR_PAGE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(10000)

        for i, frame in enumerate(page.frames):
            try:
                state = frame.evaluate("""
                () => ({
                  url: location.href,
                  title: document.title,
                  libraries: {openlayers: !!window.ol},
                  canvases: [...document.querySelectorAll('canvas')].map((e,i)=>({index:i,width:e.width,height:e.height})),
                  scripts: [...document.querySelectorAll('script[src]')]
                    .map(e=>e.src).filter(x=>/radar|map|meteopilipinas/i.test(x))
                })
                """)
                state["frame_index"] = i
                frame_states.append(state)
                script_urls.extend(state.get("scripts", []))
            except Exception as e:
                frame_states.append({"frame_index": i, "url": frame.url, "error": str(e)})

        script_urls = list(dict.fromkeys(script_urls))

        for url in script_urls:
            if "/app/radar/map.js" not in url:
                continue
            try:
                response = page.request.get(url, timeout=60000)
                text = response.text()
                SCRIPT_FILE.write_text(text, encoding="utf-8")
                config = extract_radar_config(text)
                script_result = {
                    "url": url,
                    "status": response.status,
                    "bytes": len(text),
                    "config": config,
                    "saved_file": str(SCRIPT_FILE),
                }
                print("MAP SCRIPT STATUS:", response.status)
                print("RADAR CONFIG EXTRACTION:")
                print(json.dumps(config, indent=2))
            except Exception as e:
                script_result = {"url": url, "error": str(e)}
            break

        page.screenshot(path=str(OUTPUT_DIR / "pagasa_radar_page.png"), full_page=True)
        browser.close()

    result = {
        "frames": frame_states,
        "map_script": script_result,
        "captured_images": captured,
    }
    MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    images = sorted(OUTPUT_DIR.glob("radar_*.png"))
    state = {
        "success": bool(captured),
        "checked_at": datetime.now(PH_TZ).isoformat(),
        "source": RADAR_PAGE,
        "product": "PAGASA Radar Mosaic Hybrid Reflectivity",
        "image_url": captured[-1] if captured else None,
        "image_file": str(images[-1]) if images else None,
        "map_state_file": str(MAP_STATE_FILE),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, indent=2))

    if not captured:
        raise RuntimeError("No PAGASA hybrid radar image was captured.")


if __name__ == "__main__":
    main()
