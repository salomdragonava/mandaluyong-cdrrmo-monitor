import io
import json
import re
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from radar_monitor import analyze, track, MAX_MOVEMENT_KM_PER_FRAME, OUTPUT_DIR, STATE_FILE, MAP_STATE_FILE, PREVIOUS_IMAGE

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
PH_TZ = timezone(timedelta(hours=8))
MOSAIC_RE = re.compile(r"ph_hybrid_mosaic_(\d{14})")
MOSAIC_URL_RE = re.compile(r"https?[^\"'\s]+/radar/timeline/mosaic-hybrid/[^\"'\s]+")


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


def persist(path, body):
    path.write_bytes(body)
    return body


def timestamp_from_url(url):
    match = MOSAIC_RE.search(url)
    return match.group(1) if match else None


def valid_image(body):
    return bool(body) and body.startswith((b"\x89PNG", b"\xff\xd8"))


def direct_download(url):
    diagnostics = {
        "url": url,
        "method": "urllib",
        "status": None,
        "content_type": None,
        "bytes": 0,
        "signature": None,
        "valid_image": False,
        "tls_fallback": False,
        "error": None,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36",
        "Referer": RADAR_PAGE,
        "Accept": "image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8",
    }
    try:
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as response:
                diagnostics["status"] = getattr(response, "status", None)
                diagnostics["content_type"] = response.headers.get("Content-Type")
                body = response.read()
        except (ssl.SSLError, OSError) as first_error:
            diagnostics["tls_fallback"] = True
            diagnostics["first_error"] = repr(first_error)
            with urlopen(req, timeout=30, context=ssl._create_unverified_context()) as response:
                diagnostics["status"] = getattr(response, "status", None)
                diagnostics["content_type"] = response.headers.get("Content-Type")
                body = response.read()
        diagnostics["bytes"] = len(body)
        diagnostics["signature"] = body[:16].hex()
        diagnostics["valid_image"] = valid_image(body)
        if not diagnostics["valid_image"]:
            diagnostics["error"] = "Response is not PNG/JPEG"
            return None, diagnostics
        return body, diagnostics
    except Exception as exc:
        diagnostics["error"] = repr(exc)
        return None, diagnostics


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    previous_timestamp, previous_body = load_previous()
    captured = None
    discovered_urls = []
    diagnostics = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def route_handler(route):
            request = route.request
            url = request.url
            if "/radar/timeline/mosaic-hybrid/" in url:
                route.continue_()
            elif request.resource_type in {"document", "script", "stylesheet", "xhr", "fetch"}:
                route.continue_()
            else:
                route.abort()

        page.route("**/*", route_handler)

        def on_response(response):
            nonlocal captured
            url = response.url
            if "/radar/timeline/mosaic-hybrid/" not in url:
                return
            if url not in discovered_urls:
                discovered_urls.append(url)
            if captured is not None or response.status != 200:
                return
            timestamp = timestamp_from_url(url)
            if not timestamp or timestamp == previous_timestamp:
                return
            try:
                body = response.body()
            except Exception:
                return
            if valid_image(body):
                captured = {"url": url, "timestamp": timestamp, "body": body, "method": "playwright_response"}

        page.on("response", on_response)
        page.goto(RADAR_PAGE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(12000)

        try:
            resource_urls = page.evaluate(
                """() => performance.getEntriesByType('resource').map(e => e.name).filter(u => u.includes('/radar/timeline/mosaic-hybrid/'))"""
            )
        except Exception:
            resource_urls = []
        for url in resource_urls:
            if url not in discovered_urls:
                discovered_urls.append(url)

        try:
            script_urls = page.evaluate(
                """() => [...document.scripts].map(s => s.src).filter(Boolean).filter(u => /radar|map/i.test(u))"""
            )
            for script_url in script_urls:
                try:
                    response = page.request.get(script_url, timeout=30000)
                    if response.ok:
                        text = response.text()
                        for match in MOSAIC_URL_RE.findall(text):
                            discovered_urls.append(match.replace("\\u0026", "&"))
                except Exception:
                    pass
        except Exception:
            pass

        browser.close()

    discovered_urls = list(dict.fromkeys(u for u in discovered_urls if "/radar/timeline/mosaic-hybrid/" in u))

    if captured is None:
        candidates = []
        for url in discovered_urls:
            ts = timestamp_from_url(url)
            if ts:
                candidates.append((ts, url))
        candidates.sort(reverse=True)
        for ts, url in candidates:
            body, diag = direct_download(url)
            diagnostics.append(diag)
            if body is not None and (previous_timestamp is None or ts != previous_timestamp):
                captured = {"url": url, "timestamp": ts, "body": body, "method": "direct_download"}
                break

    if captured is None:
        result = {
            "success": False,
            "checked_at": datetime.now(PH_TZ).isoformat(),
            "image_timestamp": previous_timestamp,
            "tracking_status": "source_unreachable" if not discovered_urls else "stale_or_unusable_source",
            "resource_urls": discovered_urls,
            "download_diagnostics": diagnostics,
            "message": "No newer usable PAGASA radar frame was captured. A known previous frame is retained separately from source availability.",
        }
        MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return

    current_timestamp = captured["timestamp"]
    current_body = captured["body"]
    current_path = OUTPUT_DIR / f"radar_{current_timestamp}.png"
    persist(current_path, current_body)

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
    MAP_STATE_FILE.write_text(
        json.dumps(
            {
                "frames": [{
                    "url": captured["url"],
                    "timestamp": current_timestamp,
                    "path": str(current_path),
                    "bytes": len(current_body),
                    "analysis": current_analysis,
                    "source": captured["method"],
                }],
                "captured_images": [captured["url"]],
                "resource_urls": discovered_urls,
                "radar_tracking": tracking,
                "collector": {
                    "mode": "robust_lightweight",
                    "bounded_wait_seconds": 12,
                    "direct_download_fallback": True,
                    "tls_fallback": True,
                    "download_diagnostics": diagnostics,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    STATE_FILE.write_text(
        json.dumps(
            {
                "success": True,
                "checked_at": datetime.now(PH_TZ).isoformat(),
                "image_url": captured["url"],
                "image_timestamp": current_timestamp,
                "localized_image": "radar_data/mandaluyong_radar_localized.png",
                "tracking_status": tracking["status"],
                "collector_mode": captured["method"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"success": True, "tracking_status": tracking["status"], "image_timestamp": current_timestamp, "source": captured["method"]}, indent=2))


if __name__ == "__main__":
    main()
