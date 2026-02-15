from typing import List

def build_help_text() -> str:
    return (
        "📌 可用指令（輸入範例照打即可）\n\n"
        "【設定】\n"
        "- 設定城市 台中市\n"
        "- 設定星座 獅子座\n"
        "- 設定晨報時間 07:00\n\n"
        "【查詢】\n"
        "- 天氣 / 台北天氣 / 下週台北天氣\n"
        "- 今日運勢 / 獅子座運勢\n"
        "- 全球新聞 / 新聞\n"
        "- 發送晨報\n\n"
        "【提醒】\n"
        "- 提醒我 10分鐘後 喝水\n"
        "- 提醒我 2026-02-14 09:30 開會\n\n"
        "【記憶】\n"
        "- 記住 私密內容（預設私密）\n"
        "- 記住共享 可以共享的內容\n"
        "- 想起 關鍵字\n\n"
        "【傳訊】\n"
        "- 設定別名 夫人 Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
        "- 傳訊給 夫人 我晚點回家\n"
    )

def build_privacy_intro() -> str:
    return (
        "🔒 隱私規則建議\n"
        "1) 財務/健康 永遠私密：用「記住 私密 ...」\n"
        "2) 生活喜好可共享：用「記住共享 ...」\n"
        "3) 行程/提醒預設不共享（建議只存自己）\n"
    )

def build_morning_report(
    city: str,
    zodiac: str,
    weather_now: str,
    weather_forecast: str,
    horoscope: str,
    news_items: List[str],
    tasks: List[str]
) -> str:
    news_block = "\n\n".join([f"{i+1}. {n}" for i, n in enumerate(news_items)])
    task_block = "\n".join([f"- {t}" for t in tasks]) if tasks else "（今天沒有待辦提醒）"

    return (
        f"☀️ 每日晨報\n\n"
        f"【今日天氣｜{city}】\n{weather_now}\n\n"
        f"【短期預報】\n{weather_forecast}\n\n"
        f"【今日{zodiac}運勢】\n{horoscope}\n\n"
        f"【全球新聞重點整理（8則內）】\n{news_block}\n\n"
        f"【今日待辦提醒】\n{task_block}\n"
    )
