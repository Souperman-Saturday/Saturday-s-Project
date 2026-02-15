import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

def env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()

@dataclass(frozen=True)
class Config:
    # LINE
    LINE_CHANNEL_ACCESS_TOKEN: str = env("LINE_CHANNEL_ACCESS_TOKEN")
    LINE_CHANNEL_SECRET: str = env("LINE_CHANNEL_SECRET")

    # SUPABASE
    SUPABASE_URL: str = env("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY: str = env("SUPABASE_SERVICE_ROLE_KEY")  # 務必用 service_role

    # GEMINI
    GEMINI_API_KEY: str = env("GEMINI_API_KEY")

    # SEARCH / WEATHER
    SERPER_API_KEY: str = env("SERPER_API_KEY")
    WEATHERAPI_KEY: str = env("WEATHERAPI_KEY")

    # REGION
    TZ_NAME: str = env("TZ", "Asia/Taipei")
    SERPER_HL: str = env("SERPER_HL", "zh-tw")  # 介面語系
    SERPER_GL: str = env("SERPER_GL", "tw")     # 地區

    # Tick (UptimeRobot)
    TICK_SECRET: str = env("TICK_SECRET")

    # Optional: 固定你自己的 OA 主人/夫人（你要 multi 也能留空）
    DEFAULT_OWNER_ID: str = env("DEFAULT_OWNER_ID")  # 你的 Flynn ID 可放這
    DEFAULT_WIFE_ID: str = env("DEFAULT_WIFE_ID")    # 你的夫人 ID 可放這

    @property
    def TZ(self) -> ZoneInfo:
        return ZoneInfo(self.TZ_NAME)

CFG = Config()
