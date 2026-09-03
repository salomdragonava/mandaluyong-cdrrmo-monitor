import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

PH_TZ = timezone(timedelta(hours=8))
FLOOD_STATE = Path('flood_state.json')
PAGASA_STATE = Path('pagasa_state.json')
RADAR_STATE = Path('radar_threat_state.json')
ASSESSMENT_STATE = Path('assessment_state.json')
MONITOR_STATE = Path('monitor_notification_state.json')
ALERT_FILE = Path('combined_alert.txt')
ESCALATION_FILE = Path('escalation_alert.txt')

POINTS = [
    ('P1', 'Pananalig × Villarica', 'LOCAL'),
    ('P2', 'Villarica × Nanirahan', 'LOCAL'),
    ('P3', 'J.P. Rizal × San Pedro', 'ESCAPE ROUTE'),
    ('P4', 'J.P. Rizal × Ilino Cruz', 'ESCAPE ROUTE'),
    ('P5', 'J.P. Rizal × Saniboy', 'ESCAPE ROUTE'),
    ('P6', 'J.P. Rizal × Coronado', 'ESCAPE ROUTE'),
]


def load(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def warning_description(level):
    return {
        'GREEN': 'No active PAGASA Heavy Rainfall Warning for Metro Manila',
        'YELLOW': 'Possible flooding in flood-prone areas',
        'ORANGE': 'Flooding is threatening',
        'RED': 'Severe flooding is expected',
    }.get(level, 'PAGASA warning status unavailable')


def warning_icon(level):
    return {'GREEN': '🟢', 'YELLOW': '🟡', 'ORANGE': '🟠', 'RED': '🔴'}.get(level, '⚪')


def rank(level):
    return {'Low': 1, 'Medium': 2, 'High': 3}.get(level, 0)


def radar_candidates(radar):
    if radar.get('status') != 'ok':
        return []
    return [
        item for item in radar.get('top_threats', [])
        if item.get('threat_candidate')
        and item.get('threat_score', 0) >= 55
        and item.get('distance_to_mandaluyong_km', 9999) <= 50
        and item.get('pixels_current', 0) >= 150
        and (
            item.get('current_class') in {'yellow', 'orange', 'red', 'purple'}
            or item.get('previous_class') in {'yellow', 'orange', 'red', 'purple'}
        )
    ]


def flood_summary(flood):
    points = [point for point in flood.values() if isinstance(point, dict)]
    high = [point for point in points if point.get('risk_level') == 'High']
    medium = [point for point in points if point.get('risk_level') == 'Medium']
    escape = [point for point in points if point.get('purpose') == 'ESCAPE ROUTE']
    elevated = [point for point in escape if point.get('risk_level') in ('Medium', 'High')]
    status = (
        'HIGH RISK' if high else
        'ESCAPE ROUTE ELEVATED' if len(elevated) >= 2 else
        'WATCH' if medium else
        'NO DATA' if not points else
        'NORMAL'
    )
    rainfall = [point.get('rainfall') for point in points if isinstance(point.get('rainfall'), (int, float))]
    return status, max(rainfall) if rainfall else None, elevated


def precursor_signal(pred):
    signals = [str(signal).lower() for signal in pred.get('signals', [])]
    text = ' '.join(signals)
    return any(
        marker in text
        for marker in (
            'repeated water-level rise',
            'persistent rise',
            'sustained multi-point pressure',
            'accumulation',
        )
    )


def action_status(level, flood_status, pred, candidates, elevated_escape):
    risk = pred.get('risk', 'UNKNOWN')
    imminent = bool(pred.get('imminent'))
    precursor = precursor_signal(pred)

    if level == 'RED' or flood_status == 'HIGH RISK' or imminent:
        return (
            '🔴 LEAVE',
            'Flood conditions may be imminent. Move to a safe location and do not wait for water to rise further.'
        )
    if level == 'ORANGE' or flood_status == 'ESCAPE ROUTE ELEVATED' or risk == 'HIGH' or candidates:
        return (
            '🟠 PREPARE TO LEAVE',
            'Flood risk is increasing. Prepare essentials, secure belongings, and be ready to leave if conditions worsen.'
        )
    if level == 'YELLOW' or flood_status == 'WATCH' or risk in {'WATCH', 'LOW-MODERATE'} or elevated_escape:
        return (
            '🟡 PREPARE',
            'Conditions need closer attention. Prepare essentials and monitor the next updates.'
        )
    if precursor:
        return (
            '🟡 PREPARE',
            'Water levels show a precursor signal. Prepare essentials and monitor closely for renewed rainfall or further rise.'
        )
    return '🟢 SAFE', 'No immediate action is needed. Conditions are currently stable.'


def build_messages():
    pagasa = load(PAGASA_STATE, {})
    flood = load(FLOOD_STATE, {})
    radar = load(RADAR_STATE, {})
    assess = load(ASSESSMENT_STATE, {})
    previous = load(MONITOR_STATE, {})

    level = pagasa.get('level', 'UNKNOWN')
    flood_status, rainfall, elevated_escape = flood_summary(flood)
    candidates = radar_candidates(radar)
    now = datetime.now(PH_TZ)
    pred = assess.get('prediction', {})
    action, action_text = action_status(level, flood_status, pred, candidates, elevated_escape)

    lines = [
        f'{warning_icon(level)} MANDALUYONG FLOOD MONITOR',
        '',
        action,
        action_text,
        '',
        f'Checked: {now.strftime("%Y-%m-%d %I:%M %p")}',
        '',
        '🌧️ WEATHER',
        f'PAGASA: {warning_icon(level)} {level}',
        warning_description(level),
        '',
        '📍 LOCAL MONITORING',
        f'Flood status: {flood_status}',
    ]

    if rainfall is not None:
        lines.append(f'Rainfall: {rainfall} mm')

    lines += ['', 'Monitoring points:']
    for pid, name, purpose in POINTS:
        point = flood.get(pid)
        if not point:
            lines.append(f'⚪ {pid} {name} — NO DATA')
            continue
        risk_level = point.get('risk_level', 'Unknown')
        icon = {'High': '🔴', 'Medium': '🟡', 'Low': '🟢'}.get(risk_level, '⚪')
        lines.append(f'{icon} {pid} {name} — {risk_level} ({point.get("risk_score", 0)})')

    lines.append('')
    lines.append(
        f'⚠️ Escape route: {len(elevated_escape)} point(s) elevated'
        if elevated_escape else
        '🛣️ Escape route: CLEAR'
    )

    lines += ['', '📡 RADAR MONITORING']
    if candidates:
        lines.append(f'🔴 {len(candidates)} qualifying radar threat(s) near/approaching Mandaluyong')
        for item in sorted(candidates, key=lambda item: -item.get('threat_score', 0))[:3]:
            lines.append(
                f'   • {item.get("current_class", "unknown").upper()} | '
                f'score {item.get("threat_score", 0):.0f} | '
                f'{item.get("distance_to_mandaluyong_km", 0):.1f} km | '
                f'movement {item.get("movement_km", 0):.1f} km'
            )
    elif radar.get('status') == 'ok':
        lines.append('🟢 No qualifying radar core detected for Mandaluyong')
    else:
        lines.append('⚪ Radar status unavailable')

    if pred:
        icon = {
            'HIGH': '🔴',
            'WATCH': '🟠',
            'LOW-MODERATE': '🟡',
            'LOW': '🟢',
            'UNKNOWN': '⚪',
        }.get(pred.get('risk'), '⚪')
        lines += [
            '',
            f'{icon} 3-DAY FLOOD ASSESSMENT',
            f'Prediction: {pred.get("risk", "UNKNOWN")}',
            f'Risk score: {pred.get("score", 0)}/100',
            f'Confidence: {pred.get("confidence", 0):.0%}',
            f'Imminent window: {"YES" if pred.get("imminent") else "NO"}',
            f'Window: {pred.get("window", "latest 3 days")}',
        ]
        signals = ', '.join(pred.get('signals', [])) or 'No significant precursor signals'
        lines += ['', f'Signals: {signals}', f'Assessment: {pred.get("interpretation", "")}']

    alert_text = '\n'.join(lines)
    ALERT_FILE.write_text(alert_text, encoding='utf-8')

    previous_level = previous.get('pagasa_level', 'UNKNOWN')
    previous_flood = previous.get('flood', {})
    previous_radar = previous.get('radar_candidate', False)
    previous_imminent = previous.get('imminent', False)
    escalations = []
    order = {'UNKNOWN': 0, 'GREEN': 1, 'YELLOW': 2, 'ORANGE': 3, 'RED': 4}

    if order.get(level, 0) > order.get(previous_level, 0) and previous_level != 'UNKNOWN':
        escalations.append(f'PAGASA warning level increased: {previous_level} → {level}')

    for pid, name, _ in POINTS:
        old = previous_flood.get(pid, {})
        new = flood.get(pid, {})
        if rank(new.get('risk_level')) > rank(old.get('risk_level')) and old.get('risk_level'):
            escalations.append(f'{pid} {name}: {old.get("risk_level")} → {new.get("risk_level")}')

    if elevated_escape and not previous.get('escape_elevated', False):
        escalations.append('Escape route risk became elevated')
    if candidates and not previous_radar:
        escalations.append('Qualifying radar threat detected near/approaching Mandaluyong')
    if pred.get('imminent') and not previous_imminent:
        escalations.append('Flood precursor entered IMMINENT window: sustained multi-point pressure plus forcing')

    ESCALATION_FILE.unlink(missing_ok=True)
    if escalations:
        ESCALATION_FILE.write_text(
            '🚨 MANDALUYONG MONITOR ESCALATION\n\n'
            + '\n'.join(f'• {item}' for item in escalations)
            + f'\n\nChecked: {now.strftime("%Y-%m-%d %I:%M %p")}',
            encoding='utf-8'
        )

    MONITOR_STATE.write_text(
        json.dumps(
            {
                'pagasa_level': level,
                'flood': flood,
                'radar_candidate': bool(candidates),
                'escape_elevated': bool(elevated_escape),
                'imminent': bool(pred.get('imminent')),
                'last_checked': now.isoformat(),
            },
            indent=2,
        ),
        encoding='utf-8'
    )
    print(alert_text)


if __name__ == '__main__':
    build_messages()
