from datetime import datetime
from tools_news import serper_web_search_recent

def horoscope_today(serper_key: str, zodiac: str, now_dt: datetime) -> str:
    """
    運勢用搜尋整合（不靠神秘星座 API）
    原理：抓近 7 天內的結果，整理成一段好讀的管家口吻。
    """
    q = f"{zodiac} 今日運勢"
    results = serper_web_search_recent(
        serper_key, q,
        now_dt=now_dt,
        max_items=5,
        max_age_days=7,
        hl="zh-tw",
        gl="tw"
    )
    if not results:
        return "（我暫時抓不到可靠的今日運勢來源，你要不要我換個關鍵字再試？例如：星座運勢 今日）"

    # 用 snippet 組出簡短運勢（避免爬網頁、避免超慢）
    snippets = []
    for r in results[:3]:
        s = (r.get("snippet") or "").strip()
        if s:
            snippets.append(s)

    if not snippets:
        return "（我有找到來源，但摘要內容不足；你要不要我改成：直接給你來源連結？）"

    # 管家式濃縮（100~160字）
    text = " ".join(snippets)
    text = text.replace("\n", " ").strip()
    if len(text) > 180:
        text = text[:180].rstrip("，。、； ") + "…"

    return f"{zodiac}｜今日重點：{text}"
