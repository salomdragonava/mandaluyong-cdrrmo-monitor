import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

PH_TZ = timezone(timedelta(hours=8))
FLOOD_STATE = Path("flood_state.json")
PAGASA_STATE = Path("pagasa_state.json")
RADAR_STATE = Path("radar_threat_state.json")
MONITOR_STATE = Path("monitor_notification_state.json")
ALERT_FILE = Path("combined_alert.txt")
ESCALATION_FILE = Path("escalation_alert.txt")

POINTS = [
    ("P1", "Pananalig × Villarica", "LOCAL"),
    ("P2", "Villarica × Nanirahan", "LOCAL"),
    ("P3", "J.P. Rizal × San Pedro", "ESCAPE ROUTE"),
    ("P4", "J.P. Rizal × Ilino Cruz", "ESCAPE ROUTE"),
    ("P5", "J.P. Rizal × Saniboy", "ESCAPE ROUTE"),
    ("P6", "J.P. Rizal × Coronado", "ESCAPE ROUTE"),
]

RADAR_MAX_DISTANCE_KM = 50
RADAR_MIN_THREAT_SCORE = 55
RADAR_MIN_PIXELS = 150
RADAR_ALERT_CLASSES = {"yellow", "orange", "red", "purple"}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def warning_description(level):
    return {
        "GREEN": "No active PAGASA Heavy Rainfall Warning for Metro Manila",
        "YELLOW": "Possible flooding in flood-prone areas",
        "ORANGE": "Flooding is threatening",
        "RED": "Severe flooding is expected",
    }.get(level, "PAGASA warning status unavailable")


def warning_icon(level):
    return {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(level, "⚪")


def risk_rank(level):
    return {"Low": 1, "Medium": 2, "High": 3}.get(level, 0)


def radar_candidates(radar):
    if radar.get("status") != "ok":
        return []
    return [
        item for item in radar.get("top_threats", [])
        if item.get("threat_candidate")
        and item.get("threat_score", 0) >= RADAR_MIN_THREAT_SCORE
        and item.get("distance_to_mandaluyong_km", 9999) <= RADAR_MAX_DISTANCE_KM
        and item.get("pixels_current", 0) >= RADAR_MIN_PIXELS
        and (
            item.get("current_class") in RADAR_ALERT_CLASSES
            or item.get("previous_class") in RADAR_ALERT_CLASSES
        )
    ]


def flood_summary(flood):
    points = [p for p in flood.values() if isinstance(p, dict)]
    high = [p for p in points if p.get("risk_level") == "High"]
    medium = [p for p in points if p.get("risk_level") == "Medium"]
    escape = [p for p in points if p.get("purpose") == "ESCAPE ROUTE"]
    elevated_escape = [p for p in escape if p.get("risk_level") in ("Medium", "High")]

    if high:
        status = "HIGH RISK"
    elif len(elevated_escape) >= 2:
        status = "ESCAPE ROUTE ELEVATED"
    elif medium:
        status = "WATCH"
    elif not points:
        status = "NO DATA"
    else:
        status = "NORMAL"

    rainfall = [p.get("rainfall") for p in points if isinstance(p.get("rainfall"), (int, float))]
    return status, max(rainfall) if rainfall else None, elevated_escape


def build_messages():
    pagasa = load(PAGASA_STATE, {})
    flood = load(FLOOD_STATE, {})
    radar = load(RADAR_STATE, {})
    previous = load(MONITOR_STATE, {})

    level = pagasa.get("level", "UNKNOWN")
    flood_status, rainfall, elevated_escape = flood_summary(flood)
    candidates = radar_candidates(radar)
    now = datetime.now(PH_TZ)

    icon = warning_icon(level)
    lines = [
        f"{icon} PAGASA NCR WEATHER STATUS",
        "",
        f"Warning Level: {level}",
        warning_description(level),
        "",
        f"Checked: {now.strftime('%Y-%m-%d %I:%M %p')}",
        "",
        "MANDALUYONG LOCAL MONITORING",
        "",
        f"Flood status: {flood_status}",
    ]

    if rainfall is not None:
        lines += [f"Rainfall: {rainfall} mm"]

    lines += ["", "Monitoring points:"]
    for point_id, name, purpose in POINTS:
        point = flood.get(point_id)
        if not point:
            lines.append(f"⚪ {point_id} {name} — NO DATA")
            continue
        risk = point.get("risk_level", "Unknown")
        risk_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(risk, "⚪")
        lines.append(f"{risk_icon} {point_id} {name} — {risk} ({point.get('risk_score', 0)})")

    lines += [""]
    if elevated_escape:
        lines.append(f"⚠️ Escape route: {len(elevated_escape)} point(s) elevated")
        for point in elevated_escape:
            lines.append(f"   • {point.get('name')} — {point.get('risk_level')}")
    else:
        lines.append("🛣️ Escape route: CLEAR")

    lines += ["", "Radar monitoring:"]
    if candidates:
        nearest = sorted(candidates, key=lambda x: (-x.get("threat_score", 0), x.get("distance_to_mandaluyong_km", 9999)))[:3]
        lines.append(f"🔴 {len(candidates)} qualifying radar threat(s) near/approaching Mandaluyong")
        for item in nearest:
            lines.append(
                f"   • {item.get('current_class', 'unknown').upper()} | "
                f"score {item.get('threat_score', 0):.0f} | "
                f"{item.get('distance_to_mandaluyong_km', 0):.1f} km | "
                f"movement {item.get('movement_km', 0):.1f} km"
            )
    elif radar.get("status") == "ok":
        lines.append("🟢 No qualifying radar core detected for Mandaluyong")
    else:
        lines.append("⚪ Radar status unavailable")

    combined = "\n".join(lines)
    ALERT_FILE.write_text(combined, encoding="utf-8")

    previous_level = previous.get("pagasa_level", "UNKNOWN")
    previous_flood = previous.get("flood", {})
    previous_radar = previous.get("radar_candidate", False)

    escalations = []
    warning_order = {"UNKNOWN": 0, "GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4}
    if warning_order.get(level, 0) > warning_order.get(previous_level, 0) and previous_level != "UNKNOWN":
        escalations.append(f"PAGASA warning level increased: {previous_level} → {level}")

    for point_id, name, _ in POINTS:
        old = previous_flood.get(point_id, {})
        new = flood.get(point_id, {})
        if risk_rank(new.get("risk_level")) > risk_rank(old.get("risk_level")) and old.get("risk_level"):
            escalations.append(f"{point_id} {name}: {old.get('risk_level')} → {new.get('risk_level')}")

    if elevated_escape and not previous.get("escape_elevated", False):
        escalations.append("Escape route risk became elevated")

    if candidates and not previous_radar:
        escalations.append("Qualifying radar threat detected near/approaching Mandaluyong")

    ESCALATION_FILE.unlink(missing_ok=True)
    if escalations:
        escalation = "🚨 MANDALUYONG MONITOR ESCALATION\n\n" + "\n".join(f"• {item}" for item in escalations)
        escalation += f"\n\nChecked: {now.strftime('%Y-%m-%d %I:%M %p')}"
        ESCALATION_FILE.write_text(escalation, encoding="utf-8")

    MONITOR_STATE.write_text(
        json.dumps(
            {
                "pagasa_level": level,
                "flood": flood,
                "radar_candidate": bool(candidates),
                "escape_elevated": bool(elevated_escape),
                "last_checked": now.isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(combined)
    if escalations:
        print("\nESCALATION GENERATED")
        print(ESCALATION_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    build_messages()
