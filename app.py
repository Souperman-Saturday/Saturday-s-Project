import os
import json
import hmac
import hashlib
import time
from datetime import datetime
from flask import Flask, request, abort

from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    PushMessageRequest
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from memory import (
    supa,
    ensure_profile,
    upsert_profile_city_zodiac,
    get_profile,
    remember_text,
    recall_text,
    set_contact_alias,
    resolve_contact_alias,
    create_task,
    list_tasks_today,
    mark_task_done,
    is_event_processed,
    mark_event_processed,
    should_send_morning,
    mark_morning_sent,
    get_users_due_morning
)
from nlp import parse_user_intent
from scheduler import init_scheduler
from tools_weather import get_weather_now, get_weather_forecast
from tools_news import serper_news
from tools_horoscope import get_horoscope_today
from tools_time import format_date_zh, now_tz, parse_datetime_tz
from prompts import (
    build_morning_report,
    build_help_text,
    build_privacy_intro
)

APP_NAME = os.getenv("APP_NAME", "Saturday")
TZ = os.getenv("TZ", "Asia/Taipei")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    # 讓 Render log 更好讀
    print("[FATAL] LINE env missing. Please set LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET.")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 啟動 scheduler（同一個 web process 裡跑）
scheduler = init_scheduler(
    tz=TZ,
    push_func=lambda user_id, text: push_text(user_id, text),
    due_tasks_func=poll_due_tasks,
    due_morning_func=poll_due_morning,
)

def push_text(user_id: str, text: str) -> None:
    if not user_id:
        return
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text)]
        ))

def reply_text(reply_token: str, text: str) -> None:
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)]
        ))

@app.get("/health")
def health():
    return {"ok": True, "app": APP_NAME, "ts": int(time.time())}

@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        events = parser.parse(body, signature)
    except Exception as e:
        print("[LINE] signature/parse error:", e)
        abort(400)

    data = json.loads(body)

    # webhookEventId 去重（最穩）
    webhook_event_id = data.get("webhookEventId")
    if webhook_event_id:
        try:
            if is_event_processed(webhook_event_id):
                return "OK"
            mark_event_processed(webhook_event_id)
        except Exception as e:
            # 去重失敗也不要整個掛掉
            print("[WARN] event dedupe failed:", e)

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            user_id = event.source.user_id
            text = (event.message.text or "").strip()
            ensure_profile(user_id)

            # 解析 intent
            intent = parse_user_intent(text)

            # 1) HELP / INTRO
            if intent.kind == "help":
                reply_text(event.reply_token, build_help_text())
                continue

            if intent.kind == "privacy_intro":
                reply_text(event.reply_token, build_privacy_intro())
                continue

            # 2) PROFILE SET (city / zodiac / morning_time)
            if intent.kind == "set_profile":
                upsert_profile_city_zodiac(
                    user_id=user_id,
                    city=intent.city,
                    zodiac=intent.zodiac,
                    morning_time=intent.morning_time
                )
                msg = "好的，我已更新您的設定："
                parts = []
                if intent.city:
                    parts.append(f"居住地={intent.city}")
                if intent.zodiac:
                    parts.append(f"星座={intent.zodiac}")
                if intent.morning_time:
                    parts.append(f"晨報時間={intent.morning_time}")
                reply_text(event.reply_token, msg + ("、".join(parts) if parts else "（無變更）"))
                continue

            # 3) CONTACT ALIAS
            if intent.kind == "set_alias":
                set_contact_alias(owner_id=user_id, alias=intent.alias, target_user_id=intent.target_user_id)
                reply_text(event.reply_token, f"好的，我已把「{intent.alias}」綁定完成。之後你說「傳訊給{intent.alias} …」我就會轉達。")
                continue

            # 4) SEND MESSAGE TO ALIAS
            if intent.kind == "send_to_alias":
                target_id = resolve_contact_alias(owner_id=user_id, alias=intent.alias)
                if not target_id:
                    reply_text(event.reply_token, f"我還不知道「{intent.alias}」是誰。請先用：設定別名 {intent.alias} Uxxxxxxxx")
                    continue
                push_text(target_id, f"【轉達】{intent.message}")
                reply_text(event.reply_token, f"已幫你轉達給「{intent.alias}」。")
                continue

            # 5) REMINDERS
            if intent.kind == "create_reminder":
                run_at = intent.run_at
                if not run_at:
                    reply_text(event.reply_token, "我沒抓到提醒時間。你可以用：「提醒我 10分鐘後 喝水」或「提醒我 2026-02-14 09:30 開會」。")
                    continue
                task_id = create_task(owner_id=user_id, run_at=run_at, message=intent.message)
                reply_text(event.reply_token, f"好，我會在 {run_at.strftime('%Y-%m-%d %H:%M')} 提醒你：{intent.message}")
                continue

            # 6) WEATHER
            if intent.kind == "weather":
                profile = get_profile(user_id)
                city = intent.city or (profile.get("city") if profile else None)
                if not city:
                    reply_text(event.reply_token, "我還不知道你要查哪裡天氣。你可以說：「設定城市 台中市」或「台北下週天氣」。")
                    continue
                try:
                    now_info = get_weather_now(city)
                    forecast = get_weather_forecast(city, days=3)
                    txt = f"【{city} 天氣】\n" + now_info + "\n\n" + forecast
                    reply_text(event.reply_token, txt)
                except Exception as e:
                    print("[WEATHER] error:", e)
                    reply_text(event.reply_token, "我目前抓不到天氣資料（WeatherAPI 可能失敗或額度不足）。")
                continue

            # 7) HOROSCOPE
            if intent.kind == "horoscope":
                profile = get_profile(user_id)
                zodiac = intent.zodiac or (profile.get("zodiac") if profile else None)
                if not zodiac:
                    reply_text(event.reply_token, "你要查哪個星座？你可以先說：「設定星座 獅子座」。")
                    continue
                try:
                    h = get_horoscope_today(zodiac=zodiac)
                    reply_text(event.reply_token, h)
                except Exception as e:
                    print("[HORO] error:", e)
                    reply_text(event.reply_token, "我目前抓不到星座運勢（搜尋工具可能失敗或額度不足）。")
                continue

            # 8) NEWS
            if intent.kind == "news":
                try:
                    items = serper_news(limit=8)
                    reply_text(event.reply_token, "【全球新聞重點整理】\n" + "\n\n".join(items))
                except Exception as e:
                    print("[NEWS] error:", e)
                    reply_text(event.reply_token, "我目前抓不到新聞（Serper 可能失敗或額度不足）。")
                continue

            # 9) MORNING NOW
            if intent.kind == "morning_now":
                profile = get_profile(user_id)
                city = profile.get("city") if profile else None
                zodiac = profile.get("zodiac") if profile else None
                try:
                    report = generate_morning_report(user_id=user_id, city=city, zodiac=zodiac)
                    reply_text(event.reply_token, report)
                except Exception as e:
                    print("[MORNING] error:", e)
                    reply_text(event.reply_token, "晨報產生失敗（可能是 API 額度或設定缺失）。")
                continue

            # 10) MEMORY: remember / recall
            if intent.kind == "remember":
                remember_text(owner_id=user_id, content=intent.content, visibility=intent.visibility)
                reply_text(event.reply_token, "好，我記住了。")
                continue

            if intent.kind == "recall":
                hits = recall_text(owner_id=user_id, query=intent.query, limit=5)
                if not hits:
                    reply_text(event.reply_token, "我目前想不起來相關內容。你可以換個關鍵字試試。")
                else:
                    reply_text(event.reply_token, "我找到這些：\n" + "\n".join([f"- {h}" for h in hits]))
                continue

            # 11) FALLBACK：簡單聊天（不強制）
            reply_text(event.reply_token, f"我收到：{text}\n\n你也可以輸入「指令」查看可用功能。")

    return "OK"

def generate_morning_report(user_id: str, city: str | None, zodiac: str | None) -> str:
    city = city or "台中市"
    zodiac = zodiac or "獅子座"

    weather_now = get_weather_now(city)
    weather_fc = get_weather_forecast(city, days=2)
    horoscope = get_horoscope_today(zodiac)
    news_items = serper_news(limit=8)
    tasks = list_tasks_today(owner_id=user_id)

    return build_morning_report(
        city=city,
        zodiac=zodiac,
        weather_now=weather_now,
        weather_forecast=weather_fc,
        horoscope=horoscope,
        news_items=news_items,
        tasks=tasks
    )

def poll_due_tasks():
    # 每分鐘掃一次 due tasks → push
    try:
        due = supa.get_due_tasks()
        for t in due:
            push_text(t["owner_id"], f"⏰ 提醒：{t['message']}")
            mark_task_done(t["id"])
    except Exception as e:
        print("[SCHED] due tasks error:", e)

def poll_due_morning():
    # 每分鐘檢查是否到晨報時間（每位 user）
    try:
        users = get_users_due_morning()
        for u in users:
            user_id = u["user_id"]
            profile = get_profile(user_id) or {}
            city = profile.get("city")
            zodiac = profile.get("zodiac")
            report = generate_morning_report(user_id=user_id, city=city, zodiac=zodiac)
            push_text(user_id, report)
            mark_morning_sent(user_id)
    except Exception as e:
        print("[SCHED] morning error:", e)

if __name__ == "__main__":
    # local debug
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
