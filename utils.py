import re
from datetime import datetime, timedelta

TW_CITY_MAP = {
    "台中": "Taichung, Taiwan",
    "台中市": "Taichung, Taiwan",
    "臺中": "Taichung, Taiwan",
    "臺中市": "Taichung, Taiwan",
    "台北": "Taipei, Taiwan",
    "台北市": "Taipei, Taiwan",
    "臺北": "Taipei, Taiwan",
    "臺北市": "Taipei, Taiwan",
    "新北": "New Taipei City, Taiwan",
    "新北市": "New Taipei City, Taiwan",
    "高雄": "Kaohsiung, Taiwan",
    "高雄市": "Kaohsiung, Taiwan",
    "桃園": "Taoyuan, Taiwan",
    "桃園市": "Taoyuan, Taiwan",
}

def safe_user_text(s: str) -> str:
    return (s or "").strip()

def normalize_tw_city(city: str) -> str:
    c = (city or "").strip()
    return TW_CITY_MAP.get(c, c)

# ---------- help / intent ----------
def is_help_query(text: str) -> bool:
    t = text.strip().lower()
    return t in ("/help", "help", "指令", "指令表", "幫助")

def is_news_query(text: str) -> bool:
    return any(k in text for k in ("新聞", "要聞", "時事", "全球新聞"))

def is_horoscope_query(text: str) -> bool:
    return any(k in text for k in ("運勢", "星座"))

def is_morning_report_query(text: str) -> bool:
    return "晨報" in text or "早報" in text

def is_date_time_query(text: str) -> bool:
    return any(k in text for k in ("今天日期", "現在時間", "幾點", "日期", "時間", "比特幣", "BTC", "台積電", "TSMC"))

# ---------- settings ----------
def is_setting_city_query(text: str) -> bool:
    return text.startswith("設定城市")

def parse_setting_city(text: str) -> str:
    return text.replace("設定城市", "").strip()

def is_setting_zodiac_query(text: str) -> bool:
    return text.startswith("設定星座")

def parse_setting_zodiac(text: str) -> str:
    return text.replace("設定星座", "").strip()

def is_setting_morning_time_query(text: str) -> bool:
    return text.startswith("設定晨報時間")

def parse_setting_morning_time(text: str) -> str:
    s = text.replace("設定晨報時間", "").strip()
    # 07:00
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return "07:00"
    hh = int(m.group(1))
    mm = int(m.group(2))
    hh = max(0, min(23, hh))
    mm = max(0, min(59, mm))
    return f"{hh:02d}:{mm:02d}"

# ---------- binding ----------
def is_bind_query(text: str) -> bool:
    return text.startswith("綁定 ")

def parse_bind(text: str):
    # 綁定 夫人 Uxxxx
    parts = text.split()
    if len(parts) >= 3:
        return parts[1], parts[2]
    return "", ""

def is_unbind_query(text: str) -> bool:
    return text.startswith("解除綁定 ")

def parse_unbind(text: str) -> str:
    return text.replace("解除綁定", "").strip()

def is_my_bindings_query(text: str) -> bool:
    return text in ("我的綁定", "成員", "成員清單", "我有哪些綁定")

# ---------- nickname ----------
def is_nickname_query(text: str) -> bool:
    # 我稱呼 夫人 為 老婆
    return text.startswith("我稱呼 ") and " 為 " in text

def parse_nickname(text: str):
    s = text.replace("我稱呼", "", 1).strip()
    a, b = s.split(" 為 ", 1)
    return a.strip(), b.strip()

# ---------- send message ----------
def is_send_message_query(text: str) -> bool:
    return text.startswith("傳訊給") or text.startswith("轉告")

def parse_send_message(text: str):
    if text.startswith("轉告"):
        s = text.replace("轉告", "", 1).strip()
    else:
        s = text.replace("傳訊給", "", 1).strip()
    parts = s.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0].strip(), ""
    return parts[0].strip(), parts[1].strip()

# ---------- memory ----------
def is_memory_save_query(text: str) -> bool:
    return text.startswith("記住")

def extract_memory_scope_and_text(text: str):
    # 記住(共享) xxx
    scope = "private"
    t = text
    if text.startswith("記住(共享)"):
        scope = "shared"
        t = text.replace("記住(共享)", "", 1)
    elif text.startswith("記住（共享）"):
        scope = "shared"
        t = text.replace("記住（共享）", "", 1)
    else:
        t = text.replace("記住", "", 1)
    return scope, t.strip()

def is_memory_list_query(text: str) -> bool:
    return any(k in text for k in ("我記得什麼", "我記得甚麼", "我記得什麼事", "我記得哪些", "我記得甚麼事"))

# ---------- reminder parsing (自然語句) ----------
CN_NUM = {
    "零":0,"一":1,"二":2,"兩":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10
}

def _cn_to_int(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    # 很簡化：只處理 1~99（足夠提醒分鐘）
    if s == "十":
        return 10
    if "十" in s:
        left, right = s.split("十", 1)
        a = CN_NUM.get(left, 1) if left else 1
        b = CN_NUM.get(right, 0) if right else 0
        return a * 10 + b
    return CN_NUM.get(s, 0)

def parse_reminder_text(text: str, now: datetime):
    """
    支援：
    - 2分鐘後提醒 喝水
    - 兩分鐘後提醒喝水
    - 2026-02-20 18:30 提醒 帶尿布
    - 2分鐘後提醒夫人 買晚餐
    """
    t = text.strip()

    # 絕對時間
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*提醒\s*(.+)$", t)
    if m:
        dt = datetime.fromisoformat(f"{m.group(1)} {m.group(2)}")
        dt = dt.replace(tzinfo=now.tzinfo)
        return {"due_at": dt, "due_at_str": f"{m.group(1)} {m.group(2)}", "message": m.group(3).strip()}

    # 分鐘後（含中文數字）
    m2 = re.search(r"^(.+?)分鐘後提醒(.*)$", t)
    if m2:
        n_raw = m2.group(1).strip()
        rest = m2.group(2).strip()

        mins = _cn_to_int(n_raw)
        if mins <= 0:
            return None

        # 可能寫「提醒夫人 買晚餐」
        target_name = None
        msg = rest

        # 把「夫人」抓出來（第一個詞若不是空就當 target）
        parts = rest.split(maxsplit=1)
        if len(parts) >= 2 and len(parts[0]) <= 6:
            # 小心：避免把「喝水」當 target
            if parts[0] not in ("喝水","洗澡","睡覺","起床","吃藥","運動"):
                target_name = parts[0]
                msg = parts[1]

        due = now + timedelta(minutes=mins)
        return {
            "due_at": due,
            "due_at_str": due.strftime("%Y-%m-%d %H:%M"),
            "message": msg.strip(),
            "target_name": target_name,
        }

    return None

# ---------- weather query ----------
def parse_weather_query(text: str, default_city: str = "") -> dict:
    """
    允許：
    - 今天天氣
    - 明天台中天氣
    - 3天台中天氣
    - 台中未來三天天氣
    - 未來三天天氣
    """
    t = text.strip()
    city = ""

    # 嘗試抓城市（天氣前的詞）
    m_city = re.search(r"(.+?)(?:未來|明天|後天|今天|今天天氣|天氣)", t)
    if m_city:
        cand = m_city.group(1).strip()
        # 避免抓到「給我」
        cand = cand.replace("給我", "").replace("查", "").strip()
        if cand and len(cand) <= 10 and cand not in ("未來","明天","後天","今天","今日","今天天氣"):
            city = cand

    if not city:
        city = default_city.strip()

    # 天數
    if "明天" in t:
        return {"city": city, "mode": "forecast", "days": 2}
    if "後天" in t:
        return {"city": city, "mode": "forecast", "days": 3}

    m_days = re.search(r"(\d+)\s*天", t)
    if m_days:
        return {"city": city, "mode": "forecast", "days": int(m_days.group(1))}

    m_days_cn = re.search(r"(三|四|五|六|七)天", t)
    if m_days_cn:
        days = _cn_to_int(m_days_cn.group(1))
        return {"city": city, "mode": "forecast", "days": days}

    if "未來" in t:
        # 未來三天（預設）
        return {"city": city, "mode": "forecast", "days": 3}

    return {"city": city, "mode": "current", "days": 1}

# ---------- news days ----------
def parse_news_days(text: str) -> int:
    t = text.strip()
    if "今日" in t or "今天" in t:
        return 1
    m = re.search(r"近(\d+)日", t)
    if m:
        return int(m.group(1))
    m2 = re.search(r"近(\d+)天", t)
    if m2:
        return int(m2.group(1))
    if "近七" in t or "7日" in t or "7天" in t:
        return 7
    if "近三" in t or "3日" in t or "3天" in t:
        return 3
    return 1

# ---------- formatting helpers ----------
def compact_date_str(s: str) -> str:
    return (s or "").strip()

def pick_recent_news_items(items: list, max_days: int, now: datetime) -> list:
    """
    Serper news 有時 date 是 '2 hours ago' / '1 day ago' / '2025-06-07'
    我們做保守過濾：含 2016 這種直接踢掉；其餘先保留
    """
    out = []
    for it in items or []:
        title = (it.get("title") or "").strip()
        date = (it.get("date") or "").strip()
        # 明顯很舊直接排除
        if "2016" in title or "2016" in date:
            continue
        if "2017" in title or "2017" in date:
            continue
        out.append(it)
    return out
