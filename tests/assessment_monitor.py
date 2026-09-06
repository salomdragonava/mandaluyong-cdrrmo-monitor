import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PH_TZ = timezone(timedelta(hours=8))
STATE_FILE = Path('assessment_history.json')
OUTPUT_FILE = Path('assessment_state.json')
LOG_FILE = Path('assessment_log.json')
ALERT_FILE = Path('assessment_alert.txt')
FLOOD_STATE_FILE = Path('flood_state.json')
POINTS = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6']
UNKNOWN = {'UNKNOWN', 'UNAVAILABLE', 'NONE', 'NULL', ''}
TREND_WINDOW = timedelta(hours=36)


def ts(value):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=PH_TZ)


def num(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def load(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def snapshot(flood=None):
    flood = flood if flood is not None else load(FLOOD_STATE_FILE, {})
    pagasa = load(Path('pagasa_state.json'), {})
    radar = load(Path('radar_state.json'), {})
    points = {}
    times = []
    rains = []

    for point_id in POINTS:
        row = flood.get(point_id, {})
        points[point_id] = {
            'risk_level': row.get('risk_level'),
            'risk_score': num(row.get('risk_score')),
            'rainfall_mm': num(row.get('rainfall')),
            'timestamp': row.get('timestamp'),
        }
        if row.get('timestamp'):
            try:
                times.append(ts(row['timestamp']))
            except ValueError:
                pass
        rains.append(points[point_id]['rainfall_mm'])

    return {
        'timestamp': max(times).strftime('%Y-%m-%d %H:%M:%S') if times else None,
        'rainfall_mm': max(rains) if rains else 0.0,
        'points': points,
        'pagasa_warning': str(pagasa.get('level') or pagasa.get('warning_level') or 'UNKNOWN').upper(),
        'radar_status': str(radar.get('status') or radar.get('radar_status') or 'UNKNOWN').upper(),
        'flood_status': 'ELEVATED' if any(
            str(value.get('risk_level')).lower() not in (None, 'low')
            for value in points.values()
        ) else 'NORMAL',
    }


def source_level_counts(row):
    levels = [str(value.get('risk_level') or '').upper() for value in row.get('points', {}).values()]
    high = sum(level == 'HIGH' for level in levels)
    medium = sum(level == 'MEDIUM' for level in levels)
    return high, medium


def mean_score(row):
    values = [num(value.get('risk_score')) for value in row.get('points', {}).values()]
    return sum(values) / len(values) if values else 0.0


def assess(history):
    parsed = []
    for row in history:
        try:
            parsed.append((ts(row['timestamp']), row))
        except (KeyError, TypeError, ValueError):
            pass

    if not parsed:
        return None

    parsed.sort(key=lambda item: item[0])
    now = parsed[-1][0]
    cutoff = now - timedelta(days=3)
    rows = [row for moment, row in parsed if moment >= cutoff]
    recent_rows = [row for moment, row in parsed if moment >= now - TREND_WINDOW]
    days = {moment.date() for moment, _ in parsed if moment >= cutoff}
    confidence = 0.90 if len(days) >= 3 and len(rows) >= 3 else (0.75 if len(days) >= 2 and len(rows) >= 3 else 0.45)

    score = 0.0
    signals = []
    recent_rainfall = [num(row.get('rainfall_mm')) for row in recent_rows]

    if recent_rainfall:
        maximum = max(recent_rainfall)
        if maximum >= 10:
            score += 35
            signals.append('high rainfall pulse')
        elif maximum >= 5:
            score += 20
            signals.append('moderate rainfall pulse')
        elif maximum >= 2:
            score += 8
            signals.append('rainfall pulse')

    latest = rows[-1]
    high_count, medium_count = source_level_counts(latest)

    # ProjectLIGTAS risk_level is the source classification. Its numerical
    # risk_score is used for trend detection only.
    if high_count:
        score += 40
        signals.append('high monitoring-point classification')
    elif medium_count:
        score += 25
        signals.append('medium monitoring-point classification')

    if high_count >= 2:
        score += 15
        signals.append('multi-point escalation')
    elif medium_count >= 3:
        score += 8
        signals.append('multi-point pressure')

    rises = 0
    for earlier, later in zip(recent_rows, recent_rows[1:]):
        before = mean_score(earlier)
        after = mean_score(later)
        if after > before + 0.15:
            rises += 1

    latest_mean = mean_score(latest)
    local_recovery = (
        latest_mean <= 0.5
        and high_count == 0
        and medium_count == 0
        and str(latest.get('flood_status')).upper() == 'NORMAL'
    )

    if not local_recovery:
        if rises >= 3:
            score += 25
            signals.append('persistent water-level rise')
        elif rises == 2:
            score += 12
            signals.append('repeated water-level rise')
    elif rises:
        signals.append('recent water-level rise has recovered')

    classified_pressure_rows = []
    for row in recent_rows:
        high, medium = source_level_counts(row)
        if high or medium >= 2:
            classified_pressure_rows.append(row)

    pressure_count = len(classified_pressure_rows)
    if pressure_count >= 3:
        score += 20
        signals.append('sustained classified multi-point pressure')
    elif pressure_count == 2:
        score += 10
        signals.append('developing classified multi-point pressure')

    pressure_rising = False
    if len(classified_pressure_rows) >= 2:
        first = classified_pressure_rows[0]
        last = classified_pressure_rows[-1]
        pressure_rising = mean_score(last) > mean_score(first) + 0.15
        if pressure_rising:
            score += 10
            signals.append('pressure is still rising')

    warning = latest.get('pagasa_warning')
    if warning == 'YELLOW':
        score += 10
        signals.append('PAGASA yellow')
    elif warning == 'ORANGE':
        score += 20
        signals.append('PAGASA orange')
    elif warning == 'RED':
        score += 30
        signals.append('PAGASA red')

    if latest.get('radar_status') in UNKNOWN:
        confidence -= 0.05
        signals.append('radar unavailable; confidence slightly reduced')

    score = max(0, min(100, score))
    risk = 'HIGH' if score >= 65 else 'WATCH' if score >= 35 else 'LOW-MODERATE' if score >= 15 else 'LOW'

    imminent = (
        pressure_count >= 3
        and (pressure_rising or warning in ('ORANGE', 'RED'))
        and (high_count >= 1 or medium_count >= 2)
    )
    if imminent:
        signals.append('IMMINENT WINDOW: sustained classified pressure plus forcing')

    confidence = max(0.2, min(0.95, confidence))
    return {
        'risk': risk,
        'score': round(score, 1),
        'confidence': round(confidence, 2),
        'window': 'latest 3 days; trend signals latest 36 hours',
        'imminent': imminent,
        'lead_signal': (
            'Pre-flood pressure detected; source risk classifications are persisting '
            'and/or rising under rainfall/warning forcing.' if imminent else
            'No validated imminent pattern yet.'
        ),
        'signals': signals,
        'interpretation': (
            'ProjectLIGTAS risk_level is treated as the source classification. '
            'Its numerical risk_score is used for trend detection only. Trend and '
            'rainfall-pulse signals are recency-bounded so recovered local conditions '
            'do not remain elevated solely because an older rise occurred earlier in '
            'the 3-day history. The validated precursor pattern is accumulation/'
            'persistence first, followed by renewed rainfall or warning forcing.'
        ),
        'data_quality': {
            'distinct_days': len(days),
            'rows_in_window': len(rows),
            'recent_trend_rows': len(recent_rows),
            'radar_available': latest.get('radar_status') not in UNKNOWN,
        },
    }


def main():
    current = snapshot()
    history = load(STATE_FILE, [])
    local_data_current = bool(current['timestamp'])

    if local_data_current:
        if history and history[-1].get('timestamp') == current['timestamp']:
            history[-1] = current
        else:
            history.append(current)
        current_for_assessment = current
    else:
        # A temporary outage of the flood-risk API must not kill the hourly
        # monitoring pipeline. Use the last known validated snapshot for the
        # analytical calculation, but explicitly mark the local data stale.
        # Never manufacture a timestamp or present the stale snapshot as fresh.
        valid_history = [row for row in history if row.get('timestamp')]
        if not valid_history:
            raise SystemExit('No usable flood snapshot exists for assessment')
        current_for_assessment = valid_history[-1]
        current_for_assessment = dict(current_for_assessment)
        current_for_assessment['local_data_current'] = False
        current_for_assessment['local_data_status'] = 'STALE/UNAVAILABLE'
        signals_note = 'local flood monitor unavailable; using last known snapshot'

    history = history[-5000:]
    prediction = assess(history)
    if prediction is None:
        raise SystemExit('No usable assessment history exists')

    if not local_data_current:
        prediction['confidence'] = round(max(0.2, prediction['confidence'] - 0.15), 2)
        prediction['signals'].append('local flood monitor unavailable; last known snapshot used')
        prediction['data_quality']['local_data_current'] = False
        prediction['data_quality']['local_data_status'] = 'STALE/UNAVAILABLE'
        prediction['data_quality']['last_known_flood_timestamp'] = current_for_assessment.get('timestamp')
    else:
        prediction['data_quality']['local_data_current'] = True
        prediction['data_quality']['local_data_status'] = 'CURRENT'

    STATE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    generated_at = datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
    output = {
        'schema_version': 6,
        'generated_at': generated_at,
        'model': '3-day flood precursor assessment',
        'prediction': prediction,
        'current_snapshot': current_for_assessment,
        'data_quality': prediction['data_quality'],
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    log = load(LOG_FILE, {})
    log['latest_monitor_run'] = output
    LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')

    icon = {'HIGH': '🔴', 'WATCH': '🟠', 'LOW-MODERATE': '🟡', 'LOW': '🟢', 'UNKNOWN': '⚪'}[prediction['risk']]
    imminent = 'YES' if prediction['imminent'] else 'NO'
    signals = ', '.join(prediction['signals']) or 'No significant precursor signals'
    freshness = 'CURRENT' if local_data_current else f"STALE/UNAVAILABLE — last known flood data: {current_for_assessment.get('timestamp')}"
    ALERT_FILE.write_text(
        f"{icon} MANDALUYONG FLOOD ASSESSMENT\n\n"
        f"Prediction: {prediction['risk']}\n"
        f"Risk score: {prediction['score']}/100\n"
        f"Confidence: {prediction['confidence']:.0%}\n"
        f"Imminent window: {imminent}\n"
        f"Window: latest 3 days; trend signals latest 36 hours\n\n"
        f"Local flood data: {freshness}\n"
        f"Current flood status: {current_for_assessment['flood_status']}\n"
        f"PAGASA: {current_for_assessment['pagasa_warning']}\n"
        f"Radar: {current_for_assessment['radar_status']}\n\n"
        f"Signals:\n{signals}\n\n"
        f"Assessment: {prediction['interpretation']}\n",
        encoding='utf-8',
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
