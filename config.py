import os


def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return default


class _CFG:
    # === 基本 ===
    TZ = _env("TZ", default="Asia/Taipei")

    # === LINE ===
    LINE_CHANNEL_ACCESS_TOKEN = _env("LINE_CHANNEL_ACCESS_TOKEN")
    LINE_CHANNEL_SECRET = _env("LINE_CHANNEL_SECRET")

    # === Gemini ===
    # 兼容你之前用的 GOOGLE_API_KEY
    GEMINI_API_KEY = _env("GEMINI_API_KEY", "GOOGLE_API_KEY")

    # === Supabase ===
    SUPABASE_URL = _env("SUPABASE_URL")
    # 建議用 Service Role Key（伺服器端才安全/穩）
    SUPABASE_SERVICE_ROLE_KEY = _env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY")

    # === Serper / WeatherAPI ===
    SERPER_API_KEY = _env("SERPER_API_KEY")
    WEATHERAPI_KEY = _env("WEATHERAPI_KEY")

    # === 你要求保留的預設 ID（可用 env 覆蓋）===
    MY_ID = "U7388690eb33a4e528e78cae4df00d0c2"
    WIFE_ID = "U0feb2afe319cc8a66ab89ad9fe6ab0fe"
    DEFAULT_OWNER_ID = _env("OWNER_ID", default=MY_ID)
    DEFAULT_WIFE_ID = _env("WIFE_ID", default=WIFE_ID)

    # === /help 指令表（給你與客人用）===
    HELP_TEXT = """【Saturday 指令表（V7.1）】

一、查詢自己的 ID
- 我的ID
- 查詢我的ID

二、基本設定
- 設定城市 台中
- 設定星座 獅子座
- 設定晨報時間 07:00
- 開啟晨報
- 關閉晨報

三、家人/成員綁定（主要使用者 owner）
- 綁定 夫人 Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
- 解除綁定 夫人
- 我的綁定

四、稱呼自訂（每個人可不同）
- 我稱呼 夫人 為 媽媽
- 我稱呼 先生 為 老公

五、記憶（長期）
- 記住 我早上習慣喝冰美式
- 記住(共享) 我早上習慣喝冰美式
- 我記得什麼

六、提醒（自然語句也可）
- 兩分鐘後提醒 喝水
- 1分鐘後提醒夫人 記得買晚餐
- 2026-02-14 18:30 提醒 帶尿布
- 我的提醒
- 取消提醒 <任務ID>

七、資訊
- 今天日期
- 今天天氣
- 明天台中天氣
- 3天台中天氣
- 今日運勢
- 全球新聞
- 晨報
- 看新聞 3

備註：本版本以「私聊 1對1」為主，群組建議之後再開。"""

CFG = _CFG()
