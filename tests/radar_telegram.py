import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

THREAT_STATE = Path('radar_threat_state.json')
ALERT_FILE = Path('radar_alert.txt')
NOTIFY_STATE = Path('radar_notification_state.json')
PH_TZ = timezone(timedelta(hours=8))

# Diagnostic-only notification gate. These are intentionally NOT PAGASA warning thresholds.
MAX_DISTANCE_KM = 50
MIN_THREAT_SCORE = 55
MIN_PIXELS = 150
# Orange/red/purple are treated as strong radar-core proxies for notification.
# This is not an official PAGASA dBZ/rainfall classification.
ALERT_CLASSES = {'orange', 'red', 'purple'}


def main():
    ALERT_FILE.unlink(missing_ok=True)
    if not THREAT_STATE.exists():
        return

    data = json.loads(THREAT_STATE.read_text(encoding='utf-8'))
    if data.get('status') != 'ok':
        return

    candidates = [
        m for m in data.get('top_threats', [])
        if m.get('threat_candidate')
        and m.get('threat_score', 0) >= MIN_THREAT_SCORE
        and m.get('distance_to_mandaluyong_km', 9999) <= MAX_DISTANCE_KM
        and m.get('pixels_current', 0) >= MIN_PIXELS
        and (m.get('current_class') in ALERT_CLASSES or m.get('previous_class') in ALERT_CLASSES)
    ]
    if not candidates:
        return

    current_ts = data.get('current_timestamp', '')
    # The radar workflow runs hourly. When a qualifying strong core is present,
    # deliberately allow one Telegram alert on every hourly run. We no longer
    # suppress alerts merely because a previous run already alerted.
    nearest = sorted(
        candidates,
        key=lambda m: (-m.get('threat_score', 0), m.get('distance_to_mandaluyong_km', 9999))
    )[:3]

    lines = [
        '🔴 RADAR CORE ALERT — MANDALUYONG',
        '',
        f"PAGASA radar frame: {current_ts}",
        f"Strong-core candidates: {len(candidates)}",
        'Condition remains present on this hourly check.',
        '',
    ]
    for i, cell in enumerate(nearest, 1):
        lines.append(
            f"{i}. {cell.get('current_class','unknown').upper()} core | "
            f"score {cell.get('threat_score',0):.0f} | "
            f"{cell.get('distance_to_mandaluyong_km',0):.1f} km away | "
            f"movement {cell.get('movement_km',0):.1f} km | "
            f"bearing {cell.get('bearing_deg',0):.0f}° | "
            f"trend {cell.get('trend','unknown')}"
        )
    lines += [
        '',
        'Automated radar-motion signal only; not an official PAGASA rainfall warning.',
        f"Checked: {datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
    ]
    ALERT_FILE.write_text('\n'.join(lines), encoding='utf-8')

    # Keep an audit trail of the latest hourly alert without using it as a cooldown gate.
    NOTIFY_STATE.write_text(
        json.dumps({
            'last_alert_timestamp': current_ts,
            'last_alert_sent_at': datetime.now(PH_TZ).isoformat(),
        }, indent=2),
        encoding='utf-8'
    )


if __name__ == '__main__':
    main()
