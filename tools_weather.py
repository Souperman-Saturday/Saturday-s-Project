import os
import requests

WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")

def get_weather_now(city: str) -> str:
    if not WEATHER_API_KEY:
        raise RuntimeError("WEATHER_API_KEY not set")

    url = "https://api.weatherapi.com/v1/current.json"
    params = {
        "key": WEATHER_API_KEY,
        "q": city,
        "aqi": "no",
        "lang": "zh"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    loc = data.get("location", {})
    cur = data.get("current", {})
    name = loc.get("name") or city
    temp = cur.get("temp_c")
    feels = cur.get("feelslike_c")
    hum = cur.get("humidity")
    wind = cur.get("wind_kph")
    cond = (cur.get("condition") or {}).get("text", "")

    return f"{name}：{cond}，氣溫 {temp}°C（體感 {feels}°C），濕度 {hum}% ，風速 {wind} km/h"

def get_weather_forecast(city: str, days: int = 3) -> str:
    if not WEATHER_API_KEY:
        raise RuntimeError("WEATHER_API_KEY not set")

    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": WEATHER_API_KEY,
        "q": city,
        "days": days,
        "aqi": "no",
        "alerts": "no",
        "lang": "zh"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    fc = (data.get("forecast") or {}).get("forecastday") or []
    lines = []
    for d in fc:
        date = d.get("date", "")
        day = d.get("day", {})
        cond = (day.get("condition") or {}).get("text", "")
        maxt = day.get("maxtemp_c")
        mint = day.get("mintemp_c")
        rain = day.get("daily_chance_of_rain")
        lines.append(f"{date}：{cond}，{mint}~{maxt}°C，降雨機率 {rain}%")

    return "\n".join(lines) if lines else "（預報資料不足）"
