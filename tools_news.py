import re
import requests
from datetime import datetime, timedelta
from dateutil import parser as dtparser

def _serper_post(api_key: str, endpoint: str, payload: dict, timeout: int = 10) -> dict:
    url = f"https://google.serper.dev/{endpoint}"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _parse_age_to_dt(age_str: str, now_dt: datetime):
    """
    Serper date 可能是：
    - "2 hours ago" / "1 day ago"
    - 或 ISO
    - 或 "2025-06-07"
    """
    s = (age_str or "").strip()
    if not s:
        return None

    m = re.search(r"(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks)\s*ago", s, re.I)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "minute" in unit:
            return now_dt - timedelta(minutes=n)
        if "hour" in unit:
            return now_dt - timedelta(hours=n)
        if "day" in unit:
            return now_dt - timedelta(days=n)
        if "week" in unit:
            return now_dt - timedelta(weeks=n)
    # 其他就試 parse
    try:
        dt = dtparser.parse(s)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=now_dt.tzinfo)
        return dt
    except Exception:
        return None

def serper_news_recent(api_key: str, query: str, now_dt: datetime, max_items: int = 8,
                       max_age_days: int = 7, hl: str="zh-tw", gl: str="tw"):
    """
    保證不回傳超過 max_age_days 的新聞。
    如果 date 無法判斷，就當作不合格（避免 2016 那種鬼東西）。
    """
    payload = {
        "q": query,
        "hl": hl,
        "gl": gl,
        "num": 10
    }
    data = _serper_post(api_key, "news", payload)
    raw = data.get("news") or []

    items = []
    for it in raw:
        title = (it.get("title") or "").strip()
        link = (it.get("link") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        source = (it.get("source") or "").strip()
        dstr = it.get("date") or it.get("publishedAt") or ""

        dt = _parse_age_to_dt(dstr, now_dt)
        if not dt:
            continue

        age_days = (now_dt - dt).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0

        if age_days <= max_age_days + 0.01:
            age = "剛剛" if age_days < 0.05 else f"{int(age_days)}天前" if age_days >= 1 else f"{int((now_dt-dt).total_seconds()//3600)}小時前"
            items.append({
                "title": title,
                "link": link,
                "snippet": snippet,
                "source": source,
                "dt": dt.isoformat(),
                "age": age
            })

    # 不夠就擴到 7 天（你晨報需要）
    if len(items) < max_items and max_age_days < 7:
        return serper_news_recent(api_key, query, now_dt, max_items=max_items, max_age_days=7, hl=hl, gl=gl)

    return items[:max_items]

def serper_web_search_recent(api_key: str, query: str, now_dt: datetime,
                             max_items: int = 5, max_age_days: int = 30,
                             hl: str="zh-tw", gl: str="tw"):
    """
    一般搜尋（給運勢/節日用），盡量過濾舊資訊。
    search 結果通常沒日期，所以我們用「不強制日期」策略：
    - 優先回傳前幾筆，不做嚴格過濾
    - 但避免回傳空
    """
    payload = {
        "q": query,
        "hl": hl,
        "gl": gl,
        "num": max_items
    }
    data = _serper_post(api_key, "search", payload)
    organic = data.get("organic") or []
    out = []
    for it in organic[:max_items]:
        out.append({
            "title": (it.get("title") or "").strip(),
            "link": (it.get("link") or "").strip(),
            "snippet": (it.get("snippet") or "").strip()
        })
    return out
