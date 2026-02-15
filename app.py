import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)

from memory import MemoryStore
from scheduler import TaskScheduler
from tools_weather import get_weather_now, get_weather_forecast
from tools_news import serper_news_top
from tools_horoscope import generate_horoscope_cn
from prompts import SYSTEM_STYLE, build_morning_brief_prompt

# -----------------------------
# 基本設定
# -----------------------------
TZ = os.getenv("TZ", "Asia/Taipei")
APP_TZ = ZoneInfo(TZ)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET 未設定")

app = Flask(__name__)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

store = MemoryStore(
    supabase_url=SUPABASE_URL,
    supabase_key=SUPABASE_KEY,
)

scheduler = TaskScheduler(
    store=store,
    tz=TZ,
    push_func=lambda to_user_id, text: push_text(to_user_id, text),
)

# 啟動時把未過期任務載入（Render 重啟後也能恢復）
scheduler.load_future_tasks()


# -----------------------------
# LINE 發送工具
# -----------------------------
def reply_text(reply_token: str, text: str):
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


def push_text(to_user_id: str, text: str):
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=to_user_id,
                messages=[TextMessage(text=text)],
            )
        )


# -----------------------------
# 小工具：時間/解析
# -----------------------------
def now_str() -> str:
    return datetime.now(APP_TZ).strftime("%Y-%m-%d %H:%M")


def parse_time_hhmm(s: str) -> str | None:
    m = re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", s.strip())
    return s.strip() if m else None


def normalize_city_text(city: str) -> str:
    return city.strip().replace("臺", "台")


# -----------------------------
# 指令說明（給 /help）
# -----------------------------
HELP_TEXT = """\
【Saturday 指令表（V7.1）】

一、基本設定
- 設定城市 台中
- 設定星座 獅子座
- 設定晨報時間 07:00

二、家人綁定（手動最穩）
- 綁定 夫人 Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
- 解除綁定 夫人
- 我的綁定

三、記憶（長期）
- 記住 我早上習慣喝黑咖啡
- 記住(共享) 我早上習慣喝黑咖啡
- 我記得什麼

四、提醒
- 1分鐘後提醒 喝水
- 2026-02-14 18:30 提醒 帶尿布
- 取消提醒 <任務ID>
- 我的提醒

五、資訊
- 今天日期
- 今天天氣
- 明天台中天氣
- 3天台中天氣
- 今日運勢
- 晨報
- 全球新聞（8則內）

六、傳訊（主用戶可叫管家轉告）
- 傳訊給夫人 明天記得買奶粉
- 傳訊給先生 我到家了

備註：群組內只回覆「查詢型」（不寫記憶/不設提醒/不發晨報）避免亂。
"""


# -----------------------------
# LINE Webhook
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "ts": now_str()}


@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception:
        abort(400)
    return "OK"


@handler.add(FollowEvent)
def on_follow(event: FollowEvent):
    user_id = event.source.user_id
    # 預設把第一個加好友的人當作主用戶（可後續改）
    if not store.get_primary_user():
        store.set_primary_user(user_id)
    store.ensure_profile(user_id)
    reply_text(event.reply_token, "先生/女士，您好。我是 Saturday。輸入「/help」可查看指令。")


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event: MessageEvent):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()
    is_group = getattr(event.source, "type", "") in ("group", "room")

    # 群組：只做查詢，避免記憶/提醒/晨報造成混亂
    if is_group:
        answer = handle_query_only(user_id, text)
        reply_text(event.reply_token, answer)
        return

    # 私聊：全功能
    store.ensure_profile(user_id)

    if text in ("/help", "help", "指令", "指令表"):
        reply_text(event.reply_token, HELP_TEXT)
        return

    answer = handle_full(user_id, text, reply_token=event.reply_token)
    reply_text(event.reply_token, answer)


# -----------------------------
# 群組：只允許查詢型
# -----------------------------
def handle_query_only(user_id: str, text: str) -> str:
    if "天氣" in text:
        city = extract_city_from_text(user_id, text) or store.get_city(user_id) or "台中"
        w = get_weather_now(WEATHERAPI_KEY, city)
        return w if w else "目前天氣工具未回傳資料，請稍後再試。"

    if "日期" in text or "幾號" in text:
        return f"現在是 {now_str()}（{TZ}）。"

    if "新聞" in text:
        items = serper_news_top(SERPER_API_KEY, q="全球重大新聞", gl="tw", hl="zh-tw", num=8)
        return format_news(items)

    return "群組模式只支援查詢：日期/天氣/新聞。私聊可用完整功能（/help）。"


# -----------------------------
# 私聊：全功能
# -----------------------------
def handle_full(user_id: str, text: str, reply_token: str) -> str:
    # 0) 主要使用者
    primary = store.get_primary_user() or user_id

    # 1) 設定：城市/星座/晨報時間
    if text.startswith("設定城市"):
        city = normalize_city_text(text.replace("設定城市", "", 1).strip())
        if not city:
            return "請用：設定城市 台中"
        store.set_city(user_id, city)
        return f"好的，已設定城市為「{city}」。"

    if text.startswith("設定星座"):
        zodiac = text.replace("設定星座", "", 1).strip()
        if not zodiac:
            return "請用：設定星座 獅子座"
        store.set_zodiac(user_id, zodiac)
        return f"好的，已設定星座為「{zodiac}」。"

    if text.startswith("設定晨報時間"):
        hhmm = parse_time_hhmm(text.replace("設定晨報時間", "", 1))
        if not hhmm:
            return "請用：設定晨報時間 07:00"
        store.set_morning_time(user_id, hhmm)
        return f"好的，晨報時間已設為 {hhmm}。"

    # 2) 綁定家人
    if text.startswith("綁定 "):
        # 綁定 夫人 Uxxxx
        parts = text.split()
        if len(parts) < 3:
            return "請用：綁定 夫人 Uxxxxxxxx..."
        alias = parts[1].strip()
        target_id = parts[2].strip()
        store.set_alias(primary, alias, target_id)
        return f"好的，已綁定「{alias}」。"

    if text.startswith("解除綁定 "):
        alias = text.replace("解除綁定", "", 1).strip()
        if not alias:
            return "請用：解除綁定 夫人"
        store.remove_alias(primary, alias)
        return f"好的，已解除綁定「{alias}」。"

    if text in ("我的綁定", "查看綁定"):
        mapping = store.get_aliases(primary)
        if not mapping:
            return "目前沒有綁定任何稱呼。可用：綁定 夫人 Uxxxx"
        lines = ["目前綁定："]
        for k, v in mapping.items():
            lines.append(f"- {k} -> {v[:6]}...{v[-4:]}")
        return "\n".join(lines)

    # 3) 記憶：記住 / 記住(共享)
    if text.startswith("記住(共享)"):
        content = text.replace("記住(共享)", "", 1).strip()
        if not content:
            return "請用：記住(共享) 我早上習慣喝黑咖啡"
        store.add_memory(user_id, content, share=True)
        return "好的，已記住（共享給同帳號其他成員可查）。"

    if text.startswith("記住"):
        content = text.replace("記住", "", 1).strip()
        if not content:
            return "請用：記住 我早上習慣喝黑咖啡"
        store.add_memory(user_id, content, share=False)
        return "好的，已記住（僅你可用）。"

    if text in ("我記得什麼", "我們記得什麼", "記憶清單"):
        mems = store.list_memories(user_id)
        if not mems:
            return "目前沒有長期記憶。你可以用「記住 ...」新增。"
        lines = ["目前長期記憶："]
        for m in mems[:30]:
            flag = "共享" if m["share"] else "私密"
            lines.append(f"- ({flag}) {m['content']}")
        return "\n".join(lines)

    # 4) 提醒：相對/絕對
    m = re.match(r"^(\d+)\s*分鐘後提醒\s+(.+)$", text)
    if m:
        minutes = int(m.group(1))
        content = m.group(2).strip()
        task_id = scheduler.schedule_in_minutes(user_id, minutes, content)
        return f"好的，{minutes} 分鐘後提醒你。（任務ID: {task_id}）"

    m2 = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+提醒\s+(.+)$", text)
    if m2:
        dt = f"{m2.group(1)} {m2.group(2)}"
        content = m2.group(3).strip()
        task_id = scheduler.schedule_at(user_id, dt, content)
        return f"好的，已設定提醒：{dt}。（任務ID: {task_id}）"

    if text.startswith("取消提醒"):
        parts = text.split()
        if len(parts) < 2:
            return "請用：取消提醒 <任務ID>"
        ok = scheduler.cancel(parts[1].strip())
        return "好的，已取消。" if ok else "找不到這個任務ID。"

    if text in ("我的提醒", "提醒清單"):
        tasks = store.list_tasks(user_id)
        if not tasks:
            return "你目前沒有提醒。"
        lines = ["你的提醒："]
        for t in tasks[:50]:
            lines.append(f"- {t['run_at']} | {t['content']} | ID:{t['task_id']}")
        return "\n".join(lines)

    # 5) 轉傳訊息（主用戶可用）
    if text.startswith("傳訊給"):
        # 傳訊給夫人 xxx
        rest = text.replace("傳訊給", "", 1).strip()
        if " " not in rest:
            return "請用：傳訊給夫人 明天記得買奶粉"
        alias, msg = rest.split(" ", 1)
        alias = alias.strip()
        msg = msg.strip()

        mapping = store.get_aliases(primary)
        if alias not in mapping:
            return f"找不到稱呼「{alias}」。先用：綁定 {alias} Uxxxx"
        to_id = mapping[alias]
        push_text(to_id, f"（轉告）{msg}")
        return f"已傳訊給「{alias}」。"

    # 6) 查詢：日期/天氣/運勢/新聞/晨報
    if text in ("今天日期", "今天幾號", "今天是什麼日子"):
        today = datetime.now(APP_TZ).strftime("%Y-%m-%d")
        weekday = datetime.now(APP_TZ).strftime("%A")
        return f"今天是 {today}（{weekday}）。"

    if "天氣" in text:
        city = extract_city_from_text(user_id, text) or store.get_city(user_id) or "台中"
        # 需求：可能問今天/明天/3天
        if text.startswith("明天") or "明天" in text:
            fc = get_weather_forecast(WEATHERAPI_KEY, city, days=2)
            return fc if fc else "天氣工具未回傳資料，請稍後再試。"
        if text.startswith("3天") or "3天" in text:
            fc = get_weather_forecast(WEATHERAPI_KEY, city, days=3)
            return fc if fc else "天氣工具未回傳資料，請稍後再試。"
        # 預設：現在
        w = get_weather_now(WEATHERAPI_KEY, city)
        return w if w else "天氣工具未回傳資料，請稍後再試。"

    if text in ("今日運勢", "今天運勢", "運勢"):
        zodiac = store.get_zodiac(user_id) or "獅子座"
        return generate_horoscope_cn(GEMINI_API_KEY, zodiac, tz=TZ)

    if text in ("全球新聞", "全球新聞重點", "全球新聞重點整理", "新聞"):
        items = serper_news_top(SERPER_API_KEY, q="全球重大新聞", gl="tw", hl="zh-tw", num=8)
        return format_news(items)

    if text in ("晨報", "發送晨報", "今日晨報"):
        return build_morning_brief(user_id)

    # 7) 其它：交給「管家口氣」做自然回覆（但不胡編即時資料）
    # 這裡只做溫柔引導，不會捏造天氣/新聞。
    return (
        f"先生/女士，我收到你的訊息。\n"
        f"如果你要查詢：請輸入「今天天氣 / 全球新聞 / 晨報 / 今日運勢」。\n"
        f"如果你要我記住：請輸入「記住 ...」。\n"
        f"完整指令：輸入 /help"
    )


def build_morning_brief(user_id: str) -> str:
    city = store.get_city(user_id) or "台中"
    zodiac = store.get_zodiac(user_id) or "獅子座"

    weather = get_weather_now(WEATHERAPI_KEY, city) or "（天氣工具暫無資料）"
    horoscope = generate_horoscope_cn(GEMINI_API_KEY, zodiac, tz=TZ)
    news_items = serper_news_top(SERPER_API_KEY, q="全球重大新聞", gl="tw", hl="zh-tw", num=8)
    news_txt = format_news(news_items)

    return (
        f"☀️ 每日晨報（{datetime.now(APP_TZ).strftime('%Y-%m-%d')}）\n\n"
        f"【居住地天氣｜{city}】\n{weather}\n\n"
        f"【今日運勢｜{zodiac}】\n{horoscope}\n\n"
        f"【全球新聞重點（8則內）】\n{news_txt}\n"
    )


def format_news(items: list[dict]) -> str:
    if not items:
        return "（新聞工具暫無資料）"
    lines = []
    for i, it in enumerate(items[:8], 1):
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        date = (it.get("date") or "").strip()
        # 不貼長網址，避免 LINE 字數/截斷問題（你提過的痛點）
        lines.append(f"{i}. {title}（{source}{' / ' + date if date else ''}）")
    lines.append("\n想看哪則詳細？回我：看新聞 3")
    return "\n".join(lines)


def extract_city_from_text(user_id: str, text: str) -> str | None:
    # 例：明天台北天氣、3天高雄天氣
    m = re.search(r"([\u4e00-\u9fff]{2,6})\s*天氣", text)
    if m:
        city = normalize_city_text(m.group(1))
        # 避免把「今天」「明天」誤判成城市
        if city not in ("今天", "明天", "後天", "3天"):
            return city
    return None


# Render 用 gunicorn 啟動時會讀到 app:app
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
