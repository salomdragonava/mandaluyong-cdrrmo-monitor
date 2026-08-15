import urllib.request
import urllib.parse
import json
import math
import time


# ============================================================
# MONITORING POINTS
# ============================================================

points = [
    ("P1", "Pananalig Street", "Villarica Street", "LOCAL"),
    ("P2", "Villarica Street", "Nanirahan Street", "LOCAL"),
    ("P3", "J.P. Rizal Street", "A. Mabini Street", "ESCAPE ROUTE"),
    ("P4", "J.P. Rizal Street", "San Pedro Street", "ESCAPE ROUTE"),
    ("P5", "J.P. Rizal Street", "Ilino Cruz Street", "ESCAPE ROUTE"),
    ("P6", "J.P. Rizal Street", "Saniboy Street", "ESCAPE ROUTE"),
    ("P7", "J.P. Rizal Street", "Coronado Street", "ESCAPE ROUTE"),
]


# ============================================================
# MANDALUYONG AREA
# ============================================================

# Approximate Mandaluyong bounding box
SOUTH = 14.555
WEST = 121.005
NORTH = 14.610
EAST = 121.060


# ============================================================
# HELPER
# ============================================================

def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MandaluyongFloodMonitor/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def distance_meters(lat1, lon1, lat2, lon2):
    """
    Approximate distance between two coordinates.
    Good enough for finding the closest point between
    two nearby road geometries.
    """

    R = 6371000

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return R * 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )


# ============================================================
# GET ROAD GEOMETRY FROM OPENSTREETMAP
# ============================================================

def get_roads(street_name):

    query = f"""
    [out:json][timeout:60];

    way["name"="{street_name}"](
        {SOUTH},
        {WEST},
        {NORTH},
        {EAST}
    );

    out geom;
    """

    url = "https://overpass-api.de/api/interpreter"

    data = urllib.parse.urlencode({
        "data": query
    }).encode()

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "MandaluyongFloodMonitor/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# FIND CLOSEST POINT BETWEEN TWO ROADS
# ============================================================

def find_intersection(road1, road2):

    best_distance = float("inf")
    best_point = None

    for way1 in road1["elements"]:

        geometry1 = way1.get("geometry", [])

        for way2 in road2["elements"]:

            geometry2 = way2.get("geometry", [])

            for p1 in geometry1:

                for p2 in geometry2:

                    distance = distance_meters(
                        p1["lat"],
                        p1["lon"],
                        p2["lat"],
                        p2["lon"]
                    )

                    if distance < best_distance:

                        best_distance = distance

                        best_point = (
                            (p1["lat"] + p2["lat"]) / 2,
                            (p1["lon"] + p2["lon"]) / 2
                        )

    return best_point, best_distance


# ============================================================
# PROJECTLIGTAS
# ============================================================

def get_flood_risk(lat, lon):

    params = urllib.parse.urlencode({
        "lat": lat,
        "lng": lon
    })

    url = (
        "https://projectligtas.com/floodrisk/predict?"
        + params
    )

    return get_json(url)


# ============================================================
# PROCESS ALL POINTS
# ============================================================

for point_id, street1, street2, purpose in points:

    print("\n")
    print("=" * 70)
    print(point_id)
    print(street1, "×", street2)
    print("PURPOSE:", purpose)
    print("=" * 70)

    try:

        print("\nFinding:", street1)

        road1 = get_roads(street1)

        print(
            "Found",
            len(road1.get("elements", [])),
            "road segments"
        )

        time.sleep(1)

        print("\nFinding:", street2)

        road2 = get_roads(street2)

        print(
            "Found",
            len(road2.get("elements", [])),
            "road segments"
        )

        if not road1.get("elements"):
            print("❌ Street 1 not found")
            continue

        if not road2.get("elements"):
            print("❌ Street 2 not found")
            continue

        # Find nearest geometry
        intersection, distance = find_intersection(
            road1,
            road2
        )

        if not intersection:

            print("❌ Could not determine intersection")
            continue

        lat, lon = intersection

        print("\nCandidate intersection:")
        print("LAT:", lat)
        print("LNG:", lon)

        print(
            "Distance between road geometries:",
            round(distance, 2),
            "meters"
        )

        # Important validation
        if distance > 100:

            print(
                "⚠️ WARNING: Roads are more than 100m apart."
            )

            print(
                "This may not be the intended intersection."
            )

        # ProjectLIGTAS
        print("\nProjectLIGTAS flood assessment:")

        risk = get_flood_risk(
            lat,
            lon
        )

        print(
            json.dumps(
                risk,
                indent=2
            )
        )

    except Exception as error:

        print("\n❌ ERROR:")
        print(error)

    time.sleep(2)


print("\n")
print("=" * 70)
print("FLOOD POINT DISCOVERY COMPLETE")
print("=" * 70)
