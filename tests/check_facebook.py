import urllib.request
import urllib.parse
import json
import os

# ============================================================
# MANDALUYONG FLOOD MONITORING POINTS
# ============================================================

points = [
    ("P1", "Pananalig × Villarica", 14.586620, 121.023037, "LOCAL"),
    ("P2", "Villarica × Nanirahan", 14.586289, 121.023049, "LOCAL"),
    ("P3", "J.P. Rizal × San Pedro", 14.580411, 121.020848, "ESCAPE ROUTE"),
    ("P4", "J.P. Rizal × Ilino Cruz", 14.576385, 121.024315, "ESCAPE ROUTE"),
    ("P5", "J.P. Rizal × Saniboy", 14.574723, 121.026105, "ESCAPE ROUTE"),
    ("P6", "J.P. Rizal × Coronado", 14.570609, 121.030969, "ESCAPE ROUTE"),
]

STATE_FILE = "flood_state.json"


# ============================================================
# GET JSON
# ============================================================

def get_json(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MandaluyongFloodMonitor/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# LOAD PREVIOUS STATE
# ============================================================

if os.path.exists(STATE_FILE):

    with open(STATE_FILE, "r") as file:
        previous = json.load(file)

else:

    previous = {}


# ============================================================
# CURRENT STATE
# ============================================================

current = {}


print("=" * 70)
print("MANDALUYONG FLOOD MONITOR")
print("=" * 70)


# ============================================================
# CHECK EACH POINT
# ============================================================

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

        risk_level = data.get("risk_level")
        risk_score = data.get("risk_score")
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
        # COMPARE WITH PREVIOUS RUN
        # ----------------------------------------------------

        if point_id in previous:

            old = previous[point_id]

            old_score = old.get("risk_score", 0)
            old_level = old.get("risk_level")

            score_change = risk_score - old_score

            print("\nPrevious:")
            print("Risk:", old_level)
            print("Score:", old_score)

            print("\nChange:")
            print("Score:", round(score_change, 2))

            if risk_level != old_level:

                print(
                    "⚠️ RISK LEVEL CHANGED:",
                    old_level,
                    "→",
                    risk_level
                )

            elif abs(score_change) >= 1:

                print(
                    "⚠️ RISK SCORE CHANGED:",
                    round(score_change, 2)
                )

            else:

                print("No significant change.")

        else:

            print("\n🆕 First reading for this point.")

    except Exception as error:

        print("❌ ERROR:", error)


# ============================================================
# SAVE CURRENT STATE
# ============================================================

with open(STATE_FILE, "w") as file:

    json.dump(
        current,
        file,
        indent=2
    )


print("\n")
print("=" * 70)
print("CURRENT STATE SAVED")
print("=" * 70)
