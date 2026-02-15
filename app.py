import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, PushMessageRequest
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from config import CFG
from nlp import detect_intent
from prompts import build_chat_prompt, build_tool_prompt
from scheduler import start_scheduler
from supa import Supa
from tools import Tools, RateLimitError


app = Flask(__name__)

# LINE
configuration = Configuration(access_token=CFG.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CFG.LINE_CHANNEL_SECRET)

# Supabase + Tools
supa = Supa(CFG.SUPABASE_URL, CFG.SUPABASE_SERVICE_ROLE_KEY)
tools = Tools(
    gemini_api_key=CFG.GEMINI_API_KEY,
    serper_api_key=CFG.SERPER_API_KEY,
    weatherapi_key=CFG.WEATHERAPI_KEY,
    tz_name=CFG.TZ
)


def reply_text(reply_token: str, text: str):
    # LINE 單則訊息過長會被截斷/失敗，保守切段
    chunks = []
    text = text.strip()
    while len(text) > 4500:
        cut = text[:4500]
        chunks.append(cut)
        text = text[4500:]
    chunks.append(text)

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=c) for c in chunks if c.strip()]
            )
        )


def push_text(to_user_id: str, text: str):
    # Push 失敗時要能抓到錯誤（未加好友/封鎖/權限）
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=to_user_id,
                messages=[TextMessage(text=text.strip()[:5000])]
            )
        )


@app.get("/health")
def health():
    return "ok", 200


@app.route("/callback", methods=["POST"])
def callback():
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
    if not text:
        return

    # 解析租戶/角色/稱呼（多成員 & 每人不同稱呼）
    ctx = supa.get_context(user_id, default_owner_id=CFG.DEFAULT_OWNER_ID, default_wife_id=CFG.DEFAULT_WIFE_ID)
    tenant_id = ctx["tenant_id"]
    profile = ctx["profile"]
    address_me = profile.get("address_me") or ctx["default_address_me"]
    speaker_label = ctx["speaker_label"]  # 用於 prompt：先生/夫人/成員名

    # 指令/自然語句意圖
    intent = detect_intent(text, tz_name=CFG.TZ)

    try:
        # 0) /help
        if intent.name == "HELP":
            reply_text(event.reply_token, CFG.HELP_TEXT)
            return

        # 1) 查自己的 ID（方便主要使用者綁定）
        if intent.name == "MY_ID":
            reply_text(event.reply_token, f"你的 LINE userId：\n{user_id}")
            return

        # 2) 綁定/解除綁定/看綁定
        if intent.name == "BIND":
            # 只有 owner 可以綁定（避免亂綁）
            if profile.get("role") != "owner":
                reply_text(event.reply_token, f"{address_me}，只有主要使用者（owner）可以執行綁定。")
                return
            ok, msg = supa.bind_contact(
                tenant_id=tenant_id,
                canonical_name=intent.args["name"],
                line_user_id=intent.args["user_id"]
            )
            reply_text(event.reply_token, msg)
            return

        if intent.name == "UNBIND":
            if profile.get("role") != "owner":
                reply_text(event.reply_token, f"{address_me}，只有主要使用者（owner）可以解除綁定。")
                return
            msg = supa.unbind_contact(tenant_id=tenant_id, canonical_name=intent.args["name"])
            reply_text(event.reply_token, msg)
            return

        if intent.name == "LIST_BINDINGS":
            txt = supa.list_bindings_text(tenant_id=tenant_id, viewer_user_id=user_id)
            reply_text(event.reply_token, txt)
            return

        # 3) 個人化稱呼：我稱呼 <對象> 為 <新稱呼>
        if intent.name == "SET_NICKNAME":
            msg = supa.set_nickname(
                tenant_id=tenant_id,
                viewer_user_id=user_id,
                target_name=intent.args["target_name"],
                nickname=intent.args["nickname"]
            )
            reply_text(event.reply_token, msg)
            return

        # 4) 設定：城市/星座/晨報時間/晨報開關
        if intent.name == "SET_CITY":
            supa.set_profile(tenant_id, user_id, {"home_city": intent.args["city"]})
            reply_text(event.reply_token, f"好的，{address_me}，已設定城市為「{intent.args['city']}」。")
            return

        if intent.name == "SET_ZODIAC":
            supa.set_profile(tenant_id, user_id, {"zodiac_sign": intent.args["sign"]})
            reply_text(event.reply_token, f"好的，{address_me}，已設定星座為「{intent.args['sign']}」。")
            return

        if intent.name == "SET_MORNING_TIME":
            supa.set_profile(tenant_id, user_id, {"morning_time": intent.args["time"], "morning_enabled": True})
            reply_text(event.reply_token, f"好的，{address_me}，晨報時間已設為 {intent.args['time']}（已開啟）。")
            return

        if intent.name == "MORNING_ON":
            supa.set_profile(tenant_id, user_id, {"morning_enabled": True})
            reply_text(event.reply_token, f"好的，{address_me}，晨報已開啟。")
            return

        if intent.name == "MORNING_OFF":
            supa.set_profile(tenant_id, user_id, {"morning_enabled": False})
            reply_text(event.reply_token, f"好的，{address_me}，晨報已關閉。")
            return

        # 5) 傳訊：傳訊給<稱呼> <內容>
        if intent.name == "SEND_MESSAGE":
            target_user_id, target_display = supa.resolve_target_user(tenant_id, viewer_user_id=user_id, target_text=intent.args["target"])
            if not target_user_id:
                reply_text(event.reply_token, f"{address_me}，我找不到「{intent.args['target']}」。你可以先用「我的綁定」確認。")
                return
            # 使用收件人視角稱呼發訊者
            sender_label_for_receiver = supa.get_sender_label_for_receiver(tenant_id, sender_user_id=user_id, receiver_user_id=target_user_id)
            try:
                push_text(target_user_id, f"📩 轉告（來自 {sender_label_for_receiver}）：\n{intent.args['message']}")
                reply_text(event.reply_token, f"好的，{address_me}，已替你轉告給「{target_display}」。")
            except Exception as e:
                reply_text(event.reply_token, f"{address_me}，我嘗試推播但失敗了（對方可能未加好友/封鎖/權限不足）。\n錯誤：{str(e)[:200]}")
            return

        # 6) 提醒：自然語句（兩分鐘後提醒… / 2026-02-14 18:30 提醒… / 提醒夫人…）
        if intent.name == "REMIND":
            target = intent.args.get("target")  # 可能是 None
            to_user_id = user_id
            to_display = address_me

            if target:
                resolved_id, resolved_name = supa.resolve_target_user(tenant_id, viewer_user_id=user_id, target_text=target)
                if not resolved_id:
                    reply_text(event.reply_token, f"{address_me}，我找不到「{target}」。你可以先用「我的綁定」確認。")
                    return
                to_user_id, to_display = resolved_id, resolved_name

            reminder_id = supa.create_reminder(
                tenant_id=tenant_id,
                created_by=user_id,
                to_user_id=to_user_id,
                title=intent.args["title"],
                due_at=intent.args["due_at"]
            )
            reply_text(event.reply_token, f"好的，{address_me}。\n✅ 已建立提醒（ID: {reminder_id}）\n對象：{to_display}\n時間：{intent.args['due_at']}\n內容：{intent.args['title']}")
            return

        if intent.name == "LIST_REMINDERS":
            txt = supa.list_reminders_text(tenant_id=tenant_id, user_id=user_id)
            reply_text(event.reply_token, txt)
            return

        if intent.name == "CANCEL_REMINDER":
            msg = supa.cancel_reminder(tenant_id=tenant_id, user_id=user_id, reminder_id=intent.args["id"])
            reply_text(event.reply_token, msg)
            return

        # 7) 記憶：記住 / 記住(共享)
        if intent.name == "REMEMBER":
            scope = intent.args["scope"]  # private/shared
            content = intent.args["content"].strip()

            # 共享記憶：首次確認一次（不每次都問）
            if scope == "shared" and not profile.get("share_confirmed"):
                pending_id = supa.create_pending_share(tenant_id, user_id, content)
                reply_text(
                    event.reply_token,
                    f"{address_me}，你要把這條記憶設為【家庭共享】嗎？（只問這一次）\n\n"
                    f"內容：{content}\n\n"
                    f"回覆其一：\n"
                    f"1) 共享\n"
                    f"2) 私密\n"
                    f"（待確認ID: {pending_id}）"
                )
                return

            # 寫入長期記憶（explicit 記住一定寫）
            supa.save_long_memory(tenant_id, user_id, content, scope=scope, embed_fn=tools.embed)
            reply_text(event.reply_token, f"好的，{address_me}，已記住（{ '家庭共享' if scope=='shared' else '僅你可用' }）。")
            return

        # 8) 共享確認：共享 / 私密
        if intent.name == "CONFIRM_SHARE":
            ok, msg = supa.confirm_pending_share(tenant_id, user_id, decision=intent.args["decision"], embed_fn=tools.embed)
            if ok and intent.args["decision"] == "shared":
                supa.set_profile(tenant_id, user_id, {"share_confirmed": True})
            reply_text(event.reply_token, msg)
            return

        # 9) 我記得什麼（列出長期記憶）
        if intent.name == "LIST_MEMORY":
            txt = supa.list_long_memories_text(tenant_id=tenant_id, requester_id=user_id)
            reply_text(event.reply_token, txt)
            return

        # 10) 查詢型工具：日期/天氣/預報/運勢/新聞/晨報
        tool_payload = None

        if intent.name == "DATE_NOW":
            now_str = tools.now_text()
            reply_text(event.reply_token, now_str)
            return

        if intent.name == "WEATHER_NOW":
            city = intent.args.get("city") or profile.get("home_city")
            if not city:
                reply_text(event.reply_token, f"{address_me}，你還沒設定城市。先說「設定城市 台中」即可。")
                return
            w = tools.weather_now(city)
            reply_text(event.reply_token, tools.format_weather_now(city, w))
            return

        if intent.name == "WEATHER_FORECAST":
            city = intent.args.get("city") or profile.get("home_city")
            days = intent.args.get("days", 3)
            if not city:
                reply_text(event.reply_token, f"{address_me}，你還沒設定城市。先說「設定城市 台中」即可。")
                return
            f = tools.weather_forecast(city, days=days)
            reply_text(event.reply_token, tools.format_weather_forecast(city, f, days=days))
            return

        if intent.name == "HOROSCOPE_TODAY":
            sign = profile.get("zodiac_sign")
            if not sign:
                reply_text(event.reply_token, f"{address_me}，你還沒設定星座。先說「設定星座 獅子座」即可。")
                return
            try:
                h = tools.horoscope_today(sign)
                reply_text(event.reply_token, tools.format_horoscope(sign, h))
            except RateLimitError as e:
                reply_text(event.reply_token, f"{address_me}，運勢搜尋遇到額度/限流：{e}")
            return

        if intent.name == "NEWS":
            try:
                items = tools.news(
                    query=intent.args.get("query") or "全球重大新聞",
                    days=intent.args.get("days", 7),
                    limit=intent.args.get("limit", 8)
                )
                reply_text(event.reply_token, tools.format_news(items, ask_detail=True))
            except RateLimitError as e:
                reply_text(event.reply_token, f"{address_me}，新聞搜尋遇到額度/限流：{e}")
            return

        if intent.name == "NEWS_DETAIL":
            try:
                idx = intent.args["index"]
                last = supa.get_last_news_cache(tenant_id, user_id)
                if not last or idx < 1 or idx > len(last):
                    reply_text(event.reply_token, f"{address_me}，我找不到那則新聞。你可以先說「全球新聞」。")
                    return
                item = last[idx - 1]
                reply_text(event.reply_token, tools.format_news_detail(item))
            except Exception as e:
                reply_text(event.reply_token, f"{address_me}，取得新聞詳細失敗：{str(e)[:200]}")
            return

        if intent.name == "MORNING_NOW":
            # 立刻生成晨報（不等排程）
            city = profile.get("home_city") or "（未設定）"
            sign = profile.get("zodiac_sign") or "（未設定）"
            try:
                morning = tools.build_morning_brief(city=city, zodiac=sign, news_days=7, news_limit=8)
                # 把新聞 cache 起來讓「看新聞 3」可用
                if morning.get("news_items"):
                    supa.set_last_news_cache(tenant_id, user_id, morning["news_items"])
                # 節日提示
                holiday_line = tools.today_holiday_line()
                text_out = tools.format_morning(morning, holiday_line=holiday_line)
                reply_text(event.reply_token, text_out)
            except RateLimitError as e:
                reply_text(event.reply_token, f"{address_me}，晨報搜尋遇到額度/限流：{e}")
            return

        # 11) 一般聊天（像管家）：帶短期記憶 + 長期向量聯想（私密/共享隔離）
        # 短期記憶
        short_mem = supa.load_short_memory(tenant_id=tenant_id, user_id=user_id, limit=6)
        # 長期記憶（向量搜尋）
        long_mem = supa.search_long_memory(
            tenant_id=tenant_id,
            requester_id=user_id,
            query=text,
            embed_fn=tools.embed,
            match_threshold=0.25,
            match_count=5
        )

        prompt = build_chat_prompt(
            tz_name=CFG.TZ,
            address_me=address_me,
            speaker_label=speaker_label,
            short_memory=short_mem,
            long_memory=long_mem,
            user_text=text
        )

        answer = tools.llm_generate(prompt)
        reply_text(event.reply_token, answer)

        # 寫短期記憶（每次都寫），並做簡單修剪避免爆表
        supa.save_short_memory(tenant_id, user_id, text, answer, keep_last=60)

        # 隱性長期記憶（只在像「偏好/習慣/重要資訊」且不涉敏感財務健康時才寫）
        # 你要的「自然理解」：用規則先篩，再小判斷一次（不會每句都慢）
        supa.maybe_auto_save_long(
            tenant_id=tenant_id,
            user_id=user_id,
            user_text=text,
            assistant_text=answer,
            embed_fn=tools.embed,
            llm_judge_fn=tools.llm_judge_memory
        )

    except Exception as e:
        # 任何錯誤都要回覆，避免「沒下文」
        reply_text(event.reply_token, f"{address_me}，我這邊剛剛出了一點狀況，已記錄。\n錯誤：{str(e)[:300]}")


# 啟動排程器（提醒/晨報/節日）
# 注意：請在 Render 用 workers=1，避免多個 scheduler 重複發送
_scheduler_started = False
if not _scheduler_started:
    try:
        start_scheduler(
            tz_name=CFG.TZ,
            supa=supa,
            push_fn=push_text,
            tools=tools
        )
        _scheduler_started = True
    except Exception as _e:
        # 不讓排程失敗影響 webhook
        print(f"[scheduler] start failed: {_e}")


if __name__ == "__main__":
    # 本機測試用；Render 請用 gunicorn start command
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
