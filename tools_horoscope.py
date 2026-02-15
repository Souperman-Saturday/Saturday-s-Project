import os, requests
from datetime import datetime
from dateutil import tz

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

def serper_search(query: str, hl: str="zh-tw", gl: str="tw") -> dict:
    if not SERPER_API_KEY:
        return {"ok": False, "error": "SERPER_API_KEY 未設定"}
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": 5, "hl": hl, "gl": gl, "tbs": "qdr:d"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        organic = data.get("organic", []) or []
        out = []
        for it in organic[:5]:
            out.append({
                "title": it.get("title"),
                "snippet": it.get("snippet"),
                "link": it.get("link")
            })
        return {"ok": True, "items": out}
    except Exception as e:
        return {"ok": False, "error": f"Serper search 失敗：{e}"}

def today_str_taipei() -> str:
    tz_tw = tz.gettz("Asia/Taipei")
    return datetime.now(tz_tw).strftime("%Y-%m-%d")

def get_horoscope_source(zodiac: str) -> dict:
    if not zodiac:
        return {"ok": False, "error": "未設定星座"}
    d = today_str_taipei()
    q = f"{d} {zodiac} 今日運勢"
    return serper_search(q)
