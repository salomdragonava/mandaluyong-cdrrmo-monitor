import io
import json
import re
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PIL import Image
from playwright.sync_api import sync_playwright

from radar_monitor import analyze, track, MAX_MOVEMENT_KM_PER_FRAME, OUTPUT_DIR, STATE_FILE, MAP_STATE_FILE, PREVIOUS_IMAGE

RADAR_PAGE = 'https://www.pagasa.dost.gov.ph/radar'
PANAHON_BASE = 'https://panahon.gov.ph'
PANAHON_RADAR_PAGE = 'https://panahon.gov.ph/?req=radar.rain-rate&trg=iframe'
TIMELINE_API = f'{PANAHON_BASE}/api/v1/radar/timeline?sublayer=mosaic-qpe'
RAINVIEWER_API = 'https://api.rainviewer.com/public/weather-maps.json'
RAINVIEWER_HOST = 'https://tilecache.rainviewer.com'
MANDALUYONG_LAT = 14.5794
MANDALUYONG_LON = 121.0359
RAINVIEWER_ZOOM = 6
RAINVIEWER_COLOR = 2
RAINVIEWER_SMOOTH = 1
MAX_FRESH_MINUTES = 20
PH_TZ = timezone(timedelta(hours=8))
MOSAIC_RE = re.compile(r'ph_hybrid_mosaic_(\d{14})')
TIMESTAMP_RE = re.compile(r'(\d{14})')


def load_previous():
    if not PREVIOUS_IMAGE.exists() or not STATE_FILE.exists():
        return None, None
    try:
        state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        timestamp = state.get('image_timestamp')
        body = PREVIOUS_IMAGE.read_bytes()
        if timestamp and body:
            return timestamp, body
    except Exception:
        pass
    return None, None


def valid_image(body):
    if not body:
        return False
    try:
        Image.open(io.BytesIO(body)).verify()
        return True
    except Exception:
        return False


def http_get(url, referer=RADAR_PAGE, timeout=30):
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36',
        'Referer': referer,
        'Accept': 'application/json,image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8',
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read(), getattr(response, 'status', None), response.headers.get('Content-Type')
    except ssl.SSLError:
        with urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as response:
            return response.read(), getattr(response, 'status', None), response.headers.get('Content-Type')


def timestamp_from_value(value):
    if value is None:
        return None
    text = str(value).strip()
    match = MOSAIC_RE.search(text) or TIMESTAMP_RE.search(text)
    if match:
        return match.group(1)
    if text.isdigit():
        try:
            epoch = int(text)
            if epoch > 10_000_000_000:
                epoch //= 1000
            if 1_400_000_000 < epoch < 2_100_000_000:
                return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y%m%d%H%M%S')
        except Exception:
            pass
    return None


def radar_url(value):
    if not value:
        return None
    return urljoin(PANAHON_BASE, str(value).replace('\\u0026', '&'))


def timeline_pairs(payload):
    urls = payload.get('image_urls') or []
    observations = payload.get('observation_dates') or []
    if isinstance(observations, dict):
        keys = list(observations.keys())
        values = list(observations.values())
    elif isinstance(observations, list):
        keys = list(range(len(observations)))
        values = observations
    else:
        keys, values = [], []

    frames = []
    for index, raw_url in enumerate(urls):
        resolved = radar_url(raw_url)
        if not resolved:
            continue
        candidates = []
        if index < len(values):
            candidates.append(values[index])
        if index < len(keys):
            candidates.append(keys[index])
        candidates.append(raw_url)
        ts = next((timestamp_from_value(v) for v in candidates if timestamp_from_value(v)), None)
        frames.append({'timestamp': ts, 'url': resolved, 'index': index})
    return frames


def fetch_rainviewer():
    diagnostics = {
        'source': 'RainViewer', 'endpoint': RAINVIEWER_API, 'status': None,
        'content_type': None, 'bytes': 0, 'generated': None, 'frame_count': 0,
        'latest_timestamp': None, 'age_minutes': None, 'error': None,
    }
    try:
        body, status, content_type = http_get(RAINVIEWER_API, referer='https://www.rainviewer.com/')
        diagnostics.update(status=status, content_type=content_type, bytes=len(body))
        payload = json.loads(body.decode('utf-8'))
        diagnostics['generated'] = payload.get('generated')
        frames = (payload.get('radar') or {}).get('past') or []
        diagnostics['frame_count'] = len(frames)
        if not frames:
            diagnostics['error'] = 'RainViewer returned no radar frames'
            return [], diagnostics

        parsed = []
        for frame in frames:
            epoch = frame.get('time')
            path = frame.get('path')
            if not epoch or not path:
                continue
            try:
                epoch = int(epoch)
                ts = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y%m%d%H%M%S')
            except Exception:
                continue
            url = f'{RAINVIEWER_HOST}{path}/512/{RAINVIEWER_ZOOM}/{MANDALUYONG_LAT}/{MANDALUYONG_LON}/{RAINVIEWER_COLOR}/{RAINVIEWER_SMOOTH}_0.png'
            parsed.append({'timestamp': ts, 'epoch': epoch, 'url': url})

        parsed.sort(key=lambda f: f['epoch'], reverse=True)
        if parsed:
            age = max(0, datetime.now(timezone.utc).timestamp() - parsed[0]['epoch']) / 60
            diagnostics['latest_timestamp'] = parsed[0]['timestamp']
            diagnostics['age_minutes'] = round(age, 1)
            if age > MAX_FRESH_MINUTES:
                diagnostics['error'] = f'Latest RainViewer frame is {age:.1f} minutes old'
                return [], diagnostics
        return parsed, diagnostics
    except Exception as exc:
        diagnostics['error'] = repr(exc)
        return [], diagnostics


def fetch_panahon_timeline():
    diagnostics = {
        'source': 'PANaHON', 'endpoint': TIMELINE_API, 'status': None,
        'content_type': None, 'bytes': 0, 'frame_count': 0, 'error': None,
    }
    try:
        body, status, content_type = http_get(TIMELINE_API, referer=RADAR_PAGE)
        diagnostics.update(status=status, content_type=content_type, bytes=len(body))
        data = json.loads(body.decode('utf-8'))
        if data.get('success') is False:
            diagnostics['error'] = 'PANaHON returned success=false'
            return [], diagnostics
        frames = timeline_pairs(data.get('data') or {})
        diagnostics['frame_count'] = len(frames)
        if not frames:
            diagnostics['error'] = 'Timeline returned no parseable image frames'
        return frames, diagnostics
    except Exception as exc:
        diagnostics['error'] = repr(exc)
        return [], diagnostics


def download_frame(frame, referer=PANAHON_BASE, method='PANaHON_timeline'):
    diagnostics = {
        'url': frame['url'], 'timestamp': frame.get('timestamp'), 'method': method,
        'status': None, 'content_type': None, 'bytes': 0, 'signature': None,
        'valid_image': False, 'tls_fallback': False, 'error': None,
    }
    try:
        body, status, content_type = http_get(frame['url'], referer=referer)
        diagnostics.update(status=status, content_type=content_type, bytes=len(body), signature=body[:16].hex())
        diagnostics['valid_image'] = valid_image(body)
        if not diagnostics['valid_image']:
            diagnostics['error'] = 'Response is not a valid image'
            return None, diagnostics
        return body, diagnostics
    except Exception as exc:
        diagnostics['error'] = repr(exc)
        return None, diagnostics


def persist_current(captured, previous_timestamp, previous_body, discovered_urls, diagnostics):
    ts, body = captured['timestamp'], captured['body']
    path = OUTPUT_DIR / f'radar_{ts}.png'
    path.write_bytes(body)
    current_analysis = analyze(body, False)
    previous_analysis = analyze(previous_body, False) if previous_body else None
    if previous_analysis and previous_timestamp != ts:
        matches = track(previous_analysis, current_analysis)
        tracking = {'status': 'ok', 'previous_timestamp': previous_timestamp, 'current_timestamp': ts,
                    'matches_count': len(matches), 'approaching_matches_count': sum(1 for m in matches if m['approaching_mandaluyong']),
                    'matches': matches, 'max_movement_km_per_frame': MAX_MOVEMENT_KM_PER_FRAME}
    elif previous_timestamp == ts:
        tracking = {'status': 'unchanged_frame', 'previous_timestamp': previous_timestamp, 'current_timestamp': ts,
                    'matches_count': 0, 'approaching_matches_count': 0, 'matches': [],
                    'max_movement_km_per_frame': MAX_MOVEMENT_KM_PER_FRAME}
    else:
        tracking = {'status': 'insufficient_frames', 'previous_timestamp': None, 'current_timestamp': ts,
                    'matches_count': 0, 'approaching_matches_count': 0, 'matches': [],
                    'max_movement_km_per_frame': MAX_MOVEMENT_KM_PER_FRAME}
    PREVIOUS_IMAGE.write_bytes(body)
    MAP_STATE_FILE.write_text(json.dumps({
        'frames': [{'url': captured['url'], 'timestamp': ts, 'path': str(path), 'bytes': len(body),
                    'analysis': current_analysis, 'source': captured['method']}],
        'captured_images': [captured['url']], 'resource_urls': discovered_urls,
        'radar_tracking': tracking,
        'collector': {'mode': 'RainViewer_primary_PANaHON_fallback', 'rainviewer_endpoint': RAINVIEWER_API,
                      'rainviewer_center': {'lat': MANDALUYONG_LAT, 'lon': MANDALUYONG_LON, 'zoom': RAINVIEWER_ZOOM},
                      'panahon_timeline_endpoint': TIMELINE_API, 'browser_fallback': True,
                      'browser_page': PANAHON_RADAR_PAGE, 'source_checked_at': datetime.now(PH_TZ).isoformat(),
                      'download_diagnostics': diagnostics},
    }, indent=2), encoding='utf-8')
    STATE_FILE.write_text(json.dumps({'success': True, 'checked_at': datetime.now(PH_TZ).isoformat(),
                                      'image_url': captured['url'], 'image_timestamp': ts,
                                      'localized_image': 'radar_data/mandaluyong_radar_localized.png',
                                      'tracking_status': tracking['status'], 'collector_mode': captured['method']}, indent=2), encoding='utf-8')
    print(json.dumps({'success': True, 'tracking_status': tracking['status'], 'image_timestamp': ts, 'source': captured['method']}, indent=2))


def browser_fallback(previous_timestamp):
    urls, diagnostics = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            def capture_response(response):
                u = response.url
                if response.status != 200:
                    return
                if re.search(r'(radar|mosaic|reflectivity|rain[-_ ]?rate)', u, re.I):
                    urls.append(u)

            page.on('response', capture_response)
            try:
                page.goto(PANAHON_RADAR_PAGE, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(12000)
            except Exception as exc:
                diagnostics.append({'method': 'browser_discovery', 'stage': 'page_goto', 'url': PANAHON_RADAR_PAGE, 'error': repr(exc)})

            try:
                urls.extend(page.evaluate("""() => performance.getEntriesByType('resource').map(e => e.name).filter(u => /(radar|mosaic|reflectivity|rain[-_ ]?rate)/i.test(u))"""))
            except Exception as exc:
                diagnostics.append({'method': 'browser_discovery', 'stage': 'performance_resources', 'error': repr(exc)})

            try:
                urls.extend(page.evaluate("""() => [...document.images].map(i => i.currentSrc || i.src).filter(Boolean)"""))
            except Exception as exc:
                diagnostics.append({'method': 'browser_discovery', 'stage': 'image_sources', 'error': repr(exc)})

            try:
                diagnostics.append({'method': 'browser_discovery', 'stage': 'page_state', 'url': page.url, 'frames': [f.url for f in page.frames], 'candidate_count': len(urls)})
            except Exception:
                pass
            browser.close()
    except Exception as exc:
        diagnostics.append({'method': 'browser_discovery', 'stage': 'browser_start', 'error': repr(exc)})

    candidates = []
    for url in dict.fromkeys(urls):
        ts = timestamp_from_value(url)
        if ts:
            candidates.append((ts, url))

    for ts, url in sorted(candidates, reverse=True):
        if ts == previous_timestamp:
            continue
        body, diag = download_frame({'timestamp': ts, 'url': url}, referer=PANAHON_RADAR_PAGE, method='browser_fallback')
        diagnostics.append(diag)
        if body is not None:
            return {'timestamp': ts, 'url': url, 'body': body, 'method': 'browser_fallback'}, urls, diagnostics

    return None, urls, diagnostics


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    previous_timestamp, previous_body = load_previous()
    diagnostics, captured, discovered_urls = [], None, []

    rainviewer_frames, rainviewer_diag = fetch_rainviewer()
    diagnostics.append(rainviewer_diag)
    discovered_urls.extend([f['url'] for f in rainviewer_frames])

    for frame in rainviewer_frames:
        if not frame.get('timestamp') or frame['timestamp'] == previous_timestamp:
            continue
        body, diag = download_frame(frame, referer='https://www.rainviewer.com/', method='RainViewer')
        diagnostics.append(diag)
        if body is not None:
            captured = {'timestamp': frame['timestamp'], 'url': frame['url'], 'body': body, 'method': 'RainViewer'}
            break

    if captured is None:
        frames, timeline_diag = fetch_panahon_timeline()
        diagnostics.append(timeline_diag)
        discovered_urls.extend([f['url'] for f in frames])
        for frame in sorted(frames, key=lambda f: f.get('timestamp') or '', reverse=True):
            if not frame.get('timestamp') or frame['timestamp'] == previous_timestamp:
                continue
            body, diag = download_frame(frame, method='PANaHON_API')
            diagnostics.append(diag)
            if body is not None:
                captured = {'timestamp': frame['timestamp'], 'url': frame['url'], 'body': body, 'method': 'PANaHON_API'}
                break

    if captured is None:
        captured, browser_urls, browser_diagnostics = browser_fallback(previous_timestamp)
        discovered_urls.extend(browser_urls)
        diagnostics.extend(browser_diagnostics)

    discovered_urls = list(dict.fromkeys(discovered_urls))
    if captured is None:
        rainviewer_available = bool(rainviewer_frames)
        status = 'stale_or_unusable_source' if rainviewer_available else 'source_unreachable'
        result = {'success': False, 'checked_at': datetime.now(PH_TZ).isoformat(), 'image_timestamp': previous_timestamp,
                  'tracking_status': status, 'resource_urls': discovered_urls, 'download_diagnostics': diagnostics,
                  'message': 'No newer usable real-time radar frame was captured. Previous frame is retained separately from current source availability.'}
        MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding='utf-8')
        STATE_FILE.write_text(json.dumps({'success': False, 'checked_at': result['checked_at'], 'image_timestamp': previous_timestamp,
                                          'tracking_status': status, 'source_checked': True,
                                          'collector_mode': 'RainViewer_primary_PANaHON_fallback'}, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        return
    persist_current(captured, previous_timestamp, previous_body, discovered_urls, diagnostics)


if __name__ == '__main__':
    main()
