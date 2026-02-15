from __future__ import annotations

import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import google.generativeai as genai
from lunardate import LunarDate


class RateLimitError(Exception):
    pass


class Tools:
    def __init__(self, gemini_api_key: str, serper_api_key: str, weatherapi_key: str, tz_name: str):
        if not gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY 未設定")
        genai.configure(api_key=gemini_api_key)
        # 你指定核心：gemini-3-pro-preview
        self.llm = genai.GenerativeModel("gemini-3-pro-preview")
        self.serper_key = serper_api_key
        self.weather_key = weatherapi_key
        self.tz = ZoneInfo(tz_name)

    # ---------- time ----------
    def now_text(self) -> str:
        now = datetime.now(self.tz)
        # 只有問時間才回，不要每句都帶
        return f"台灣時間：{now.strftime('%Y-%m-%d %H:%M:%S')}（{self.tz.key}）"

    # ---------- gemini ----------
    def llm_generate(self, prompt: str) -> str:
        resp = self.llm.generate_content(prompt)
        # 防止 response.text 取不到
        text = getattr(resp, "text", "") or ""
        return text.strip() if text.strip() else "（我剛剛沒有成功生成回覆，請你再說一次。）"

    def llm_judge_memory(self, doc: str) -> str:
        """
        回傳：
        - no
        - private
        - shared
        """
        prompt = (
            "你是記憶管理員。判斷以下內容是否值得成為「長期記憶」。\n"
            "規則：\n"
            "1) 財務/健康 永遠不要自動存（回 no）\n"
            "2) 生活偏好/習慣/住址城市/家中規則 可以存\n"
            "3) 若內容看起來適合家庭共享（例如生活喜好、家庭規則）回 shared，否則 private\n"
            "只回答三選一：no / private / shared\n\n"
            f"內容：\n{doc}\n"
        )
        resp = self.llm.generate_content(prompt)
        ans = (getattr(resp, "text", "") or "").strip().lower()
        if "shared" in ans:
            return "shared"
        if "private" in ans:
            return "private"
        return "no"

    def embed(self, text: str) -> list[float]:
        emb_obj = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document"
        )
        # 新版是 .embedding
        return emb_obj.embedding

    # ---------- Serper ----------
    def _serper_post(self, endpoint: str, payload: dict) -> dict:
        if not self.serper_key:
            raise RateLimitError("SERPER_API_KEY 未設定")
        url = f"https://google.serper.dev/{endpoint}"
        headers = {"X-API-KEY": self.serper_key, "Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=25)
        if r.status_code == 429:
            raise RateLimitError("Serper 額度已用完或被限流（HTTP 429）。")
        if r.status_code >= 400:
            raise RuntimeError(f"Serper error: {r.status_code} {r.text[:200]}")
        return r.json()

    def news(self, query: str, days: int = 7, limit: int = 8) -> list[dict]:
        """
        用 Serper News，並「硬過濾」：超過 days 的直接丟掉，避免出現 2016 這種離譜舊聞。
        """
        endpoint = "news"
        # Google tbs：qdr:d / qdr:w / qdr:m
        tbs = "qdr:d" if days <= 1 else ("qdr:w" if days <= 7 else "qdr:m")
        payload = {
            "q": query,
            "gl": "tw",
            "hl": "zh-tw",
            "num": max(limit * 2, 10),
            "tbs": tbs
        }
        data = self._serper_post(endpoint, payload)
        items = data.get("news", []) or []
        filtered = []

        # parse relative date like "1 day ago" / "3 小時前" etc
        now = datetime.now(self.tz)
        for it in items:
            title = it.get("title", "")
            link = it.get("link", "")
            source = it.get("source", "")
            date_str = (it.get("date") or "").strip()
            snippet = (it.get("snippet") or "").strip()

            if not title or not link:
                continue

            # 若 Serper 已經給 "date"（通常是相對時間），做保守判斷
            ok_recent = True
            if date_str:
                ok_recent = self._is_recent(date_str, now, days=days)

            if ok_recent:
                filtered.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "date": date_str,
                    "snippet": snippet
                })
            if len(filtered) >= limit:
                break

        return filtered[:limit]

    def _is_recent(self, date_str: str, now: datetime, days: int) -> bool:
        s = date_str.lower()
        # examples: "1 day ago", "3 hours ago", "31 分鐘前", "2 天前"
        try:
            if "分鐘" in s or "minute" in s:
                return True
            if "小時" in s or "hour" in s:
                return True
            if "天前" in s or "day" in s:
                n = int("".join([c for c in s if c.isdigit()]) or "999")
                return n <= days
            if "週前" in s or "week" in s:
                n = int("".join([c for c in s if c.isdigit()]) or "999")
                return n * 7 <= days
        except Exception:
            return True
        # 沒法解析就保守收
        return True

    def horoscope_today(self, sign: str) -> dict:
        """
        不用星座 API，改用搜尋摘要（你原本預期的方式），但做成「穩」：
        - 搜尋「獅子座 今日運勢」
        - 取前幾筆 snippet 並請 Gemini 用一句話整理
        """
        if not self.serper_key:
            raise RateLimitError("SERPER_API_KEY 未設定")

        q = f"{sign} 今日運勢"
        data = self._serper_post("search", {"q": q, "gl": "tw", "hl": "zh-tw", "num": 5})
        organic = data.get("organic", []) or []
        snippets = []
        sources = []
        for r in organic[:5]:
            sn = (r.get("snippet") or "").strip()
            if sn:
                snippets.append(sn)
            sources.append({"title": r.get("title"), "link": r.get("link")})

        if not snippets:
            return {"summary": "（目前找不到可靠的運勢摘要）", "sources": sources}

        prompt = (
            "你是一位貼心管家，根據以下網頁摘要，請用 3~5 句繁中整理今日運勢重點，"
            "不要玄學廢話，講重點即可。\n\n摘要：\n"
            + "\n".join(f"- {s}" for s in snippets)
        )
        summary = self.llm_generate(prompt)
        return {"summary": summary, "sources": sources}

    # ---------- WeatherAPI ----------
    def weather_now(self, city: str) -> dict:
        if not self.weather_key:
            raise RuntimeError("WEATHERAPI_KEY 未設定")
        # WeatherAPI 支援中文地名，建議加上 Taiwan
        q = f"{city}, Taiwan"
        url = "https://api.weatherapi.com/v1/current.json"
        params = {"key": self.weather_key, "q": q, "aqi": "no", "lang": "zh"}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 429:
            raise RateLimitError("WeatherAPI 額度已用完或被限流（HTTP 429）。")
        if r.status_code >= 400:
            raise RuntimeError(f"WeatherAPI error: {r.status_code} {r.text[:200]}")
        return r.json()

    def weather_forecast(self, city: str, days: int = 3) -> dict:
        if not self.weather_key:
            raise RuntimeError("WEATHERAPI_KEY 未設定")
        q = f"{city}, Taiwan"
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {"key": self.weather_key, "q": q, "days": max(2, min(days, 10)), "aqi": "no", "alerts": "no", "lang": "zh"}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 429:
            raise RateLimitError("WeatherAPI 額度已用完或被限流（HTTP 429）。")
        if r.status_code >= 400:
            raise RuntimeError(f"WeatherAPI error: {r.status_code} {r.text[:200]}")
        return r.json()

    def format_weather_now(self, city: str, data: dict) -> str:
        loc = data.get("location", {})
        cur = data.get("current", {})
        cond = (cur.get("condition") or {}).get("text", "")
        temp = cur.get("temp_c")
        feel = cur.get("feelslike_c")
        hum = cur.get("humidity")
        wind = cur.get("wind_kph")
        name = loc.get("name") or city
        return f"{name}：{cond}\n氣溫 {temp}°C（體感 {feel}°C）｜濕度 {hum}%｜風速 {wind} km/h"

    def format_weather_forecast(self, city: str, data: dict, days: int = 3) -> str:
        loc = data.get("location", {})
        name = loc.get("name") or city
        out = [f"{name} 未來 {days} 天："]
        days_list = ((data.get("forecast") or {}).get("forecastday") or [])[:days]
        for d in days_list:
            date = d.get("date")
            day = d.get("day") or {}
            cond = (day.get("condition") or {}).get("text", "")
            min_t = day.get("mintemp_c")
            max_t = day.get("maxtemp_c")
            rain = day.get("daily_chance_of_rain")
            out.append(f"- {date}：{cond}｜{min_t}~{max_t}°C｜降雨機率 {rain}%")
        return "\n".join(out)

    def format_horoscope(self, sign: str, data: dict) -> str:
        return f"{sign} 今日運勢：\n{data.get('summary','（無）')}".strip()

    # ---------- Morning brief ----------
    def build_morning_brief(self, city: str, zodiac: str, news_days: int, news_limit: int) -> dict:
        result = {"city": city, "zodiac": zodiac, "weather": None, "horoscope": None, "news_items": []}

        if city and "未設定" not in city:
            try:
                result["weather"] = self.weather_now(city)
            except Exception:
                result["weather"] = None

        if zodiac and "未設定" not in zodiac:
            try:
                result["horoscope"] = self.horoscope_today(zodiac)
            except Exception:
                result["horoscope"] = None

        try:
            result["news_items"] = self.news("全球重大新聞", days=news_days, limit=news_limit)
        except Exception:
            result["news_items"] = []

        return result

    def today_holiday_line(self) -> str:
        now = datetime.now(self.tz).date()
        # 若今天剛好是內建節日，顯示一行提醒（更像管家）
        holidays = self.tw_major_holidays(now.year)
        for h in holidays:
            if h["date"] == now.strftime("%Y-%m-%d"):
                return f"🎌 今天是：{h['name']}"
        return ""

    def format_news(self, items: list[dict], ask_detail: bool = True) -> str:
        if not items:
            return "（我目前找不到可靠的近 7 日新聞清單，可能是搜尋額度/網路來源問題。）"
        out = []
        for i, it in enumerate(items, start=1):
            date = it.get("date") or ""
            src = it.get("source") or ""
            out.append(f"{i}. {it['title']}（{src} {date}）")
        if ask_detail:
            out.append("")
            out.append("想看哪則詳細？回我：看新聞 3")
        return "\n".join(out)

    def format_news_detail(self, item: dict) -> str:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        src = item.get("source", "")
        date = item.get("date", "")
        # LINE 長度保守
        return f"{title}\n（{src} {date}）\n\n摘要：{snippet}\n\n連結：{link}".strip()

    def format_morning(self, morning: dict, holiday_line: str = "", date_str: str | None = None) -> str:
        date_str = date_str or datetime.now(self.tz).strftime("%Y-%m-%d")
        out = [f"☀️ 每日晨報（{date_str}）"]

        if holiday_line:
            out.append(holiday_line)

        out.append("")
        # Weather
        city = morning.get("city") or "（未設定）"
        out.append(f"【居住地天氣｜{city}】")
        if morning.get("weather"):
            out.append(self.format_weather_now(city, morning["weather"]))
        else:
            out.append("（天氣工具暫時失敗或未設定城市：先說「設定城市 台中」）")

        out.append("")
        # Horoscope
        zodiac = morning.get("zodiac") or "（未設定）"
        out.append(f"【今日運勢｜{zodiac}】")
        if morning.get("horoscope"):
            out.append(morning["horoscope"].get("summary", "（運勢工具暫時失敗）"))
        else:
            out.append("（運勢工具暫時失敗或未設定星座：先說「設定星座 獅子座」）")

        out.append("")
        # News
        out.append("【全球新聞重點（8則內｜近7日）】")
        news_items = morning.get("news_items") or []
        if news_items:
            out.append(self.format_news(news_items, ask_detail=True))
        else:
            out.append("（新聞工具暫時失敗或搜尋額度不足）")

        return "\n".join(out)

    # ---------- Taiwan major holidays (with lunar conversion) ----------
    def tw_major_holidays(self, year: int) -> list[dict]:
        """
        這裡做「節日提醒」：農曆節日換算為國曆（不含政府補班/連假調整）。
        節日：
        - 元旦 1/1
        - 228 2/28
        - 國慶 10/10
        - 農曆：除夕、春節(初一)、元宵(1/15)、端午(5/5)、中秋(8/15)
        """
        out = []
        # solar
        out.append({"name": "元旦", "date": f"{year}-01-01"})
        out.append({"name": "和平紀念日（228）", "date": f"{year}-02-28"})
        out.append({"name": "國慶日", "date": f"{year}-10-10"})

        # lunar conversions
        # 春節：農曆 1/1
        cny = LunarDate(year, 1, 1).toSolarDate()
        out.append({"name": "農曆春節（初一）", "date": cny.strftime("%Y-%m-%d")})

        # 元宵：1/15
        lantern = LunarDate(year, 1, 15).toSolarDate()
        out.append({"name": "元宵節", "date": lantern.strftime("%Y-%m-%d")})

        # 端午：5/5
        duanwu = LunarDate(year, 5, 5).toSolarDate()
        out.append({"name": "端午節", "date": duanwu.strftime("%Y-%m-%d")})

        # 中秋：8/15
        mid = LunarDate(year, 8, 15).toSolarDate()
        out.append({"name": "中秋節", "date": mid.strftime("%Y-%m-%d")})

        # 除夕：下一個農曆新年(明年 1/1) - 1 day
        next_cny = LunarDate(year + 1, 1, 1).toSolarDate()
        eve = next_cny - timedelta(days=1)
        out.append({"name": "除夕", "date": eve.strftime("%Y-%m-%d")})

        # sort
        out.sort(key=lambda x: x["date"])
        return out
