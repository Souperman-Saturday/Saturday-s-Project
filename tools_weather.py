import os, requests

WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "")

def get_weather(city: str) -> dict:
    if not WEATHERAPI_KEY:
        return {"ok": False, "error": "WEATHERAPI_KEY 未設定"}
    if not city:
        return {"ok": False, "error": "未設定城市"}

    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": WEATHERAPI_KEY,
        "q": city,
        "days": 3,
        "aqi": "no",
        "alerts": "no",
        "lang": "zh"
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        loc = data.get("location", {})
        cur = data.get("current", {})
        forecast = data.get("forecast", {}).get("forecastday", [])
        return {
            "ok": True,
            "location": f"{loc.get('name','')}, {loc.get('region','')}",
            "temp_c": cur.get("temp_c"),
            "condition": (cur.get("condition") or {}).get("text"),
            "humidity": cur.get("humidity"),
            "wind_kph": cur.get("wind_kph"),
            "forecast": [
                {
                    "date": d.get("date"),
                    "max_c": (d.get("day") or {}).get("maxtemp_c"),
                    "min_c": (d.get("day") or {}).get("mintemp_c"),
                    "text": ((d.get("day") or {}).get("condition") or {}).get("text"),
                    "rain_chance": (d.get("day") or {}).get("daily_chance_of_rain"),
                } for d in forecast
            ]
        }
    except Exception as e:
        return {"ok": False, "error": f"WeatherAPI 查詢失敗：{e}"}

