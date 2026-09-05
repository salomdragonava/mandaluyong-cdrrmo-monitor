import io
import json
import math
from collections import Counter, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from radar_monitor import MAX_MOVEMENT_KM_PER_FRAME, OUTPUT_DIR, STATE_FILE, MAP_STATE_FILE, PREVIOUS_IMAGE
from radar_light import fetch_panahon_timeline, download_frame, PANAHON_RADAR_PAGE

RAINVIEWER_API = 'https://api.rainviewer.com/public/weather-maps.json'
RAINVIEWER_HOST = 'https://tilecache.rainviewer.com'
TARGET_LAT, TARGET_LON = 14.5794, 121.0359
ZOOM = 6
IMAGE_SIZE = 512
COLOR = 2
SMOOTH = 1
MAX_FRESH_MINUTES = 20
PH_TZ = timezone(timedelta(hours=8))

COLOR_CLASS_RANGES = {
    'cyan': lambda r, g, b: b > 180 and g > 180 and r < 190,
    'blue': lambda r, g, b: b > 180 and g < 190 and r < 160,
    'green': lambda r, g, b: g > 180 and r < 190 and b < 130,
    'yellow': lambda r, g, b: r > 200 and g > 190 and b < 130,
    'orange': lambda r, g, b: r > 180 and 70 < g < 200 and b < 100,
    'red': lambda r, g, b: r > 150 and g < 100 and b < 80,
    'purple': lambda r, g, b: r > 100 and b > 100 and g < 150,
}


def http_get(url, timeout=30):
    req = Request(url, headers={
        'User-Agent': 'Mandaluyong-Flood-Monitor/1.0',
        'Referer': 'https://www.rainviewer.com/',
        'Accept': 'application/json,image/png,image/*,*/*;q=0.8',
    })
    with urlopen(req, timeout=timeout) as response:
        return response.read(), getattr(response, 'status', None), response.headers.get('Content-Type')


def valid_image(body):
    try:
        Image.open(io.BytesIO(body)).verify()
        return True
    except Exception:
        return False


def global_pixel(lon, lat):
    n = 256 * (2 ** ZOOM)
    x = n * (lon + 180.0) / 360.0
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = n * (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0
    return x, y


def image_projection():
    cx, cy = global_pixel(TARGET_LON, TARGET_LAT)
    return cx - IMAGE_SIZE / 2.0, cy - IMAGE_SIZE / 2.0


def pixel_to_lonlat(x, y):
    x0, y0 = image_projection()
    n = 256 * (2 ** ZOOM)
    gx, gy = x0 + x, y0 + y
    lon = gx / n * 360.0 - 180.0
    merc_y = math.pi * (1.0 - 2.0 * gy / n)
    lat = math.degrees(math.atan(math.sinh(merc_y)))
    return lon, lat


def classify(r, g, b):
    for name, fn in COLOR_CLASS_RANGES.items():
        if fn(r, g, b):
            return name
    return None


def analyze_rainviewer(body, draw_target=False):
    im = Image.open(io.BytesIO(body)).convert('RGBA')
    w, h = im.size
    p = im.load()
    counts = Counter()
    pixels_by_class = {k: [] for k in COLOR_CLASS_RANGES}

    for y in range(h):
        for x in range(w):
            r, g, b, a = p[x, y]
            if a <= 10:
                continue
            cls = classify(r, g, b)
            if cls:
                counts[cls] += 1
                pixels_by_class[cls].append((x, y))

    components = []
    for cls, pts in pixels_by_class.items():
        pts_set = set(pts)
        seen = set()
        for seed in list(pts_set):
            if seed in seen:
                continue
            q = deque([seed])
            seen.add(seed)
            comp = []
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx or dy:
                            nxy = (x + dx, y + dy)
                            if nxy in pts_set and nxy not in seen:
                                seen.add(nxy)
                                q.append(nxy)
            if len(comp) >= 8:
                cx = sum(x for x, _ in comp) / len(comp)
                cy = sum(y for _, y in comp) / len(comp)
                lon, lat = pixel_to_lonlat(cx, cy)
                dist = 111 * math.sqrt(
                    (lat - TARGET_LAT) ** 2
                    + (math.cos(math.radians(TARGET_LAT)) * (lon - TARGET_LON)) ** 2
                )
                components.append({
                    'class': cls,
                    'pixels': len(comp),
                    'centroid_pixel': [round(cx, 1), round(cy, 1)],
                    'centroid_lonlat': [round(lon, 4), round(lat, 4)],
                    'distance_km': round(dist, 1),
                })

    if draw_target:
        out = im.copy()
        d = ImageDraw.Draw(out)
        tx, ty = global_pixel(TARGET_LON, TARGET_LAT)
        x0, y0 = image_projection()
        tx -= x0
        ty -= y0
        r = 8
        d.ellipse((tx-r, ty-r, tx+r, ty+r), outline=(255, 0, 0, 255), width=3)
        d.line((tx-r*2, ty, tx+r*2, ty), fill=(255, 0, 0, 255), width=2)
        d.line((tx, ty-r*2, tx, ty+r*2), fill=(255, 0, 0, 255), width=2)
        out.save(OUTPUT_DIR / 'mandaluyong_radar_localized.png')

    return {
        'image_size': [w, h],
        'projection': 'WebMercator',
        'target': {'lat': TARGET_LAT, 'lon': TARGET_LON},
        'zoom': ZOOM,
        'target_pixel': [IMAGE_SIZE / 2, IMAGE_SIZE / 2],
        'color_class_counts': dict(counts),
        'components_count': len(components),
        'components': components,
        'note': 'Color classes are diagnostic only. They are not dBZ or PAGASA rainfall warning thresholds.',
    }


def track(previous, current):
    matches = []
    used = set()
    pairs = []
    for ai, a in enumerate(previous.get('components', [])):
        for bi, b in enumerate(current.get('components', [])):
            dx = b['centroid_pixel'][0] - a['centroid_pixel'][0]
            dy = b['centroid_pixel'][1] - a['centroid_pixel'][1]
            dist_px = math.hypot(dx, dy)
            if dist_px > 140:
                continue
            lon1, lat1 = a['centroid_lonlat']
            lon2, lat2 = b['centroid_lonlat']
            mid = (lat1 + lat2) / 2
            movement_km = 111 * math.sqrt((lat2-lat1)**2 + (math.cos(math.radians(mid))*(lon2-lon1))**2)
            if movement_km > MAX_MOVEMENT_KM_PER_FRAME:
                continue
            size = max(a['pixels'], b['pixels']) / max(1, min(a['pixels'], b['pixels']))
            score = dist_px + 12 * (min(size, 4.0) - 1) + (0 if a['class'] == b['class'] else 18 * 0.75)
            pairs.append((score, ai, bi))

    for _, ai, bi in sorted(pairs):
        if ai in used or bi in used:
            continue
        a = previous['components'][ai]
        b = current['components'][bi]
        used.add(ai)
        used.add(bi)
        lon1, lat1 = a['centroid_lonlat']
        lon2, lat2 = b['centroid_lonlat']
        mid = (lat1 + lat2) / 2
        km = 111 * math.sqrt((lat2-lat1)**2 + (math.cos(math.radians(mid))*(lon2-lon1))**2)
        olddist = 111 * math.sqrt((lat1-TARGET_LAT)**2 + (math.cos(math.radians(TARGET_LAT))*(lon1-TARGET_LON))**2)
        bearing = (math.degrees(math.atan2((lon2-lon1)*math.cos(math.radians(mid)), lat2-lat1)) + 360) % 360
        matches.append({
            'previous_class': a['class'], 'current_class': b['class'],
            'pixels_previous': a['pixels'], 'pixels_current': b['pixels'],
            'from_lonlat': [lon1, lat1], 'to_lonlat': [lon2, lat2],
            'movement_km': round(km, 1), 'bearing_deg': round(bearing, 1),
            'previous_distance_km': round(olddist, 1), 'distance_to_mandaluyong_km': b['distance_km'],
            'distance_change_km': round(b['distance_km'] - olddist, 1),
            'approaching_mandaluyong': b['distance_km'] < olddist - 2,
        })
    return matches


def load_previous():
    if not PREVIOUS_IMAGE.exists() or not STATE_FILE.exists():
        return None, None
    try:
        state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        ts = state.get('image_timestamp')
        body = PREVIOUS_IMAGE.read_bytes()
        return (ts, body) if ts and body else (None, None)
    except Exception:
        return None, None


def persist(ts, url, body, previous_timestamp, previous_body, diagnostics, source):
    path = OUTPUT_DIR / f'radar_{ts}.png'
    path.write_bytes(body)
    current_analysis = analyze_rainviewer(body, draw_target=True)
    previous_analysis = analyze_rainviewer(previous_body, draw_target=False) if previous_body else None
    if previous_analysis and previous_timestamp != ts:
        matches = track(previous_analysis, current_analysis)
        tracking = {'status': 'ok', 'previous_timestamp': previous_timestamp, 'current_timestamp': ts,
                    'matches_count': len(matches), 'approaching_matches_count': sum(1 for m in matches if m['approaching_mandaluyong']),
                    'matches': matches, 'max_movement_km_per_frame': MAX_MOVEMENT_KM_PER_FRAME}
    elif previous_timestamp == ts:
        tracking = {'status': 'unchanged_frame', 'previous_timestamp': previous_timestamp, 'current_timestamp': ts,
                    'matches_count': 0, 'approaching_matches_count': 0, 'matches': []}
    else:
        tracking = {'status': 'insufficient_frames', 'current_timestamp': ts, 'matches_count': 0, 'approaching_matches_count': 0, 'matches': []}

    PREVIOUS_IMAGE.write_bytes(body)
    MAP_STATE_FILE.write_text(json.dumps({
        'frames': [{'url': url, 'timestamp': ts, 'path': str(path), 'bytes': len(body), 'analysis': current_analysis, 'source': source}],
        'captured_images': [url], 'resource_urls': [url], 'radar_tracking': tracking,
        'collector': {'mode': 'RainViewer_primary', 'source': 'RainViewer/PAGASA-PANAHON',
                      'endpoint': RAINVIEWER_API, 'center': {'lat': TARGET_LAT, 'lon': TARGET_LON, 'zoom': ZOOM},
                      'freshness_limit_minutes': MAX_FRESH_MINUTES,
                      'source_checked_at': datetime.now(PH_TZ).isoformat(), 'download_diagnostics': diagnostics},
    }, indent=2), encoding='utf-8')
    STATE_FILE.write_text(json.dumps({
        'success': True, 'checked_at': datetime.now(PH_TZ).isoformat(), 'image_url': url,
        'image_timestamp': ts, 'localized_image': 'radar_data/mandaluyong_radar_localized.png',
        'tracking_status': tracking['status'], 'collector_mode': 'RainViewer_primary',
        'source': 'RainViewer/PAGASA-PANAHON', 'freshness_limit_minutes': MAX_FRESH_MINUTES,
    }, indent=2), encoding='utf-8')
    print(json.dumps({'success': True, 'tracking_status': tracking['status'], 'image_timestamp': ts, 'source': source}, indent=2))


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    previous_timestamp, previous_body = load_previous()
    diagnostics = []
    captured = None

    try:
        body, status, content_type = http_get(RAINVIEWER_API)
        payload = json.loads(body.decode('utf-8'))
        frames = (payload.get('radar') or {}).get('past') or []
        diagnostics.append({'source': 'RainViewer', 'endpoint': RAINVIEWER_API, 'status': status,
                            'content_type': content_type, 'bytes': len(body), 'frame_count': len(frames),
                            'generated': payload.get('generated')})
        for frame in sorted(frames, key=lambda f: int(f.get('time', 0)), reverse=True):
            epoch = int(frame['time'])
            age = (datetime.now(timezone.utc).timestamp() - epoch) / 60
            ts = datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y%m%d%H%M%S')
            if age > MAX_FRESH_MINUTES or ts == previous_timestamp:
                continue
            url = f'{RAINVIEWER_HOST}{frame["path"]}/512/{ZOOM}/{TARGET_LAT}/{TARGET_LON}/{COLOR}/{SMOOTH}_0.png'
            try:
                image_body, image_status, image_type = http_get(url)
                diag = {'source': 'RainViewer', 'url': url, 'timestamp': ts, 'age_minutes': round(age, 1),
                        'status': image_status, 'content_type': image_type, 'bytes': len(image_body),
                        'valid_image': valid_image(image_body)}
                diagnostics.append(diag)
                if diag['valid_image']:
                    captured = (ts, url, image_body)
                    break
            except Exception as exc:
                diagnostics.append({'source': 'RainViewer', 'url': url, 'timestamp': ts, 'error': repr(exc)})
    except Exception as exc:
        diagnostics.append({'source': 'RainViewer', 'endpoint': RAINVIEWER_API, 'error': repr(exc)})

    if captured:
        persist(*captured, previous_timestamp, previous_body, diagnostics, 'RainViewer')
        return

    # Secondary fallback: retain the existing PANaHON collector, but never turn
    # an unavailable radar source into a green/no-rain result.
    try:
        frames, diag = fetch_panahon_timeline()
        diagnostics.append(diag)
        for frame in sorted(frames, key=lambda f: f.get('timestamp') or '', reverse=True):
            if not frame.get('timestamp') or frame['timestamp'] == previous_timestamp:
                continue
            body, frame_diag = download_frame(frame, referer=PANAHON_RADAR_PAGE, method='PANaHON_fallback')
            diagnostics.append(frame_diag)
            if body:
                persist(frame['timestamp'], frame['url'], body, previous_timestamp, previous_body, diagnostics, 'PANaHON_fallback')
                return
    except Exception as exc:
        diagnostics.append({'source': 'PANaHON_fallback', 'error': repr(exc)})

    checked = datetime.now(PH_TZ).isoformat()
    result = {'success': False, 'checked_at': checked, 'image_timestamp': previous_timestamp,
              'tracking_status': 'source_unreachable', 'resource_urls': [], 'download_diagnostics': diagnostics,
              'message': 'No fresh usable radar frame was captured. Previous frame is retained only as historical data.'}
    MAP_STATE_FILE.write_text(json.dumps(result, indent=2), encoding='utf-8')
    STATE_FILE.write_text(json.dumps({'success': False, 'checked_at': checked, 'image_timestamp': previous_timestamp,
                                      'tracking_status': 'source_unreachable', 'source_checked': True,
                                      'collector_mode': 'RainViewer_primary'}, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
