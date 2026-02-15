import os
import uuid
from datetime import datetime, timezone

from supabase import create_client


class MemoryStore:
    """
    只做「穩」：Profiles / Aliases / Memories / Tasks
    """

    def __init__(self, supabase_url: str, supabase_key: str):
        if not supabase_url or not supabase_key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 未設定")
        self.sb = create_client(supabase_url, supabase_key)

    # -----------------------------
    # Primary user
    # -----------------------------
    def get_primary_user(self) -> str | None:
        r = self.sb.table("user_settings").select("*").eq("k", "primary_user").limit(1).execute()
        if r.data:
            return r.data[0]["v"]
        return None

    def set_primary_user(self, user_id: str):
        self.sb.table("user_settings").upsert({"k": "primary_user", "v": user_id}).execute()

    # -----------------------------
    # Profiles
    # -----------------------------
    def ensure_profile(self, user_id: str):
        r = self.sb.table("profiles").select("*").eq("user_id", user_id).limit(1).execute()
        if r.data:
            return
        self.sb.table("profiles").insert(
            {
                "user_id": user_id,
                "city": None,
                "zodiac": None,
                "morning_time": "07:00",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

    def get_city(self, user_id: str) -> str | None:
        r = self.sb.table("profiles").select("city").eq("user_id", user_id).limit(1).execute()
        return r.data[0]["city"] if r.data else None

    def set_city(self, user_id: str, city: str):
        self.sb.table("profiles").upsert({"user_id": user_id, "city": city}).execute()

    def get_zodiac(self, user_id: str) -> str | None:
        r = self.sb.table("profiles").select("zodiac").eq("user_id", user_id).limit(1).execute()
        return r.data[0]["zodiac"] if r.data else None

    def set_zodiac(self, user_id: str, zodiac: str):
        self.sb.table("profiles").upsert({"user_id": user_id, "zodiac": zodiac}).execute()

    def get_morning_time(self, user_id: str) -> str:
        r = self.sb.table("profiles").select("morning_time").eq("user_id", user_id).limit(1).execute()
        if r.data and r.data[0]["morning_time"]:
            return r.data[0]["morning_time"]
        return "07:00"

    def set_morning_time(self, user_id: str, hhmm: str):
        self.sb.table("profiles").upsert({"user_id": user_id, "morning_time": hhmm}).execute()

    # -----------------------------
    # Aliases (primary_user scope)
    # -----------------------------
    def get_aliases(self, primary_user_id: str) -> dict:
        r = self.sb.table("aliases").select("*").eq("primary_user_id", primary_user_id).execute()
        out = {}
        for row in (r.data or []):
            out[row["alias"]] = row["target_user_id"]
        return out

    def set_alias(self, primary_user_id: str, alias: str, target_user_id: str):
        self.sb.table("aliases").upsert(
            {
                "primary_user_id": primary_user_id,
                "alias": alias,
                "target_user_id": target_user_id,
            },
            on_conflict="primary_user_id,alias",
        ).execute()

    def remove_alias(self, primary_user_id: str, alias: str):
        self.sb.table("aliases").delete().eq("primary_user_id", primary_user_id).eq("alias", alias).execute()

    # -----------------------------
    # Memories
    # -----------------------------
    def add_memory(self, user_id: str, content: str, share: bool):
        self.sb.table("memories").insert(
            {
                "user_id": user_id,
                "content": content,
                "share": share,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

    def list_memories(self, user_id: str) -> list[dict]:
        r = self.sb.table("memories").select("*").eq("user_id", user_id).order("id", desc=True).execute()
        return r.data or []

    # -----------------------------
    # Tasks
    # -----------------------------
    def create_task(self, user_id: str, run_at_iso: str, content: str) -> str:
        task_id = uuid.uuid4().hex[:10]
        self.sb.table("tasks").insert(
            {
                "task_id": task_id,
                "user_id": user_id,
                "run_at": run_at_iso,
                "content": content,
                "status": "scheduled",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
        return task_id

    def list_tasks(self, user_id: str) -> list[dict]:
        r = self.sb.table("tasks").select("*").eq("user_id", user_id).eq("status", "scheduled").order("run_at").execute()
        return r.data or []

    def get_task(self, task_id: str) -> dict | None:
        r = self.sb.table("tasks").select("*").eq("task_id", task_id).limit(1).execute()
        return r.data[0] if r.data else None

    def cancel_task(self, task_id: str) -> bool:
        r = self.sb.table("tasks").update({"status": "cancelled"}).eq("task_id", task_id).execute()
        return bool(r.data)

    def mark_task_done(self, task_id: str):
        self.sb.table("tasks").update({"status": "done"}).eq("task_id", task_id).execute()

    def list_future_tasks_global(self, now_iso: str) -> list[dict]:
        r = (
            self.sb.table("tasks")
            .select("*")
            .eq("status", "scheduled")
            .gte("run_at", now_iso)
            .order("run_at")
            .execute()
        )
        return r.data or []
