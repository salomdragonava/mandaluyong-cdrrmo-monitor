import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

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
REQUEST_TIMEOUT = 8


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MandaluyongFloodMonitor/1.0"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def check_point(point):
    point_id, name, lat, lng, purpose = point
    try:
        params = urllib.parse.urlencode({"lat": lat, "lng": lng})
        url = "https://projectligtas.com/floodrisk/predict?" + params
        result = get_json(url)
        data = result.get("data", {})
        return point_id, {
            "name": name,
            "purpose": purpose,
            "lat": lat,
            "lng": lng,
            "risk_level": data.get("risk_level", "Unknown"),
            "risk_score": float(data.get("risk_score", 0)),
            "rainfall": data.get("rainfall"),
            "flood_history": data.get("flood_history"),
            "timestamp": data.get("timestamp"),
        }, None
    except Exception as error:
        return point_id, None, f"{point_id} {name}: {error}"


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
print(f"Checking {len(POINTS)} points in parallel (timeout {REQUEST_TIMEOUT}s each)")

# Run all six independent API calls concurrently. This prevents six sequential
# timeouts from turning one hourly check into a ~3 minute job.
with ThreadPoolExecutor(max_workers=len(POINTS)) as executor:
    futures = [executor.submit(check_point, point) for point in POINTS]
    results = [future.result() for future in as_completed(futures)]

for point_id, data, error in results:
    point = next(p for p in POINTS if p[0] == point_id)
    _, name, _, _, purpose = point
    print("\n" + "-" * 70)
    print(point_id, "-", name)
    print("PURPOSE:", purpose)

    if error:
        print("❌ ERROR:", error.split(": ", 1)[-1])
        errors.append(error)
        continue

    current[point_id] = data
    print("Risk:", data["risk_level"])
    print("Score:", data["risk_score"])
    print("Rainfall:", data["rainfall"], "mm")
    print("Flood history:", data["flood_history"])

    if point_id in previous:
        old = previous[point_id]
        old_level = old.get("risk_level", "Unknown")
        old_score = float(old.get("risk_score", 0))
        print("\nPrevious:")
        print("Risk:", old_level)
        print("Score:", old_score)
        print("Change:")
        print("Score:", round(data["risk_score"] - old_score, 2))
        if old_level != data["risk_level"]:
            changes.append(f"{point_id} {old_level} → {data['risk_level']}")
    else:
        print("🆕 First reading for this point.")

high_points = [p for p in current.values() if p["risk_level"] == "High"]
medium_points = [p for p in current.values() if p["risk_level"] == "Medium"]
escape_points = [p for p in current.values() if p["purpose"] == "ESCAPE ROUTE"]
elevated_escape_points = [p for p in escape_points if p["risk_level"] in ("Medium", "High")]

if high_points:
    overall_status, overall_icon = "HIGH RISK", "🔴"
elif len(elevated_escape_points) >= 2:
    overall_status, overall_icon = "ESCAPE ROUTE ELEVATED", "🚨"
elif medium_points:
    overall_status, overall_icon = "WATCH", "🟡"
elif errors and not current:
    overall_status, overall_icon = "MONITORING ERROR", "⚠️"
else:
    overall_status, overall_icon = "NORMAL", "🟢"

rainfall_values = [
    p["rainfall"] for p in current.values()
    if isinstance(p.get("rainfall"), (int, float))
]

message_lines = [
    f"{overall_icon} MANDALUYONG FLOOD MONITOR",
    "",
    f"Status: {overall_status}",
    "",
]

if rainfall_values:
    message_lines += [f"Rainfall: {max(rainfall_values)} mm", ""]

message_lines.append("Monitoring points:")
for point_id, name, lat, lng, purpose in POINTS:
    if point_id not in current:
        message_lines.append(f"⚪ {point_id} {name} — NO DATA")
        continue
    point = current[point_id]
    level = point["risk_level"]
    icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(level, "⚪")
    message_lines.append(f"{icon} {point_id} {name} — {level} ({point['risk_score']})")

message_lines.append("")
if not elevated_escape_points:
    message_lines.append("🛣️ Escape route: CLEAR")
elif len(elevated_escape_points) == 1:
    p = elevated_escape_points[0]
    message_lines.append(f"⚠️ Escape route: {p['name']} is {p['risk_level']}")
else:
    message_lines.append(f"🚨 Escape route: {len(elevated_escape_points)} points elevated")
    for p in elevated_escape_points:
        message_lines.append(f"   • {p['name']} — {p['risk_level']}")

message_lines.append("")
if changes:
    message_lines.append("Changes detected:")
    message_lines.extend(f"• {change}" for change in changes)
else:
    message_lines.append("No significant risk-level changes detected.")

if errors:
    message_lines += ["", "⚠️ Monitoring errors:"]
    message_lines.extend(f"• {error}" for error in errors)

message = "\n".join(message_lines)
with open(ALERT_FILE, "w", encoding="utf-8") as file:
    file.write(message)

print("\n" + "=" * 70)
print("TELEGRAM STATUS GENERATED")
print("=" * 70)
print(message)

with open(STATE_FILE, "w", encoding="utf-8") as file:
    json.dump(current, file, indent=2)

print("\n" + "=" * 70)
print("CURRENT STATE SAVED")
print("=" * 70)
