# supa.py
import os
import math
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from supabase import create_client

# ✅ 方案B：用 google-genai 做 embedding（不要再用舊 google-generativeai 的 embedContent + text-embedding-004）
from google import genai
from google.genai import types


def _now_iso() -> str:
    # for logs / debug
    return datetime.utcnow().isoformat()


def _normalize(vec: List[float]) -> List[float]:
    # Gemini embed 如果你指定 output_dimensionality（例如 768）官方建議自己做 normalize
    # 不然 cosine distance / similarity 會不穩
    # https://ai.google.dev/gemini-api/docs/embeddings?hl=en
    norm = math.sqrt(sum((v * v) for v in vec)) or 1.0
    return [v / norm for v in vec]


class Supa:
    """
    你原本 app / scheduler 會用到的 Supa 物件。
    這版只針對「embedding 模型下架」做一次到位修正，
    其餘方法名稱盡量保持你原本的用法（你不用改其他檔案）。
    """

    def __init__(self):
        self.url = os.getenv("SUPA_URL", "").strip()
        self.key = os.getenv("SUPA_KEY", "").strip()
        if not self.url or not self.key:
            raise RuntimeError("SUPA_URL / SUPA_KEY 未設定")

        self.sb = create_client(self.url, self.key)

        # Gemini client（embedding 用）
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not self.gemini_api_key:
            # 讓 app 自己處理提示，但不要在 import 階段直接炸掉整個 service
            self.gemini = None
        else:
            self.gemini = genai.Client(api_key=self.gemini_api_key)

        # ✅ 方案B：固定使用 Gemini Embedding 模型
        self.embedding_model = "gemini-embedding-001"
        self.embedding_dims = 768

    # ----------------------------
    # Embeddings (方案B核心修正)
    # ----------------------------
    def embed_text_768(self, text: str) -> List[float]:
        """
        回傳 768 維 float list（已 normalize）。
        """
        if not self.gemini:
            raise RuntimeError("GEMINI_API_KEY 未設定")

        cleaned = (text or "").strip()
        if not cleaned:
            return [0.0] * self.embedding_dims

        # 注意：google-genai 的 embed_content 回來是 result.embeddings[0].values
        result = self.gemini.models.embed_content(
            model=self.embedding_model,
            contents=cleaned,
            config=types.EmbedContentConfig(
                output_dimensionality=self.embedding_dims
            ),
        )

        values = None
        if hasattr(result, "embeddings") and result.embeddings:
            emb0 = result.embeddings[0]
            if hasattr(emb0, "values"):
                values = list(emb0.values)
            elif isinstance(emb0, list):
                values = emb0

        if not values or len(values) != self.embedding_dims:
            # 保底：避免整個流程炸掉
            raise RuntimeError(
                f"Embedding 回傳維度不正確：{0 if not values else len(values)}"
            )

        return _normalize(values)

    # ----------------------------
    # Settings
    # ----------------------------
    def set_setting(self, user_id: str, key: str, value: str) -> None:
        self.sb.table("saturday_settings").upsert(
            {"user_id": user_id, "key": key, "value": value}
        ).execute()

    def get_setting(self, user_id: str, key: str, default: Optional[str] = None) -> Optional[str]:
        res = self.sb.table("saturday_settings").select("value").eq("user_id", user_id).eq("key", key).limit(1).execute()
        data = getattr(res, "data", None) or []
        if not data:
            return default
        return data[0].get("value", default)

    # convenience
    def set_city(self, user_id: str, city: str) -> None:
        self.set_setting(user_id, "city", city)

    def get_city(self, user_id: str) -> Optional[str]:
        return self.get_setting(user_id, "city")

    def set_zodiac(self, user_id: str, zodiac: str) -> None:
        self.set_setting(user_id, "zodiac", zodiac)

    def get_zodiac(self, user_id: str) -> Optional[str]:
        return self.get_setting(user_id, "zodiac")

    def set_brief_time(self, user_id: str, hhmm: str) -> None:
        self.set_setting(user_id, "brief_time", hhmm)

    def get_brief_time(self, user_id: str, default: str = "07:00") -> str:
        return self.get_setting(user_id, "brief_time", default) or default

    # ----------------------------
    # Long-term memory (vector)
    # ----------------------------
    def remember(self, user_id: str, content: str, scope: str = "private") -> None:
        """
        scope: 'private' or 'shared'
        """
        emb = self.embed_text_768(content)

        payload = {
            "owner_id": user_id,
            "scope": scope,
            "content": content,
            "embedding": emb,
        }
        self.sb.table("saturday_memories").insert(payload).execute()

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        res = self.sb.table("saturday_memories").select("id,scope,content,created_at").eq("owner_id", user_id).order("created_at", desc=True).limit(50).execute()
        return getattr(res, "data", None) or []

    def search_memories(self, user_id: str, query: str, scope: str = "private", k: int = 5) -> List[Dict[str, Any]]:
        """
        需要你 Supabase 有建立 RPC：match_saturday_memories
        （你之前 V7.1 版應該已經有；若沒有我再給你 SQL）
        """
        qv = self.embed_text_768(query)

        args = {
            "match_owner": user_id,
            "match_scope": scope,
            "query_embedding": qv,
            "match_count": k,
        }
        res = self.sb.rpc("match_saturday_memories", args).execute()
        return getattr(res, "data", None) or []

    # ----------------------------
    # Reminders (你原本的介面保留)
    # ----------------------------
    def create_reminder(self, owner_id: str, target_id: str, due_at_yyyy_mm_dd_hhmm: str, text: str) -> str:
        payload = {
            "owner_id": owner_id,
            "target_id": target_id,
            "due_at": due_at_yyyy_mm_dd_hhmm,  # 你原本就是用字串格式
            "text": text,
            "status": "pending",
        }
        res = self.sb.table("saturday_reminders").insert(payload).execute()
        data = getattr(res, "data", None) or []
        if data and "id" in data[0]:
            return str(data[0]["id"])
        return ""

    def fetch_due_reminders(self, now_yyyy_mm_dd_hhmm: str) -> List[Dict[str, Any]]:
        res = (
            self.sb.table("saturday_reminders")
            .select("*")
            .eq("status", "pending")
            .lte("due_at", now_yyyy_mm_dd_hhmm)
            .order("due_at", desc=False)
            .limit(50)
            .execute()
        )
        return getattr(res, "data", None) or []

    def mark_reminder_sent(self, reminder_id: str) -> None:
        self.sb.table("saturday_reminders").update({"status": "sent"}).eq("id", reminder_id).execute()

    def cancel_reminder(self, owner_id: str, reminder_id: str) -> None:
        self.sb.table("saturday_reminders").update({"status": "cancelled"}).eq("id", reminder_id).eq("owner_id", owner_id).execute()

    # ----------------------------
    # Relations / alias (如果你原本有用到)
    # ----------------------------
    def bind_alias(self, owner_id: str, alias: str, user_id: str) -> None:
        self.sb.table("saturday_aliases").upsert(
            {"owner_id": owner_id, "alias": alias, "user_id": user_id}
        ).execute()

    def unbind_alias(self, owner_id: str, alias: str) -> None:
        self.sb.table("saturday_aliases").delete().eq("owner_id", owner_id).eq("alias", alias).execute()

    def resolve_alias(self, owner_id: str, alias: str) -> Optional[str]:
        res = self.sb.table("saturday_aliases").select("user_id").eq("owner_id", owner_id).eq("alias", alias).limit(1).execute()
        data = getattr(res, "data", None) or []
        if not data:
            return None
        return data[0].get("user_id")
