from datetime import datetime
from supabase import create_client

class Supa:
    """
    這版重點：所有 app.py 會呼叫的方法，這裡都一定存在（避免 AttributeError）
    且 get_context() 永遠回 tenant_id（避免 KeyError: tenant_id）
    """
    def __init__(self, url: str, service_role_key: str):
        self.sb = create_client(url, service_role_key)

    # ---------- tenant / member ----------
    def get_context(self, user_id: str, default_owner_id: str = "", default_wife_id: str = "") -> dict:
        """
        tenant_id 規則：
        - 有 DEFAULT_OWNER_ID：所有人都以 DEFAULT_OWNER_ID 為 tenant（你這個 OA 的家）
        - 沒有 DEFAULT_OWNER_ID：第一次講話的人自成一個 tenant（適合每客戶一個 OA）
        """
        if default_owner_id:
            tenant_id = default_owner_id
            self.ensure_tenant_owner(tenant_id, default_owner_id)
        else:
            # 嘗試從 membership 找 tenant
            tenant_id = self.find_tenant_id_by_user(user_id)
            if not tenant_id:
                tenant_id = user_id
                self.ensure_tenant_owner(tenant_id, user_id)

        # 確保本人至少是 member（避免之後提醒/互傳被擋）
        self.ensure_member(tenant_id, user_id)

        me = self.get_member_row(tenant_id, user_id)
        role = (me.get("role") or "member") if me else "member"
        canonical = me.get("canonical_name") if me else ""

        # honorific：你要「先生/夫人」口氣
        if user_id == default_owner_id:
            honorific = "先生"
        elif user_id == default_wife_id:
            honorific = "夫人"
        else:
            # 若 owner 綁定過名字，例如「小孩」「閨密」，就用那個
            honorific = canonical or "先生/女士"

        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": role,
            "canonical_name": canonical or "",
            "honorific": honorific,
            "owner_id": default_owner_id or tenant_id,
        }

    def ensure_tenant_owner(self, tenant_id: str, owner_id: str):
        # tenants
        self.sb.table("saturday_tenants").upsert(
            {"tenant_id": tenant_id, "owner_id": owner_id},
            on_conflict="tenant_id",
        ).execute()
        # owner as member
        self.sb.table("saturday_tenant_users").upsert(
            {
                "tenant_id": tenant_id,
                "user_id": owner_id,
                "role": "owner",
                "canonical_name": "先生",
                "is_active": True,
            },
            on_conflict="tenant_id,user_id",
        ).execute()
        # prefs
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": owner_id},
            on_conflict="tenant_id,user_id",
        ).execute()

    def find_tenant_id_by_user(self, user_id: str) -> str:
        res = (
            self.sb.table("saturday_tenant_users")
            .select("tenant_id")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["tenant_id"]
        return ""

    def ensure_member(self, tenant_id: str, user_id: str):
        # 若沒有資料就建立 member
        row = self.get_member_row(tenant_id, user_id)
        if not row:
            self.sb.table("saturday_tenant_users").insert(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role": "member",
                    "canonical_name": "",
                    "is_active": True,
                }
            ).execute()
            self.sb.table("saturday_user_prefs").upsert(
                {"tenant_id": tenant_id, "user_id": user_id},
                on_conflict="tenant_id,user_id",
            ).execute()

    def is_member(self, tenant_id: str, user_id: str) -> bool:
        row = self.get_member_row(tenant_id, user_id)
        return bool(row and row.get("is_active"))

    def get_member_row(self, tenant_id: str, user_id: str) -> dict:
        res = (
            self.sb.table("saturday_tenant_users")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else {}

    def bind_member(self, tenant_id: str, label: str, user_id: str, added_by: str):
        # upsert member
        self.sb.table("saturday_tenant_users").upsert(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role": "member",
                "canonical_name": label,
                "is_active": True,
                "added_by": added_by,
            },
            on_conflict="tenant_id,user_id",
        ).execute()
        # ensure prefs
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": user_id},
            on_conflict="tenant_id,user_id",
        ).execute()

    def unbind_member(self, tenant_id: str, label: str) -> bool:
        res = (
            self.sb.table("saturday_tenant_users")
            .select("user_id")
            .eq("tenant_id", tenant_id)
            .eq("canonical_name", label)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return False
        uid = res.data[0]["user_id"]
        self.sb.table("saturday_tenant_users").update({"is_active": False}).eq("tenant_id", tenant_id).eq("user_id", uid).execute()
        return True

    def list_members_text(self, tenant_id: str, viewer_id: str) -> str:
        res = (
            self.sb.table("saturday_tenant_users")
            .select("user_id,role,canonical_name,is_active")
            .eq("tenant_id", tenant_id)
            .eq("is_active", True)
            .execute()
        )
        if not res.data:
            return "目前沒有成員。"
        lines = ["【成員清單】"]
        for r in res.data:
            name = r["canonical_name"] or "（未命名）"
            lines.append(f"- {name} | {r['role']} | {r['user_id']}")
        lines.append("\n你也可以設定『我稱呼 XXX 為 YYY』，讓自己講話更自然。")
        return "\n".join(lines)

    def set_nickname(self, tenant_id: str, viewer_id: str, target_user_id: str, nickname: str):
        self.sb.table("saturday_nicknames").upsert(
            {
                "tenant_id": tenant_id,
                "viewer_user_id": viewer_id,
                "target_user_id": target_user_id,
                "nickname": nickname,
            },
            on_conflict="tenant_id,viewer_user_id,target_user_id",
        ).execute()

    def resolve_target_user_id(self, tenant_id: str, viewer_id: str, name: str) -> str:
        name = (name or "").strip()
        if not name:
            return ""

        # 直接給 user_id
        if name.startswith("U") and len(name) >= 20:
            return name

        # 先找 viewer 自己的暱稱
        res = (
            self.sb.table("saturday_nicknames")
            .select("target_user_id")
            .eq("tenant_id", tenant_id)
            .eq("viewer_user_id", viewer_id)
            .eq("nickname", name)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["target_user_id"]

        # 再找 canonical_name（owner 綁定的名字）
        res2 = (
            self.sb.table("saturday_tenant_users")
            .select("user_id")
            .eq("tenant_id", tenant_id)
            .eq("canonical_name", name)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if res2.data:
            return res2.data[0]["user_id"]

        # 常用別名：先生->owner
        if name in ("先生", "主人", "老公"):
            ten = self.sb.table("saturday_tenants").select("owner_id").eq("tenant_id", tenant_id).limit(1).execute()
            if ten.data:
                return ten.data[0]["owner_id"]

        return ""

    def label_of(self, user_id: str, tenant_id: str, viewer_id: str = "") -> str:
        """
        viewer_id 有傳就優先顯示 viewer 自己設定的暱稱（更人性）
        """
        if viewer_id:
            res = (
                self.sb.table("saturday_nicknames")
                .select("nickname")
                .eq("tenant_id", tenant_id)
                .eq("viewer_user_id", viewer_id)
                .eq("target_user_id", user_id)
                .limit(1)
                .execute()
            )
            if res.data and res.data[0].get("nickname"):
                return res.data[0]["nickname"]

        row = self.get_member_row(tenant_id, user_id)
        if row and row.get("canonical_name"):
            return row["canonical_name"]
        if row and row.get("role") == "owner":
            return "先生"
        return "成員"

    # ---------- prefs ----------
    def get_user_prefs(self, user_id: str, tenant_id: str) -> dict:
        res = (
            self.sb.table("saturday_user_prefs")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return {"city": "", "zodiac": "", "morning_time": "07:00"}
        d = res.data[0]
        return {
            "city": d.get("city") or "",
            "zodiac": d.get("zodiac") or "",
            "morning_time": d.get("morning_time") or "07:00",
            "morning_prefs": d.get("morning_prefs") or {},
            "last_morning_sent": d.get("last_morning_sent") or None,
        }

    def set_city(self, user_id: str, tenant_id: str, city: str):
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": user_id, "city": city},
            on_conflict="tenant_id,user_id",
        ).execute()

    def set_zodiac(self, user_id: str, tenant_id: str, zodiac: str):
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": user_id, "zodiac": zodiac},
            on_conflict="tenant_id,user_id",
        ).execute()

    def set_morning_time(self, user_id: str, tenant_id: str, hhmm: str):
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": user_id, "morning_time": hhmm},
            on_conflict="tenant_id,user_id",
        ).execute()

    def set_last_morning_sent(self, user_id: str, tenant_id: str, today_yyyy_mm_dd: str):
        self.sb.table("saturday_user_prefs").upsert(
            {"tenant_id": tenant_id, "user_id": user_id, "last_morning_sent": today_yyyy_mm_dd},
            on_conflict="tenant_id,user_id",
        ).execute()

    # ---------- turns (short memory) ----------
    def save_turn(self, tenant_id: str, user_id: str, role: str, user_text: str, bot_text: str):
        self.sb.table("saturday_turns").insert(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role": role,
                "user_text": user_text,
                "bot_text": bot_text,
            }
        ).execute()

    def load_recent_turns(self, tenant_id: str, user_id: str, limit: int = 6) -> list:
        res = (
            self.sb.table("saturday_turns")
            .select("user_text,bot_text,created_at")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(reversed(res.data)) if res.data else []

    # ---------- long memory ----------
    def save_memory(self, tenant_id: str, user_id: str, scope: str, content: str):
        self.sb.table("saturday_memories").insert(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "scope": scope,
                "content": content,
            }
        ).execute()

    def list_recent_memories(self, tenant_id: str, user_id: str, limit: int = 20) -> list:
        res = (
            self.sb.table("saturday_memories")
            .select("content,scope,created_at")
            .eq("tenant_id", tenant_id)
            .or_(f"scope.eq.shared,user_id.eq.{user_id}")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def search_memories(self, tenant_id: str, user_id: str, query: str, limit: int = 8) -> list:
        # RPC：同時做 trigram + tsv，並且只回傳「共享 + 自己私密」
        res = self.sb.rpc(
            "saturday_search_memories",
            {
                "p_tenant_id": tenant_id,
                "p_user_id": user_id,
                "p_query": query,
                "p_limit": limit,
            },
        ).execute()
        return res.data or []

    # ---------- reminders ----------
    def create_reminder(self, tenant_id: str, created_by: str, target_user_id: str, due_at, message: str) -> str:
        res = self.sb.table("saturday_reminders").insert(
            {
                "tenant_id": tenant_id,
                "created_by": created_by,
                "target_user_id": target_user_id,
                "due_at": due_at.isoformat(),
                "message": message,
                "status": "pending",
            }
        ).execute()
        return (res.data[0]["id"] if res.data else "")

    def fetch_due_reminders(self, now_dt: datetime) -> list:
        res = (
            self.sb.table("saturday_reminders")
            .select("*")
            .eq("status", "pending")
            .lte("due_at", now_dt.isoformat())
            .order("due_at", desc=False)
            .limit(50)
            .execute()
        )
        return res.data or []

    def mark_reminder_sent(self, reminder_id: str, now_dt: datetime):
        self.sb.table("saturday_reminders").update(
            {"status": "sent", "sent_at": now_dt.isoformat()}
        ).eq("id", reminder_id).execute()

    # ---------- morning targets (RPC) ----------
    def list_morning_targets(self, hhmm: str, today_yyyy_mm_dd: str) -> list:
        res = self.sb.rpc(
            "saturday_list_morning_targets",
            {"p_hhmm": hhmm, "p_today": today_yyyy_mm_dd},
        ).execute()
        return res.data or []
