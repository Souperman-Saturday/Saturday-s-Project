import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dtparser

TZ = ZoneInfo("Asia/Taipei")

RE_TIME_HHMM = re.compile(r"(\d{1,2}):(\d{2})")
RE_REMIND_MIN = re.compile(r"(\d+)\s*(分鐘|分)\s*後")
RE_REMIND_HR = re.compile(r"(\d+)\s*(小時)\s*後")
RE_REMIND_DAY = re.compile(r"(\d+)\s*(天)\s*後")

def parse_morning_time(text: str):
    m = RE_TIME_HHMM.search(text)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"

def parse_reminder(text: str, now: datetime):
    """
    支援：
    - 提醒我 10分鐘後 喝水
    - 1分鐘後提醒我 喝水
    - 提醒我 2小時後：回電
    - 提醒我 2/15 18:00 去拜拜
    """
    t = text.strip()

    if "提醒" not in t:
        return None

    # 先抓相對時間
    m = RE_REMIND_MIN.search(t)
    if m:
        mins = int(m.group(1))
        due = now + timedelta(minutes=mins)
        payload = re.sub(RE_REMIND_MIN, "", t)
        payload = payload.replace("提醒我", "").replace("提醒", "").replace("：", " ").strip()
        return due, payload

    m = RE_REMIND_HR.search(t)
    if m:
        hrs = int(m.group(1))
        due = now + timedelta(hours=hrs)
        payload = re.sub(RE_REMIND_HR, "", t)
        payload = payload.replace("提醒我", "").replace("提醒", "").replace("：", " ").strip()
        return due, payload

    m = RE_REMIND_DAY.search(t)
    if m:
        days = int(m.group(1))
        due = now + timedelta(days=days)
        payload = re.sub(RE_REMIND_DAY, "", t)
        payload = payload.replace("提醒我", "").replace("提醒", "").replace("：", " ").strip()
        return due, payload

    # 再抓「明確日期時間」
    # e.g. 提醒我 2/15 18:00 去拜拜
    try:
        # 把 "提醒我" 等拿掉再 parse
        tmp = t.replace("提醒我", "").replace("提醒", "").replace("：", " ").strip()
        # 嘗試找到日期時間片段：取前 16~20 字較常成功
        candidate = tmp[:22]
        due = dtparser.parse(candidate, fuzzy=True, default=now)
        if due.tzinfo is None:
            due = due.replace(tzinfo=TZ)
        payload = tmp
        return due, payload
    except Exception:
        return None

def wants_weather(text: str) -> bool:
    keys = ["天氣", "氣溫", "下雨", "降雨", "預報", "溫度", "會不會冷"]
    return any(k in text for k in keys)

def wants_horoscope(text: str) -> bool:
    keys = ["運勢", "星座", "獅子座", "今日運勢"]
    return any(k in text for k in keys)

def wants_news(text: str) -> bool:
    keys = ["新聞", "要聞", "全球", "重大事件", "重點整理"]
    return any(k in text for k in keys)

def is_set_morning_time(text: str) -> bool:
    return ("晨報" in text and ("時間" in text or "設定" in text)) or text.strip().startswith("設定晨報時間")

def is_send_morning_now(text: str) -> bool:
    return ("發送晨報" in text) or ("立刻給我晨報" in text) or ("給我晨報" in text and "設定" not in text)

def is_set_city(text: str) -> bool:
    return ("設定" in text and ("城市" in text or "居住地" in text or "住在" in text)) or text.strip().startswith("設定城市")

def extract_city(text: str):
    # 設定城市 台中市 / 我住在台中
    t = text.strip()
    t = t.replace("設定城市", "").replace("設定居住地", "").replace("我住在", "").replace("住在", "")
    t = t.replace("設定", "").replace("城市", "").replace("居住地", "")
    city = t.strip(" ：:，,。 ")
    return city if city else None

def is_set_zodiac(text: str) -> bool:
    return ("設定" in text and "星座" in text) or text.strip().startswith("設定星座")

def extract_zodiac(text: str):
    t = text.replace("設定星座", "").replace("我的星座是", "").replace("星座", "").replace("設定", "")
    z = t.strip(" ：:，,。 ")
    return z if z else None

def parse_push_to_family(text: str):
    # 傳訊給夫人：xxx / 傳訊給先生 xxx
    m = re.match(r"^\s*傳訊給\s*(夫人|先生)\s*[：: ]\s*(.+)$", text.strip())
    if not m:
        return None
    who = m.group(1)
    msg = m.group(2).strip()
    return who, msg
