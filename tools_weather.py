import requests

def _get(url: str, timeout: int = 10):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def weather_resolve_location(api_key: str, city: str) -> dict:
    """
    用 search.json 把城市對到 WeatherAPI 的 query
    """
    city = (city or "").strip()
    url = f"https://api.weatherapi.com/v1/search.json?key={api_key}&q={city}"
    data = _get(url)
    if not data:
        # fallback：直接用 city 當 query
        return {"name": city, "query": city}
    top = data[0]
    name = f"{top.get('name','')}"
    region = top.get("region") or ""
    country = top.get("country") or ""
    display = " / ".join([x for x in [name, region, country] if x])
    # WeatherAPI 接受 q=lat,lon 或 q=名稱
    query = f"{top.get('lat')},{top.get('lon')}"
    return {"name": display, "query": query}

def weather_current(api_key: str, query: str, lang: str="zh"):
    url = f"https://api.weatherapi.com/v1/current.json?key={api_key}&q={query}&lang={lang}"
    data = _get(url)
    cur = data["current"]
    return {
        "text": cur["condition"]["text"],
        "temp_c": cur["temp_c"],
        "feelslike_c": cur["feelslike_c"],
        "humidity": cur["humidity"],
        "wind_kph": cur["wind_kph"]
    }

def weather_forecast_days(api_key: str, query: str, days: int = 3, lang: str="zh"):
    """
    免費通常支援 3 days forecast（依方案）
    """
    days = max(1, min(int(days), 3))
    url = f"https://api.weatherapi.com/v1/forecast.json?key={api_key}&q={query}&days={days}&lang={lang}"
    data = _get(url)
    fds = data["forecast"]["forecastday"]
    out = []
    for d in fds:
        day = d["day"]
        out.append({
            "date": d["date"],
            "text": day["condition"]["text"],
            "min_c": day["mintemp_c"],
            "max_c": day["maxtemp_c"],
            "chance_rain": day.get("daily_chance_of_rain", 0)
        })
    return out
