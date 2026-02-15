from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler


def start_scheduler(tz_name: str, supa, push_fn, tools):
    tz = ZoneInfo(tz_name)
    sched = BackgroundScheduler(timezone=str(tz))

    # 每 30 秒掃描到期提醒（補發機制：重啟也不怕漏）
    def tick_reminders():
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        due = supa.fetch_due_reminders(now)
        for r in due:
            try:
                # 收件人視角稱呼建立者
                sender_label = supa.get_sender_label_for_receiver(
                    tenant_id=r["tenant_id"],
                    sender_user_id=r["created_by"],
                    receiver_user_id=r["to_user_id"]
                )
                push_fn(r["to_user_id"], f"⏰ 提醒（來自 {sender_label}）：\n{r['title']}")
                supa.mark_reminder_sent(r["tenant_id"], r["id"])
            except Exception as e:
                # 不要卡死，保留 pending 下次再送
                print(f"[reminder] push failed: {e}")

    # 每分鐘檢查是否有人的晨報時間到了
    def tick_morning():
        now = datetime.now(tz)
        hhmm = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")

        profiles = supa.list_morning_targets(hhmm)
        for p in profiles:
            try:
                city = p.get("home_city") or "（未設定）"
                sign = p.get("zodiac_sign") or "（未設定）"
                morning = tools.build_morning_brief(city=city, zodiac=sign, news_days=7, news_limit=8)
                holiday_line = tools.today_holiday_line()
                text_out = tools.format_morning(morning, holiday_line=holiday_line, date_str=today)

                # cache news for detail command
                if morning.get("news_items"):
                    supa.set_last_news_cache(p["tenant_id"], p["user_id"], morning["news_items"])

                push_fn(p["user_id"], text_out)
            except Exception as e:
                print(f"[morning] failed: {e}")

    # 每天凌晨 02:10 生成「台灣重大節日提醒」（含農曆換算），預設所有租戶都內建
    def tick_holidays():
        now = datetime.now(tz)
        year = now.year
        tenants = supa.list_tenants()

        for tenant_id in tenants:
            try:
                # 確保今年與明年都有（避免年底跨年漏）
                for y in (year, year + 1):
                    holidays = tools.tw_major_holidays(y)
                    # 為 tenant 裡每位成員建立節日提醒（預設 09:00）
                    members = supa.list_member_user_ids(tenant_id)
                    for uid in members:
                        for h in holidays:
                            due_at = f"{h['date']} 09:00"
                            supa.ensure_holiday_reminder(
                                tenant_id=tenant_id,
                                to_user_id=uid,
                                title=f"🎌 重要節日：{h['name']}",
                                due_at=due_at
                            )
            except Exception as e:
                print(f"[holiday] failed tenant={tenant_id}: {e}")

    # 排程
    sched.add_job(tick_reminders, "interval", seconds=30, id="tick_reminders", replace_existing=True)
    sched.add_job(tick_morning, "interval", minutes=1, id="tick_morning", replace_existing=True)
    sched.add_job(tick_holidays, "cron", hour=2, minute=10, id="tick_holidays", replace_existing=True)

    sched.start()
    print("[scheduler] started")
