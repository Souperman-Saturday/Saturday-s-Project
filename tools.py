import time
import json
import requests
import google.generativeai as genai
from datetime import datetime, timedelta
from lunardate import LunarDate

from utils import (
    normalize_tw_city,
    compact_date_str,
    pick_recent_news_items,
)

class RateLimitError(Exception):
    pass

class Tools:
    def __init__(self, gemini_api_key: str, serper_api_key: str, weatherapi_key: str, tz, news_hl="zh-tw", news_gl="tw"):
        self.tz = tz
        self.serper_key = serper_api_key
        self.weather_key = weatherapi_key
        self.news_hl = news_hl
        self.news_gl = news_gl

        genai.configure(api_key=gemini_api_key)
        self.llm = genai.GenerativeModel("gemini-3-pro-preview")

        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "Saturday-V7.2"})

        # 簡易 session：記住上一輪新聞清單，支援「看新聞 3」
        self.news_sessions = {}  # user_id -> {"ts":..., "items":[...]}

    def help_text(self) -> str:
        return (
            "【Saturday 指令表（V7.2）】\n\n"
            "一、設定\n"
            "- 設定城市 台中\n"
            "- 設定星座 獅子座\n"
            "- 設定晨報時間 07:00\n\n"
            "二、成員（主人可綁定/解除）\n"
            "- 綁定 夫人 Uxxxxxxxx...\n"
            "- 解除綁定 夫人\n"
            "- 我的綁定\n"
            "- 我稱呼 夫人 為 老婆（每個人可自訂怎麼叫對方）\n\n"
            "三、記憶（長期）\n"
            "- 記住 我早上最愛喝冰美式\n"
            "- 記住(共享) 我們家週末都去公園\n"
            "- 我記得什麼 / 我記得甚麼\n\n"
            "四、提醒（自然語句可）\n"
            "- 2分鐘後提醒 喝水\n"
            "- 兩分鐘後提醒喝水\n"
            "- 2026-02-20 18:30 提醒 帶尿布\n"
            "- 2分鐘後提醒夫人 買晚餐\n\n"
            "五、查詢\n"
            "- 今天天氣 / 明天台中天氣 / 3天台中天氣 / 未來三天天氣\n"
            "- 今日運勢 / 我的今日運勢\n"
            "- 全球新聞 / 近7日新聞 / 近3日新聞\n"
            "- 晨報\n"
            "- 今天日期 / 現在時間 / 比特幣最新價格 / 台積電最新股價\n\n"
            "六、互傳（同一成員清單內可互傳）\n"
            "- 傳訊給夫人 明天記得買奶粉\n"
            "- 傳訊給先生 我到家了\n"
        )

    # ---------------- SERPER ----------------
    def _serper_post(self, endpoint: str, payload: dict, timeout=12) -> dict:
        url = f"https://google.serper.dev/{endpoint}"
        headers = {"X-API-KEY": self.serper_key, "Content-Type": "application/json"}
        r = self.http.post(url, headers=headers, data=json.dumps(payload), timeout=timeout)
        if r.status_code == 429:
            raise RateLimitError("Serper 429：已達免費額度或太頻繁。")
        if r.status_code >= 400:
            raise RateLimitError(f"Serper {r.status_code}：{r.text[:200]}")
        return r.json()

    def serper_news(self, q: str, tbs: str, num: int = 8) -> list:
        data = self._serper_post(
            "news",
            {
                "q": q,
                "num": num,
                "hl": self.news_hl,
                "gl": self.news_gl,
                "tbs": tbs,  # 重要：qdr:d / qdr:w
            },
        )
        items = data.get("news") or []
        return items

    def serper_search(self, q: str, tbs: str, num: int = 8) -> list:
        data = self._serper_post(
            "search",
            {
                "q": q,
                "num": num,
                "hl": self.news_hl,
                "gl": self.news_gl,
                "tbs": tbs,
            },
        )
        items = data.get("organic") or []
        return items

    # ---------------- WEATHERAPI ----------------
    def weather_current(self, city: str) -> dict:
        city_q = normalize_tw_city(city)
        url = "https://api.weatherapi.com/v1/current.json"
        r = self.http.get(url, params={"key": self.weather_key, "q": city_q, "lang": "zh"}, timeout=12)
        if r.status_code == 429:
            raise RateLimitError("WeatherAPI 429：已達免費額度。")
        if r.status_code >= 400:
            raise RateLimitError(f"WeatherAPI {r.status_code}：{r.text[:200]}")
        return r.json()

    def weather_forecast(self, city: str, days: int) -> dict:
        # WeatherAPI 免費通常 3 天；超過就提示
        if days > 3:
            days = 3
        city_q = normalize_tw_city(city)
        url = "https://api.weatherapi.com/v1/forecast.json"
        r = self.http.get(url, params={"key": self.weather_key, "q": city_q, "days": days, "lang": "zh"}, timeout=12)
        if r.status_code == 429:
            raise RateLimitError("WeatherAPI 429：已達免費額度。")
        if r.status_code >= 400:
            raise RateLimitError(f"WeatherAPI {r.status_code}：{r.text[:200]}")
        return r.json()

    def answer_weather(self, wq: dict, now: datetime) -> str:
        city = wq["city"]
        mode = wq.get("mode", "current")  # current/forecast
        days = int(wq.get("days", 1))

        if mode == "current":
            data = self.weather_current(city)
            loc = data["location"]["name"]
            cur = data["current"]
            cond = cur["condition"]["text"]
            temp = cur["temp_c"]
            feels = cur["feelslike_c"]
            hum = cur["humidity"]
            wind = cur["wind_kph"]
            return f"{loc}：{cond}\n氣溫 {temp}°C（體感 {feels}°C）｜濕度 {hum}%｜風速 {wind} km/h"

        # forecast
        data = self.weather_forecast(city, days=max(days, 1))
        loc = data["location"]["name"]
        out = [f"{loc} 未來 {min(days,3)} 天："]
        for d in data["forecast"]["forecastday"]:
            date = d["date"]
            day = d["day"]
            cond = day["condition"]["text"]
            lo = day["mintemp_c"]
            hi = day["maxtemp_c"]
            rain = day.get("daily_chance_of_rain", "")
            rain_s = f"｜降雨機率 {rain}%" if str(rain) != "" else ""
            out.append(f"- {date}：{cond}｜{lo}~{hi}°C{rain_s}")
        if days > 3:
            out.append("（WeatherAPI 免費方案最多提供 3 天預報；若需要 7~14 天需升級方案）")
        return "\n".join(out)

    # ---------------- HOROSCOPE ----------------
    def answer_horoscope(self, zodiac: str, now: datetime) -> str:
        # 用搜尋（qdr:d）避免跑出老文
        q = f"{zodiac} 今日運勢"
        items = self.serper_search(q, tbs="qdr:d", num=6)
        if not items:
            # fallback 7d
            items = self.serper_search(q, tbs="qdr:w", num=6)
        if not items:
            return "（我暫時找不到可靠來源的今日運勢，晚點再試）"

        # 不做長摘要，避免慢；直接挑 2~3 個片段整理
        picks = items[:3]
        lines = [f""]
        for i, it in enumerate(picks, 1):
            title = it.get("title", "").strip()
            snippet = (it.get("snippet", "") or "").strip()
            lines.append(f"{i}. {title}\n   - {snippet}")
        lines.append("（想看更細：回我「再詳細一點」我會再幫你整理）")
        return "\n".join(lines)

    # ---------------- NEWS ----------------
    def answer_news(self, days: int, now: datetime, session_user_id: str) -> str:
        # 你的規則：今日沒有重大 -> 才退到 7 日內
        if days <= 1:
            tbs = "qdr:d"
        elif days <= 7:
            tbs = "qdr:w"
        else:
            # Serper 主要就 qdr:w，超過就提醒
            tbs = "qdr:w"

        q = "全球 重大 新聞 重點 整理"
        items = self.serper_news(q, tbs=tbs, num=10)

        # 避免出現 2016：過濾明顯很舊/無日期的
        items2 = pick_recent_news_items(items, max_days=days, now=now)
        if len(items2) < 4 and tbs != "qdr:w":
            # 今日太少 -> 退到 7 日內
            items = self.serper_news(q, tbs="qdr:w", num=10)
            items2 = pick_recent_news_items(items, max_days=7, now=now)

        if not items2:
            return "（我暫時抓不到合格的近期新聞來源；可能是搜尋額度/網路暫時不穩）"

        # 存 session 供「看新聞 3」
        self.news_sessions[session_user_id] = {"ts": time.time(), "items": items2[:8]}

        out = [f"【全球新聞重點｜近{min(days,7)}日｜最多8則】"]
        for idx, it in enumerate(items2[:8], 1):
            title = (it.get("title") or "").strip()
            source = (it.get("source") or "").strip()
            date = (it.get("date") or "").strip()
            link = (it.get("link") or "").strip()
            out.append(f"{idx}. {title}（{source}｜{date}）\n   {link}")
        out.append("\n想看哪則詳細？回我：看新聞 3")
        return "\n".join(out)

    # ---------------- MORNING REPORT ----------------
    def build_morning_report(self, ctx: dict, prefs: dict, now: datetime) -> str:
        honorific = ctx["honorific"]
        city = prefs.get("city") or ""
        zodiac = prefs.get("zodiac") or ""

        lines = [f"好的，{honorific}。\n☀️ 每日晨報（{now.strftime('%Y-%m-%d')}）\n"]

        # 節日提醒（農曆三大節 + 接近提醒）
        fest = self.upcoming_festivals(now)
        if fest:
            lines.append("【重要節日提醒】")
            lines.extend([f"- {x}" for x in fest])
            lines.append("")

        # 天氣
        lines.append(f"【居住地天氣｜{city or '未設定'}】")
        if city:
            try:
                lines.append(self.answer_weather({"city": city, "mode": "current"}, now))
            except Exception as e:
                lines.append(f"（天氣工具暫時失敗：{e}）")
        else:
            lines.append("（未設定城市：輸入「設定城市 台中」）")
        lines.append("")

        # 運勢
        lines.append(f"【今日運勢｜{zodiac or '未設定'}】")
        if zodiac:
            try:
                lines.append(self.answer_horoscope(zodiac, now))
            except Exception as e:
                lines.append(f"（運勢工具暫時失敗：{e}）")
        else:
            lines.append("（未設定星座：輸入「設定星座 獅子座」）")
        lines.append("")

        # 新聞（預設今日，少才退 7 日）
        lines.append("【全球新聞重點（8則內）】")
        try:
            lines.append(self.answer_news(days=1, now=now, session_user_id=ctx["user_id"]))
        except Exception as e:
            lines.append(f"（新聞工具暫時失敗：{e}）")

        return "\n".join(lines)

    def upcoming_festivals(self, now: datetime) -> list:
        # 只抓你指定的：春節(正月初一)、端午(五月初五)、中秋(八月十五)
        # 回傳「7天內提醒」
        out = []
        for d in range(0, 8):
            day = (now + timedelta(days=d)).date()
            ld = LunarDate.fromSolarDate(day.year, day.month, day.day)
            if (ld.month, ld.day) == (1, 1):
                out.append(f"春節（農曆正月初一）在 {day.isoformat()}（{d}天後）")
            if (ld.month, ld.day) == (5, 5):
                out.append(f"端午節（農曆五月初五）在 {day.isoformat()}（{d}天後）")
            if (ld.month, ld.day) == (8, 15):
                out.append(f"中秋節（農曆八月十五）在 {day.isoformat()}（{d}天後）")
        return out

    # ---------------- MARKET QUICK (避免亂編) ----------------
    def answer_market_quick(self, text: str, now: datetime) -> str:
        # 用新聞搜尋，不直接亂報數字（你之前被胡扯到 1815）
        q = text.replace("最新", "").strip()
        items = self.serper_news(q, tbs="qdr:d", num=6)
        items2 = pick_recent_news_items(items, max_days=7, now=now)
        if not items2:
            return "（我暫時抓不到可靠的即時來源；可能是搜尋額度用完或來源不足）"
        out = [f"【即時資訊（近24h/近7日備援）】"]
        for it in items2[:5]:
            out.append(f"- {it.get('title','').strip()}（{it.get('source','')}｜{it.get('date','')}）\n  {it.get('link','')}")
        return "\n".join(out)

    # ---------------- CHAT (LLM) ----------------
    def chat(self, ctx: dict, text: str, now: datetime, turns: list, memories: list) -> str:
        honorific = ctx["honorific"]

        short_block = []
        for t in turns:
            short_block.append(f"使用者：{t['user_text']}\nSaturday：{t['bot_text']}")
        short = "\n\n".join(short_block) if short_block else "（無）"

        mem_block = []
        for m in memories:
            # RPC 回 content/scope/created_at/score
            tag = "共享" if m.get("scope") == "shared" else "私密"
            mem_block.append(f"- ({tag}) {m.get('content','')}")
        mem = "\n".join(mem_block) if mem_block else "（無）"

        prompt = f"""
妳是 Saturday，{honorific} 的私人 AI 管家。

【現在時間】
{now.strftime('%Y-%m-%d %H:%M:%S')}（{self.tz.key}）

【風格】
- 口氣像真管家：自然、可靠、簡短但有溫度
- 沒被問到的不要硬加（除非是必要提醒）
- 不要亂編：不確定就說不確定，必要時建議「我可以幫你查」並用對應工具（但此回合先直接回答）

【短期對話（最近）】
{short}

【長期記憶（已檢索，可能有用）】
{mem}

使用者說：
{text}

請回覆（不要附帶多餘時間/系統訊息）：
""".strip()

        resp = self.llm.generate_content(prompt)
        # 避免 resp.text 炸：若空就給保底
        out = (getattr(resp, "text", "") or "").strip()
        return out or "好的。我在。你再說一次你要我幫你做什麼？"
