import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")
MAP_STATE_FILE = Path("radar_map_state.json")
SCRIPT_FILE = OUTPUT_DIR / "pagasa_radar_map.js"
PH_TZ = timezone(timedelta(hours=8))

# Mandaluyong / central target used for the first localization test.
TARGET_LAT = 14.5794
TARGET_LON = 121.0359

# PAGASA radar image geographic extent discovered from map.js.
WEST, SOUTH = 115.969111093, 3.80912641587
EAST, NORTH = 129.511990464, 22.322581275


def extract_assignment(text, name):
    m = re.search(rf"(?:const|let|var)?\s*{re.escape(name)}\s*=\s*", text, re.I)
    if not m:
        return None
    start = m.end()
    while start < len(text) and text[start].isspace():
        start += 1
    opener = text[start:start + 1]
    if opener not in "[{":
        return text[start:start + 500]
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
                return text[start:i + 1]
    return text[start:start + 2000]


def extract_radar_config(text):
    result = {}
    for name in ["radarBoundaries", "images", "radarImages", "products"]:
        value = extract_assignment(text, name)
        if value is not None:
            result[name] = value
    contexts = []
    for m in re.finditer(r"radarBoundaries", text, re.I):
        contexts.append(text[max(0, m.start() - 1000):min(len(text), m.end() + 2500)])
    result["radarBoundaries_contexts"] = contexts[:10]
    return result


def lonlat_to_pixel(lon, lat, width, height):
    """Linear mapping for the EPSG:4326 ImageStatic extent used by PAGASA."""
    x = (lon - WEST) / (EAST - WEST) * width
    y = (NORTH - lat) / (NORTH - SOUTH) * height
    return x, y


def pixel_to_lonlat(x, y, width, height):
    lon = WEST + (x / width) * (EAST - WEST)
    lat = NORTH - (y / height) * (NORTH - SOUTH)
    return lon, lat


def analyze_radar(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size
    tx, ty = lonlat_to_pixel(TARGET_LON, TARGET_LAT, width, height)

    # Analyze a ~25 km radius around Mandaluyong. Because this is the first
    # localization stage, classify only whether radar echo is present. We do
    # not yet translate colors to dBZ; PAGASA's reflectivity legend will be
    # calibrated separately before alerts are enabled.
    lat_radius = 25 / 111.0
    lon_radius = 25 / (111.0 * max(0.1, __import__('math').cos(__import__('math').radians(TARGET_LAT))))
    x0, y0 = lonlat_to_pixel(TARGET_LON - lon_radius, TARGET_LAT + lat_radius, width, height)
    x1, y1 = lonlat_to_pixel(TARGET_LON + lon_radius, TARGET_LAT - lat_radius, width, height)
    x0, x1 = max(0, int(min(x0, x1))), min(width, int(max(x0, x1)))
    y0, y1 = max(0, int(min(y0, y1))), min(height, int(max(y0, y1)))

    pixels = image.load()
    echo = 0
    total = 0
    color_counts = {}

    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b, a = pixels[x, y]
            total += 1
            # Radar imagery is normally transparent outside echoes. Exclude
            # fully transparent/near-white background pixels.
            if a > 20 and not (r > 245 and g > 245 and b > 245):
                echo += 1
                key = (r // 16 * 16, g // 16 * 16, b // 16 * 16, a // 32 * 32)
                color_counts[key] = color_counts.get(key, 0) + 1

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    radius_px = max(4, int(2.5 * width / (EAST - WEST)))
    draw.ellipse((tx - radius_px, ty - radius_px, tx + radius_px, ty + radius_px), outline=(255, 0, 0, 255), width=3)
    draw.line((tx - radius_px * 2, ty, tx + radius_px * 2, ty), fill=(255, 0, 0, 255), width=2)
    draw.line((tx, ty - radius_px * 2, tx, ty + radius_px * 2), fill=(255, 0, 0, 255), width=2)
    annotated.save(OUTPUT_DIR / "mandaluyong_radar_localized.png")

    top_colors = sorted(color_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]
    return {
        "image_size": [width, height],
        "target": {"lat": TARGET_LAT, "lon": TARGET_LON},
        "target_pixel": {"x": round(tx, 2), "y": round(ty, 2)},
        "analysis_box_pixels": [x0, y0, x1, y1],
        "analysis_radius_km": 25,
        "echo_pixels": echo,
        "analyzed_pixels": total,
        "echo_fraction": round(echo / total, 5) if total else 0,
        "top_quantized_colors": [[list(k), v] for k, v in top_colors],
        "note": "Echo presence only. dBZ thresholds are intentionally not assigned until the PAGASA reflectivity legend is calibrated."
    }


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    captured = []
    frame_states = []
    script_urls = []
    script_result = {}
    latest_image_bytes = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_response(response):
            nonlocal latest_image_bytes
            url = response.url
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200 or url in captured:
                return
            try:
                body = response.body()
                latest_image_bytes = body
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
                script_result = {"url": url, "status": response.status, "bytes": len(text), "config": extract_radar_config(text), "saved_file": str(SCRIPT_FILE)}
            except Exception as e:
                script_result = {"url": url, "error": str(e)}
            break

        page.screenshot(path=str(OUTPUT_DIR / "pagasa_radar_page.png"), full_page=True)
        browser.close()

    if not latest_image_bytes:
        raise RuntimeError("No PAGASA hybrid radar image was captured.")

    analysis = analyze_radar(latest_image_bytes)
    result = {"frames": frame_states, "map_script": script_result, "captured_images": captured, "mandaluyong_analysis": analysis}
    MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    images = sorted(OUTPUT_DIR.glob("radar_*.png"))
    state = {
        "success": bool(captured),
        "checked_at": datetime.now(PH_TZ).isoformat(),
        "source": RADAR_PAGE,
        "product": "PAGASA Radar Mosaic Hybrid Reflectivity",
        "image_url": captured[-1],
        "image_file": str(images[-1]) if images else None,
        "localized_image": str(OUTPUT_DIR / "mandaluyong_radar_localized.png"),
        "map_state_file": str(MAP_STATE_FILE),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
