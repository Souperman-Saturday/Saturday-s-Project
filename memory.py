import os
from supabase import create_client

SUPA_URL = os.getenv("SUPABASE_URL", "")
SUPA_KEY = os.getenv("SUPABASE_KEY", "")

class DB:
    def __init__(self):
        if not SUPA_URL or not SUPA_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 未設定")
        self.sb = create_client(SUPA_URL, SUPA_KEY)

    # ---------- processed events (idempotency) ----------
    def seen_event(self, webhook_event_id: str) -> bool:
        if not webhook_event_id:
            return False
        r = self.sb.table("saturday_processed_events").select("webhook_event_id").eq("webhook_event_id", webhook_event_id).execute()
        return bool(r.data)

    def mark_event(self, webhook_event_id: str) -> None:
        if not webhook_event_id:
            return
        # upsert style: insert ignore conflict
        try:
            self.sb.table("saturday_processed_events").insert({"webhook_event_id": webhook_event_id}).execute()
        except Exception:
            pass

    # ---------- profiles ----------
    def get_profile(self, user_id: str):
        r = self.sb.table("saturday_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        return (r.data[0] if r.data else None)

    def ensure_profile(self, user_id: str, display_name: str | None = None):
        p = self.get_profile(user_id)
        if p:
            return p
        payload = {"user_id": user_id, "display_name": display_name or None}
        self.sb.table("saturday_profiles").insert(payload).execute()
        return self.get_profile(user_id)

    def update_profile(self, user_id: str, patch: dict):
        self.sb.table("saturday_profiles").update(patch).eq("user_id", user_id).execute()
        return self.get_profile(user_id)

    # ---------- contacts ----------
    def set_contact_alias(self, owner_id: str, contact_id: str, alias: str):
        # remove existing by alias or contact_id
        self.sb.table("saturday_contacts").delete().eq("owner_id", owner_id).eq("alias", alias).execute()
        self.sb.table("saturday_contacts").delete().eq("owner_id", owner_id).eq("contact_id", contact_id).execute()
        self.sb.table("saturday_contacts").insert({
            "owner_id": owner_id,
            "contact_id": contact_id,
            "alias": alias
        }).execute()

    def delete_contact_alias(self, owner_id: str, alias: str):
        self.sb.table("saturday_contacts").delete().eq("owner_id", owner_id).eq("alias", alias).execute()

    def list_contacts(self, owner_id: str):
        r = self.sb.table("saturday_contacts").select("alias,contact_id").eq("owner_id", owner_id).execute()
        return r.data or []

    def resolve_alias(self, owner_id: str, alias: str) -> str | None:
        r = self.sb.table("saturday_contacts").select("contact_id").eq("owner_id", owner_id).eq("alias", alias).limit(1).execute()
        return (r.data[0]["contact_id"] if r.data else None)

    # ---------- memories ----------
    def add_memory(self, owner_id: str, subject_id: str, scope: str, content: str):
        scope = scope.lower().strip()
        if scope not in ("private", "shared"):
            scope = "private"
        self.sb.table("saturday_memories").insert({
            "owner_id": owner_id,
            "subject_id": subject_id,
            "scope": scope,
            "content": content
        }).execute()

    def list_memories(self, owner_id: str, subject_id: str, scopes: list[str], limit: int = 30):
        q = self.sb.table("saturday_memories").select("id,scope,content,created_at").eq("owner_id", owner_id).eq("subject_id", subject_id)
        # supabase python doesn't have in_ always stable; do manual filter
        r = q.order("id", desc=True).limit(limit).execute()
        data = r.data or []
        scopes_set = set([s.lower() for s in scopes])
        return [x for x in data if x.get("scope") in scopes_set]

    # ---------- tasks ----------
    def add_task(self, owner_id: str, subject_id: str, due_at_iso: str, text: str):
        self.sb.table("saturday_tasks").insert({
            "owner_id": owner_id,
            "subject_id": subject_id,
            "due_at": due_at_iso,
            "text": text,
            "status": "pending"
        }).execute()

    def list_tasks(self, owner_id: str, subject_id: str, status: str = "pending", limit: int = 20):
        r = (self.sb.table("saturday_tasks")
             .select("id,due_at,text,status")
             .eq("owner_id", owner_id)
             .eq("subject_id", subject_id)
             .eq("status", status)
             .order("due_at", desc=False)
             .limit(limit)
             .execute())
        return r.data or []

    def cancel_task(self, owner_id: str, subject_id: str, task_id: str):
        self.sb.table("saturday_tasks").update({"status": "cancelled"}).eq("owner_id", owner_id).eq("subject_id", subject_id).eq("id", task_id).execute()

    def due_tasks(self, now_iso: str):
        # get pending tasks due <= now
        r = (self.sb.table("saturday_tasks")
             .select("id,owner_id,subject_id,due_at,text")
             .eq("status", "pending")
             .lte("due_at", now_iso)
             .order("due_at", desc=False)
             .limit(50)
             .execute())
        return r.data or []

    def mark_task_sent(self, task_id: str):
        self.sb.table("saturday_tasks").update({"status": "sent"}).eq("id", task_id).execute()
