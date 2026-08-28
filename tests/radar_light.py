import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from radar_monitor import analyze, track, TARGET_LAT, TARGET_LON, MAX_MOVEMENT_KM_PER_FRAME, OUTPUT_DIR, STATE_FILE, MAP_STATE_FILE, PREVIOUS_IMAGE

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
PH_TZ = timezone(timedelta(hours=8))
MOSAIC_RE = re.compile(r"ph_hybrid_mosaic_(\d{14})")


def load_previous():
    if not PREVIOUS_IMAGE.exists() or not STATE_FILE.exists():
        return None, None
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        timestamp = state.get("image_timestamp")
        body = PREVIOUS_IMAGE.read_bytes()
        if timestamp and body:
            return timestamp, body
    except Exception:
        pass
    return None, None


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    previous_timestamp, previous_body = load_previous()
    captured = None
    resource_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Keep the browser only as a lightweight discovery mechanism. Block
        # unrelated images, fonts, media, analytics and other page assets.
        def route_handler(route):
            request = route.request
            url = request.url
            resource_type = request.resource_type
            if "/radar/timeline/mosaic-hybrid/" in url:
                route.continue_()
            elif resource_type in {"document", "script", "stylesheet", "xhr", "fetch"}:
                route.continue_()
            else:
                route.abort()

        page.route("**/*", route_handler)

        def on_response(response):
            nonlocal captured
            url = response.url
            if captured is not None:
                return
            if "/radar/timeline/mosaic-hybrid/" not in url or response.status != 200:
                return
            match = MOSAIC_RE.search(url)
            if not match:
                return
            timestamp = match.group(1)
            if timestamp == previous_timestamp:
                return
            try:
                body = response.body()
            except Exception:
                return
            if not body.startswith((b"\x89PNG", b"\xff\xd8")):
                return
            captured = {"url": url, "timestamp": timestamp, "body": body}

        page.on("response", on_response)
        page.goto(RADAR_PAGE, wait_until="domcontentloaded", timeout=60000)

        # Give the radar application a short, bounded window to request the
        # current mosaic. No network-idle wait and no extra resource downloads.
        try:
            page.wait_for_timeout(6000)
        except Exception:
            pass

        try:
            resource_urls = page.evaluate(
                """() => performance.getEntriesByType('resource').map(e => e.name).filter(u => u.includes('/radar/timeline/mosaic-hybrid/'))"""
            )
        except Exception:
            resource_urls = []

        browser.close()

    if captured is None:
        result = {
            "success": False,
            "checked_at": datetime.now(PH_TZ).isoformat(),
            "image_timestamp": previous_timestamp,
            "tracking_status": "no_new_frame",
            "resource_urls": list(dict.fromkeys(resource_urls)),
            "message": "No newer PAGASA radar frame was captured during the bounded discovery window.",
        }
        MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return

    current_timestamp = captured["timestamp"]
    current_body = captured["body"]
    current_path = OUTPUT_DIR / f"radar_{current_timestamp}.png"
    current_path.write_bytes(current_body)

    current_analysis = analyze(current_body, False)
    previous_analysis = analyze(previous_body, False) if previous_body else None

    if previous_analysis and previous_timestamp != current_timestamp:
        matches = track(previous_analysis, current_analysis)
        tracking = {
            "status": "ok",
            "previous_timestamp": previous_timestamp,
            "current_timestamp": current_timestamp,
            "matches_count": len(matches),
            "approaching_matches_count": sum(1 for m in matches if m["approaching_mandaluyong"]),
            "matches": matches,
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }
    elif previous_timestamp == current_timestamp:
        tracking = {
            "status": "unchanged_frame",
            "previous_timestamp": previous_timestamp,
            "current_timestamp": current_timestamp,
            "matches_count": 0,
            "approaching_matches_count": 0,
            "matches": [],
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }
    else:
        tracking = {
            "status": "insufficient_frames",
            "previous_timestamp": None,
            "current_timestamp": current_timestamp,
            "matches_count": 0,
            "approaching_matches_count": 0,
            "matches": [],
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }

    PREVIOUS_IMAGE.write_bytes(current_body)

    result = {
        "frames": [
            {
                "url": captured["url"],
                "timestamp": current_timestamp,
                "path": str(current_path),
                "bytes": len(current_body),
                "analysis": current_analysis,
                "source": "lightweight_playwright",
            }
        ],
        "captured_images": [captured["url"]],
        "resource_urls": list(dict.fromkeys(resource_urls)),
        "radar_tracking": tracking,
        "collector": {
            "mode": "lightweight",
            "blocked_unrelated_resources": True,
            "additional_radar_downloads": 0,
            "bounded_wait_seconds": 6,
        },
    }
    MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    STATE_FILE.write_text(
        json.dumps(
            {
                "success": True,
                "checked_at": datetime.now(PH_TZ).isoformat(),
                "image_url": captured["url"],
                "image_timestamp": current_timestamp,
                "localized_image": "radar_data/mandaluyong_radar_localized.png",
                "tracking_status": tracking["status"],
                "collector_mode": "lightweight",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
