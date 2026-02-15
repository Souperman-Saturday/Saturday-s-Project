import datetime
from zoneinfo import ZoneInfo

import google.generativeai as genai


def generate_horoscope_cn(gemini_key: str, zodiac_cn: str, tz: str = "Asia/Taipei") -> str:
    if not gemini_key:
        return "（Gemini API Key 未設定，無法產生運勢）"

    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    today = datetime.datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")

    prompt = f"""\
你是台灣使用者的生活管家。請用全中文寫「{zodiac_cn}」在 {today} 的今日運勢：
- 3~5 句，口吻溫暖但務實
- 包含：整體、工作/學業、人際、健康、提醒一句
- 不要迷信恐嚇、不提任何需要查證的事實
"""

    try:
        r = model.generate_content(prompt)
        text = (r.text or "").strip()
        return text if text else "（運勢產生失敗，請稍後再試）"
    except Exception:
        return "（運勢工具暫時失敗，請稍後再試）"
