import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
OUTPUT_DIR = Path("radar_data")
STATE_FILE = Path("radar_state.json")

PH_TZ = timezone(timedelta(hours=8))


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    captured = []

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
                print(f"RADAR IMAGE CAPTURED: {url}")
                print(f"SAVED: {path}")
                print(f"BYTES: {len(body)}")
                print(f"RADAR TIMESTAMP: {timestamp or 'unknown'}")
            except Exception as exc:
                print(f"RADAR CAPTURE ERROR: {exc}")

        page.on("response", handle_response)

        print(f"Opening {RADAR_PAGE}")
        page.goto(RADAR_PAGE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        browser.close()

    now = datetime.now(PH_TZ)

    state = {
        "success": bool(captured),
        "checked_at": now.isoformat(),
        "source": RADAR_PAGE,
        "product": "PAGASA Radar Mosaic Hybrid Reflectivity",
        "image_url": captured[-1] if captured else None,
        "image_file": str(sorted(OUTPUT_DIR.glob("radar_*.png"))[-1])
        if list(OUTPUT_DIR.glob("radar_*.png"))
        else None,
        "next_phase": "geospatial cell detection and movement tracking",
    }

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print("=" * 70)
    print("PAGASA RADAR CAPTURE PROTOTYPE")
    print("=" * 70)
    print(json.dumps(state, indent=2))

    if not captured:
        raise RuntimeError("No PAGASA hybrid radar image was captured.")


if __name__ == "__main__":
    main()
