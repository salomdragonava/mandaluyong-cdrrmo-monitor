import urllib.request
import urllib.parse
import json
import time

# ============================================
# MANDALUYONG FLOOD MONITORING POINTS
# ============================================

points = [
    (
        "P1",
        "Pananalig Street and Villarica Street, Mandaluyong, Philippines",
        "LOCAL"
    ),
    (
        "P2",
        "Villarica Street and Nanirahan Street, Mandaluyong, Philippines",
        "LOCAL"
    ),
    (
        "P3",
        "J.P. Rizal Street and A. Mabini Street, Mandaluyong, Philippines",
        "ESCAPE ROUTE"
    ),
    (
        "P4",
        "J.P. Rizal Street and San Pedro Street, Mandaluyong, Philippines",
        "ESCAPE ROUTE"
    ),
    (
        "P5",
        "J.P. Rizal Street and Ilino Cruz Street, Mandaluyong, Philippines",
        "ESCAPE ROUTE"
    ),
    (
        "P6",
        "J.P. Rizal Street and Saniboy Street, Mandaluyong, Philippines",
        "ESCAPE ROUTE"
    ),
    (
        "P7",
        "J.P. Rizal Street and Coronado Street, Mandaluyong, Philippines",
        "ESCAPE ROUTE"
    ),
]


# ============================================
# HELPER: GET JSON
# ============================================

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


# ============================================
# PROCESS EACH MONITORING POINT
# ============================================

for point_id, location, purpose in points:

    print("\n")
    print("=" * 60)
    print(point_id)
    print("LOCATION:", location)
    print("PURPOSE:", purpose)
    print("=" * 60)

    # ----------------------------------------
    # 1. FIND LOCATION USING OPENSTREETMAP
    # ----------------------------------------

    params = urllib.parse.urlencode({
        "format": "json",
        "q": location,
        "countrycodes": "ph",
        "limit": 5
    })

    search_url = (
        "https://nominatim.openstreetmap.org/search?"
        + params
    )

    try:

        results = get_json(search_url)

        if not results:
            print("❌ NO LOCATION FOUND")
            continue

        print("\nOpenStreetMap results:")

        for result in results:

            print(
                "•",
                result.get("display_name"),
                "| LAT:",
                result.get("lat"),
                "| LNG:",
                result.get("lon")
            )

        # ----------------------------------------
        # 2. SELECT FIRST RESULT
        # ----------------------------------------

        result = results[0]

        lat = float(result["lat"])
        lng = float(result["lon"])

        print("\nSelected coordinates:")
        print("LAT:", lat)
        print("LNG:", lng)

        # ----------------------------------------
        # 3. QUERY PROJECTLIGTAS
        # ----------------------------------------

        api_params = urllib.parse.urlencode({
            "lat": lat,
            "lng": lng
        })

        api_url = (
            "https://projectligtas.com/floodrisk/predict?"
            + api_params
        )

        risk = get_json(api_url)

        print("\nProjectLIGTAS response:")

        print(
            json.dumps(
                risk,
                indent=2
            )
        )

    except Exception as error:

        print("\n❌ ERROR:")
        print(error)

    # ----------------------------------------
    # 4. WAIT BEFORE NEXT NOMINATIM REQUEST
    # ----------------------------------------

    time.sleep(1)


print("\n")
print("=" * 60)
print("MONITORING POINT DISCOVERY COMPLETE")
print("=" * 60)
