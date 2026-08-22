import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")
MAP_STATE_FILE = Path("radar_map_state.json")
PH_TZ = timezone(timedelta(hours=8))


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    captured = []
    map_state = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def handle_response(response):
            url = response.url
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200:
                return
            if url in captured:
                return
            try:
                body = response.body()
                match = re.search(r"ph_hybrid_mosaic_(\d{14})", url)
                timestamp = match.group(1) if match else None
                filename = f"radar_{timestamp}.png" if timestamp else "radar_latest.png"
                path = OUTPUT_DIR / filename
                path.write_bytes(body)
                captured.append(url)
                print("RADAR IMAGE CAPTURED")
                print(url)
                print("SAVED:", path)
                print("BYTES:", len(body))
                print("TIMESTAMP:", timestamp)
            except Exception as exc:
                print("RADAR CAPTURE ERROR:", exc)

        page.on("response", handle_response)
        print("=" * 70)
        print("OPENING PAGASA RADAR")
        print("=" * 70)
        page.goto(RADAR_PAGE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(10000)

        map_state = page.evaluate("""
        () => {
            const result = { globals: [], leaflet_containers: [], candidates: [] };

            for (const key of Object.keys(window)) {
                try {
                    const value = window[key];
                    if (value && typeof value.getBounds === "function" && typeof value.getCenter === "function") {
                        const bounds = value.getBounds();
                        const center = value.getCenter();
                        result.candidates.push({
                            name: key,
                            center: {lat: center.lat, lng: center.lng},
                            bounds: {
                                north: bounds.getNorth(),
                                south: bounds.getSouth(),
                                east: bounds.getEast(),
                                west: bounds.getWest()
                            },
                            zoom: typeof value.getZoom === "function" ? value.getZoom() : null
                        });
                    }
                } catch (e) {}
            }

            document.querySelectorAll(".leaflet-container").forEach((element, index) => {
                const rect = element.getBoundingClientRect();
                result.leaflet_containers.push({
                    index,
                    width: rect.width,
                    height: rect.height,
                    leaflet_id: element._leaflet_id || null
                });
            });

            return result;
        }
        """)

        print("=" * 70)
        print("MAP DISCOVERY")
        print("=" * 70)
        print(json.dumps(map_state, indent=2))
        MAP_STATE_FILE.write_text(json.dumps(map_state, indent=2), encoding="utf-8")
        page.screenshot(path="radar_data/pagasa_radar_page.png", full_page=True)
        browser.close()

    now = datetime.now(PH_TZ)
    images = sorted(OUTPUT_DIR.glob("radar_*.png"))
    state = {
        "success": bool(captured),
        "checked_at": now.isoformat(),
        "source": RADAR_PAGE,
        "product": "PAGASA Radar Mosaic Hybrid Reflectivity",
        "image_url": captured[-1] if captured else None,
        "image_file": str(images[-1]) if images else None,
        "map_state_file": str(MAP_STATE_FILE),
        "next_phase": "georeference radar pixels to latitude/longitude"
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("=" * 70)
    print("PAGASA RADAR GEOSPATIAL PROBE")
    print("=" * 70)
    print(json.dumps(state, indent=2))

    if not captured:
        raise RuntimeError("No PAGASA hybrid radar image was captured.")


if __name__ == "__main__":
    main()
