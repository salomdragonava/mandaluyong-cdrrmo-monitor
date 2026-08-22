import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

MAP_STATE = Path('radar_map_state.json')
ALERT_FILE = Path('radar_alert.txt')
NOTIFY_STATE = Path('radar_notification_state.json')
PH_TZ = timezone(timedelta(hours=8))

# Diagnostic-only radar notification gate. These are intentionally NOT PAGASA warning thresholds.
MAX_DISTANCE_KM = 50
MIN_APPROACHING_MATCHES = 3
MIN_CELL_PIXELS = 150
ALERT_CLASSES = {'yellow', 'orange', 'red', 'purple'}


def main():
    ALERT_FILE.unlink(missing_ok=True)
    if not MAP_STATE.exists():
        return
    data = json.loads(MAP_STATE.read_text(encoding='utf-8'))
    tracking = data.get('radar_tracking', {})
    if tracking.get('status') != 'ok':
        return

    matches = tracking.get('matches', [])
    approaching = [m for m in matches if m.get('approaching_mandaluyong')]
    candidates = [
        m for m in approaching
        if m.get('distance_to_mandaluyong_km', 9999) <= MAX_DISTANCE_KM
        and m.get('pixels_current', 0) >= MIN_CELL_PIXELS
        and (m.get('current_class') in ALERT_CLASSES or m.get('previous_class') in ALERT_CLASSES)
    ]
    if len(approaching) < MIN_APPROACHING_MATCHES or not candidates:
        return

    current_ts = tracking.get('current_timestamp', '')
    prior = {}
    if NOTIFY_STATE.exists():
        try:
            prior = json.loads(NOTIFY_STATE.read_text(encoding='utf-8'))
        except Exception:
            prior = {}
    if prior.get('last_alert_timestamp') == current_ts:
        return

    nearest = sorted(candidates, key=lambda m: m.get('distance_to_mandaluyong_km', 9999))[:3]
    lines = [
        '🟠 RADAR MOVEMENT ALERT — MANDALUYONG',
        '',
        f'PAGASA radar frame: {current_ts}',
        f'Approaching cells detected: {len(approaching)}',
        '',
    ]
    for i, cell in enumerate(nearest, 1):
        lines.append(
            f"{i}. {cell.get('current_class','unknown').upper()} cell | "
            f"{cell.get('distance_to_mandaluyong_km', 0):.1f} km away | "
            f"movement {cell.get('movement_km', 0):.1f} km | "
            f"bearing {cell.get('bearing_deg', 0):.0f}°"
        )
    lines += [
        '',
        'This is an automated radar-motion signal, not an official PAGASA rainfall warning.',
        f"Checked: {datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
    ]
    ALERT_FILE.write_text('\n'.join(lines), encoding='utf-8')
    NOTIFY_STATE.write_text(json.dumps({'last_alert_timestamp': current_ts}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
