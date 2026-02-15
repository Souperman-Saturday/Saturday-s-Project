import os
import requests
from datetime import datetime

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def _serper_search(q: str, recency: str = "d", gl: str = "tw", hl: str = "zh-tw", num: int = 5) -> dict:
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": q, "gl": gl, "hl": hl, "num": num}
    if recency:
        payload["tbs"] = f"qdr:{recency}"  # d=day, w=week
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def get_horoscope_today(zodiac: str) -> str:
    """
    用 Serper 搜尋近24h 的星座內容，再做精簡中文整理。
    """
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not set")

    q = f"{zodiac} 今日運勢"
    data = _serper_search(q, recency="d", num=5)

    snippets = []
    for item in (data.get("organic") or [])[:5]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        link = item.get("link", "")
        if snippet:
            snippets.append(f"{title}：{snippet}")

    if not snippets:
        return f"【{zodiac} 今日運勢】\n我目前找不到可靠的近24小時內容。"

    # 先不強依賴 Gemini，避免額度/鍵問題
    # 直接做 rule-based 精簡
    merged = "；".join(snippets)
    merged = merged.replace("\n", " ")
    if len(merged) > 380:
        merged = merged[:380] + "…"

    return f"【{zodiac} 今日運勢（近24h 摘要）】\n{merged}"
