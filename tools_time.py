import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = os.getenv("TZ", "Asia/Taipei")

def now_tz() -> datetime:
    return datetime.now(ZoneInfo(TZ))

def format_date_zh(dt: datetime) -> str:
    # 簡單中文日期（可擴充）
    return dt.strftime("%Y-%m-%d")

def parse_minutes_after(text: str):
    # 「提醒我 10分鐘後 喝水」
    m = re.search(r"(\d+)\s*分鐘後", text)
    if not m:
        return None
    mins = int(m.group(1))
    return now_tz() + timedelta(minutes=mins)

def parse_datetime_tz(text: str):
    # 「提醒我 2026-02-14 09:30 開會」
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    if not m:
        return None
    dt = datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=ZoneInfo(TZ))
