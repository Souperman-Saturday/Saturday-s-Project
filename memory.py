import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from typing import Any, Optional

from supabase import create_client, Client

TZ = os.getenv("TZ", "Asia/Taipei")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[WARN] SUPABASE_URL / SUPABASE_KEY missing")

_client: Optional[Client] = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client

@dataclass
class Supa:
    client: Client

    def ensure_profile(self, user_id: str) -> None:
        # upsert blank profile
        self.client.table("saturday_profiles").upsert({
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).execute()

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        res = self.client.table("saturday_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        if res.data:
            return res.data[0]
        return None

    def upsert_profile(self, user_id: str, patch: dict[str, Any]) -> None:
        patch["user_id"] = user_id
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.client.table("saturday_profiles").upsert(patch).execute()

    def remember(self, owner_id: str, content: str, visibility: str) -> None:
        self.client.table("saturday_memories").insert({
            "owner_id": owner_id,
            "visibility": visibility,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()

    def recall(self, owner_id: str, q: str, limit: int) -> list[str]:
        # 不用 embedding，直接 ILIKE
        res = self.client.table("saturday_memories") \
            .select("content") \
            .eq("owner_id", owner_id) \
            .ilike("content", f"%{q}%") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return [r["content"] for r in (res.data or [])]

    def set_alias(self, owner_id: str, alias: str, target_user_id: str) -> None:
        self.client.table("saturday_contacts").upsert({
            "owner_id": owner_id,
            "alias": alias,
            "target_user_id": target_user_id,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="owner_id,alias").execute()

    def resolve_alias(self, owner_id: str, alias: str) -> str | None:
        res = self.client.table("saturday_contacts") \
            .select("target_user_id") \
            .eq("owner_id", owner_id) \
            .eq("alias", alias) \
            .limit(1).execute()
        if res.data:
            return res.data[0]["target_user_id"]
        return None

    def create_task(self, owner_id: str, run_at_iso: str, message: str) -> str:
        res = self.client.table("saturday_tasks").insert({
            "owner_id": owner_id,
            "run_at": run_at_iso,
            "message": message,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        return str(res.data[0]["id"]) if res.data else ""

    def get_due_tasks(self) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        res = self.client.table("saturday_tasks") \
            .select("*") \
            .eq("status", "pending") \
            .lte("run_at", now_iso) \
            .limit(50).execute()
        return res.data or []

    def mark_task_done(self, task_id: int) -> None:
        self.client.table("saturday_tasks").update({
            "status": "done",
            "done_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", task_id).execute()

    def list_tasks_today(self, owner_id: str, start_iso: str, end_iso: str) -> list[str]:
        res = self.client.table("saturday_tasks") \
            .select("run_at,message,status") \
            .eq("owner_id", owner_id) \
            .gte("run_at", start_iso) \
            .lt("run_at", end_iso) \
            .order("run_at", desc=False).execute()
        items = []
        for r in (res.data or []):
            status = r.get("status")
            items.append(f"{r['run_at'][:16].replace('T',' ')}（{status}）: {r['message']}")
        return items

    def is_event_processed(self, event_id: str) -> bool:
        res = self.client.table("saturday_events").select("event_id").eq("event_id", event_id).limit(1).execute()
        return bool(res.data)

    def mark_event_processed(self, event_id: str) -> None:
        self.client.table("saturday_events").insert({
            "event_id": event_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()

    def get_users_due_morning(self, now_hhmm: str, today_iso: str) -> list[dict[str, Any]]:
        # morning_time == now_hhmm 且 last_morning_date != today
        res = self.client.table("saturday_profiles") \
            .select("user_id,morning_time,last_morning_date") \
            .eq("morning_time", now_hhmm) \
            .limit(200).execute()
        due = []
        for r in (res.data or []):
            if r.get("last_morning_date") != today_iso:
                due.append(r)
        return due

    def mark_morning_sent(self, user_id: str, today_iso: str) -> None:
        self.client.table("saturday_profiles").update({
            "last_morning_date": today_iso,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("user_id", user_id).execute()


supa = Supa(get_client())

# ---------- helper wrappers ----------
def ensure_profile(user_id: str) -> None:
    supa.ensure_profile(user_id)

def get_profile(user_id: str) -> dict[str, Any] | None:
    return supa.get_profile(user_id)

def upsert_profile_city_zodiac(user_id: str, city: str | None, zodiac: str | None, morning_time: str | None) -> None:
    patch: dict[str, Any] = {}
    if city:
        patch["city"] = city
    if zodiac:
        patch["zodiac"] = zodiac
    if morning_time:
        patch["morning_time"] = morning_time
    if patch:
        supa.upsert_profile(user_id, patch)

def remember_text(owner_id: str, content: str, visibility: str = "private") -> None:
    supa.remember(owner_id, content, visibility)

def recall_text(owner_id: str, query: str, limit: int = 5) -> list[str]:
    return supa.recall(owner_id, query, limit)

def set_contact_alias(owner_id: str, alias: str, target_user_id: str) -> None:
    supa.set_alias(owner_id, alias, target_user_id)

def resolve_contact_alias(owner_id: str, alias: str) -> str | None:
    return supa.resolve_alias(owner_id, alias)

def create_task(owner_id: str, run_at: datetime, message: str) -> str:
    return supa.create_task(owner_id, run_at.astimezone(timezone.utc).isoformat(), message)

def list_tasks_today(owner_id: str) -> list[str]:
    # 今日 00:00~24:00 UTC(粗略)；用 UTC 也可以用 TZ 更精準，這裡先穩定不爆
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end = start.replace(day=start.day)  # placeholder
    # 安全處理：用 +1 day
    end = start.replace()  # keep tz
    from datetime import timedelta
    end = start + timedelta(days=1)
    return supa.list_tasks_today(owner_id, start.isoformat(), end.isoformat())

def mark_task_done(task_id: int) -> None:
    supa.mark_task_done(task_id)

def is_event_processed(event_id: str) -> bool:
    return supa.is_event_processed(event_id)

def mark_event_processed(event_id: str) -> None:
    supa.mark_event_processed(event_id)

def get_users_due_morning() -> list[dict[str, Any]]:
    from tools_time import now_tz
    now = now_tz()
    hhmm = now.strftime("%H:%M")
    today = now.date().isoformat()
    return supa.get_users_due_morning(now_hhmm=hhmm, today_iso=today)

def mark_morning_sent(user_id: str) -> None:
    from tools_time import now_tz
    today = now_tz().date().isoformat()
    supa.mark_morning_sent(user_id, today_iso=today)

def should_send_morning(user_id: str) -> bool:
    # not used in v7.1 (batch check), keep for extension
    prof = get_profile(user_id) or {}
    today = now_tz().date().isoformat()
    return prof.get("last_morning_date") != today
