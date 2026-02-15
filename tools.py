import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = os.getenv("TZ", "Asia/Taipei")

def now_tz():
    return datetime.now(ZoneInfo(TZ))

def weatherapi_forecast(city: str, days: int = 3) -> dict:
    key = os.getenv("WEATHERAPI_KEY")
    if not key:
        return {"ok": False, "error": "WEATHERAPI_KEY 未設定"}
    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": key,
        "q": city,
        "days": max(1, min(days, 10)),
        "aqi": "no",
        "alerts": "no",
        "lang": "zh"
    }
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return {"ok": False, "error": f"WeatherAPI 失敗 {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "data": r.json()}

def serper_news(query: str, num: int = 8, hl: str = "zh-tw", gl: str = "tw") -> dict:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return {"ok": False, "error": "SERPER_API_KEY 未設定"}
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    payload = {
        "q": query,
        "num": max(3, min(num, 8)),
        "hl": hl,
        "gl": gl,
        "tbs": "qdr:d"  # 近 1 天
    }
    r = requests.post(url, headers=headers, json=payload, timeout=25)
    if r.status_code != 200:
        return {"ok": False, "error": f"Serper(news) 失敗 {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "data": r.json()}

def serper_search(query: str, hl: str = "zh-tw", gl: str = "tw") -> dict:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return {"ok": False, "error": "SERPER_API_KEY 未設定"}
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    payload = {"q": query, "hl": hl, "gl": gl, "num": 5, "tbs": "qdr:d"}  # 近 1 天偏好
    r = requests.post(url, headers=headers, json=payload, timeout=25)
    if r.status_code != 200:
        return {"ok": False, "error": f"Serper(search) 失敗 {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "data": r.json()}
