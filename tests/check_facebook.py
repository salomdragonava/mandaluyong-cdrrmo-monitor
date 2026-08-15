import urllib.request
import urllib.parse

LAT = 14.5790363
LNG = 121.0496244

base_url = "https://projectligtas.com/floodrisk/predict"

params = urllib.parse.urlencode({
    "lat": LAT,
    "lng": LNG
})

url = f"{base_url}?{params}"

print("===== FLOOD RISK API TEST =====")
print("URL:", url)

try:
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read().decode("utf-8")

        print("STATUS:", response.status)
        print("CONTENT TYPE:", response.headers.get("content-type"))

        print("\n===== RESPONSE =====")
        print(body[:10000])

except Exception as e:
    print("ERROR:", e)
