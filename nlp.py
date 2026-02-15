import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ---------- Intent ----------
@dataclass
class Intent:
    name: str
    args: dict


# ---------- Chinese numeral ----------
_CN_NUM = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10
}


def cn_to_int(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    # 10, 11, 12, 20, 21...
    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十")
        left = parts[0]
        right = parts[1] if len(parts) > 1 else ""
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_NUM.get(s, 0)


def _now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


# ---------- time parse ----------
def parse_due_at(text: str, tz_name: str) -> str | None:
    """
    支援：
    - 兩分鐘後/1分鐘後/半小時後/2小時後
    - 2026-02-14 18:30
    - 2/14 18:30（用今年）
    - 明天 18:30 / 今天 18:30 / 今晚8點
    """
    t = text.strip()

    now = _now(tz_name)

    # 1) duration: X(秒/分鐘/小時/天)後
    m = re.search(r"(?P<n>(\d+|[零〇一二兩三四五六七八九十]+|半))\s*(?P<u>秒|分鐘|分|小時|時|天)\s*後", t)
    if m:
        n_raw = m.group("n")
        unit = m.group("u")
        if n_raw == "半":
            n = 0.5
        else:
            n = float(cn_to_int(n_raw)) if not n_raw.isdigit() else float(int(n_raw))

        delta = timedelta()
        if unit in ("秒",):
            delta = timedelta(seconds=int(n))
        elif unit in ("分鐘", "分"):
            delta = timedelta(minutes=int(n))
        elif unit in ("小時", "時"):
            delta = timedelta(hours=float(n))
        elif unit in ("天",):
            delta = timedelta(days=float(n))
        due = now + delta
        return due.strftime("%Y-%m-%d %H:%M")

    # 2) absolute: YYYY-MM-DD HH:MM
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", t)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        due = datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(tz_name))
        return due.strftime("%Y-%m-%d %H:%M")

    # 3) absolute: M/D HH:MM
    m = re.search(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", t)
    if m:
        mo, d, hh, mm = map(int, m.groups())
        due = datetime(now.year, mo, d, hh, mm, tzinfo=ZoneInfo(tz_name))
        return due.strftime("%Y-%m-%d %H:%M")

    # 4) relative day + HH:MM
    m = re.search(r"(今天|今日|明天|後天)\s*(\d{1,2}):(\d{2})", t)
    if m:
        day_word, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        base = now.date()
        if day_word in ("明天",):
            base = (now + timedelta(days=1)).date()
        elif day_word in ("後天",):
            base = (now + timedelta(days=2)).date()
        due = datetime(base.year, base.month, base.day, hh, mm, tzinfo=ZoneInfo(tz_name))
        return due.strftime("%Y-%m-%d %H:%M")

    # 5) tonight 8點 / 明天8點
    m = re.search(r"(今天|明天|後天|今晚)\s*(\d{1,2})\s*點", t)
    if m:
        day_word, hh = m.group(1), int(m.group(2))
        base = now.date()
        if day_word in ("明天",):
            base = (now + timedelta(days=1)).date()
        elif day_word in ("後天",):
            base = (now + timedelta(days=2)).date()
        due = datetime(base.year, base.month, base.day, hh, 0, tzinfo=ZoneInfo(tz_name))
        return due.strftime("%Y-%m-%d %H:%M")

    return None


def detect_intent(text: str, tz_name: str) -> Intent:
    t = text.strip()

    # help
    if t.lower() in ("/help", "help", "指令", "指令表"):
        return Intent("HELP", {})

    # my id
    if t in ("我的ID", "查詢我的ID", "我的 id", "my id", "myid"):
        return Intent("MY_ID", {})

    # bind/unbind/list
    m = re.match(r"^綁定\s+(\S+)\s+(U[a-zA-Z0-9]{10,})$", t)
    if m:
        return Intent("BIND", {"name": m.group(1), "user_id": m.group(2)})

    m = re.match(r"^解除綁定\s+(\S+)$", t)
    if m:
        return Intent("UNBIND", {"name": m.group(1)})

    if t in ("我的綁定", "查看綁定", "綁定清單"):
        return Intent("LIST_BINDINGS", {})

    # nickname
    m = re.match(r"^我稱呼\s+(\S+)\s+為\s+(\S+)$", t)
    if m:
        return Intent("SET_NICKNAME", {"target_name": m.group(1), "nickname": m.group(2)})

    # settings
    m = re.match(r"^設定城市\s+(.+)$", t)
    if m:
        return Intent("SET_CITY", {"city": m.group(1).strip()})

    m = re.match(r"^設定星座\s+(.+)$", t)
    if m:
        return Intent("SET_ZODIAC", {"sign": m.group(1).strip()})

    m = re.match(r"^設定晨報時間\s+(\d{2}:\d{2})$", t)
    if m:
        return Intent("SET_MORNING_TIME", {"time": m.group(1)})

    if t in ("開啟晨報", "晨報開啟"):
        return Intent("MORNING_ON", {})

    if t in ("關閉晨報", "晨報關閉"):
        return Intent("MORNING_OFF", {})

    # send message
    m = re.match(r"^傳訊給\s*(\S+)\s+(.+)$", t)
    if m:
        return Intent("SEND_MESSAGE", {"target": m.group(1), "message": m.group(2).strip()})

    # reminders list/cancel
    if t in ("我的提醒", "提醒清單", "看提醒"):
        return Intent("LIST_REMINDERS", {})

    m = re.match(r"^取消提醒\s+([0-9a-fA-F-]{6,})$", t)
    if m:
        return Intent("CANCEL_REMINDER", {"id": m.group(1)})

    # remember explicit
    m = re.match(r"^記住\s*\(共享\)\s*(.+)$", t)
    if m:
        return Intent("REMEMBER", {"scope": "shared", "content": m.group(1).strip()})

    m = re.match(r"^記住\s+(.+)$", t)
    if m:
        return Intent("REMEMBER", {"scope": "private", "content": m.group(1).strip()})

    # share confirm
    if t in ("共享", "家庭共享"):
        return Intent("CONFIRM_SHARE", {"decision": "shared"})
    if t in ("私密", "不共享"):
        return Intent("CONFIRM_SHARE", {"decision": "private"})

    # list memory
    if t in ("我記得什麼", "我記得甚麼", "記得我甚麼", "我記得什麼事情"):
        return Intent("LIST_MEMORY", {})

    # date
    if t in ("今天日期", "今天幾號", "今天星期幾", "現在日期", "現在時間", "今天日期時間"):
        return Intent("DATE_NOW", {})

    # weather
    if "天氣" in t:
        # 例如：今天天氣 / 明天台中天氣 / 3天台中天氣 / 未來三天天氣 / 台中未來三天天氣
        m = re.search(r"(\d+|[零〇一二兩三四五六七八九十]+)\s*天", t)
        days = None
        if m:
            days = cn_to_int(m.group(1))
        if "明天" in t:
            days = 2  # 明天等於 forecast 至少 2 天（含今天）
        # city
        city = None
        m2 = re.search(r"(台北|臺北|台中|臺中|台南|臺南|高雄|桃園|新竹|基隆|嘉義|彰化|南投|雲林|屏東|宜蘭|花蓮|台東|臺東|澎湖|金門|馬祖)\S*", t)
        if m2:
            city = m2.group(0).strip()

        if days and days >= 2:
            return Intent("WEATHER_FORECAST", {"city": city, "days": min(max(days, 2), 10)})
        # 沒寫幾天就當現在
        return Intent("WEATHER_NOW", {"city": city})

    # horoscope
    if "運勢" in t:
        return Intent("HOROSCOPE_TODAY", {})

    # news
    if t.startswith("看新聞"):
        m = re.search(r"(\d+)", t)
        if m:
            return Intent("NEWS_DETAIL", {"index": int(m.group(1))})
        return Intent("NEWS_DETAIL", {"index": 1})

    if "新聞" in t or "要聞" in t:
        # 可指定天數：7天內新聞 / 3天新聞
        m = re.search(r"(\d+|[零〇一二兩三四五六七八九十]+)\s*天", t)
        days = 7
        if m:
            days = cn_to_int(m.group(1))
        return Intent("NEWS", {"query": "全球重大新聞", "days": min(max(days, 1), 30), "limit": 8})

    # morning brief now
    if t in ("晨報", "今日晨報", "給我晨報"):
        return Intent("MORNING_NOW", {})

    # reminders (natural)
    # 兩分鐘後提醒 喝水 / 1分鐘後提醒夫人 買晚餐 / 明天18:30提醒 帶尿布
    if "提醒" in t:
        due_at = parse_due_at(t, tz_name=tz_name)
        if due_at:
            # 提取對象（提醒夫人 / 提醒老公）
            target = None
            m = re.search(r"提醒\s*(給|給我|我)?\s*(\S+)?\s*(.+)$", t)
            title = t
            if m:
                maybe_target = m.group(2)
                rest = m.group(3).strip() if m.group(3) else ""
                # 判斷 m.group(2) 是否其實是內容的一部分
                if maybe_target and len(maybe_target) <= 6 and "後" not in maybe_target and ":" not in maybe_target:
                    # 可能是人名/稱呼
                    target = maybe_target
                    title = rest
                else:
                    title = rest or t
            title = re.sub(r".*提醒\s*(給|給我|我)?\s*\S*\s*", "", t).strip()
            title = title if title else "提醒事項"
            return Intent("REMIND", {"due_at": due_at, "title": title, "target": target})

    return Intent("UNKNOWN", {})
