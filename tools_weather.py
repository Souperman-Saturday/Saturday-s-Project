import requests


def _weatherapi_get(url: str, params: dict) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def get_weather_now(api_key: str, city: str) -> str | None:
    """
    WeatherAPI：Current Weather
    Docs: weatherapi.com :contentReference[oaicite:1]{index=1}
    """
    if not api_key:
        return None

    url = "https://api.weatherapi.com/v1/current.json"
    data = _weatherapi_get(url, {"key": api_key, "q": city, "lang": "zh"})
    if not data:
        return None

    loc = data.get("location", {})
    cur = data.get("current", {})
    cond = (cur.get("condition") or {}).get("text", "")

    name = loc.get("name", city)
    temp_c = cur.get("temp_c")
    feels = cur.get("feelslike_c")
    hum = cur.get("humidity")
    wind_kph = cur.get("wind_kph")

    return (
        f"{name}：{cond}\n"
        f"氣溫 {temp_c}°C（體感 {feels}°C）｜濕度 {hum}%｜風速 {wind_kph} km/h"
    )


def get_weather_forecast(api_key: str, city: str, days: int = 3) -> str | None:
    """
    WeatherAPI：Forecast
    Docs: weatherapi.com :contentReference[oaicite:2]{index=2}
    """
    if not api_key:
        return None

    url = "https://api.weatherapi.com/v1/forecast.json"
    days = max(1, min(int(days), 3))
    data = _weatherapi_get(url, {"key": api_key, "q": city, "days": days, "lang": "zh"})
    if not data:
        return None

    loc = data.get("location", {})
    fc = (data.get("forecast") or {}).get("forecastday") or []
    name = loc.get("name", city)

    lines = [f"{name} 未來 {days} 天："]
    for d in fc:
        date = d.get("date")
        day = d.get("day", {})
        cond = (day.get("condition") or {}).get("text", "")
        maxt = day.get("maxtemp_c")
        mint = day.get("mintemp_c")
        rain = day.get("daily_chance_of_rain")
        lines.append(f"- {date}：{cond}｜{mint}~{maxt}°C｜降雨機率 {rain}%")
    return "\n".join(lines)
