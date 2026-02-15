import os
import re
import json
import traceback
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import google.generativeai as genai

from nlu import parse_intent
from storage import Storage
from tools_weather import (
    weather_resolve_location, weather_current, weather_forecast_days
)
from tools_news import serper_news_recent, serper_web_search_recent
from tools_horoscope import horoscope_today


# =========================
# 基本設定
# =========================

TZ_NAME = os.environ.get("APP_TZ", "Asia/Taipei").strip() or "Asia/Taipei"
TZ = ZoneInfo(TZ_NAME)

def now_tz() -> datetime:
    return datetime.now(TZ)

def env(*keys, default=""):
    for k in keys:
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return default

LINE_TOKEN = env("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = env("LINE_CHANNEL_SECRET")

# 你之前用 GOOGLE_API_KEY，後來改成 GEMINI_API_KEY：兩個都支援
GEMINI_KEY = env("GEMINI_API_KEY", "GOOGLE_API_KEY")

SUPA_URL = env("SUPABASE_URL")
# 建議用 Service Role key（更穩），也相容你舊的 SUPABASE_KEY
SUPA_KEY = env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY")

SERPER_KEY = env("SERPER_API_KEY")
WEATHERAPI_KEY = env("WEATHERAPI_KEY")

CRON_KEY = env("CRON_KEY")  # 可選：保護 /cron
# =========================
# 你自己的固定 ID（保留，不刪）
# =========================
MY_ID = "U7388690eb33a4e528e78cae4df00d0c2"
WIFE_ID = "U0feb2afe319cc8a66ab89ad9fe6ab0fe"


app = Flask(__name__)

# LINE
configuration = Configuration(access_token=LINE_TOKEN) if LINE_TOKEN else None
handler = WebhookHandler(LINE_SECRET) if LINE_SECRET else None

# Supabase
store = Storage(SUPA_URL, SUPA_KEY)

# Gemini
llm = None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # 你指定：核心用 3-pro-preview
    llm = genai.GenerativeModel("gemini-3-pro-preview")


# =========================
# 共用：LINE Reply / Push
# =========================

def split_for_line(text: str, limit: int = 4300):
    """LINE TextMessage 上限約 5000，但保守切 4300，避免超長標題/網址爆掉"""
    if not text:
        return ["（空）"]
    chunks = []
    buf = ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > limit:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf:
        chunks.append(buf)
    return chunks

def reply(event, text: str):
    if not configuration:
        return
    parts = split_for_line(text)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=p) for p in parts[:5]]  # 最多 5 則，避免洗版
            )
        )

def push(to_user_id: str, text: str):
    """主動推播（提醒/轉告/晨報）"""
    if not configuration:
        raise RuntimeError("LINE_TOKEN 未設定，無法 push")
    if not to_user_id:
        raise ValueError("push: to_user_id 空值")

    parts = split_for_line(text)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=to_user_id,
                messages=[TextMessage(text=p) for p in parts[:5]]
            )
        )


# =========================
# 健康檢查（給 UptimeRobot）
# =========================
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200


# =========================
# Cron：每分鐘跑一次（提醒 + 晨報）
# 用 UptimeRobot 打：https://你的Render網址/cron?key=CRON_KEY
# =========================
@app.route("/cron", methods=["GET"])
def cron():
    if CRON_KEY:
        if request.args.get("key", "").strip() != CRON_KEY:
            return "Forbidden", 403

    # 1) 發送到期提醒
    sent_count = 0
    try:
        due = store.get_due_tasks(now_tz())
        for task in due:
            try:
                to_id = task["target_user_id"]
                msg = f"⏰ 提醒：{task['message']}"
                push(to_id, msg)
                store.mark_task_sent(task["id"], now_tz())
                sent_count += 1
            except Exception as e:
                # 不要整批中斷，單筆失敗就記錄
                store.mark_task_failed(task["id"], str(e))
    except Exception:
        traceback.print_exc()

    # 2) 晨報：到點就送（一天一次）
    morning_sent = 0
    try:
        profiles = store.get_all_profiles()
        for p in profiles:
            if not p.get("morning_enabled"):
                continue
            user_id = p["user_id"]
            hhmm = p.get("morning_time") or "07:00"
            # 到點判斷
            h, m = hhmm.split(":")
            target_dt = now_tz().replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            if now_tz() < target_dt:
                continue

            ymd = now_tz().date()
            if store.morning_already_sent(user_id, ymd):
                continue

            report = build_morning_report(p)
            try:
                push(user_id, report)
                store.log_morning_sent(user_id, ymd, now_tz())
                morning_sent += 1
            except Exception as e:
                store.log_morning_failed(user_id, ymd, str(e))
    except Exception:
        traceback.print_exc()

    return f"cron ok | reminders_sent={sent_count} | morning_sent={morning_sent}", 200


def build_morning_report(profile: dict) -> str:
    city = (profile.get("city") or "").strip()
    zodiac = (profile.get("zodiac") or "").strip()
    max_news = int(profile.get("news_count") or 8)

    lines = []
    lines.append(f"☀️ 每日晨報（{now_tz().date().isoformat()}）")

    # 天氣（用 WeatherAPI 結構化）
    if city and WEATHERAPI_KEY:
        try:
            loc = weather_resolve_location(WEATHERAPI_KEY, city)
            w = weather_current(WEATHERAPI_KEY, loc["query"])
            lines.append("")
            lines.append(f"【居住地天氣｜{loc['name']}】")
            lines.append(
                f"{w['text']}｜{w['temp_c']}°C（體感 {w['feelslike_c']}°C）｜濕度 {w['humidity']}%｜風 {w['wind_kph']} km/h"
            )
        except Exception:
            lines.append("")
            lines.append("【居住地天氣】（取得失敗，稍後可再問我）")
    else:
        lines.append("")
        lines.append("【居住地天氣】（尚未設定城市或未設定 WEATHERAPI_KEY）")

    # 運勢（用 Serper 搜尋 + 文字整合）
    lines.append("")
    lines.append(f"【今日運勢｜{zodiac or '未設定'}】")
    if zodiac and SERPER_KEY:
        try:
            hx = horoscope_today(SERPER_KEY, zodiac, now_tz())
            lines.append(hx)
        except Exception:
            lines.append("（運勢取得失敗，稍後可再試）")
    else:
        lines.append("（尚未設定星座或未設定 SERPER_API_KEY）")

    # 新聞（只取近 1–7 天，不夠才放寬）
    lines.append("")
    lines.append(f"【全球新聞重點（{max_news} 則內｜近 7 日）】")
    if SERPER_KEY:
        try:
            items = serper_news_recent(
                SERPER_KEY,
                query="全球 重大 新聞 重點",
                now_dt=now_tz(),
                max_items=max_news,
                max_age_days=7,
                hl="zh-tw",
                gl="tw"
            )
            if not items:
                lines.append("（近 7 日未抓到合格來源，可用：全球新聞 + 關鍵字）")
            else:
                for i, it in enumerate(items, 1):
                    lines.append(f"{i}. {it['title']}（{it.get('source','')} / {it.get('age','')}）")
        except Exception:
            lines.append("（新聞取得失敗，稍後可再試）")
    else:
        lines.append("（未設定 SERPER_API_KEY）")

    # 節日（用搜尋簡單帶過）
    lines.append("")
    lines.append("【重要節日提醒】")
    if SERPER_KEY:
        try:
            q = f"{now_tz().date().isoformat()} 台灣 什麼節日"
            results = serper_web_search_recent(
                SERPER_KEY, q, now_dt=now_tz(), max_items=3, max_age_days=30, hl="zh-tw", gl="tw"
            )
            if results:
                for r in results[:3]:
                    lines.append(f"- {r['title']}")
            else:
                lines.append("（未找到明確節日資訊）")
        except Exception:
            lines.append("（節日資訊取得失敗）")
    else:
        lines.append("（未設定 SERPER_API_KEY）")

    return "\n".join(lines)


# =========================
# Webhook
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    if not handler:
        abort(500)

    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()

    # 先確保 profile 存在
    store.ensure_profile(user_id)

    # 你自己的固定稱呼（保留）
    if user_id == MY_ID:
        store.set_role_label_if_empty(user_id, "先生")
    elif user_id == WIFE_ID:
        store.set_role_label_if_empty(user_id, "夫人")

    profile = store.get_profile(user_id) or {}
    role = profile.get("role_label") or "主人"

    # 短期對話存檔（user）
    store.save_turn(user_id, "user", text, now_tz())

    # 解析意圖（自然語句）
    intent = parse_intent(text, profile)

    try:
        # ==========
        # 1) 設定/指令
        # ==========
        if intent["type"] == "help":
            reply(event, store.help_text())
            return

        if intent["type"] == "set_city":
            city = intent["city"]
            store.update_profile(user_id, {"city": city})
            reply(event, f"好的，{role}。已設定城市為「{city}」。")
            return

        if intent["type"] == "set_zodiac":
            zodiac = intent["zodiac"]
            store.update_profile(user_id, {"zodiac": zodiac})
            reply(event, f"好的，{role}。已設定星座為「{zodiac}」。")
            return

        if intent["type"] == "set_morning_time":
            hhmm = intent["hhmm"]
            store.update_profile(user_id, {"morning_time": hhmm, "morning_enabled": True})
            reply(event, f"好的，{role}。晨報時間已設定為 {hhmm}（{TZ_NAME}）。")
            return

        if intent["type"] == "toggle_morning":
            on = intent["on"]
            store.update_profile(user_id, {"morning_enabled": bool(on)})
            reply(event, f"好的，{role}。晨報已{'開啟' if on else '關閉'}。")
            return

        if intent["type"] == "bind_alias":
            alias = intent["alias"]
            target_id = intent["user_id"]
            store.bind_alias(alias, target_id, user_id)
            reply(event, f"好的，{role}。已綁定「{alias}」。之後可直接說：傳訊給{alias} ...")
            return

        if intent["type"] == "unbind_alias":
            alias = intent["alias"]
            store.unbind_alias(alias)
            reply(event, f"好的，{role}。已解除綁定「{alias}」。")
            return

        if intent["type"] == "list_alias":
            reply(event, store.alias_list_text())
            return

        if intent["type"] == "set_my_name":
            myname = intent["myname"]
            store.update_profile(user_id, {"my_name": myname})
            reply(event, f"好的，{role}。我記住你希望我稱呼你為「{myname}」。")
            return

        if intent["type"] == "set_callname":
            # 「我叫夫人什麼」這種每人不同稱呼
            target_alias = intent["target_alias"]
            callname = intent["callname"]
            target_id = store.get_user_id_by_alias(target_alias)
            if not target_id:
                reply(event, f"{role}，我找不到「{target_alias}」是誰。先用：綁定 {target_alias} Uxxxx")
                return
            store.upsert_callname(user_id, target_id, callname)
            reply(event, f"好的，{role}。以後你提到「{target_alias}」時，我會用你指定的稱呼「{callname}」。")
            return

        if intent["type"] == "set_prefs_default":
            # 預設共享/私密（一次設定後就不煩你）
            mode = intent["mode"]  # 'shared' or 'private'
            store.update_profile(user_id, {"prefs_default": mode})
            reply(event, f"好的，{role}。以後「生活喜好」我將預設記為：{'家庭共享' if mode=='shared' else '僅你可見'}。")
            return

        # ==========
        # 2) 日期/時間（永遠程式給，避免模型亂猜）
        # ==========
        if intent["type"] == "datetime":
            dt = now_tz()
            reply(event, f"{TZ_NAME}：{dt.strftime('%Y-%m-%d %H:%M:%S')}")
            return

        # ==========
        # 3) 天氣（WeatherAPI）
        # ==========
        if intent["type"] == "weather":
            if not WEATHERAPI_KEY:
                reply(event, f"{role}，我還沒拿到 WEATHERAPI_KEY，所以暫時不能查結構化天氣。")
                return

            city = (intent.get("city") or profile.get("city") or "").strip()
            if not city:
                reply(event, f"{role}，你要查哪個城市的天氣？你也可以先說：設定城市 台中")
                return

            loc = weather_resolve_location(WEATHERAPI_KEY, city)

            # 今天/明天/後天 -> 用 forecast
            days = intent.get("days")
            offset = intent.get("offset")

            if days:
                # WeatherAPI 免費通常只給 3 天，超過就縮回 3
                days = min(int(days), 3)
                fc = weather_forecast_days(WEATHERAPI_KEY, loc["query"], days=days)
                lines = [f"{loc['name']} 未來 {days} 天："]
                for d in fc:
                    lines.append(f"- {d['date']}：{d['text']}｜{d['min_c']}~{d['max_c']}°C｜降雨機率 {d['chance_rain']}%")
                reply(event, "\n".join(lines))
                return

            # offset = 0/1/2
            fc = weather_forecast_days(WEATHERAPI_KEY, loc["query"], days=3)
            idx = int(offset or 0)
            idx = max(0, min(idx, len(fc)-1))
            d = fc[idx]
            label = "今天" if idx == 0 else "明天" if idx == 1 else "後天"
            reply(event, f"{loc['name']}（{label} {d['date']}）：{d['text']}｜{d['min_c']}~{d['max_c']}°C｜降雨機率 {d['chance_rain']}%")
            return

        # ==========
        # 4) 新聞（Serper /news，保證近 1–7 天）
        # ==========
        if intent["type"] == "news":
            if not SERPER_KEY:
                reply(event, f"{role}，我還沒拿到 SERPER_API_KEY，所以暫時不能查即時新聞。")
                return

            q = intent.get("query") or "全球 重大 新聞 重點"
            days = int(intent.get("days") or 7)
            days = max(1, min(days, 30))  # 最多 30 天
            max_items = int(intent.get("count") or 8)
            max_items = max(1, min(max_items, 8))

            items = serper_news_recent(
                SERPER_KEY, q,
                now_dt=now_tz(),
                max_items=max_items,
                max_age_days=days,
                hl="zh-tw",
                gl="tw"
            )
            if not items:
                reply(event, f"{role}，我在近 {days} 天內沒有抓到足夠合格新聞。你可以換關鍵字，例如：全球新聞 以色列 或 全球新聞 AI")
                return

            lines = []
            lines.append(f"📰 {q}（近 {days} 天｜{len(items)} 則）")
            for i, it in enumerate(items, 1):
                lines.append(f"{i}. {it['title']}（{it.get('source','')} / {it.get('age','')}）")
            lines.append("")
            lines.append("想看哪則詳細？回我：看新聞 3")
            store.cache_news_items(user_id, items, now_tz())
            reply(event, "\n".join(lines))
            return

        if intent["type"] == "news_detail":
            idx = int(intent["index"]) - 1
            items = store.get_cached_news_items(user_id)
            if not items or idx < 0 or idx >= len(items):
                reply(event, f"{role}，我找不到那則新聞。你可以先說：全球新聞")
                return
            it = items[idx]
            # 詳細就給摘要 + 連結（不一次塞爆）
            reply(event, f"🧾 {it['title']}\n\n{it.get('snippet','')}\n\n{it.get('link','')}")
            return

        # ==========
        # 5) 運勢（Serper 搜尋 + 精簡整合）
        # ==========
        if intent["type"] == "horoscope":
            if not SERPER_KEY:
                reply(event, f"{role}，我還沒拿到 SERPER_API_KEY，所以暫時不能查運勢。")
                return

            zodiac = (intent.get("zodiac") or profile.get("zodiac") or "").strip()
            if not zodiac:
                reply(event, f"{role}，你是什麼星座？你也可以先說：設定星座 獅子座")
                return

            hx = horoscope_today(SERPER_KEY, zodiac, now_tz())
            reply(event, hx)
            return

        # ==========
        # 6) 提醒（存 Supabase，靠 /cron 推播）
        # ==========
        if intent["type"] == "remind":
            due_at = intent["due_at"]  # datetime
            msg = intent["message"]

            # 目標對象：可省略 -> 自己
            target_alias = intent.get("target_alias")
            target_user_id = user_id
            if target_alias:
                tid = store.get_user_id_by_alias(target_alias)
                if not tid:
                    reply(event, f"{role}，我找不到「{target_alias}」。先用：綁定 {target_alias} Uxxxx")
                    return
                target_user_id = tid

            task_id = store.create_task(
                created_by=user_id,
                target_user_id=target_user_id,
                due_at=due_at,
                message=msg
            )

            # 回覆時不要太制式
            when = due_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            who = target_alias if target_alias else "你"
            reply(event, f"好的，{role}。我會在 {when} 提醒{who}：{msg}\n（任務ID：{task_id}）")
            return

        if intent["type"] == "list_tasks":
            tasks = store.list_tasks(user_id)
            if not tasks:
                reply(event, f"好的，{role}。目前沒有待送提醒。")
                return
            lines = [f"📌 {role} 的提醒："]
            for t in tasks[:20]:
                lines.append(f"- ID {t['id']}｜{t['due_at']}｜{t['message']}｜{t['status']}")
            reply(event, "\n".join(lines))
            return

        if intent["type"] == "cancel_task":
            tid = int(intent["task_id"])
            ok = store.cancel_task(tid, user_id)
            reply(event, f"好的，{role}。{'已取消' if ok else '取消失敗（可能不存在或非你建立）'}：{tid}")
            return

        # ==========
        # 7) 傳訊（互傳）
        # ==========
        if intent["type"] == "send":
            alias = intent["target_alias"]
            msg = intent["message"]

            tid = store.get_user_id_by_alias(alias)
            if not tid:
                reply(event, f"{role}，我找不到「{alias}」。先用：綁定 {alias} Uxxxx")
                return

            # 接收者看到的稱呼：用「接收者」對「發送者」的稱呼（更人性）
            sender_name = store.get_preferred_callname(receiver_id=tid, sender_id=user_id) \
                          or (profile.get("my_name") or role)

            push(tid, f"📩 {sender_name} 轉告：{msg}")
            reply(event, f"好的，{role}。我已轉告 {alias}。")
            return

        # ==========
        # 8) 記憶（長期向量）
        # ==========
        if intent["type"] == "remember":
            scope = intent["scope"]  # 'private' or 'shared'
            content = intent["content"]

            if not llm:
                reply(event, f"{role}，Gemini 未連線（GEMINI_API_KEY 未設定），我暫時無法做向量記憶。")
                return

            emb = embed_text(content, task_type="retrieval_document")
            store.save_long_memory(owner_user_id=user_id, scope=scope, content=content, embedding=emb, created_at=now_tz())

            # 生活喜好一次性詢問（你要的：只問一次，不一直問）
            hint = ""
            if scope == "private" and store.is_preference_like(content) and not (profile.get("prefs_default")):
                hint = "\n\n（順便問一次：以後「生活喜好」要預設記為家庭共享嗎？回我：預設共享 / 預設私密）"

            reply(event, f"好的，{role}。已記住（{'家庭共享' if scope=='shared' else '僅你可見'}）。{hint}".strip())
            return

        if intent["type"] == "list_memory":
            mems = store.list_long_memories(user_id, limit=10)
            if not mems:
                reply(event, f"好的，{role}。目前沒有可用的長期記憶。")
                return
            lines = ["🧠 你的長期記憶（最近 10 筆）："]
            for m in mems:
                tag = "共享" if m["scope"] == "shared" else "私密"
                lines.append(f"- [{tag}] {m['content']}")
            reply(event, "\n".join(lines))
            return

        # ==========
        # 9) 一般聊天：帶入短期 + 長期記憶（只抓你可見 + 共享）
        # ==========
        if not llm:
            reply(event, f"{role}，大腦（Gemini）未連線：請確認 GEMINI_API_KEY。")
            return

        short_ctx = store.get_recent_turns(user_id, limit=10)

        long_ctx = ""
        try:
            # 只有當句子看起來是在「問習慣/偏好/資訊」才做向量檢索（省延遲）
            if store.should_memory_search(text):
                qemb = embed_text(text, task_type="retrieval_query")
                hits = store.search_long_memory(qemb, requester_id=user_id, threshold=0.25, count=6)
                if hits:
                    long_ctx = "\n".join([f"- {h['content']}" for h in hits])
        except Exception:
            traceback.print_exc()

        prompt = build_chat_prompt(
            role=role,
            now_dt=now_tz(),
            user_text=text,
            short_ctx=short_ctx,
            long_ctx=long_ctx
        )

        answer = safe_llm_text(prompt)

        # assistant 回覆存短期
        store.save_turn(user_id, "assistant", answer, now_tz())
        reply(event, answer)

    except Exception as e:
        traceback.print_exc()
        reply(event, f"{role}，我這裡出錯了：{e}")


def embed_text(text: str, task_type: str):
    """google-generativeai embed_content 回傳格式有兩種：都兼容"""
    emb_obj = genai.embed_content(
        model="models/text-embedding-004",
        content=text,
        task_type=task_type
    )
    # 有時是 .embedding，有時是 dict
    if hasattr(emb_obj, "embedding"):
        return emb_obj.embedding
    if isinstance(emb_obj, dict) and "embedding" in emb_obj:
        return emb_obj["embedding"]
    raise ValueError("embedding 回傳格式不支援")


def safe_llm_text(prompt: str) -> str:
    """避免 response.text 取不到（極少數狀況），有保底"""
    resp = llm.generate_content(prompt)
    # google-generativeai 有時候 resp.text 會因候選空而炸
    try:
        t = (resp.text or "").strip()
        if t:
            return t
    except Exception:
        pass

    # 保底：盡量從 candidates 拼出文字
    try:
        if hasattr(resp, "candidates") and resp.candidates:
            parts = []
            for c in resp.candidates:
                if hasattr(c, "content") and c.content and getattr(c.content, "parts", None):
                    for p in c.content.parts:
                        if getattr(p, "text", None):
                            parts.append(p.text)
            t = "\n".join(parts).strip()
            if t:
                return t
    except Exception:
        pass

    return "好的，先生。我收到你的訊息，但剛剛大腦回傳內容異常，請你再說一次。"


def build_chat_prompt(role: str, now_dt: datetime, user_text: str, short_ctx: str, long_ctx: str) -> str:
    # 重要：不要每句都附時間，只有被問到才回答；這裡只做「內部參考」
    return f"""
你是 Saturday，{role}的私人 AI 管家。你說話要自然、有人味、像真正管家。
嚴格規則：
- 不要自動報時間或日期，除非主人問你。
- 不要瞎掰：不知道就說不知道，並提出你能做的下一步。
- 若「長期記憶」有答案，優先引用；不要跟記憶矛盾。
- 回覆盡量精簡，不要教科書長篇。
- 語氣：台灣常用中文，尊稱「先生/夫人/主人」。

（系統時間參考：{now_dt.strftime('%Y-%m-%d %H:%M:%S')} {TZ_NAME}）

【短期對話（最近）】
{short_ctx or "（無）"}

【長期記憶（你可見 + 共享）】
{long_ctx or "（無）"}

主人說：
{user_text}

請回覆：
""".strip()


if __name__ == "__main__":
    # Render 會用 gunicorn 啟動；本地可用 python app.py
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
