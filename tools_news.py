import os
import requests

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

def _serper_news_raw(q: str, recency: str = "d", gl: str = "tw", hl: str = "zh-tw", num: int = 10) -> dict:
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": q, "gl": gl, "hl": hl, "num": num}
    if recency:
        payload["tbs"] = f"qdr:{recency}"
    r = requests.post(url, headers=headers, json=payload, timeout=25)
    r.raise_for_status()
    return r.json()

def serper_news(limit: int = 8) -> list[str]:
    """
    ✅ 你現在遇到的 ImportError 就是因為這個 function 名稱必須存在：
    from tools_news import serper_news
    """
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not set")

    # 全球重大事件（繁中）
    data = _serper_news_raw(q="全球 重大新聞", recency="d", gl="tw", hl="zh-tw", num=max(limit, 8))

    items = []
    for n in (data.get("news") or [])[:limit]:
        title = (n.get("title") or "").strip()
        source = (n.get("source") or "").strip()
        date = (n.get("date") or "").strip()
        link = (n.get("link") or "").strip()

        # 避免太長造成 LINE 文字截斷：控制單則長度
        line = f"{title}（{source}｜{date}）"
        if len(line) > 140:
            line = line[:140] + "…"
        # 不直接貼長網址（避免爆字數）
        items.append(line)

    if not items:
        return ["（近24h 內我目前找不到足夠的全球重大新聞）"]

    return items
