import json, math
from pathlib import Path

STATE = Path('radar_map_state.json')
OUTPUT = Path('radar_threat_state.json')

# Diagnostic-only threat ranking. This deliberately does not claim dBZ/PAGASA warning levels.
CLASS_SCORE = {
    'cyan': 1,
    'blue': 2,
    'green': 3,
    'yellow': 5,
    'orange': 7,
    'red': 9,
    'purple': 10,
}


def threat_score(m):
    distance = float(m.get('distance_to_mandaluyong_km', 9999))
    previous_distance = float(m.get('previous_distance_km', distance))
    movement = float(m.get('movement_km', 0))
    approaching = bool(m.get('approaching_mandaluyong'))
    cls = m.get('current_class', 'cyan')
    intensity = CLASS_SCORE.get(cls, 0)

    # Distance factor: strongest inside 25 km, fades to zero at 100 km.
    distance_factor = max(0.0, min(1.0, (100.0 - distance) / 75.0))
    # Movement factor: reward clear displacement while avoiding making tiny changes dominant.
    movement_factor = max(0.0, min(1.0, movement / 20.0))
    direction_factor = 1.0 if approaching else 0.0
    score = 100 * (
        0.45 * (intensity / 10.0) +
        0.30 * distance_factor +
        0.15 * movement_factor +
        0.10 * direction_factor
    )

    # Require genuinely approaching and a meaningful current distance for a threat candidate.
    candidate = approaching and distance <= 100 and movement >= 2
    if previous_distance > distance:
        trend = 'approaching'
    elif previous_distance < distance:
        trend = 'receding'
    else:
        trend = 'stationary'

    return {
        **m,
        'threat_score': round(score, 1),
        'trend': trend,
        'threat_candidate': candidate,
    }


def main():
    if not STATE.exists():
        OUTPUT.write_text(json.dumps({'status': 'no_radar_state'}, indent=2))
        return

    state = json.loads(STATE.read_text())
    tracking = state.get('radar_tracking', {})
    matches = tracking.get('matches', [])
    scored = [threat_score(m) for m in matches]
    candidates = sorted(
        [m for m in scored if m['threat_candidate']],
        key=lambda x: x['threat_score'],
        reverse=True,
    )

    result = {
        'status': 'ok' if tracking.get('status') == 'ok' else tracking.get('status', 'unknown'),
        'previous_timestamp': tracking.get('previous_timestamp'),
        'current_timestamp': tracking.get('current_timestamp'),
        'match_count': len(scored),
        'threat_candidate_count': len(candidates),
        'top_threats': candidates[:20],
        'alert_recommendation': (
            'MONITOR' if candidates else 'NO_THREAT_CANDIDATE'
        ),
        'note': 'Diagnostic ranking only. Color classes are not assigned PAGASA dBZ or rainfall-warning thresholds.'
    }
    OUTPUT.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
