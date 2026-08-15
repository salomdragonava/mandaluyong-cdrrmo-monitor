import urllib.request
import urllib.parse
import json
import os

POINTS = [
    ("P1", "Pananalig × Villarica", 14.586620, 121.023037, "LOCAL"),
    ("P2", "Villarica × Nanirahan", 14.586289, 121.023049, "LOCAL"),
    ("P3", "J.P. Rizal × San Pedro", 14.580411, 121.020848, "ESCAPE ROUTE"),
    ("P4", "J.P. Rizal × Ilino Cruz", 14.576385, 121.024315, "ESCAPE ROUTE"),
    ("P5", "J.P. Rizal × Saniboy", 14.574723, 121.026105, "ESCAPE ROUTE"),
    ("P6", "J.P. Rizal × Coronado", 14.570609, 121.030969, "ESCAPE ROUTE"),
]

STATE_FILE = "flood_state.json"
ALERT_FILE = "telegram_alert.txt"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MandaluyongFloodMonitor/1.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ------------------------------------------------------------
# LOAD PREVIOUS STATE
# ------------------------------------------------------------

previous = {}

if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            previous = json.load(file)
    except Exception:
        previous = {}


current = {}
errors = []
changes = []


print("=" * 70)
print("MANDALUYONG FLOOD MONITOR")
print("=" * 70)


# ------------------------------------------------------------
# CHECK ALL MONITORING POINTS
# ------------------------------------------------------------

for point_id, name, lat, lng, purpose in POINTS:

    print("\n" + "-" * 70)
    print(point_id, "-", name)
    print("PURPOSE:", purpose)

    try:
        params = urllib.parse.urlencode({
            "lat": lat,
            "lng": lng
        })

        url = (
            "https://projectligtas.com/floodrisk/predict?"
            + params
        )

        result = get_json(url)
        data = result.get("data", {})

        risk_level = data.get("risk_level", "Unknown")
        risk_score = float(data.get("risk_score", 0))
        rainfall = data.get("rainfall")
        flood_history = data.get("flood_history")

        current[point_id] = {
            "name": name,
            "purpose": purpose,
            "lat": lat,
            "lng": lng,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "rainfall": rainfall,
            "flood_history": flood_history,
            "timestamp": data.get("timestamp")
        }

        print("Risk:", risk_level)
        print("Score:", risk_score)
        print("Rainfall:", rainfall, "mm")
        print("Flood history:", flood_history)

        # ----------------------------------------------------
        # COMPARE WITH PREVIOUS READING
        # ----------------------------------------------------

        if point_id in previous:

            old = previous[point_id]

            old_level = old.get("risk_level", "Unknown")
            old_score = float(old.get("risk_score", 0))

            score_change = risk_score - old_score

            print("\nPrevious:")
            print("Risk:", old_level)
            print("Score:", old_score)

            print("Change:")
            print("Score:", round(score_change, 2))

            if old_level != risk_level:
                changes.append(
                    f"{point_id} {old_level} → {risk_level}"
                )

        else:
            print("🆕 First reading for this point.")

    except Exception as error:

        print("❌ ERROR:", error)

        errors.append(
            f"{point_id} {name}: {error}"
        )


# ------------------------------------------------------------
# DETERMINE OVERALL STATUS
# ------------------------------------------------------------

risk_levels = [
    point["risk_level"]
    for point in current.values()
]

high_points = [
    point for point in current.values()
    if point["risk_level"] == "High"
]

medium_points = [
    point for point in current.values()
    if point["risk_level"] == "Medium"
]

escape_points = [
    point for point in current.values()
    if point["purpose"] == "ESCAPE ROUTE"
]

elevated_escape_points = [
    point for point in escape_points
    if point["risk_level"] in ("Medium", "High")
]


if high_points:
    overall_status = "HIGH RISK"
    overall_icon = "🔴"

elif len(elevated_escape_points) >= 2:
    overall_status = "ESCAPE ROUTE ELEVATED"
    overall_icon = "🚨"

elif medium_points:
    overall_status = "WATCH"
    overall_icon = "🟡"

elif errors and not current:
    overall_status = "MONITORING ERROR"
    overall_icon = "⚠️"

else:
    overall_status = "NORMAL"
    overall_icon = "🟢"


# ------------------------------------------------------------
# RAINFALL SUMMARY
# ------------------------------------------------------------

rainfall_values = [
    point["rainfall"]
    for point in current.values()
    if isinstance(point.get("rainfall"), (int, float))
]

if rainfall_values:
    max_rainfall = max(rainfall_values)
else:
    max_rainfall = None


# ------------------------------------------------------------
# BUILD TELEGRAM MESSAGE
# ------------------------------------------------------------

message_lines = [
    f"{overall_icon} MANDALUYONG FLOOD MONITOR",
    "",
    f"Status: {overall_status}",
    ""
]


if max_rainfall is not None:
    message_lines.append(
        f"Rainfall: {max_rainfall} mm"
    )
    message_lines.append("")


message_lines.append("Monitoring points:")

for point_id, name, lat, lng, purpose in POINTS:

    if point_id not in current:
        message_lines.append(
            f"⚪ {point_id} {name} — NO DATA"
        )
        continue

    point = current[point_id]
    level = point["risk_level"]
    score = point["risk_score"]

    if level == "High":
        icon = "🔴"
    elif level == "Medium":
        icon = "🟡"
    elif level == "Low":
        icon = "🟢"
    else:
        icon = "⚪"

    message_lines.append(
        f"{icon} {point_id} {name} — {level} ({score})"
    )


message_lines.append("")

# ------------------------------------------------------------
# ESCAPE ROUTE STATUS
# ------------------------------------------------------------

if not elevated_escape_points:

    message_lines.append(
        "🛣️ Escape route: CLEAR"
    )

elif len(elevated_escape_points) == 1:

    point = elevated_escape_points[0]

    message_lines.append(
        f"⚠️ Escape route: {point['name']} is "
        f"{point['risk_level']}"
    )

else:

    message_lines.append(
        f"🚨 Escape route: "
        f"{len(elevated_escape_points)} points elevated"
    )

    for point in elevated_escape_points:
        message_lines.append(
            f"   • {point['name']} — {point['risk_level']}"
        )


# ------------------------------------------------------------
# CHANGES
# ------------------------------------------------------------

if changes:

    message_lines.append("")
    message_lines.append("Changes detected:")

    for change in changes:
        message_lines.append(f"• {change}")

else:

    message_lines.append("")
    message_lines.append(
        "No significant risk-level changes detected."
    )


# ------------------------------------------------------------
# ERRORS
# ------------------------------------------------------------

if errors:

    message_lines.append("")
    message_lines.append("⚠️ Monitoring errors:")

    for error in errors:
        message_lines.append(f"• {error}")


message = "\n".join(message_lines)


# ------------------------------------------------------------
# WRITE TELEGRAM MESSAGE
# ------------------------------------------------------------

with open(ALERT_FILE, "w", encoding="utf-8") as file:
    file.write(message)


print("\n" + "=" * 70)
print("TELEGRAM STATUS GENERATED")
print("=" * 70)
print(message)


# ------------------------------------------------------------
# SAVE CURRENT STATE
# ------------------------------------------------------------

with open(STATE_FILE, "w", encoding="utf-8") as file:
    json.dump(current, file, indent=2)

print("\n" + "=" * 70)
print("CURRENT STATE SAVED")
print("=" * 70)
