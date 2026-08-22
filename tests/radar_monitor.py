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


def inspect_frame(frame):
    """Inspect a frame for map libraries, canvases, containers and useful scripts."""
    return frame.evaluate(
        """
        () => {
            const result = {
                url: location.href,
                title: document.title,
                libraries: {},
                map_candidates: [],
                canvases: [],
                map_elements: [],
                scripts: []
            };

            // Common map libraries / globals.
            const checks = [
                ['leaflet', 'L'],
                ['mapboxgl', 'mapboxgl'],
                ['maplibregl', 'maplibregl'],
                ['openlayers', 'ol'],
                ['google_maps', 'google'],
                ['esri', 'esri'],
                ['here', 'H']
            ];

            for (const [name, key] of checks) {
                try {
                    result.libraries[name] = !!window[key];
                } catch (e) {
                    result.libraries[name] = false;
                }
            }

            // Search known globals and common map-like objects.
            const names = Object.keys(window);
            for (const name of names) {
                if (!/map|radar|layer|leaflet|ol|globe/i.test(name)) continue;

                try {
                    const value = window[name];
                    if (!value || typeof value !== 'object') continue;

                    const candidate = { name };

                    if (typeof value.getCenter === 'function') {
                        const center = value.getCenter();
                        if (center) {
                            candidate.center = {
                                lat: center.lat,
                                lng: center.lng
                            };
                        }
                    }

                    if (typeof value.getBounds === 'function') {
                        const bounds = value.getBounds();
                        if (bounds) {
                            candidate.bounds = {
                                north: bounds.getNorth(),
                                south: bounds.getSouth(),
                                east: bounds.getEast(),
                                west: bounds.getWest()
                            };
                        }
                    }

                    if (typeof value.getZoom === 'function') {
                        candidate.zoom = value.getZoom();
                    }

                    if (Object.keys(candidate).length > 1) {
                        result.map_candidates.push(candidate);
                    }
                } catch (e) {}
            }

            // Canvas/WebGL elements are strong indicators of map rendering.
            document.querySelectorAll('canvas').forEach((el, index) => {
                const rect = el.getBoundingClientRect();
                result.canvases.push({
                    index,
                    width: el.width,
                    height: el.height,
                    css_width: rect.width,
                    css_height: rect.height,
                    class: el.className || '',
                    id: el.id || ''
                });
            });

            // Map-like DOM elements, including Leaflet/Mapbox/OL classes.
            document.querySelectorAll('[class*="map"], [class*="leaflet"], [class*="ol-"], [class*="mapbox"]').forEach((el, index) => {
                if (index >= 100) return;
                const rect = el.getBoundingClientRect();
                result.map_elements.push({
                    index,
                    tag: el.tagName,
                    id: el.id || '',
                    class: typeof el.className === 'string' ? el.className : '',
                    width: rect.width,
                    height: rect.height
                });
            });

            // Capture script URLs, especially radar/map code.
            document.querySelectorAll('script[src]').forEach(script => {
                const src = script.src;
                if (/radar|map|weather|hiraia|meteopilipinas/i.test(src)) {
                    result.scripts.push(src);
                }
            });

            return result;
        }
        """
    )


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    captured = []
    frame_states = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def handle_response(response):
            url = response.url

            if "/radar/timeline/mosaic-hybrid/" not in url:
                return
            if response.status != 200:
                return
            if url in captured:
                return

            try:
                body = response.body()
                match = re.search(r"ph_hybrid_mosaic_(\d{14})", url)
                timestamp = match.group(1) if match else None

                filename = (
                    f"radar_{timestamp}.png"
                    if timestamp
                    else "radar_latest.png"
                )

                path = OUTPUT_DIR / filename
                path.write_bytes(body)
                captured.append(url)

                print("RADAR IMAGE CAPTURED:", url)
                print("SAVED:", path)
                print("BYTES:", len(body))
                print("RADAR TIMESTAMP:", timestamp or "unknown")

            except Exception as exc:
                print("RADAR CAPTURE ERROR:", exc)

        page.on("response", handle_response)

        print("=" * 70)
        print("OPENING PAGASA RADAR")
        print("=" * 70)

        page.goto(RADAR_PAGE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(10000)

        # Inspect every frame. The radar/map may be rendered in an iframe.
        for index, frame in enumerate(page.frames):
            try:
                state = inspect_frame(frame)
                state["frame_index"] = index
                frame_states.append(state)
            except Exception as exc:
                frame_states.append({
                    "frame_index": index,
                    "url": frame.url,
                    "error": str(exc)
                })

        print("=" * 70)
        print("FRAME / MAP DISCOVERY")
        print("=" * 70)
        print(json.dumps(frame_states, indent=2))

        page.screenshot(
            path="radar_data/pagasa_radar_page.png",
            full_page=True
        )

        browser.close()

    MAP_STATE_FILE.write_text(
        json.dumps(frame_states, indent=2),
        encoding="utf-8"
    )

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
        "frames_inspected": len(frame_states),
        "next_phase": "identify radar map projection/georeferencing"
    }

    STATE_FILE.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8"
    )

    print("=" * 70)
    print("PAGASA RADAR MAP DISCOVERY")
    print("=" * 70)
    print(json.dumps(state, indent=2))

    if not captured:
        raise RuntimeError("No PAGASA hybrid radar image was captured.")


if __name__ == "__main__":
    main()
