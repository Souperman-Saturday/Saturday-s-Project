import re
from datetime import datetime, timedelta
from dateutil import tz

TZ = tz.gettz("Asia/Taipei")

def is_group_source(source_type: str) -> bool:
    return source_type in ("group", "room")

def norm_time_hhmm(s: str) -> str | None:
    m = re.search(r"(\d{1,2})[:：](\d{2})", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None

def parse_reminder(text: str) -> tuple[str, str] | None:
    """
    支援：
      - 提醒我 10分鐘後 喝水
      - 提醒我 1分鐘後 喝水
      - 提醒我 2026-02-14 19:30 去接小孩
    回傳 (due_iso, task_text)
    """
    t = text.strip()

    # N分鐘後
    m = re.search(r"提醒我\s*(\d+)\s*分鐘後\s*(.+)$", t)
    if m:
        n = int(m.group(1))
        task_text = m.group(2).strip()
        due = datetime.now(TZ) + timedelta(minutes=n)
        return (due.isoformat(), task_text)

    # YYYY-MM-DD HH:MM
    m = re.search(r"提醒我\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2})[:：](\d{2})\s*(.+)$", t)
    if m:
        y, mo, d, hh, mm = map(int, [m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)])
        task_text = m.group(6).strip()
        due = datetime(y, mo, d, hh, mm, tzinfo=TZ)
        return (due.isoformat(), task_text)

    return None

def extract_command(text: str) -> tuple[str, str]:
    """
    回傳 (cmd, args)
    cmd: lowercase
    """
    t = text.strip()
    # 快捷
    if t.lower() in ("ping",):
        return ("ping", "")
    if t in ("狀態",):
        return ("status", "")
    if t in ("我的設定",):
        return ("my_settings", "")
    if t.startswith("設定城市"):
        return ("set_city", t.replace("設定城市", "", 1).strip())
    if t.startswith("設定星座"):
        return ("set_zodiac", t.replace("設定星座", "", 1).strip())
    if t.startswith("設定晨報時間"):
        return ("set_morning_time", t.replace("設定晨報時間", "", 1).strip())
    if t in ("開啟晨報",):
        return ("morning_on", "")
    if t in ("關閉晨報",):
        return ("morning_off", "")
    if t in ("發送晨報",):
        return ("send_morning", "")

    if t.startswith("設定新聞數量"):
        return ("set_news_count", t.replace("設定新聞數量", "", 1).strip())

    if t.startswith("設定家人"):
        # 設定家人 Uxxx=夫人
        return ("set_contact", t.replace("設定家人", "", 1).strip())
    if t.startswith("刪除家人"):
        return ("delete_contact", t.replace("刪除家人", "", 1).strip())
    if t in ("家人清單",):
        return ("list_contacts", "")

    if t.startswith("傳訊給"):
        return ("relay", t.replace("傳訊給", "", 1).strip())

    if t.startswith("提醒我"):
        return ("remind", t)

    if t in ("我的提醒",):
        return ("list_tasks", "")

    if t.startswith("取消提醒"):
        return ("cancel_task", t.replace("取消提醒", "", 1).strip())

    if t.startswith("記住"):
        return ("remember", t)

    if t in ("查看記憶",):
        return ("list_memories", "")
    if t in ("清除記憶",):
        return ("clear_memories", "")

    return ("chat", t)

def parse_contact_set(arg: str) -> tuple[str, str] | None:
    # Uxxx=夫人
    m = re.search(r"(U[0-9a-fA-F]{10,})\s*=\s*(\S+)", arg)
    if not m:
        return None
    return (m.group(1), m.group(2))

def parse_relay(arg: str) -> tuple[str, str] | None:
    # 夫人 我晚點回家
    parts = arg.strip().split(None, 1)
    if len(parts) < 2:
        return None
    return (parts[0], parts[1].strip())

def parse_remember(text: str) -> tuple[str, str] | None:
    # 記住(共享) xxx
    m = re.search(r"^記住(\((共享|私密)\))?\s*(.+)$", text.strip())
    if not m:
        return None
    scope = "private"
    if m.group(2) == "共享":
        scope = "shared"
    if m.group(2) == "私密":
        scope = "private"
    content = (m.group(3) or "").strip()
    if not content:
        return None
    return (scope, content)
