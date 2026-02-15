import re
from dataclasses import dataclass
from typing import Optional
from tools_time import parse_datetime_tz, parse_minutes_after

@dataclass
class Intent:
    kind: str

    # profile
    city: Optional[str] = None
    zodiac: Optional[str] = None
    morning_time: Optional[str] = None

    # alias
    alias: Optional[str] = None
    target_user_id: Optional[str] = None

    # send message
    message: Optional[str] = None

    # reminders
    run_at: Optional[object] = None  # datetime
    # memory
    content: Optional[str] = None
    query: Optional[str] = None
    visibility: str = "private"

def parse_user_intent(text: str) -> Intent:
    t = text.strip()

    if t in ("指令", "help", "Help", "HELP", "幫助"):
        return Intent(kind="help")

    if t in ("隱私", "隱私設定", "私密"):
        return Intent(kind="privacy_intro")

    # 設定城市/星座/晨報
    m = re.match(r"^(設定城市|城市設定)\s+(.+)$", t)
    if m:
        return Intent(kind="set_profile", city=m.group(2).strip())

    m = re.match(r"^(設定星座|星座設定)\s+(.+)$", t)
    if m:
        return Intent(kind="set_profile", zodiac=m.group(2).strip())

    m = re.match(r"^(設定晨報時間|晨報時間)\s+(\d{2}:\d{2})$", t)
    if m:
        return Intent(kind="set_profile", morning_time=m.group(2))

    # 設定別名 夫人 Uxxxx
    m = re.match(r"^設定別名\s+(\S+)\s+(U[a-fA-F0-9]{32}|U\w+)$", t)
    if m:
        return Intent(kind="set_alias", alias=m.group(1), target_user_id=m.group(2))

    # 傳訊給夫人 ... / 傳話給夫人 ...
    m = re.match(r"^(傳訊給|傳話給)\s+(\S+)\s+(.+)$", t)
    if m:
        return Intent(kind="send_to_alias", alias=m.group(2), message=m.group(3).strip())

    # 晨報（立即）
    if t in ("發送晨報", "給我晨報", "晨報"):
        return Intent(kind="morning_now")

    # 天氣：今天/下週/某地
    if "天氣" in t:
        # 例：台中天氣、下週台北天氣
        city = None
        m = re.match(r"^(.+?)\s*天氣", t)
        if m:
            maybe = m.group(1).strip()
            if maybe and maybe not in ("今天", "明天", "下週", "下周"):
                city = maybe
        return Intent(kind="weather", city=city)

    # 星座運勢
    if "運勢" in t or "星座" in t:
        z = None
        m = re.match(r"^(.+?)\s*(今日)?運勢", t)
        if m:
            maybe = m.group(1).strip()
            if maybe and maybe not in ("今日", "今天"):
                z = maybe
        return Intent(kind="horoscope", zodiac=z)

    # 新聞
    if "新聞" in t or "要聞" in t or "全球新聞" in t:
        return Intent(kind="news")

    # 記住（私密/共享）
    m = re.match(r"^記住(共享|私密)?\s+(.+)$", t)
    if m:
        vis = "private"
        if m.group(1) == "共享":
            vis = "shared"
        return Intent(kind="remember", content=m.group(2).strip(), visibility=vis)

    # 想起 / 回想
    m = re.match(r"^(我記得什麼|想起|回想)\s+(.+)$", t)
    if m:
        return Intent(kind="recall", query=m.group(2).strip())

    # 提醒：N分鐘後 / 指定日期
    # 提醒我 10分鐘後 喝水
    if t.startswith("提醒我"):
        # 先試 N分鐘後
        dt = parse_minutes_after(t)
        msg = extract_reminder_message(t)
        if dt:
            return Intent(kind="create_reminder", run_at=dt, message=msg)
        # 再試 YYYY-MM-DD HH:MM
        dt = parse_datetime_tz(t)
        if dt:
            return Intent(kind="create_reminder", run_at=dt, message=msg)
        return Intent(kind="create_reminder", run_at=None, message=msg)

    return Intent(kind="fallback")

def extract_reminder_message(t: str) -> str:
    # 移除「提醒我」「10分鐘後」「YYYY-MM-DD HH:MM」
    msg = t
    msg = re.sub(r"^提醒我\s*", "", msg)
    msg = re.sub(r"\d+\s*分鐘後\s*", "", msg)
    msg = re.sub(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*", "", msg)
    return msg.strip() or "（未填內容）"
