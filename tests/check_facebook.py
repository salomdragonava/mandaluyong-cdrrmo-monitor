import requests
import json

LAT = 14.5790363
LNG = 121.0496244

URL = "https://projectligtas.com/floodrisk/predict"

response = requests.get(
    URL,
    params={
        "lat": LAT,
        "lng": LNG
    },
    timeout=30
)

print("===== FLOOD RISK API TEST =====")
print("URL:", response.url)
print("STATUS:", response.status_code)
print("CONTENT TYPE:", response.headers.get("content-type"))

print("\n===== RESPONSE =====")

try:
    data = response.json()
    print(json.dumps(data, indent=2))
except Exception:
    print(response.text[:10000])
