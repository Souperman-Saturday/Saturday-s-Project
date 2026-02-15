import os
from flask import Flask, request, abort

from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, PushMessageRequest
)

import google.generativeai as genai

from persona import SYSTEM_PERSONA
from tools import weatherapi_forecast, serper_news, serper_search, now_tz
from memory import (
    ensure_profile, update_profile,
    add_memory, search_memories,
    add_task, get_supabase
)
from commands import (
    parse_reminder, wants_weather, wants_horoscope, wants_news,
    is_set_morning_time, parse_morning_time,
    is_send_morning_now,
    is_set_city, extract_city,
    is_set_zodiac, extract_zodiac,
    parse_push_to_family
)

app = Flask(__name__)

# LINE
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN 未設定")

handler = WebhookHandler(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY 未設定")
genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3-pro-preview"

MY_ID = os.getenv("MY_ID", "")
WIFE_ID = os.getenv("WIFE_ID", "")

CRON_SECRET = os.getenv("CRON_SECRET", "")

def reply_text(reply_token: str, text: str):
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text[:4900])]
            )
        )

def push_text(to_user_id: str, text: str):
    if not to_user_id:
        return
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=to_user_id,
                messages=[TextMessage(text=text[:4900])]
            )
        )

def gemini_chat(user_text: str, memory_snippets: list[str]) -> str:
    model = genai.GenerativeModel(
        MODEL_NAME,
        system_instruction=SYSTEM_PERSONA
    )
    mem_block = "\n".join([f"- {m}" for m in memory_snippets]) if memory_snippets else "(無)"
    prompt = f"""以下是你可用的已知記憶（可能包含偏好、習慣、已設定資料）：
{mem_block}

使用者說：{user_text}

請用自然的管家口吻回覆。"""
    try:
        resp = model.generate_content(prompt)
        return (resp.text or "").strip() or "我在，想先做哪件事？"
    except Exception as e:
        return f"先生，大腦（Gemini）連線失敗：{str(e)}"

def format_weather(city: str, days: int = 3) -> str:
    w = weatherapi_forecast(city, days=days)
    if not w["ok"]:
        return f"目前無法查到天氣：{w['error']}"
    data = w["data"]
    loc = data.get("location", {})
    cur = data.get("current", {})
    fdays = data.get("forecast", {}).get("forecastday", [])

    lines = []
    lines.append(f"📍{loc.get('name','')} 天氣")
    if cur:
        lines.append(f"現在：{cur.get('condition',{}).get('text','')}，{cur.get('temp_c','?')}°C，體感 {cur.get('feelslike_c','?')}°C，濕度 {cur.get('humidity','?')}%")
    if fdays:
        lines.append("未來預報：")
        for d in fdays[:days]:
            day = d.get("day", {})
            dt = d.get("date", "")
            cond = day.get("condition", {}).get("text", "")
            maxc = day.get("maxtemp_c", "?")
            minc = day.get("mintemp_c", "?")
            rain = day.get("daily_chance_of_rain", "?")
            lines.append(f"- {dt}：{cond}，{minc}~{maxc}°C，降雨機率 {rain}%")
    return "\n".join(lines)

def build_horoscope(zodiac: str) -> str:
    # 走 Serper（偏好近 1 天），拿到來源後再做短摘要
    today = now_tz().date().isoformat()
    q = f"{zodiac} 今日運勢 {today}"
    r = serper_search(q)
    if not r["ok"]:
        # 退化：用 Gemini 生成「一般性」建議（不宣稱真實資料）
        model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PERSONA)
        resp = model.generate_content(f"請給 {zodiac} 一段「今日」的簡短建議運勢（300字內），語氣像管家提醒，避免宣稱引用了真實網站資料。")
        return f"♌ {zodiac} 今日提醒\n{(resp.text or '').strip()}"
    items = r["data"].get("organic", [])[:3]
    src = items[0] if items else {}
    title = src.get("title", "")
    link = src.get("link", "")
    snippet = src.get("snippet", "")

    model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PERSONA)
    resp = model.generate_content(
        f"根據以下資料，整理成 {zodiac} 今日提醒（200字內，列3點）：\n標題:{title}\n摘要:{snippet}"
    )
    text = (resp.text or "").strip()
    return f"♌ {zodiac} 今日運勢（摘要）\n{text}\n\n來源：{title}\n{link}"

def build_news_digest() -> str:
    r = serper_news("global top news major events", num=8, hl="zh-tw", gl="tw")
    if not r["ok"]:
        return f"🗞️ 全球新聞：目前無法取得（{r['error']}）"
    news = r["data"].get("news", [])[:8]

    # 把長網址/長標題控制住，避免 LINE 超長截斷
    lines = ["🗞️ 全球新聞重點整理（8則內）"]
    for i, n in enumerate(news, start=1):
        title = (n.get("title") or "").strip()
        source = (n.get("source") or "").strip()
        link = (n.get("link") or "").strip()
        snippet = (n.get("snippet") or "").strip()

        # 只保留短摘要，避免爆字數
        title = title[:60]
        snippet = snippet[:80]

        lines.append(f"{i}. {title}（{source}）")
        if snippet:
            lines.append(f"   - {snippet}")
        if link:
            lines.append(f"   {link}")
    lines.append("如果你想看哪一則的更詳細內容，直接回我「第X則詳情」。")
    return "\n".join(lines)

def build_holiday_hint() -> str:
    # 用 Serper 找「今天節日/節氣/紀念日」
    today = now_tz().date()
    q = f"{today.month}月{today.day}日 今天 節日 紀念日 台灣"
    r = serper_search(q)
    if not r["ok"]:
        return "🎎 重要節日提醒：目前查詢不到可靠來源。"
    items = r["data"].get("organic", [])[:2]
    if not items:
        return "🎎 重要節日提醒：今天沒有查到明確節日資訊。"
    title = items[0].get("title","").strip()[:80]
    snippet = items[0].get("snippet","").strip()[:120]
    link = items[0].get("link","")
    return f"🎎 重要節日提醒\n- {title}\n- {snippet}\n{link}"

def build_morning_report(user_id: str, prof: dict) -> str:
    city = prof.get("home_city", "台中市")
    zodiac = prof.get("zodiac", "獅子座")

    parts = []
    parts.append(f"☀️ 早安！每日晨報（{now_tz().date().isoformat()}）")
    parts.append("")
    parts.append("【天氣】")
    parts.append(format_weather(city, days=3))
    parts.append("")
    parts.append("【星座提醒】")
    parts.append(build_horoscope(zodiac))
    parts.append("")
    parts.append("【新聞】")
    parts.append(build_news_digest())
    parts.append("")
    parts.append("【節日】")
    parts.append(build_holiday_hint())

    return "\n".join(parts)[:4900]

@app.get("/")
def health():
    return "ok", 200

@app.get("/cron")
def cron():
    secret = request.args.get("secret", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return "forbidden", 403

    # 延遲 import 避免循環依賴
    from scheduler import run_cron_once
    run_cron_once()
    return "ok", 200

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
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()
    now = now_tz()

    # 先確保 profile 存在
    prof = ensure_profile(user_id)

    # 1) 傳訊給家人
    pm = parse_push_to_family(text)
    if pm:
        who, msg = pm
        if who == "夫人":
            push_text(WIFE_ID, f"先生轉告：{msg}")
            reply_text(event.reply_token, "已幫你轉告夫人。")
            return
        if who == "先生":
            push_text(MY_ID, f"夫人轉告：{msg}")
            reply_text(event.reply_token, "已幫你轉告先生。")
            return

    # 2) 設定晨報時間
    if is_set_morning_time(text):
        hhmm = parse_morning_time(text)
        if not hhmm:
            reply_text(event.reply_token, "晨報時間我需要像 07:00 這種格式。你想設幾點？")
            return
        update_profile(user_id, morning_time=hhmm)
        reply_text(event.reply_token, f"好的，晨報時間已設定為 {hhmm}。")
        return

    # 3) 設定城市
    if is_set_city(text):
        city = extract_city(text)
        if not city:
            reply_text(event.reply_token, "你想把居住地設定成哪個城市？例如：台中市")
            return
        update_profile(user_id, home_city=city)
        reply_text(event.reply_token, f"收到，我已把你的居住地設定為「{city}」。")
        return

    # 4) 設定星座
    if is_set_zodiac(text):
        z = extract_zodiac(text)
        if not z:
            reply_text(event.reply_token, "你想把星座設定成什麼？例如：獅子座")
            return
        update_profile(user_id, zodiac=z)
        reply_text(event.reply_token, f"收到，你的星座我記成「{z}」。")
        return

    # 5) 設定提醒
    parsed = parse_reminder(text, now)
    if parsed:
        due, payload = parsed
        if not payload:
            payload = "（提醒事項未填）"
        add_task(user_id, due.isoformat(), payload, scope="private")
        reply_text(event.reply_token, f"好，我會在 {due.strftime('%m/%d %H:%M')} 提醒你：{payload}")
        return

    # 6) 立刻晨報
    if is_send_morning_now(text):
        reply_text(event.reply_token, "收到，我現在整理給你。")
        push_text(user_id, build_morning_report(user_id, prof))
        return

    # 7) 工具路由：天氣/運勢/新聞
    if wants_weather(text):
        city = prof.get("home_city", "台中市")
        reply_text(event.reply_token, format_weather(city, days=3))
        return

    if wants_horoscope(text):
        zodiac = prof.get("zodiac", "獅子座")
        reply_text(event.reply_token, build_horoscope(zodiac))
        return

    if wants_news(text):
        reply_text(event.reply_token, build_news_digest())
        return

    # 8) 記憶（自然語句）
    # 例：記住 私密：我早上都喝黑咖啡
    #     記住 共享：我們家週末會去拜拜
    if text.startswith("記住"):
        scope = "private"
        content = text.replace("記住", "", 1).strip()
        if content.startswith("私密"):
            scope = "private"
            content = content.replace("私密", "", 1).lstrip("：: ")
        elif content.startswith("共享"):
            scope = "family"
            content = content.replace("共享", "", 1).lstrip("：: ")
        if not content:
            reply_text(event.reply_token, "你要我記住什麼？例如：記住 私密：我早上習慣喝黑咖啡")
            return
        add_memory(user_id, content, memory_type="long", scope=scope, importance=2)
        reply_text(event.reply_token, "好，我記下來了。")
        return

    # 9) 一般聊天：用記憶輔助
    mem_private = search_memories(user_id, text, scope="private", k=6, threshold=0.68)
    snippets = [m["content"] for m in mem_private]

    # 若是家人聊天（先生/夫人），也可以再補 family 記憶（不會問第二次）
    mem_family = search_memories(user_id, text, scope="family", k=4, threshold=0.68)
    snippets += [m["content"] for m in mem_family]

    ans = gemini_chat(text, snippets)
    reply_text(event.reply_token, ans)
