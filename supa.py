# supa.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from supabase import create_client


class SupaError(RuntimeError):
    pass


@dataclass
class SupaConfig:
    url: str
    key: str


class Supa:
    """
    Supabase 存取層（V7.1）
    - 明確支援 Supa(SUPABASE_URL, SERVICE_ROLE_KEY)
    - 提供通用 table/rpc + 常用功能（提醒/設定/記憶）
    """

    def __init__(self, supabase_url: str, supabase_key: str):
        supabase_url = (supabase_url or "").strip()
        supabase_key = (supabase_key or "").strip()

        if not supabase_url:
            raise SupaError("SUPABASE_URL 未設定或為空")
        if not supabase_key:
            raise SupaError("SUPABASE_SERVICE_ROLE_KEY / SUPABASE_KEY 未設定或為空")

        self.cfg = SupaConfig(url=supabase_url, key=supabase_key)
        self.sb = create_client(self.cfg.url, self.cfg.key)

    # ----------------------------
    # 基礎工具
    # ----------------------------
    def table(self, name: str):
        """讓外部仍可用：supa.table('xxx').select(...).execute()"""
        return self.sb.table(name)

    def rpc(self, fn: str, args: Dict[str, Any]):
        """讓外部仍可用：supa.rpc('match_memories', {...}).execute()"""
        return self.sb.rpc(fn, args)

    def ping(self) -> bool:
        """
        快速測試連線是否 OK：
        - 只做一個非常輕量查詢（不依賴特定資料表內容）
        """
        try:
            # 任何專案都會有 auth schema，但 postgrest 不一定可 select。
            # 所以用你已存在的表之一做輕量查詢（沒有也會回可讀錯）。
            self.sb.table("saturday_reminders").select("id").limit(1).execute()
            return True
        except Exception as e:
            raise SupaError(f"Supabase 連線測試失敗：{e}") from e

    # ----------------------------
    # 提醒（scheduler 會用到）
    # ----------------------------
    def fetch_due_reminders(self, now_yyyy_mm_dd_hhmm: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        取出到期且待送的提醒（scheduler.py tick_reminders 會用到）
        預期表：saturday_reminders
        欄位建議：
          - id (uuid or bigint)
          - status: 'pending' / 'sent' / 'cancelled'
          - due_at: 建議 timestamptz；若你是 text 也可（用 YYYY-MM-DD HH:MM）
        """
        try:
            res = (
                self.sb.table("saturday_reminders")
                .select("*")
                .eq("status", "pending")
                .lte("due_at", now_yyyy_mm_dd_hhmm)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            raise SupaError(f"fetch_due_reminders 失敗：{e}") from e

    def mark_reminder_sent(self, reminder_id: Any) -> None:
        try:
            (
                self.sb.table("saturday_reminders")
                .update({"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", reminder_id)
                .execute()
            )
        except Exception as e:
            raise SupaError(f"mark_reminder_sent 失敗：{e}") from e

    def create_reminder(
        self,
        owner_id: str,
        target_id: str,
        due_at: str,
        message: str,
        created_by: Optional[str] = None,
        scope: str = "private",
    ) -> Dict[str, Any]:
        """
        建立提醒
        - owner_id：誰建立（通常主用戶）
        - target_id：要推播給誰
        - due_at：建議 'YYYY-MM-DD HH:MM' 或 ISO string
        - scope：private/shared（你要做家庭共享時可用）
        """
        payload = {
            "owner_id": owner_id,
            "target_id": target_id,
            "due_at": due_at,
            "message": message,
            "status": "pending",
            "created_by": created_by or owner_id,
            "scope": scope,
        }
        try:
            res = self.sb.table("saturday_reminders").insert(payload).execute()
            return (res.data or [{}])[0]
        except Exception as e:
            raise SupaError(f"create_reminder 失敗：{e}") from e

    def list_reminders(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            res = (
                self.sb.table("saturday_reminders")
                .select("*")
                .or_(f"owner_id.eq.{user_id},target_id.eq.{user_id}")
                .order("due_at", desc=False)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            raise SupaError(f"list_reminders 失敗：{e}") from e

    def cancel_reminder(self, user_id: str, reminder_id: Any) -> bool:
        """
        取消提醒：只允許 owner / target 取消
        """
        try:
            # 先查權限
            r = self.sb.table("saturday_reminders").select("*").eq("id", reminder_id).limit(1).execute()
            row = (r.data or [None])[0]
            if not row:
                return False
            if row.get("owner_id") != user_id and row.get("target_id") != user_id:
                return False

            self.sb.table("saturday_reminders").update({"status": "cancelled"}).eq("id", reminder_id).execute()
            return True
        except Exception as e:
            raise SupaError(f"cancel_reminder 失敗：{e}") from e

    # ----------------------------
    # 使用者設定（城市 / 星座 / 晨報時間 等）
    # ----------------------------
    def upsert_user_setting(self, user_id: str, key: str, value: str) -> None:
        """
        建議表：saturday_user_settings
        欄位：user_id, key, value, updated_at
        """
        payload = {
            "user_id": user_id,
            "key": key,
            "value": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # upsert 需要表上有 unique(user_id, key)
            self.sb.table("saturday_user_settings").upsert(payload, on_conflict="user_id,key").execute()
        except Exception as e:
            raise SupaError(f"upsert_user_setting 失敗（表/unique 可能未建）：{e}") from e

    def get_user_setting(self, user_id: str, key: str) -> Optional[str]:
        try:
            res = (
                self.sb.table("saturday_user_settings")
                .select("value")
                .eq("user_id", user_id)
                .eq("key", key)
                .limit(1)
                .execute()
            )
            row = (res.data or [None])[0]
            return row.get("value") if row else None
        except Exception as e:
            raise SupaError(f"get_user_setting 失敗：{e}") from e

    # ----------------------------
    # 記憶（長期向量 / 私密+共享）
    # ----------------------------
    def save_memory(
        self,
        user_id: str,
        content: str,
        scope: str = "private",  # private | shared
        embedding: Optional[List[float]] = None,
        importance: int = 1,
    ) -> None:
        """
        建議表：saturday_memories
        欄位：id(uuid), user_id, scope, content, embedding(vector), importance(int), created_at
        """
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "scope": scope,
            "content": content,
            "importance": importance,
        }
        if embedding is not None:
            payload["embedding"] = embedding

        try:
            self.sb.table("saturday_memories").insert(payload).execute()
        except Exception as e:
            raise SupaError(f"save_memory 失敗（表欄位可能不一致）：{e}") from e

    def list_recent_memories(self, user_id: str, limit: int = 10, scope: Optional[str] = None) -> List[str]:
        try:
            q = (
                self.sb.table("saturday_memories")
                .select("content,created_at,scope")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if scope:
                q = q.eq("scope", scope)

            res = q.execute()
            rows = res.data or []
            rows.reverse()
            return [r["content"] for r in rows if r.get("content")]
        except Exception as e:
            raise SupaError(f"list_recent_memories 失敗：{e}") from e

    def search_memories_rpc(
        self,
        query_embedding: List[float],
        user_id: str,
        match_threshold: float = 0.25,
        match_count: int = 5,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        你 Supabase 需要有 RPC：match_memories(...)
        若你有 scope 欄位，可在 SQL function 內做過濾；否則在這裡事後過濾
        """
        try:
            res = self.sb.rpc(
                "match_memories",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": match_count,
                    "p_user_id": user_id,
                },
            ).execute()

            rows = res.data or []
            if scope:
                rows = [r for r in rows if r.get("scope") == scope or r.get("memory_scope") == scope]
            return rows
        except Exception as e:
            raise SupaError(f"search_memories_rpc 失敗（RPC 或參數可能不一致）：{e}") from e
