import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dtparser

TZ = ZoneInfo("Asia/Taipei")

CN_NUM = {
    "零":0, "一":1, "二":2, "兩":2, "三":3, "四":4, "五":5, "六":6, "七":7, "八":8, "九":9,
    "十":10, "百":100
}

def cn_to_int(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    # 若是純阿拉伯數字
    if re.fullmatch(r"\d+", s):
        return int(s)
    # 簡單中文數字（到 999 足夠用提醒）
    total = 0
    num = 0
    unit = 1
    if "百" in s:
        parts = s.split("百")
        total += CN_NUM.get(parts[0], 0) * 100
        s = parts[1] if len(parts) > 1 else ""
    if "十" in s:
        parts = s.split("十")
        left = parts[0]
        right = parts[1] if len(parts) > 1 else ""
        total += (CN_NUM.get(left, 1) if left != "" else 1) * 10
        if right:
            total += CN_NUM.get(right, 0)
        return total
    # 個位
    for ch in s:
        if ch in CN_NUM:
            total = total * 10 + CN_NUM[ch]
    return total

ZODIACS = ["牡羊座","金牛座","雙子座","巨蟹座","獅子座","處女座","天秤座","天蠍座","射手座","摩羯座","水瓶座","雙魚座"]

def normalize(text: str) -> str:
    t = (text or "").strip()
    # 全形數字轉半形（簡化）
    t = t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return t

def parse_intent(text: str, profile: dict) -> dict:
    t = normalize(text)

    # help
    if t.lower() in ["/help", "help", "指令", "指令表"]:
        return {"type": "help"}

    # 設定：城市
    m = re.search(r"(?:設定城市|設定居住地|居住地|城市)\s*[:：]?\s*([^\s]+)", t)
    if m:
        return {"type": "set_city", "city": m.group(1).strip()}

    # 自然句：我住在台中 -> 也當作 set_city（更像管家）
    m = re.search(r"我住在\s*([^\s]+)", t)
    if m:
        return {"type": "set_city", "city": m.group(1).strip()}

    # 設定：星座
    m = re.search(r"(?:設定星座|我的星座)\s*[:：]?\s*([^\s]+)", t)
    if m:
        z = m.group(1).strip()
        return {"type": "set_zodiac", "zodiac": z}

    m = re.search(r"我(?:是|的星座是)\s*(.+?座)", t)
    if m:
        z = m.group(1).strip()
        if any(z == x for x in ZODIACS):
            return {"type": "set_zodiac", "zodiac": z}

    # 設定晨報時間 / 開關
    m = re.search(r"(?:設定晨報時間|晨報時間)\s*[:：]?\s*(\d{1,2}:\d{2})", t)
    if m:
        return {"type": "set_morning_time", "hhmm": m.group(1)}
    if re.search(r"(?:開啟晨報|打開晨報|晨報開啟)", t):
        return {"type": "toggle_morning", "on": True}
    if re.search(r"(?:關閉晨報|關掉晨報|晨報關閉)", t):
        return {"type": "toggle_morning", "on": False}

    # 綁定 / 解除 / 名冊
    m = re.search(r"(?:綁定)\s*([^\s]+)\s*(U[a-zA-Z0-9]+)", t)
    if m:
        return {"type": "bind_alias", "alias": m.group(1).strip(), "user_id": m.group(2).strip()}
    m = re.search(r"(?:解除綁定)\s*([^\s]+)", t)
    if m:
        return {"type": "unbind_alias", "alias": m.group(1).strip()}
    if re.search(r"(?:我的綁定|名冊|綁定清單)", t):
        return {"type": "list_alias"}

    # 我希望你叫我…（自我稱呼）
    m = re.search(r"(?:叫我|稱呼我為|我叫)\s*([^\s]+)", t)
    if m and len(m.group(1).strip()) <= 20:
        return {"type": "set_my_name", "myname": m.group(1).strip()}

    # 你對某人的稱呼（每個人可以不同）
    m = re.search(r"(?:我叫|我稱呼)\s*([^\s]+)\s*(?:叫|為)\s*([^\s]+)", t)
    # 例：我叫 夫人 叫 寶貝
    if m:
        return {"type": "set_callname", "target_alias": m.group(1).strip(), "callname": m.group(2).strip()}

    # 預設共享/私密
    if re.search(r"(?:預設共享)", t):
        return {"type": "set_prefs_default", "mode": "shared"}
    if re.search(r"(?:預設私密)", t):
        return {"type": "set_prefs_default", "mode": "private"}

    # 看新聞 N
    m = re.search(r"(?:看新聞)\s*(\d+)", t)
    if m:
        return {"type": "news_detail", "index": m.group(1)}

    # 日期/時間
    if re.search(r"(今天日期|現在時間|幾點|幾號|今天幾號|今天幾月幾號|現在幾點)", t):
        return {"type": "datetime"}

    # 天氣（自然：台中未來三天天氣 / 未來三天天氣 / 明天台中天氣 / 我下週去台北天氣如何）
    if "天氣" in t or "氣溫" in t or "下雨" in t:
        city = None
        # 抓「X天氣」前面的 X
        m = re.search(r"([^\s]{1,10})\s*(?:的)?(?:天氣|氣溫)", t)
        if m:
            cand = m.group(1).strip()
            # 避免抓到「今天天氣」的「今天」
            if cand not in ["今天","明天","後天","未來","下週","下周","這週","这周","未来","三天","3天"]:
                city = cand

        # 3天/未來三天
        if re.search(r"(未來三天|未来三天|3天|三天)", t):
            # 特判：如果文字像「未來三天天氣」但 city 抓不到 -> 用設定城市
            return {"type": "weather", "city": city, "days": 3}

        # 明天/後天
        if "明天" in t:
            return {"type": "weather", "city": city, "offset": 1}
        if "後天" in t:
            return {"type": "weather", "city": city, "offset": 2}
        # 下週：先給 3 天（免費方案極限），提示主人可再問更長
        if "下週" in t or "下周" in t:
            return {"type": "weather", "city": city, "days": 3}

        # 默認：今天
        return {"type": "weather", "city": city, "offset": 0}

    # 新聞（全球新聞 / 今日要聞 / 近7天新聞 / 指定關鍵字）
    if re.search(r"(全球新聞|世界新聞|今日要聞|重大新聞|新聞重點)", t):
        days = 7
        m = re.search(r"(?:近|最近)\s*([0-9一二兩三四五六七八九十]+)\s*(?:天|日)", t)
        if m:
            days = cn_to_int(m.group(1))
        # 可加關鍵字：全球新聞 AI
        q = t
        q = re.sub(r"(全球新聞|世界新聞|今日要聞|重大新聞|新聞重點)", "全球 重大 新聞 重點", q).strip()
        return {"type": "news", "query": q, "days": days, "count": 8}

    # 今日運勢 / 我的運勢 / 獅子座運勢
    if re.search(r"(今日運勢|我的運勢|運勢)", t):
        z = None
        for zz in ZODIACS:
            if zz in t:
                z = zz
                break
        return {"type": "horoscope", "zodiac": z}

    # 記住（共享/私密）
    if t.startswith("記住"):
        scope = "private"
        # 記住(共享) 或 記住 共享：...
        if "共享" in t[:12]:
            scope = "shared"
        content = re.sub(r"^記住(\(共享\))?\s*[:：]?\s*", "", t).strip()
        if not content:
            content = "（空白記憶）"
        return {"type": "remember", "scope": scope, "content": content}

    if t in ["我記得什麼", "我記得甚麼", "我記得哪些", "我記得啥", "我記得什麼嗎", "我記得甚麼嗎", "我記得什麼？", "我記得甚麼？", "我記得什麼呢", "我記得甚麼呢"]:
        return {"type": "list_memory"}

    # 提醒：N分鐘後提醒(我)xxx / N分鐘後提醒 夫人 xxx / 2026-02-14 18:30 提醒 xxx
    # 1) 絕對時間
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(\d{1,2}:\d{2})\s*提醒\s*(.+)", t)
    if m:
        dt = dtparser.parse(f"{m.group(1)} {m.group(2)}").replace(tzinfo=TZ)
        return {"type": "remind", "due_at": dt, "message": m.group(3).strip()}

    # 2) 相對時間（中文/數字都行）
    m = re.search(r"([0-9一二兩三四五六七八九十百]+)\s*(分鐘|小時|天)\s*後\s*提醒(?:我)?\s*(.+)", t)
    if m:
        n = cn_to_int(m.group(1))
        unit = m.group(2)
        msg = m.group(3).strip()
        delta = timedelta(minutes=n) if unit == "分鐘" else timedelta(hours=n) if unit == "小時" else timedelta(days=n)
        return {"type": "remind", "due_at": datetime.now(TZ) + delta, "message": msg}

    # 3) 相對時間 + 指定對象
    m = re.search(r"([0-9一二兩三四五六七八九十百]+)\s*(分鐘|小時|天)\s*後\s*提醒\s*([^\s]+)\s*(.+)", t)
    if m:
        n = cn_to_int(m.group(1))
        unit = m.group(2)
        target = m.group(3).strip()
        msg = m.group(4).strip()
        delta = timedelta(minutes=n) if unit == "分鐘" else timedelta(hours=n) if unit == "小時" else timedelta(days=n)
        return {"type": "remind", "due_at": datetime.now(TZ) + delta, "message": msg, "target_alias": target}

    # 我的提醒 / 取消提醒
    if re.search(r"(我的提醒|提醒清單|待辦)", t):
        return {"type": "list_tasks"}
    m = re.search(r"(?:取消提醒|刪除提醒)\s*(\d+)", t)
    if m:
        return {"type": "cancel_task", "task_id": m.group(1)}

    # 傳訊給X ...
    m = re.search(r"(?:傳訊給|轉告|告訴|跟)\s*([^\s]+)\s*(?:說)?\s*(.+)", t)
    if m and len(m.group(1)) <= 20:
        return {"type": "send", "target_alias": m.group(1).strip(), "message": m.group(2).strip()}

    # 預設：一般聊天
    return {"type": "chat"}
