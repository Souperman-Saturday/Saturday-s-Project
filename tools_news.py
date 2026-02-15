import os, requests

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

def serper_news(query: str, num: int = 5, hl: str = "zh-tw", gl: str = "tw") -> dict:
    if not SERPER_API_KEY:
        return {"ok": False, "error": "SERPER_API_KEY 未設定"}
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {
        "q": query,
        "num": max(3, min(int(num), 8)),
        "hl": hl,
        "gl": gl,
        "tbs": "qdr:d"  # last 24 hours
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("news", []) or []
        out = []
        for it in items:
            out.append({
                "title": it.get("title"),
                "source": it.get("source"),
                "date": it.get("date"),
                "snippet": it.get("snippet"),
                "link": it.get("link")
            })
        return {"ok": True, "items": out}
    except Exception as e:
        return {"ok": False, "error": f"Serper news 失敗：{e}"}
