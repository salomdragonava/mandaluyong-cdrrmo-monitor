import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path("assessment_history.json")
OUTPUT_FILE = Path("assessment_state.json")
LOG_FILE = Path("assessment_log.json")

POINTS = ["P1", "P2", "P3", "P4", "P5", "P6"]


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))


def level_score(value):
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_current_snapshot():
    flood = load_json(Path("flood_state.json"), {})
    pagasa = load_json(Path("pagasa_state.json"), {})
    radar = load_json(Path("radar_state.json"), {})

    points = {}
    timestamps = []
    rain_values = []
    for point in POINTS:
        row = flood.get(point, {})
        points[point] = {
            "risk_level": row.get("risk_level"),
            "risk_score": level_score(row.get("risk_score")),
            "rainfall_mm": level_score(row.get("rainfall")),
            "timestamp": row.get("timestamp"),
        }
        if row.get("timestamp"):
            try:
                timestamps.append(parse_ts(row["timestamp"]))
            except ValueError:
                pass
        rain_values.append(points[point]["rainfall_mm"])

    latest_ts = max(timestamps).isoformat() if timestamps else None
    warning = str(pagasa.get("level") or pagasa.get("warning_level") or "UNKNOWN").upper()
    radar_status = str(radar.get("status") or radar.get("radar_status") or "UNKNOWN").upper()
    flood_status = "NORMAL"
    if points and any(v["risk_level"] not in (None, "Low", "LOW") for v in points.values()):
        flood_status = "ELEVATED"

    return {
        "timestamp": latest_ts,
        "rainfall_mm": max(rain_values) if rain_values else 0.0,
        "points": points,
        "pagasa_warning": warning,
        "radar_status": radar_status,
        "flood_status": flood_status,
    }


def assess(history):
    if not history:
        return {"risk": "UNKNOWN", "score": 0, "confidence": 0.2, "signals": ["No history"]}

    now = datetime.fromisoformat(history[-1]["timestamp"])
    cutoff = now - timedelta(days=3)
    rows = [r for r in history if datetime.fromisoformat(r["timestamp"]) >= cutoff]
    if len(rows) < 3:
        confidence = 0.45
    else:
        confidence = 0.75

    score = 0.0
    signals = []

    rainfall = [r["rainfall_mm"] for r in rows]
    if rainfall:
        max_rain = max(rainfall)
        recent_rain = rainfall[-1]
        rain_total_proxy = sum(rainfall)
        if max_rain >= 10:
            score += 35
            signals.append("high rainfall pulse")
        elif max_rain >= 5:
            score += 20
            signals.append("moderate rainfall pulse")
        elif max_rain >= 2:
            score += 8
        if recent_rain > 0.0 and len(rainfall) >= 3:
            trend = rainfall[-1] - rainfall[-3]
            if trend > 1.0:
                score += 10
                signals.append("rainfall increasing")

    latest = rows[-1]
    point_scores = [level_score(v["risk_score"]) for v in latest["points"].values()]
    if point_scores:
        max_point = max(point_scores)
        high_count = sum(v >= 2.0 for v in point_scores)
        medium_count = sum(v >= 1.0 for v in point_scores)
        if max_point >= 3.0:
            score += 40
            signals.append("high monitoring-point level")
        elif max_point >= 2.0:
            score += 25
            signals.append("elevated monitoring-point level")
        elif max_point >= 1.0:
            score += 10
        if high_count >= 2:
            score += 15
            signals.append("multi-point escalation")
        elif medium_count >= 3:
            score += 8
            signals.append("multi-point pressure")

    # Persistence / accumulation: repeated increases over consecutive observations.
    rising_steps = 0
    for prev, cur in zip(rows, rows[1:]):
        prev_avg = sum(v["risk_score"] for v in prev["points"].values()) / len(POINTS)
        cur_avg = sum(v["risk_score"] for v in cur["points"].values()) / len(POINTS)
        if cur_avg > prev_avg + 0.15:
            rising_steps += 1
    if rising_steps >= 3:
        score += 25
        signals.append("persistent water-level rise")
    elif rising_steps == 2:
        score += 12
        signals.append("repeated water-level rise")

    # Recovery is a strong negative signal: rise followed by a fall across most points.
    if len(rows) >= 3:
        mid = rows[-2]
        rises = 0
        recoveries = 0
        for p in POINTS:
            a = rows[-3]["points"][p]["risk_score"]
            b = mid["points"][p]["risk_score"]
            c = latest["points"][p]["risk_score"]
            if b > a + 0.15:
                rises += 1
                if c < b - 0.15:
                    recoveries += 1
        if rises >= 2 and recoveries >= max(2, math.ceil(rises * 0.6)):
            score -= 18
            signals.append("pulse-and-recovery pattern")

    if latest["pagasa_warning"] == "YELLOW":
        score += 10
        signals.append("PAGASA yellow")
    elif latest["pagasa_warning"] == "ORANGE":
        score += 20
        signals.append("PAGASA orange")
    elif latest["pagasa_warning"] == "RED":
        score += 30
        signals.append("PAGASA red")

    # Radar unavailable increases uncertainty, not flood probability.
    radar_unknown = latest["radar_status"] in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", ""}
    if radar_unknown:
        confidence -= 0.15
        signals.append("radar unavailable; uncertainty elevated")

    score = max(0.0, min(100.0, score))
    if score >= 65:
        risk = "HIGH"
    elif score >= 35:
        risk = "WATCH"
    elif score >= 15:
        risk = "LOW-MODERATE"
    else:
        risk = "LOW"

    confidence = max(0.2, min(0.95, confidence))
    return {
        "risk": risk,
        "score": round(score, 1),
        "confidence": round(confidence, 2),
        "window": "latest 3 days",
        "signals": signals,
        "interpretation": (
            "Persistent accumulation and multi-point escalation are the key predictors; isolated rainfall pulses that recover should not trigger a flood prediction."
        ),
    }


def main():
    current = load_current_snapshot()
    if not current["timestamp"]:
        raise SystemExit("flood_state.json has no usable timestamp")

    history = load_json(STATE_FILE, [])
    if history and history[-1].get("timestamp") == current["timestamp"]:
        history[-1] = current
    else:
        history.append(current)

    # Retain enough data to support the rolling 3-day window plus gaps.
    history = history[-5000:]
    STATE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    result = assess(history)
    output = {
        "schema_version": 1,
        "generated_at": current["timestamp"],
        "model": "3-day flood precursor assessment",
        "prediction": result,
        "current_snapshot": current,
        "data_quality": {
            "history_rows": len(history),
            "radar_available": current["radar_status"] not in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", ""},
        },
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    log = load_json(LOG_FILE, {})
    log["latest_monitor_run"] = output
    LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
