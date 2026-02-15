import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

def init_scheduler(tz: str, push_func, due_tasks_func, due_morning_func):
    """
    在 Render web service 同一個 process 裡跑 scheduler
    - 每 60 秒掃提醒
    - 每 60 秒檢查晨報是否到點
    """
    scheduler = BackgroundScheduler(timezone=tz)

    scheduler.add_job(
        func=due_tasks_func,
        trigger=IntervalTrigger(seconds=60),
        id="poll_due_tasks",
        replace_existing=True,
        max_instances=1
    )

    scheduler.add_job(
        func=due_morning_func,
        trigger=IntervalTrigger(seconds=60),
        id="poll_due_morning",
        replace_existing=True,
        max_instances=1
    )

    scheduler.start()
    print("[SCHED] started")
    return scheduler
