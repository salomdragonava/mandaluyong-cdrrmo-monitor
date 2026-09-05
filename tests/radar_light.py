import io
import json
import re
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PIL import Image

from radar_monitor import analyze, track, MAX_MOVEMENT_KM_PER_FRAME, OUTPUT_DIR, STATE_FILE, MAP_STATE_FILE, PREVIOUS_IMAGE

RADAR_PAGE = "https://www.pagasa.dost.gov.ph/radar"
PANAHON_BASE = "https://panahon.gov.ph"
TIMELINE_API = f"{PANAHON_BASE}/api/v1/radar/timeline?sublayer=mosaic-qpe"
PH_TZ = timezone(timedelta(hours=8))
MOSAIC_RE = re.compile(r"ph_hybrid_mosaic_(\d{14})")
TIMESTAMP_RE = re.compile(r"(\d{14})")


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


def valid_image(body):
    return bool(body) and body.startswith((b"\x89PNG", b"\xff\xd8"))


def http_get(url, referer=RADAR_PAGE, timeout=30):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36",
        "Referer": referer,
        "Accept": "application/json,image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8",
    }
    req = Request(url, headers=headers)
    try:
        return urlopen(req, timeout=timeout).read()
    except (ssl.SSLError, OSError):
        return urlopen(req, timeout=timeout, context=ssl._create_unverified_context()).read()


def timestamp_from_value(value):
    text = str(value)
    match = MOSAIC_RE.search(text) or TIMESTAMP_RE.search(text)
    return match.group(1) if match else None


def radar_url(value):
    if not value:
        return None
    value = str(value).replace("\\u0026", "&")
    return urljoin(PANAHON_BASE, value)


def fetch_panahon_timeline():
    diagnostics = {
        "source": "PANaHON",
        "endpoint": TIMELINE_API,
        "status": None,
        "bytes": 0,
        "frame_count": 0,
        "error": None,
    }
    try:
        body = http_get(TIMELINE_API, referer=RADAR_PAGE)
        diagnostics["bytes"] = len(body)
        data = json.loads(body.decode("utf-8"))
        diagnostics["status"] = "ok"
        payload = data.get("data") or {}
        urls = payload.get("image_urls") or []
        observations = payload.get("observation_dates") or {}
        keys = sorted(observations.keys(), key=lambda x: int(x)) if observations else []
        frames = []
        for index, url in enumerate(urls):
            key = keys[index] if index < len(keys) else None
            ts = timestamp_from_value(key) or timestamp_from_value(url)
            resolved = radar_url(url)
            if resolved:
                frames.append({"timestamp": ts, "url": resolved, "index": index})
        diagnostics["frame_count"] = len(frames)
        return frames, diagnostics
    except Exception as exc:
        diagnostics["error"] = repr(exc)
        return [], diagnostics


def download_frame(frame):
    diagnostics = {
        "url": frame["url"],
        "timestamp": frame.get("timestamp"),
        "method": "PANaHON_timeline",
        "status": None,
        "content_type": None,
        "bytes": 0,
        "signature": None,
        "valid_image": False,
        "tls_fallback": False,
        "error": None,
    }
    try:
        body = http_get(frame["url"], referer=PANAHON_BASE)
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


def persist_current(captured, previous_timestamp, previous_body, discovered_urls, diagnostics):
    ts = captured["timestamp"]
    body = captured["body"]
    path = OUTPUT_DIR / f"radar_{ts}.png"
    path.write_bytes(body)
    current_analysis = analyze(body, False)
    previous_analysis = analyze(previous_body, False) if previous_body else None
    if previous_analysis and previous_timestamp != ts:
        matches = track(previous_analysis, current_analysis)
        tracking = {
            "status": "ok",
            "previous_timestamp": previous_timestamp,
            "current_timestamp": ts,
            "matches_count": len(matches),
            "approaching_matches_count": sum(1 for m in matches if m["approaching_mandaluyong"]),
            "matches": matches,
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }
    elif previous_timestamp == ts:
        tracking = {
            "status": "unchanged_frame",
            "previous_timestamp": previous_timestamp,
            "current_timestamp": ts,
            "matches_count": 0,
            "approaching_matches_count": 0,
            "matches": [],
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }
    else:
        tracking = {
            "status": "insufficient_frames",
            "previous_timestamp": None,
            "current_timestamp": ts,
            "matches_count": 0,
            "approaching_matches_count": 0,
            "matches": [],
            "max_movement_km_per_frame": MAX_MOVEMENT_KM_PER_FRAME,
        }
    PREVIOUS_IMAGE.write_bytes(body)
    MAP_STATE_FILE.write_text(json.dumps({
        "frames": [{
            "url": captured["url"],
            "timestamp": ts,
            "path": str(path),
            "bytes": len(body),
            "analysis": current_analysis,
            "source": captured["method"],
        }],
        "captured_images": [captured["url"]],
        "resource_urls": discovered_urls,
        "radar_tracking": tracking,
        "collector": {
            "mode": "PANaHON_API_first",
            "timeline_endpoint": TIMELINE_API,
            "browser_fallback": True,
            "download_diagnostics": diagnostics,
        },
    }, indent=2), encoding="utf-8")
    STATE_FILE.write_text(json.dumps({
        "success": True,
        "checked_at": datetime.now(PH_TZ).isoformat(),
        "image_url": captured["url"],
        "image_timestamp": ts,
        "localized_image": "radar_data/mandaluyong_radar_localized.png",
        "tracking_status": tracking["status"],
        "collector_mode": captured["method"],
    }, indent=2), encoding="utf-8")
    print(json.dumps({"success": True, "tracking_status": tracking["status"], "image_timestamp": ts, "source": captured["method"]}, indent=2))


def browser_fallback(previous_timestamp):
    urls = []
    with __import__("playwright.sync_api", fromlist=["sync_playwright"]).sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", lambda r: urls.append(r.url) if "/radar/timeline/mosaic-hybrid/" in r.url and r.status == 200 else None)
        page.goto(RADAR_PAGE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(12000)
        try:
            urls.extend(page.evaluate("""() => performance.getEntriesByType('resource').map(e => e.name).filter(u => /radar\/timeline\/mosaic-hybrid/i.test(u))"""))
        except Exception:
            pass
        browser.close()
    candidates = []
    for url in dict.fromkeys(urls):
        ts = timestamp_from_value(url)
        if ts:
            candidates.append((ts, url))
    candidates.sort(reverse=True)
    diagnostics = []
    for ts, url in candidates:
        body, diag = download_frame({"timestamp": ts, "url": url})
        diagnostics.append(diag)
        if body is not None and ts != previous_timestamp:
            return {"timestamp": ts, "url": url, "body": body, "method": "browser_fallback"}, urls, diagnostics
    return None, urls, diagnostics


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    previous_timestamp, previous_body = load_previous()
    frames, timeline_diag = fetch_panahon_timeline()
    discovered_urls = [f["url"] for f in frames]
    diagnostics = [timeline_diag]
    captured = None

    for frame in sorted(frames, key=lambda f: f.get("timestamp") or "", reverse=True):
        ts = frame.get("timestamp")
        if not ts or ts == previous_timestamp:
            continue
        body, diag = download_frame(frame)
        diagnostics.append(diag)
        if body is not None:
            captured = {"timestamp": ts, "url": frame["url"], "body": body, "method": "PANaHON_API"}
            break

    if captured is None:
        captured, browser_urls, browser_diagnostics = browser_fallback(previous_timestamp)
        discovered_urls.extend(browser_urls)
        diagnostics.extend(browser_diagnostics)

    discovered_urls = list(dict.fromkeys(discovered_urls))
    if captured is None:
        status = "stale_or_unusable_source" if frames else "source_unreachable"
        result = {
            "success": False,
            "checked_at": datetime.now(PH_TZ).isoformat(),
            "image_timestamp": previous_timestamp,
            "tracking_status": status,
            "resource_urls": discovered_urls,
            "download_diagnostics": diagnostics,
            "message": "No newer usable PAGASA/PANaHON radar frame was captured. The previous frame is retained separately from current source availability.",
        }
        MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return

    persist_current(captured, previous_timestamp, previous_body, discovered_urls, diagnostics)


if __name__ == "__main__":
    main()
