import json
from datetime import datetime
from flask import Flask, request, abort, jsonify

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from config import CFG
from line_client import LineClient
from supa import Supa
from tools import Tools, RateLimitError
from utils import (
    parse_reminder_text,
    parse_weather_query,
    parse_news_days,
    is_date_time_query,
    is_help_query,
    is_morning_report_query,
    is_horoscope_query,
    is_news_query,
    is_memory_save_query,
    extract_memory_scope_and_text,
    is_memory_list_query,
    is_send_message_query,
    parse_send_message,
    is_setting_city_query,
    parse_setting_city,
    is_setting_zodiac_query,
    parse_setting_zodiac,
    is_setting_morning_time_query,
    parse_setting_morning_time,
    is_bind_query,
    parse_bind,
    is_unbind_query,
    parse_unbind,
    is_my_bindings_query,
    is_nickname_query,
    parse_nickname,
    safe_user_text,
)

app = Flask(__name__)

line = LineClient(CFG.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CFG.LINE_CHANNEL_SECRET)

SUPA_BOOT_ERROR = None
GEMINI_BOOT_ERROR = None

supa = None
tools = None

try:
    supa = Supa(CFG.SUPABASE_URL, CFG.SUPABASE_SERVICE_ROLE_KEY)
except Exception as e:
    SUPA_BOOT_ERROR = str(e)

try:
    tools = Tools(
        gemini_api_key=CFG.GEMINI_API_KEY,
        serper_api_key=CFG.SERPER_API_KEY,
        weatherapi_key=CFG.WEATHERAPI_KEY,
        tz=CFG.TZ,
        news_hl=CFG.SERPER_HL,
        news_gl=CFG.SERPER_GL,
    )
except Exception as e:
    GEMINI_BOOT_ERROR = str(e)


@app.get("/health")
def health():
    ok = True
    issues = []
    if SUPA_BOOT_ERROR:
        ok = False
        issues.append(f"SUPABASE: {SUPA_BOOT_ERROR}")
    if GEMINI_BOOT_ERROR:
        ok = False
        issues.append(f"GEMINI: {GEMINI_BOOT_ERROR}")
    if not CFG.LINE_CHANNEL_ACCESS_TOKEN or not CFG.LINE_CHANNEL_SECRET:
        ok = False
        issues.append("LINE env missing")

    return jsonify(
        {
            "ok": ok,
            "time": datetime.now(CFG.TZ).isoformat(timespec="seconds"),
            "issues": issues,
            "version": "Saturday V7.2",
        }
    ), (200 if ok else 500)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    if not signature:
        abort(400)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        # webhook 不能 500，否則 LINE 會一直重送 / 使用者覺得完全沒回
        print(f"[callback] handler crashed: {e}")
        return "OK"
    return "OK"


@app.route("/tick", methods=["GET", "POST"])
def tick():
    # 用 UptimeRobot 每分鐘打一發：/tick?k=你的密鑰
    k = request.args.get("k", "").strip()
    if not CFG.TICK_SECRET or k != CFG.TICK_SECRET:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    if not supa:
        return jsonify({"ok": False, "error": f"supabase down: {SUPA_BOOT_ERROR}"}), 200

    now = datetime.now(CFG.TZ)
    out = {"ok": True, "now": now.isoformat(timespec="seconds"), "sent": 0, "morning": 0, "errors": []}

    # 1) 到點提醒
    try:
        due = supa.fetch_due_reminders(now)
        for r in due[:20]:
            try:
                text = f"⏰ 提醒：{r['message']}\n（建立者：{supa.label_of(r['created_by'], r['tenant_id'])}）"
                line.push_text(r["target_user_id"], text)
                supa.mark_reminder_sent(r["id"], now)
                out["sent"] += 1
            except Exception as e:
                out["errors"].append(f"reminder:{r.get('id')}:{e}")
    except Exception as e:
        out["errors"].append(f"fetch_due:{e}")

    # 2) 晨報（每位使用者每天一次）
    try:
        hhmm = now.strftime("%H:%M")
        today = now.date().isoformat()
        targets = supa.list_morning_targets(hhmm, today)  # RPC
        for p in targets[:8]:  # 防止一次送太多人拖慢
            user_id = p["user_id"]
            tenant_id = p["tenant_id"]
            try:
                ctx = supa.get_context(user_id, CFG.DEFAULT_OWNER_ID, CFG.DEFAULT_WIFE_ID)
                prefs = supa.get_user_prefs(user_id, tenant_id)
                report = tools.build_morning_report(ctx=ctx, prefs=prefs, now=now)
                line.push_text(user_id, report)
                supa.set_last_morning_sent(user_id, tenant_id, today)
                out["morning"] += 1
            except RateLimitError as e:
                # 搜尋額度用完：要提示
                line.push_text(user_id, f"☀️ 晨報：搜尋額度已達上限（免費方案常見）。\n細節：{e}")
                supa.set_last_morning_sent(user_id, tenant_id, today)
                out["morning"] += 1
            except Exception as e:
                out["errors"].append(f"morning:{user_id}:{e}")
    except Exception as e:
        out["errors"].append(f"list_morning_targets:{e}")

    return jsonify(out), 200


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = safe_user_text(event.message.text)

    if not text:
        return

    # 任何錯都不能讓 webhook 500
    try:
        if not supa:
            line.reply_text(event.reply_token, f"先生/女士，記憶庫連線失敗：{SUPA_BOOT_ERROR}")
            return
        if not tools:
            line.reply_text(event.reply_token, f"先生/女士，大腦（Gemini）連線失敗：{GEMINI_BOOT_ERROR}")
            return

        ctx = supa.get_context(user_id, CFG.DEFAULT_OWNER_ID, CFG.DEFAULT_WIFE_ID)
        tenant_id = ctx["tenant_id"]
        honorific = ctx["honorific"]

        # ===== 1) help =====
        if is_help_query(text):
            line.reply_text(event.reply_token, tools.help_text())
            return

        # ===== 2) 綁定/解除 =====
        if is_bind_query(text) and ctx["role"] in ("owner",):
            label, target_id = parse_bind(text)
            supa.bind_member(tenant_id, label, target_id, added_by=user_id)
            line.reply_text(event.reply_token, f"好的，{honorific}。已綁定「{label}」✅")
            return

        if is_unbind_query(text) and ctx["role"] in ("owner",):
            label = parse_unbind(text)
            ok = supa.unbind_member(tenant_id, label)
            line.reply_text(event.reply_token, f"好的，{honorific}。已解除「{label}」✅" if ok else f"找不到「{label}」的綁定。")
            return

        if is_my_bindings_query(text):
            s = supa.list_members_text(tenant_id, viewer_id=user_id)
            line.reply_text(event.reply_token, s)
            return

        # ===== 3) 暱稱（每個人自己定義怎麼叫別人）=====
        if is_nickname_query(text):
            target_name, nickname = parse_nickname(text)
            target_id = supa.resolve_target_user_id(tenant_id, viewer_id=user_id, name=target_name)
            if not target_id:
                line.reply_text(event.reply_token, f"我找不到「{target_name}」是誰。你可以先用「綁定」或改用對方的 user_id。")
                return
            supa.set_nickname(tenant_id, viewer_id=user_id, target_user_id=target_id, nickname=nickname)
            line.reply_text(event.reply_token, f"好的，{honorific}。以後你提到「{nickname}」我就知道是他/她。")
            return

        # ===== 4) 設定：城市 / 星座 / 晨報時間 =====
        if is_setting_city_query(text):
            city = parse_setting_city(text)
            supa.set_city(user_id, tenant_id, city)
            line.reply_text(event.reply_token, f"好的，{honorific}。已設定城市為「{city}」。")
            return

        if is_setting_zodiac_query(text):
            zodiac = parse_setting_zodiac(text)
            supa.set_zodiac(user_id, tenant_id, zodiac)
            line.reply_text(event.reply_token, f"好的，{honorific}。已設定星座為「{zodiac}」。")
            return

        if is_setting_morning_time_query(text):
            hhmm = parse_setting_morning_time(text)
            supa.set_morning_time(user_id, tenant_id, hhmm)
            line.reply_text(event.reply_token, f"好的，{honorific}。晨報時間已設定為 {hhmm}。")
            return

        # ===== 5) 傳訊 =====
        if is_send_message_query(text):
            target_name, msg = parse_send_message(text)
            target_id = supa.resolve_target_user_id(tenant_id, viewer_id=user_id, name=target_name)
            if not target_id:
                line.reply_text(event.reply_token, f"我找不到「{target_name}」是誰。先用「綁定」或讓對方先跟我說句話。")
                return

            # 互傳允許：同 tenant 成員之間都可以
            if not supa.is_member(tenant_id, user_id) or not supa.is_member(tenant_id, target_id):
                line.reply_text(event.reply_token, "目前你或對方不在同一個成員清單內，無法轉告。")
                return

            from_label = supa.label_of(user_id, tenant_id)
            to_label = supa.label_of(target_id, tenant_id, viewer_id=target_id)
            line.push_text(target_id, f"📩 轉告（來自 {from_label}）：\n{msg}")
            line.reply_text(event.reply_token, f"好的，{honorific}。我已轉告給「{to_label}」。")
            return

        # ===== 6) 提醒 =====
        r = parse_reminder_text(text, now=datetime.now(CFG.TZ))
        if r:
            # target 可用「提醒夫人...」這種寫法；沒寫就提醒自己
            target_id = None
            if r.get("target_name"):
                target_id = supa.resolve_target_user_id(tenant_id, viewer_id=user_id, name=r["target_name"])
                if not target_id:
                    line.reply_text(event.reply_token, f"我找不到「{r['target_name']}」是誰。")
                    return
            else:
                target_id = user_id

            if not supa.is_member(tenant_id, user_id):
                # 第一次說話的人，先視為 owner/member
                supa.ensure_member(tenant_id, user_id)

            rid = supa.create_reminder(
                tenant_id=tenant_id,
                created_by=user_id,
                target_user_id=target_id,
                due_at=r["due_at"],
                message=r["message"],
            )
            to_label = supa.label_of(target_id, tenant_id, viewer_id=target_id)
            line.reply_text(
                event.reply_token,
                f"好的，{honorific}。我會在 {r['due_at_str']} 提醒「{to_label}」：{r['message']}\n任務ID：{rid}",
            )
            return

        # ===== 7) 日期時間 =====
        if is_date_time_query(text):
            now = datetime.now(CFG.TZ)
            if "比特幣" in text or "BTC" in text.upper() or "台積電" in text or "TSMC" in text.upper():
                # 這類走新聞/搜尋（避免亂報）
                try:
                    ans = tools.answer_market_quick(text, now=now)
                    line.reply_text(event.reply_token, ans)
                except RateLimitError as e:
                    line.reply_text(event.reply_token, f"搜尋額度已達上限（免費方案常見）。\n{e}")
                return

            line.reply_text(event.reply_token, f"台灣時間：{now.strftime('%Y-%m-%d %H:%M:%S')}（{CFG.TZ.key}）")
            return

        # ===== 8) 天氣 =====
        if "天氣" in text:
            prefs = supa.get_user_prefs(user_id, tenant_id)
            wq = parse_weather_query(text, default_city=prefs.get("city") or "")
            if not wq.get("city"):
                line.reply_text(event.reply_token, f"好的，{honorific}。你要查哪個城市的天氣？（例如：台中、台北）")
                return
            try:
                ans = tools.answer_weather(wq, now=datetime.now(CFG.TZ))
                line.reply_text(event.reply_token, ans)
            except RateLimitError as e:
                line.reply_text(event.reply_token, f"（天氣服務或搜尋額度已達上限）\n{e}")
            return

        # ===== 9) 運勢 =====
        if is_horoscope_query(text):
            prefs = supa.get_user_prefs(user_id, tenant_id)
            zodiac = prefs.get("zodiac") or ""
            if not zodiac:
                line.reply_text(event.reply_token, f"好的，{honorific}。你還沒設定星座，輸入：設定星座 獅子座")
                return
            try:
                ans = tools.answer_horoscope(zodiac=zodiac, now=datetime.now(CFG.TZ))
                line.reply_text(event.reply_token, ans)
            except RateLimitError as e:
                line.reply_text(event.reply_token, f"搜尋額度已達上限（免費方案常見）。\n{e}")
            return

        # ===== 10) 新聞 =====
        if is_news_query(text):
            try:
                days = parse_news_days(text)  # 今日/近7日/近3日...
                ans = tools.answer_news(days=days, now=datetime.now(CFG.TZ), session_user_id=user_id)
                line.reply_text(event.reply_token, ans)
            except RateLimitError as e:
                line.reply_text(event.reply_token, f"搜尋額度已達上限（免費方案常見）。\n{e}")
            return

        # ===== 11) 晨報（手動）=====
        if is_morning_report_query(text):
            prefs = supa.get_user_prefs(user_id, tenant_id)
            try:
                report = tools.build_morning_report(ctx=ctx, prefs=prefs, now=datetime.now(CFG.TZ))
                line.reply_text(event.reply_token, report)
            except RateLimitError as e:
                line.reply_text(event.reply_token, f"☀️ 晨報：搜尋額度已達上限（免費方案常見）。\n{e}")
            return

        # ===== 12) 記憶：存 =====
        if is_memory_save_query(text):
            scope, mem = extract_memory_scope_and_text(text)
            if not mem:
                line.reply_text(event.reply_token, f"好的，{honorific}。你要我記住什麼？例如：記住 我早上愛喝冰美式")
                return
            supa.save_memory(tenant_id=tenant_id, user_id=user_id, scope=scope, content=mem)
            line.reply_text(event.reply_token, f"好的，{honorific}。已記住（{'共享' if scope=='shared' else '僅你可用'}）。")
            return

        # ===== 13) 記憶：列出 =====
        if is_memory_list_query(text):
            mems = supa.list_recent_memories(tenant_id=tenant_id, user_id=user_id, limit=20)
            if not mems:
                line.reply_text(event.reply_token, f"好的，{honorific}。目前沒有可用記憶。你可以用「記住 ...」建立。")
                return
            out = [f"【我記得的事（最多20筆）】"]
            for m in mems:
                tag = "共享" if m["scope"] == "shared" else "私密"
                out.append(f"- ({tag}) {m['content']}")
            line.reply_text(event.reply_token, "\n".join(out))
            return

        # ===== 14) 一般聊天（用 LLM + 記憶檢索）=====
        # 短期：最近 6 回合
        turns = supa.load_recent_turns(tenant_id=tenant_id, user_id=user_id, limit=6)

        # 長期：FTS/相似度檢索（共享+私密）
        memories = supa.search_memories(tenant_id=tenant_id, user_id=user_id, query=text, limit=8)

        reply = tools.chat(
            ctx=ctx,
            text=text,
            now=datetime.now(CFG.TZ),
            turns=turns,
            memories=memories,
        )

        line.reply_text(event.reply_token, reply)

        # 存短期回合（永遠存，供連貫）
        supa.save_turn(tenant_id=tenant_id, user_id=user_id, role=ctx["role"], user_text=text, bot_text=reply)

    except Exception as e:
        print(f"[handle_message] crashed: {e}")
        try:
            line.reply_text(event.reply_token, "先生/女士，我剛剛出了點狀況，但我還在。你可以再說一次，或輸入 /help。")
        except Exception:
            pass
