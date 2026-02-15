import os
import re
from flask import Flask, request, abort, jsonify
from dateutil import tz
from datetime import datetime

from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest

import google.generativeai as genai

from prompts import SYSTEM_PROMPT
from memory import DB
from nlp import (
    extract_command, parse_contact_set, parse_relay, parse_reminder, parse_remember,
    norm_time_hhmm, is_group_source
)
from tools_weather import get_weather
from tools_news import serper_news
from tools_horoscope import get_horoscope_source

from scheduler import init_scheduler

TZNAME = os.getenv("TZ", "Asia/Taipei")
TZ = tz.gettz(TZNAME)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

app = Flask(__name__)

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET 未設定")

handler = WebhookHandler(LINE_CHANNEL_SECRET)
config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

db = DB()

if not GEMINI_API_KEY:
    # 不直接炸掉，讓 /health 仍能活著，並回覆提示
    genai_ok = False
else:
    genai.configure(api_key=GEMINI_API_KEY)
    genai_ok = True

def now_tw_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def reply_text(reply_token: str, text: str):
    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text[:4900])]
        ))

def push_text(user_id: str, text: str):
    # LINE push 需要使用者先加好友
    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text[:4900])]
        ))

# 啟動 scheduler（Render Web Service 內）
init_scheduler(db, push_text)

def is_query_like(text: str) -> bool:
    # 群組只允許查詢型
    keywords = ["天氣", "運勢", "新聞", "今天", "明天", "下週", "查", "搜尋", "幾點", "日期", "節日"]
    return any(k in text for k in keywords)

def gemini_chat(user_id: str, owner_id: str, scope_list: list[str], user_text: str) -> str:
    if not genai_ok:
        return "先生，大腦（Gemini）連線失敗：GEMINI_API_KEY 未設定"

    # 拉一些記憶（短、乾淨）
    mems = db.list_memories(owner_id=owner_id, subject_id=owner_id, scopes=scope_list, limit=20)
    mem_lines = []
    for m in reversed(mems):
        s = m.get("scope")
        c = (m.get("content") or "").strip()
        if c:
            mem_lines.append(f"- ({s}) {c}")

    profile = db.ensure_profile(owner_id)
    city = profile.get("city") or ""
    zodiac = profile.get("zodiac") or ""

    context = SYSTEM_PROMPT + "\n\n"
    context += f"現在時間（台灣）：{now_tw_str()}\n"
    if city:
        context += f"使用者城市：{city}\n"
    if zodiac:
        context += f"使用者星座：{zodiac}\n"
    if mem_lines:
        context += "已知長期記憶（僅供參考，勿編造）：\n" + "\n".join(mem_lines) + "\n"

    model = genai.GenerativeModel("gemini-1.5-flash")
    resp = model.generate_content([
        {"role": "user", "parts": [context + "\n使用者訊息：" + user_text]}
    ])
    return (resp.text or "").strip() or "先生，我收到，但我需要你再說清楚一點。"

def format_weather_block(city: str) -> str:
    w = get_weather(city)
    if not w.get("ok"):
        return f"【今日天氣】\n（{w.get('error','查詢失敗')}）"
    fc = w.get("forecast", [])
    today = fc[0] if fc else None
    line1 = f"{w.get('location','')}\n現況：{w.get('condition')}，{w.get('temp_c')}°C，濕度 {w.get('humidity')}%"
    if today:
        line2 = f"今日：{today.get('text')}，{today.get('min_c')}~{today.get('max_c')}°C，降雨機率 {today.get('rain_chance')}%"
        return "【今日天氣】\n" + line1 + "\n" + line2
    return "【今日天氣】\n" + line1

def format_news_block(n: int) -> str:
    q = "全球 重大新聞 重點整理（台灣視角 中文）"
    r = serper_news(q, num=n, hl="zh-tw", gl="tw")
    if not r.get("ok"):
        return f"【今日要聞（近24h）】\n（{r.get('error','查詢失敗')}）"
    items = r.get("items", [])[:n]
    if not items:
        return "【今日要聞（近24h）】\n（查無結果）"
    lines = []
    for i, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        src = (it.get("source") or "").strip()
        date = (it.get("date") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        # 只留短摘要，避免 LINE 字數爆炸
        snippet = snippet[:80] + ("…" if len(snippet) > 80 else "")
        lines.append(f"{i}. {title}\n   {src} / {date}\n   {snippet}")
    return "【今日要聞（近24h）】\n" + "\n".join(lines)

def format_horoscope_block(owner_id: str) -> str:
    p = db.ensure_profile(owner_id)
    zodiac = (p.get("zodiac") or "").strip()
    if not zodiac:
        return "【今日運勢】\n（未設定星座：請輸入「設定星座 獅子」）"

    # 用 serper 搜來源，再讓 gemini 摘要（避免舊文/瞎掰）
    src = get_horoscope_source(zodiac)
    if not src.get("ok"):
        return f"【今日運勢】\n（{src.get('error','查詢失敗')}）"
    items = src.get("items", [])
    if not items:
        return "【今日運勢】\n（查無可用來源，稍後再試）"

    if not genai_ok:
        # 沒 gemini 就給來源摘要
        top = items[0]
        return f"【今日運勢】\n{zodiac}：{top.get('title')}\n{top.get('snippet')}"
    # gemini 摘要：要求「今日」
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    context = f"今天是 {today}（台灣時間）。請根據以下搜尋到的來源，整理「{zodiac} 今日運勢」成 4 行內重點（感情/工作/財運/健康），不要引用過期內容。"
    sources = "\n".join([f"- {it['title']}：{it['snippet']}" for it in items[:3]])
    model = genai.GenerativeModel("gemini-1.5-flash")
    resp = model.generate_content([{"role":"user","parts":[context + "\n來源：\n" + sources]}])
    text = (resp.text or "").strip()
    text = text[:500]
    return "【今日運勢】\n" + text

def build_morning(owner_id: str) -> str:
    p = db.ensure_profile(owner_id)
    city = (p.get("city") or "").strip()
    zodiac = (p.get("zodiac") or "").strip()
    news_count = int(p.get("news_count") or 5)

    header = f"☀️ 每日晨報（{datetime.now(TZ).strftime('%Y-%m-%d')}）"
    blocks = []

    if city:
        blocks.append(format_weather_block(city))
    else:
        blocks.append("【今日天氣】\n（未設定城市：請輸入「設定城市 台中」）")

    blocks.append(format_horoscope_block(owner_id))
    blocks.append(format_news_block(news_count))

    # 近期待辦提醒
    tasks = db.list_tasks(owner_id=owner_id, subject_id=owner_id, status="pending", limit=8)
    if tasks:
        lines = []
        for i, t in enumerate(tasks[:8], 1):
            lines.append(f"{i}. {t['due_at']}：{t['text']} (id={t['id']})")
        blocks.append("【待辦提醒】\n" + "\n".join(lines))
    else:
        blocks.append("【待辦提醒】\n（今天目前沒有待送提醒）")

    return header + "\n\n" + "\n\n".join(blocks)

def handle_internal_tasks():
    # scheduler 會塞 __SEND_MORNING__ 進 tasks，這裡在每次 webhook 時順便掃一次（讓晨報更容易發）
    now_iso = datetime.now(TZ).isoformat()
    due = db.due_tasks(now_iso)
    for t in due:
        if t.get("text") == "__SEND_MORNING__":
            try:
                msg = build_morning(t["owner_id"])
                push_text(t["subject_id"], msg)
            finally:
                db.mark_task_sent(t["id"])

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "time": now_tw_str(),
        "tz": TZNAME,
        "gemini_ok": genai_ok
    })

@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def on_text(event: MessageEvent):
    # 防重複（Webhook redelivery / 重送）
    webhook_event_id = getattr(event, "webhook_event_id", None)
    if webhook_event_id and db.seen_event(webhook_event_id):
        return
    if webhook_event_id:
        db.mark_event(webhook_event_id)

    # 先處理 internal tasks（晨報觸發）
    try:
        handle_internal_tasks()
    except Exception:
        pass

    user_id = event.source.user_id
    source_type = event.source.type  # user/group/room
    text = (event.message.text or "").strip()

    # 群組模式：只允許查詢型
    if is_group_source(source_type):
        if not is_query_like(text):
            reply_text(event.reply_token, "我在群組只提供查詢（天氣/新聞/運勢/日期），避免把家庭記憶與提醒寫錯。需要設定或記憶請私聊我。")
            return

    # ensure profile
    db.ensure_profile(user_id)

    cmd, args = extract_command(text)

    # 群組禁止：設定、記憶、提醒、傳訊
    if is_group_source(source_type) and cmd in (
        "set_city","set_zodiac","set_morning_time","morning_on","morning_off",
        "set_news_count","set_contact","delete_contact","relay","remind","remember",
        "cancel_task","clear_memories"
    ):
        reply_text(event.reply_token, "群組模式不做設定/記憶/提醒/傳訊，請私聊我操作，這樣比較不會亂。")
        return

    # ---------- commands ----------
    if cmd == "ping":
        reply_text(event.reply_token, "pong")
        return

    if cmd == "status":
        p = db.get_profile(user_id) or {}
        reply_text(event.reply_token,
                   f"🧠 Saturday 狀態\n"
                   f"- 時間：{now_tw_str()} ({TZNAME})\n"
                   f"- 城市：{p.get('city') or '未設定'}\n"
                   f"- 星座：{p.get('zodiac') or '未設定'}\n"
                   f"- 晨報：{'開啟' if p.get('morning_enabled') else '關閉'} / {p.get('morning_time')}\n"
                   f"- 新聞數量：{p.get('news_count')}\n"
                   f"- Gemini：{'OK' if genai_ok else '未設定 KEY'}")
        return

    if cmd == "my_settings":
        p = db.get_profile(user_id) or {}
        reply_text(event.reply_token,
                   f"✅ 你的設定\n"
                   f"- 城市：{p.get('city') or '未設定'}\n"
                   f"- 星座：{p.get('zodiac') or '未設定'}\n"
                   f"- 晨報：{'開啟' if p.get('morning_enabled') else '關閉'} / {p.get('morning_time')}\n"
                   f"- 新聞數量：{p.get('news_count')}\n")
        return

    if cmd == "set_city":
        city = args.strip()
        if not city:
            reply_text(event.reply_token, "用法：設定城市 台中")
            return
        db.update_profile(user_id, {"city": city, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, f"✅ 已設定城市：{city}")
        return

    if cmd == "set_zodiac":
        zodiac = args.strip()
        if not zodiac:
            reply_text(event.reply_token, "用法：設定星座 獅子")
            return
        db.update_profile(user_id, {"zodiac": zodiac, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, f"✅ 已設定星座：{zodiac}")
        return

    if cmd == "set_morning_time":
        hhmm = norm_time_hhmm(args)
        if not hhmm:
            reply_text(event.reply_token, "用法：設定晨報時間 07:00")
            return
        db.update_profile(user_id, {"morning_time": hhmm, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, f"✅ 晨報時間已設定為 {hhmm}（每天推播）")
        return

    if cmd == "morning_on":
        db.update_profile(user_id, {"morning_enabled": True, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, "✅ 已開啟晨報")
        return

    if cmd == "morning_off":
        db.update_profile(user_id, {"morning_enabled": False, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, "✅ 已關閉晨報")
        return

    if cmd == "set_news_count":
        try:
            n = int(args.strip())
            n = max(3, min(n, 8))
        except Exception:
            reply_text(event.reply_token, "用法：設定新聞數量 5（3～8）")
            return
        db.update_profile(user_id, {"news_count": n, "updated_at": datetime.now(TZ).isoformat()})
        reply_text(event.reply_token, f"✅ 新聞數量已設定為 {n}（近24h）")
        return

    if cmd == "send_morning":
        msg = build_morning(user_id)
        reply_text(event.reply_token, msg)
        return

    if cmd == "set_contact":
        parsed = parse_contact_set(args)
        if not parsed:
            reply_text(event.reply_token, "用法：設定家人 Uxxxxxxxxxxxx=夫人")
            return
        cid, alias = parsed
        db.set_contact_alias(owner_id=user_id, contact_id=cid, alias=alias)
        reply_text(event.reply_token, f"✅ 已設定：{alias} = {cid}")
        return

    if cmd == "delete_contact":
        alias = args.strip()
        if not alias:
            reply_text(event.reply_token, "用法：刪除家人 夫人")
            return
        db.delete_contact_alias(owner_id=user_id, alias=alias)
        reply_text(event.reply_token, f"✅ 已刪除家人：{alias}")
        return

    if cmd == "list_contacts":
        items = db.list_contacts(owner_id=user_id)
        if not items:
            reply_text(event.reply_token, "目前沒有家人映射。用法：設定家人 Uxxxx=夫人")
            return
        lines = [f"- {x['alias']} = {x['contact_id']}" for x in items]
        reply_text(event.reply_token, "👨‍👩‍👧 家人清單\n" + "\n".join(lines))
        return

    if cmd == "relay":
        parsed = parse_relay(args)
        if not parsed:
            reply_text(event.reply_token, "用法：傳訊給夫人 我晚點回家")
            return
        alias, msg = parsed
        cid = db.resolve_alias(owner_id=user_id, alias=alias)
        if not cid:
            reply_text(event.reply_token, f"找不到「{alias}」。先用：設定家人 Uxxxx={alias}")
            return
        try:
            push_text(cid, f"📨 來自先生的訊息：{msg}")
            reply_text(event.reply_token, f"✅ 已傳訊給 {alias}")
        except Exception as e:
            reply_text(event.reply_token, f"傳訊失敗：{e}")
        return

    if cmd == "remind":
        parsed = parse_reminder(args)
        if not parsed:
            reply_text(event.reply_token, "用法：\n- 提醒我 10分鐘後 喝水\n- 提醒我 2026-02-14 19:30 去接小孩")
            return
        due_iso, task_text = parsed
        db.add_task(owner_id=user_id, subject_id=user_id, due_at_iso=due_iso, text=task_text)
        reply_text(event.reply_token, f"✅ 已建立提醒：{task_text}\n時間：{due_iso}")
        return

    if cmd == "list_tasks":
        items = db.list_tasks(owner_id=user_id, subject_id=user_id, status="pending", limit=20)
        if not items:
            reply_text(event.reply_token, "目前沒有待送提醒。")
            return
        lines = [f"- {x['due_at']}：{x['text']} (id={x['id']})" for x in items]
        reply_text(event.reply_token, "⏰ 待送提醒\n" + "\n".join(lines))
        return

    if cmd == "cancel_task":
        tid = args.strip()
        if not tid:
            reply_text(event.reply_token, "用法：取消提醒 <提醒ID>")
            return
        db.cancel_task(owner_id=user_id, subject_id=user_id, task_id=tid)
        reply_text(event.reply_token, "✅ 已取消提醒")
        return

    if cmd == "remember":
        parsed = parse_remember(args)
        if not parsed:
            reply_text(event.reply_token, "用法：\n- 記住(共享) 我早上喝冰美式\n- 記住(私密) 我資金壓力大")
            return
        scope, content = parsed
        # 本版：記憶主體=自己（user_id）
        db.add_memory(owner_id=user_id, subject_id=user_id, scope=scope, content=content)
        reply_text(event.reply_token, f"✅ 已記住（{ '共享' if scope=='shared' else '私密' }）：{content}")
        return

    if cmd == "list_memories":
        mems = db.list_memories(owner_id=user_id, subject_id=user_id, scopes=["private","shared"], limit=30)
        if not mems:
            reply_text(event.reply_token, "目前沒有可列出的長期記憶。")
            return
        lines = []
        for m in mems[:20]:
            lines.append(f"- ({m['scope']}) {m['content']}")
        reply_text(event.reply_token, "🧾 長期記憶（最近）\n" + "\n".join(lines))
        return

    if cmd == "clear_memories":
        # 只清 private（穩妥）
        try:
            db.sb.table("saturday_memories").delete().eq("owner_id", user_id).eq("subject_id", user_id).eq("scope", "private").execute()
            reply_text(event.reply_token, "✅ 已清除你的私密記憶（共享記憶保留）")
        except Exception as e:
            reply_text(event.reply_token, f"清除失敗：{e}")
        return

    # ---------- query helpers (weather/news/horoscope) ----------
    if "天氣" in text:
        p = db.ensure_profile(user_id)
        city = (p.get("city") or "").strip()
        if not city:
            reply_text(event.reply_token, "我可以查天氣，但你還沒設定城市。請輸入：設定城市 台中")
            return
        reply_text(event.reply_token, format_weather_block(city))
        return

    if "新聞" in text and ("搜尋" in text or "整理" in text or "重大" in text):
        p = db.ensure_profile(user_id)
        n = int(p.get("news_count") or 5)
        reply_text(event.reply_token, format_news_block(n))
        return

    if "運勢" in text or "星座" in text:
        reply_text(event.reply_token, format_horoscope_block(user_id))
        return

    # ---------- default chat ----------
    # 記憶 scope：
    # - 自己：private+shared
    # - 其他家人（若你未來要做）：本版先只做自己 user 的記憶，群組不寫。
    scopes = ["private", "shared"]
    ans = gemini_chat(user_id=user_id, owner_id=user_id, scope_list=scopes, user_text=text)
    reply_text(event.reply_token, ans)
