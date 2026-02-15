import requests


def serper_news_top(api_key: str, q: str, gl: str = "tw", hl: str = "zh-tw", num: int = 8) -> list[dict]:
    """
    Serper 提供 News 查詢能力（你之前用的是一般 search 摘要，這裡改用 news 類型）。 :contentReference[oaicite:0]{index=0}
    """
    if not api_key:
        return []

    url = "https://google.serper.dev/news"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": q,
        "gl": gl,
        "hl": hl,
        "num": max(1, min(int(num), 8)),
        "timeRange": "d",  # 近一天（Serper 常見參數）
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        items = data.get("news") or []
        out = []
        for it in items:
            out.append(
                {
                    "title": it.get("title"),
                    "source": it.get("source"),
                    "date": it.get("date"),
                    "link": it.get("link"),
                }
            )
        return out
    except Exception:
        return []
