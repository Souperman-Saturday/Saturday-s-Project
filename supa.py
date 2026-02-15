# supa.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client


class SupaError(RuntimeError):
    pass


@dataclass
class SupaConfig:
    url: str
    key: str


class Supa:
    """
    Supabase 存取層（V7.x 兼容版）
    目標：
    - 修掉 Supa.__init__ 參數錯誤
    - 補回 app.py / scheduler.py 依賴的方法（get_context / list_morning_targets）
    - 表或欄位不存在時「不崩潰」，回預設值，讓 webhook 可繼續回覆
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
    # 基礎：讓外部可以繼續 table/rpc
    # ----------------------------
    def table(self, name: str):
        return self.sb.table(name)

    def rpc(self, fn: str, args: Dict[str, Any]):
        return self.sb.rpc(fn, args)

    def ping(self) -> bool:
        """輕量連線測試（不保證表存在，只保證 key/url 可用）"""
        try:
            # 若表不存在也可能噴錯，但至少可以讓你在 log 看懂
            self.sb.table("saturday_user_settings").select("user_id").limit(1).execute()
            return True
        except Exception as e:
            raise SupaError(f"Supabase 連線測試失敗：{e}") from e

    # ----------------------------
    # 兼容：app.py 需要的 get_context
    # ----------------------------
    def get_context(self, user_id: str, default_owner_id: str = "", default_wife_id: str = "") -> Dict[str, Any]:
        """
        回傳使用者上下文（避免 app.py 因查不到綁定而炸掉）
        結構盡量穩：
        {
          "user_id": "...",
          "owner_id": "...",
          "role": "先生|夫人|家人|訪客",
          "is_owner": bool,
          "tz": "Asia/Taipei"
        }

        綁定優先順序：
        1) 若 user_id == default_owner_id => owner
        2) 若 user_id == default_wife_id => wife (owner=default_owner_id)
        3) 若有表 saturday_bindings（owner_id, member_id, label） => 依 member_id 找 owner
        4) 都沒有 => 若 default_owner_id 有值：當訪客（owner=default_owner_id），否則 user 自己當 owner
        """
        user_id = (user_id or "").strip()
        default_owner_id = (default_owner_id or "").strip()
        default_wife_id = (default_wife_id or "").strip()

        ctx = {
            "user_id": user_id,
            "owner_id": default_owner_id or user_id,
            "role": "訪客",
            "is_owner": False,
            "tz": "Asia/Taipei",
        }

        # 1) default owner
        if default_owner_id and user_id == default_owner_id:
            ctx["owner_id"] = user_id
            ctx["role"] = "先生"
            ctx["is_owner"] = True
            return ctx

        # 2) default wife
        if default_wife_id and user_id == default_wife_id:
            ctx["owner_id"] = default_owner_id or user_id
            ctx["role"] = "夫人"
            ctx["is_owner"] = False
            return ctx

        # 3) 查綁定表（若存在）
        # 表名固定用 saturday_bindings：owner_id, member_id, label
        try:
            res = (
                self.sb.table("saturday_bindings")
                .select("owner_id,label")
                .eq("member_id", user_id)
                .limit(1)
                .execute()
            )
            row = (res.data or [None])[0]
            if row and row.get("owner_id"):
                ctx["owner_id"] = row["owner_id"]
                # 若 label 是 "夫人" 就套用；否則統一叫「家人」
                ctx["role"] = "夫人" if row.get("label") == "夫人" else "家人"
                ctx["is_owner"] = False
                return ctx
        except Exception:
            # 沒有表/沒權限/欄位不同都不該讓 webhook 爆炸
            pass

        # 4) fallback：如果 default_owner_id 有設，視為訪客；沒設就把 user 當 owner
        if not default_owner_id:
            ctx["owner_id"] = user_id
            ctx["role"] = "先生"
            ctx["is_owner"] = True
        return ctx

    # ----------------------------
    # 兼容：scheduler.py 需要的 list_morning_targets
    # ----------------------------
    def list_morning_targets(self, hhmm: str) -> List[Dict[str, Any]]:
        """
        找出需要在 hhmm（例如 07:00）發晨報的 user（owner）
        使用表：saturday_user_settings（user_id,key,value）
        需要：
          - key = 'morning_time' value = '07:00'
        可選：
          - key = 'morning_enabled' value = '1' / 'true' / 'on'

        回傳 list[profile]：
        [
          {"user_id": "...", "owner_id": "...", "tz": "Asia/Taipei"}
        ]
        """
        hhmm = (hhmm or "").strip()
        if not hhmm:
            return []

        try:
            # 取出 morning_time == hhmm 的 user_id 清單
            res = (
                self.sb.table("saturday_user_settings")
                .select("user_id,value")
                .eq("key", "morning_time")
                .eq("value", hhmm)
                .limit(500)
                .execute()
            )
            rows = res.data or []
            user_ids = list({r["user_id"] for r in rows if r.get("user_id")})

            if not user_ids:
                return []

            # 若有 morning_enabled，就過濾掉 disabled
            enabled_set: Optional[set] = None
            try:
                res2 = (
                    self.sb.table("saturday_user_settings")
                    .select("user_id,value")
                    .eq("key", "morning_enabled")
                    .in_("user_id", user_ids)
                    .limit(500)
                    .execute()
                )
                rows2 = res2.data or []
                # 沒設定 morning_enabled 的當作 enabled
                enabled_set = set(user_ids)
                for r in rows2:
                    v = str(r.get("value", "")).strip().lower()
                    if v in ("0", "false", "off", "no"):
                        enabled_set.discard(r.get("user_id"))
            except Exception:
                enabled_set = set(user_ids)

            return [{"user_id": uid, "owner_id": uid, "tz": "Asia/Taipei"} for uid in sorted(enabled_set)]
        except Exception:
            # 表不存在/欄位不同/權限問題：不要讓 scheduler 炸
            return []

    # ----------------------------
    # 提醒（scheduler 會用到）
    # ----------------------------
    def fetch_due_reminders(self, now_yyyy_mm_dd_hhmm: str, limit: int = 50) -> List[Dict[str, Any]]:
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
            self.sb.table("saturday_reminders").update(
                {"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", reminder_id).execute()
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
        try:
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
        payload = {
            "user_id": user_id,
            "key": key,
            "value": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
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
