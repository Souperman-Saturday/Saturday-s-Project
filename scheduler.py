import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from dateutil import tz

from memory import DB

TZNAME = os.getenv("TZ", "Asia/Taipei")
TZ = tz.gettz(TZNAME)

scheduler = BackgroundScheduler(timezone=TZ)
_db: DB | None = None
_push_fn = None  # function(user_id, text)

def init_scheduler(db: DB, push_fn):
    global _db, _push_fn
    _db = db
    _push_fn = push_fn

    if not scheduler.running:
        scheduler.start()

    # 每 20 秒掃 pending tasks
    scheduler.add_job(send_due_tasks, "interval", seconds=20, id="scan_tasks", replace_existing=True)
    # 每分鐘檢查晨報（依 user profile 動態）
    scheduler.add_job(scan_morning_reports, "interval", seconds=30, id="scan_morning", replace_existing=True)

def send_due_tasks():
    if not _db or not _push_fn:
        return
    now_iso = datetime.now(TZ).isoformat()
    due = _db.due_tasks(now_iso)
    for t in due:
        try:
            _push_fn(t["subject_id"], f"⏰ 提醒：{t['text']}")
            _db.mark_task_sent(t["id"])
        except Exception:
            # 不要讓 scheduler 斷
            pass

def scan_morning_reports():
    if not _db or not _push_fn:
        return

    # 找所有晨報開啟的 profiles
    # supabase python 取全部 profiles：保守 limit 200
    try:
        sb = _db.sb
        r = sb.table("saturday_profiles").select("user_id,morning_enabled,morning_time,city,zodiac,news_count").eq("morning_enabled", True).limit(200).execute()
        profiles = r.data or []
    except Exception:
        return

    now = datetime.now(TZ)
    hhmm = now.strftime("%H:%M")
    # 只在「當分鐘」發一次：用 tasks 表做鎖（更穩），但先用 processed marker in memories table? 這裡用簡易記憶：每天每人一則 marker
    # 使用 saturday_memories 寫入一條 shared marker: "MORNING_SENT:YYYY-MM-DD"
    today = now.strftime("%Y-%m-%d")

    for p in profiles:
        try:
            if (p.get("morning_time") or "07:00") != hhmm:
                continue
            uid = p["user_id"]
            # check marker
            existing = _db.list_memories(owner_id=uid, subject_id=uid, scopes=["private","shared"], limit=10)
            marker = f"MORNING_SENT:{today}"
            if any(marker in (m.get("content") or "") for m in existing):
                continue
            # 讓 app 內的 /morning endpoint 產生內容較乾淨：這裡只 push 一個 signal，由 app 組晨報內容
            _push_fn(uid, "☀️ 先生，到了晨報時間，我正在為您整理今日晨報，稍等一下。")
            # 用「待辦」的方式觸發：寫一個 0 分鐘後的 task，讓 app 的同一套流程生成晨報（更一致）
            _db.add_task(uid, uid, datetime.now(TZ).isoformat(), "__SEND_MORNING__")
            _db.add_memory(uid, uid, "private", marker)
        except Exception:
            pass
