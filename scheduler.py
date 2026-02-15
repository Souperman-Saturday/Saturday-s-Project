from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler


class TaskScheduler:
    """
    Render free 也能跑（你用 UptimeRobot 讓 web service 不睡就行）
    任務落地 Supabase，重啟可恢復。
    """

    def __init__(self, store, tz: str, push_func):
        self.store = store
        self.tz = tz
        self.tzinfo = ZoneInfo(tz)
        self.push_func = push_func
        self.scheduler = BackgroundScheduler(timezone=self.tzinfo)
        self.scheduler.start()

    def load_future_tasks(self):
        now_iso = datetime.now(self.tzinfo).isoformat()
        tasks = self.store.list_future_tasks_global(now_iso)
        for t in tasks:
            self._schedule_job(t["task_id"], t["user_id"], t["run_at"], t["content"])

    def schedule_in_minutes(self, user_id: str, minutes: int, content: str) -> str:
        run_at = datetime.now(self.tzinfo) + timedelta(minutes=minutes)
        task_id = self.store.create_task(user_id, run_at.isoformat(), content)
        self._schedule_job(task_id, user_id, run_at.isoformat(), content)
        return task_id

    def schedule_at(self, user_id: str, dt_str: str, content: str) -> str:
        # dt_str: "YYYY-MM-DD HH:MM"
        run_at = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=self.tzinfo)
        task_id = self.store.create_task(user_id, run_at.isoformat(), content)
        self._schedule_job(task_id, user_id, run_at.isoformat(), content)
        return task_id

    def cancel(self, task_id: str) -> bool:
        # APScheduler 的 job id 用 task_id
        try:
            self.scheduler.remove_job(task_id)
        except Exception:
            pass
        return self.store.cancel_task(task_id)

    def _schedule_job(self, task_id: str, user_id: str, run_at_iso: str, content: str):
        run_at = datetime.fromisoformat(run_at_iso)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=self.tzinfo)

        def _fire():
            try:
                self.push_func(user_id, f"⏰ 提醒：{content}")
            finally:
                self.store.mark_task_done(task_id)

        # replace_existing=True：重啟載入時不會重複排
        self.scheduler.add_job(
            _fire,
            trigger="date",
            run_date=run_at,
            id=task_id,
            replace_existing=True,
            misfire_grace_time=60 * 10,
        )
