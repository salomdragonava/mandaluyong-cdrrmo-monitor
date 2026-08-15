import urllib.request
import urllib.parse
import json
import os

points = [
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


previous = {}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as file:
        previous = json.load(file)

current = {}
alerts = []

print("=" * 70)
print("MANDALUYONG FLOOD MONITOR")
print("=" * 70)

for point_id, name, lat, lng, purpose in points:

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

        if point_id not in previous:
            print("🆕 First reading for this point.")
            continue

        old = previous[point_id]
        old_level = old.get("risk_level", "Unknown")
        old_score = float(old.get("risk_score", 0))

        score_change = risk_score - old_score

        print("\nPrevious:")
        print("Risk:", old_level)
        print("Score:", old_score)

        print("\nChange:")
        print("Score:", round(score_change, 2))

        if old_level == "Low" and risk_level == "Medium":

            alerts.append(
                f"🟡 WATCH — {point_id}\n"
                f"{name}\n"
                f"Risk changed: Low → Medium\n"
                f"Score: {risk_score}"
            )

        elif old_level in ("Low", "Medium") and risk_level == "High":

            alerts.append(
                f"🔴 FLOOD RISK ALERT — {point_id}\n"
                f"{name}\n"
                f"Risk changed: {old_level} → High\n"
                f"Score: {risk_score}"
            )

        elif old_level in ("Medium", "High") and risk_level == "Low":

            alerts.append(
                f"🟢 RECOVERY — {point_id}\n"
                f"{name}\n"
                f"Risk changed: {old_level} → Low\n"
                f"Score: {risk_score}"
            )

        else:
            print("No individual alert.")

    except Exception as error:
        print("❌ ERROR:", error)


route_ids = ["P3", "P4", "P5", "P6"]

elevated_route = [
    current[p]
    for p in route_ids
    if p in current and current[p]["risk_level"] in ("Medium", "High")
]

previous_elevated_route = [
    previous[p]
    for p in route_ids
    if p in previous and previous[p].get("risk_level") in ("Medium", "High")
]

if len(elevated_route) >= 2 and len(previous_elevated_route) < 2:

    lines = [
        "🚨 ESCAPE ROUTE ALERT",
        "",
        f"{len(elevated_route)} points on the J.P. Rizal escape route "
        "are now elevated:",
        ""
    ]

    for point in elevated_route:
        lines.append(
            f"{point['name']} — {point['risk_level']} "
            f"(score {point['risk_score']})"
        )

    lines.extend([
        "",
        "⚠️ Multiple points along the monitored route are affected.",
        "Check conditions before using this route."
    ])

    alerts.append("\n".join(lines))


if alerts:

    message = (
        "🚨 MANDALUYONG CDRRMO MONITOR\n\n"
        + "\n\n".join(alerts)
    )

    with open(ALERT_FILE, "w", encoding="utf-8") as file:
        file.write(message)

    print("\n🚨 ALERT GENERATED")
    print(message)

else:

    open(ALERT_FILE, "w", encoding="utf-8").close()

    print("\nNo alerts generated.")


with open(STATE_FILE, "w") as file:
    json.dump(current, file, indent=2)

print("\n" + "=" * 70)
print("CURRENT STATE SAVED")
print("=" * 70)
