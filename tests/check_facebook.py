import urllib.request
import urllib.parse
import json

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


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MandaluyongFloodMonitor/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body)


print("=" * 70)
print("MANDALUYONG FLOOD POINT VALIDATION")
print("=" * 70)

for point_id, name, lat, lng, purpose in points:

    print("\n")
    print("=" * 70)
    print(point_id, "-", name)
    print("PURPOSE:", purpose)
    print("COORDINATES:", lat, lng)
    print("=" * 70)

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

        print("\nPROJECTLIGTAS RESULT:")
        print(json.dumps(result, indent=2))

        data = result.get("data", {})

        print("\nSUMMARY:")
        print("Risk Level:", data.get("risk_level"))
        print("Risk Score:", data.get("risk_score"))
        print("Rainfall:", data.get("rainfall"), "mm")
        print("Elevation:", data.get("elevation"), "m")
        print("Flood History:", data.get("flood_history"))
        print("API Location:", data.get("location"))

    except Exception as error:

        print("\n❌ ERROR:")
        print(error)


print("\n")
print("=" * 70)
print("VALIDATION COMPLETE")
print("=" * 70)
