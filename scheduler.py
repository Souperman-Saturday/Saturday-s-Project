import os
from datetime import datetime
from zoneinfo import ZoneInfo

from memory import (
    fetch_due_tasks, mark_task_sent,
    ensure_profile, morning_already_sent, mark_morning_sent
)
from tools import now_tz
from app import push_text, build_morning_report  # 由 app.py 提供

TZ = os.getenv("TZ", "Asia/Taipei")

def run_cron_once():
    # 1) 送到期提醒
    due = fetch_due_tasks(limit=50)
    for task in due:
        owner = task["owner_user_id"]
        payload = task["payload"]
        push_text(owner, f"⏰ 提醒：{payload}")
        mark_task_sent(task["id"])

    # 2) 晨報：到點送一次（每個 user 每天一次）
    now = now_tz()
    today = now.date().isoformat()

    # 目前你是「單一管家（你＋夫人）」：用環境變數列出要送的人
    targets = [os.getenv("MY_ID"), os.getenv("WIFE_ID")]
    targets = [t for t in targets if t]

    for uid in targets:
        prof = ensure_profile(uid)
        hhmm = prof.get("morning_time", "07:00")
        try:
            hh, mm = hhmm.split(":")
            target_time = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            continue

        # 允許 0~3 分鐘內觸發（避免 cron 漏掉）
        if now >= target_time and (now - target_time).total_seconds() <= 180:
            if not morning_already_sent(uid, today):
                text = build_morning_report(uid, prof)
                push_text(uid, text)
                mark_morning_sent(uid, today)
