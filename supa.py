from __future__ import annotations

from supabase import create_client
from datetime import datetime
from zoneinfo import ZoneInfo
import json


class Supa:
    def __init__(self, url: str, key: str):
        if not url or not key:
            raise RuntimeError("Supabase URL/KEY 未設定")
        self.sb = create_client(url, key)

    # ---------- context / profile ----------
    def get_context(self, user_id: str, default_owner_id: str, default_wife_id: str) -> dict:
        # resolve tenant:
        # 1) profiles
        prof = self._get_profile_row(user_id)
        if prof:
            tenant_id = prof["tenant_id"]
        else:
            # 2) contacts by line_user_id
            tenant_id = None
            c = self.sb.table("saturday_contacts").select("tenant_id").eq("line_user_id", user_id).limit(1).execute()
            if c.data:
                tenant_id = c.data[0]["tenant_id"]
            # 3) fallbacks
            if not tenant_id:
                if user_id in (default_owner_id, default_wife_id):
                    tenant_id = default_owner_id
                else:
                    tenant_id = user_id

            # create profile
            role = "owner" if user_id == default_owner_id else "member"
            address_me = "先生" if user_id == default_owner_id else ("夫人" if user_id == default_wife_id else "先生/女士")
            self.sb.table("saturday_profiles").upsert({
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
                "address_me": address_me,
                "morning_time": "07:00",
                "morning_enabled": True,
                "share_confirmed": False
            }).execute()
            prof = self._get_profile_row(user_id)

        # speaker label (prompt)
        speaker_label = prof.get("address_me") or ("先生" if prof.get("role") == "owner" else "成員")
        default_address_me = prof.get("address_me") or "先生/女士"

        return {
            "tenant_id": prof["tenant_id"],
            "profile": prof,
            "speaker_label": speaker_label,
            "default_address_me": default_address_me
        }

    def _get_profile_row(self, user_id: str) -> dict | None:
        res = self.sb.table("saturday_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else None

    def set_profile(self, tenant_id: str, user_id: str, fields: dict):
        payload = {"user_id": user_id, "tenant_id": tenant_id, **fields}
        self.sb.table("saturday_profiles").upsert(payload).execute()

    # ---------- contacts / nicknames ----------
    def bind_contact(self, tenant_id: str, canonical_name: str, line_user_id: str) -> tuple[bool, str]:
        canonical_name = canonical_name.strip()
        line_user_id = line_user_id.strip()
        if not canonical_name or not line_user_id.startswith("U"):
            return False, "格式錯誤：綁定 <稱呼> <Uxxxx...>"

        # upsert
        self.sb.table("saturday_contacts").upsert({
            "tenant_id": tenant_id,
            "canonical_name": canonical_name,
            "line_user_id": line_user_id
        }, on_conflict="tenant_id,canonical_name").execute()

        # ensure member profile exists under same tenant
        self.sb.table("saturday_profiles").upsert({
            "user_id": line_user_id,
            "tenant_id": tenant_id,
            "role": "member",
            "address_me": canonical_name,
            "morning_time": "07:00",
            "morning_enabled": False,
            "share_confirmed": False
        }).execute()

        return True, f"✅ 已綁定：{canonical_name} -> {line_user_id}"

    def unbind_contact(self, tenant_id: str, canonical_name: str) -> str:
        self.sb.table("saturday_contacts").delete().eq("tenant_id", tenant_id).eq("canonical_name", canonical_name).execute()
        return f"✅ 已解除綁定：{canonical_name}"

    def list_bindings_text(self, tenant_id: str, viewer_user_id: str) -> str:
        res = self.sb.table("saturday_contacts").select("canonical_name,line_user_id").eq("tenant_id", tenant_id).order("canonical_name").execute()
        if not res.data:
            return "目前沒有綁定任何成員。"

        lines = ["【已綁定成員】"]
        for r in res.data:
            lines.append(f"- {r['canonical_name']}：{r['line_user_id']}")
        lines.append("")
        lines.append("你也可以自訂稱呼：我稱呼 夫人 為 媽媽")
        return "\n".join(lines)

    def set_nickname(self, tenant_id: str, viewer_user_id: str, target_name: str, nickname: str) -> str:
        target_id, target_display = self.resolve_target_user(tenant_id, viewer_user_id, target_name)
        if not target_id:
            return f"我找不到「{target_name}」。先用「我的綁定」確認。"
        self.sb.table("saturday_nicknames").upsert({
            "tenant_id": tenant_id,
            "viewer_user_id": viewer_user_id,
            "target_user_id": target_id,
            "nickname": nickname
        }, on_conflict="tenant_id,viewer_user_id,target_user_id").execute()
        return f"✅ 好的，已把你對「{target_display}」的稱呼設為「{nickname}」。"

    def resolve_target_user(self, tenant_id: str, viewer_user_id: str, target_text: str) -> tuple[str | None, str | None]:
        target_text = (target_text or "").strip()
        if not target_text:
            return None, None

        # 1) if direct user id
        if target_text.startswith("U") and len(target_text) > 10:
            return target_text, target_text

        # 2) viewer nickname match
        n = self.sb.table("saturday_nicknames").select("target_user_id,nickname").eq("tenant_id", tenant_id).eq("viewer_user_id", viewer_user_id).eq("nickname", target_text).limit(1).execute()
        if n.data:
            uid = n.data[0]["target_user_id"]
            return uid, target_text

        # 3) canonical contact
        c = self.sb.table("saturday_contacts").select("line_user_id,canonical_name").eq("tenant_id", tenant_id).eq("canonical_name", target_text).limit(1).execute()
        if c.data:
            return c.data[0]["line_user_id"], c.data[0]["canonical_name"]

        # 4) special: owner
        owner = self._get_owner_user_id(tenant_id)
        if target_text in ("先生", "老公", "主人", "我老公") and owner:
            return owner, "先生"

        return None, None

    def _get_owner_user_id(self, tenant_id: str) -> str | None:
        res = self.sb.table("saturday_profiles").select("user_id").eq("tenant_id", tenant_id).eq("role", "owner").limit(1).execute()
        return res.data[0]["user_id"] if res.data else None

    def get_sender_label_for_receiver(self, tenant_id: str, sender_user_id: str, receiver_user_id: str) -> str:
        # receiver's nickname preference for sender
        n = self.sb.table("saturday_nicknames").select("nickname").eq("tenant_id", tenant_id).eq("viewer_user_id", receiver_user_id).eq("target_user_id", sender_user_id).limit(1).execute()
        if n.data:
            return n.data[0]["nickname"]

        # else: canonical name
        c = self.sb.table("saturday_contacts").select("canonical_name").eq("tenant_id", tenant_id).eq("line_user_id", sender_user_id).limit(1).execute()
        if c.data:
            return c.data[0]["canonical_name"]

        # else: role
        p = self._get_profile_row(sender_user_id)
        if p and p.get("role") == "owner":
            return "先生"
        return "成員"

    # ---------- memories ----------
    def save_short_memory(self, tenant_id: str, user_id: str, user_text: str, assistant_text: str, keep_last: int = 60):
        content = f"使用者：{user_text}\nSaturday：{assistant_text}"
        self.sb.table("saturday_memories").insert({
            "tenant_id": tenant_id,
            "owner_id": user_id,
            "memory_type": "short",
            "scope": "private",
            "content": content
        }).execute()

        # prune old short memories (keep last N)
        res = self.sb.table("saturday_memories").select("id").eq("tenant_id", tenant_id).eq("owner_id", user_id).eq("memory_type", "short").order("created_at", desc=True).execute()
        if res.data and len(res.data) > keep_last:
            to_delete = [r["id"] for r in res.data[keep_last:]]
            self.sb.table("saturday_memories").delete().in_("id", to_delete).execute()

    def load_short_memory(self, tenant_id: str, user_id: str, limit: int = 6) -> str:
        res = self.sb.table("saturday_memories").select("content").eq("tenant_id", tenant_id).eq("owner_id", user_id).eq("memory_type", "short").order("created_at", desc=True).limit(limit).execute()
        if not res.data:
            return "（無）"
        items = list(reversed([r["content"] for r in res.data]))
        return "\n\n".join(items)

    def save_long_memory(self, tenant_id: str, user_id: str, content: str, scope: str, embed_fn):
        emb = embed_fn(content)
        self.sb.table("saturday_memories").insert({
            "tenant_id": tenant_id,
            "owner_id": user_id,
            "memory_type": "long",
            "scope": scope,
            "content": content,
            "embedding": emb
        }).execute()

    def create_pending_share(self, tenant_id: str, user_id: str, content: str) -> str:
        res = self.sb.table("saturday_memories").insert({
            "tenant_id": tenant_id,
            "owner_id": user_id,
            "memory_type": "pending_share",
            "scope": "shared",
            "content": content
        }).execute()
        # supabase insert returns row if select enabled; fallback: return short marker
        try:
            return res.data[0]["id"]
        except Exception:
            return "pending"

    def confirm_pending_share(self, tenant_id: str, user_id: str, decision: str, embed_fn) -> tuple[bool, str]:
        # find latest pending
        res = self.sb.table("saturday_memories").select("*").eq("tenant_id", tenant_id).eq("owner_id", user_id).eq("memory_type", "pending_share").order("created_at", desc=True).limit(1).execute()
        if not res.data:
            return False, "目前沒有待確認的共享記憶。"

        row = res.data[0]
        content = row["content"]

        # delete pending
        self.sb.table("saturday_memories").delete().eq("id", row["id"]).execute()

        if decision == "private":
            # save as private long
            self.save_long_memory(tenant_id, user_id, content, scope="private", embed_fn=embed_fn)
            return True, "好的，已改為【私密】並記住（僅你可用）。"

        # shared
        self.save_long_memory(tenant_id, user_id, content, scope="shared", embed_fn=embed_fn)
        return True, "好的，已設為【家庭共享】並記住。"

    def search_long_memory(self, tenant_id: str, requester_id: str, query: str, embed_fn, match_threshold: float, match_count: int) -> str:
        emb = embed_fn(query)
        res = self.sb.rpc("match_memories", {
            "query_embedding": emb,
            "match_threshold": match_threshold,
            "match_count": match_count,
            "p_tenant_id": tenant_id,
            "p_requester_id": requester_id
        }).execute()
        if not res.data:
            return "（無）"
        lines = []
        for r in res.data:
            # 自然呈現
            scope = r.get("scope", "")
            prefix = "（共享）" if scope == "shared" else "（私密）"
            lines.append(f"{prefix} {r['content']}")
        return "\n".join(lines)

    def list_long_memories_text(self, tenant_id: str, requester_id: str) -> str:
        res = self.sb.table("saturday_memories").select("content,scope,owner_id,created_at").eq("tenant_id", tenant_id).eq("memory_type", "long").order("created_at", desc=True).limit(30).execute()
        if not res.data:
            return "目前沒有長期記憶。"

        out = ["【你可用的長期記憶（最近30筆內）】"]
        shown = 0
        for r in res.data:
            scope = r.get("scope")
            if scope == "private" and r.get("owner_id") != requester_id:
                continue
            label = "共享" if scope == "shared" else "私密"
            out.append(f"- ({label}) {r['content']}")
            shown += 1
        if shown == 0:
            return "目前沒有你可用的長期記憶（你的私密記憶不會對其他成員曝光）。"
        out.append("")
        out.append("提示：想新增就說「記住 ...」或「記住(共享) ...」")
        return "\n".join(out)

    def maybe_auto_save_long(self, tenant_id: str, user_id: str, user_text: str, assistant_text: str, embed_fn, llm_judge_fn):
        # 先用規則篩（避免每句都慢）
        trigger_keywords = ["我最愛", "我喜歡", "我不吃", "我住", "我的生日", "結婚", "紀念日", "習慣", "偏好"]
        if not any(k in user_text for k in trigger_keywords):
            return

        # 財務/健康永遠私密且預設不自動存（避免敏感）
        sensitive = ["錢", "收入", "貸款", "信用卡", "病", "診斷", "症狀", "憂鬱", "焦慮"]
        if any(k in user_text for k in sensitive):
            return

        doc = f"使用者：{user_text}\nSaturday：{assistant_text}"

        decision = llm_judge_fn(doc)
        # decision: "no" / "private" / "shared"
        if decision == "no":
            return
        scope = "shared" if decision == "shared" else "private"
        self.save_long_memory(tenant_id, user_id, user_text, scope=scope, embed_fn=embed_fn)

    # ---------- reminders ----------
    def create_reminder(self, tenant_id: str, created_by: str, to_user_id: str, title: str, due_at: str) -> str:
        res = self.sb.table("saturday_reminders").insert({
            "tenant_id": tenant_id,
            "created_by": created_by,
            "to_user_id": to_user_id,
            "title": title,
            "due_at": due_at,
            "status": "pending"
        }).execute()
        try:
            return res.data[0]["id"]
        except Exception:
            return "created"

    def fetch_due_reminders(self, now_yyyy_mm_dd_hhmm: str) -> list[dict]:
        res = self.sb.table("saturday_reminders").select("*").eq("status", "pending").lte("due_at", now_yyyy_mm_dd_hhmm).limit(50).execute()
        return res.data or []

    def mark_reminder_sent(self, tenant_id: str, reminder_id: str):
        self.sb.table("saturday_reminders").update({"status": "sent"}).eq("tenant_id", tenant_id).eq("id", reminder_id).execute()

    def list_reminders_text(self, tenant_id: str, user_id: str) -> str:
        res = self.sb.table("saturday_reminders").select("id,title,due_at,status,to_user_id").eq("tenant_id", tenant_id).eq("created_by", user_id).order("due_at").limit(30).execute()
        if not res.data:
            return "目前沒有你建立的提醒。"
        out = ["【我的提醒】"]
        for r in res.data:
            out.append(f"- ID:{r['id']}｜{r['due_at']}｜{r['title']}｜{r['status']}")
        out.append("取消：取消提醒 <ID>")
        return "\n".join(out)

    def cancel_reminder(self, tenant_id: str, user_id: str, reminder_id: str) -> str:
        # 只允許取消自己建立的
        self.sb.table("saturday_reminders").update({"status": "cancelled"}).eq("tenant_id", tenant_id).eq("id", reminder_id).eq("created_by", user_id).execute()
        return f"✅ 已嘗試取消提醒：{reminder_id}"

    def ensure_holiday_reminder(self, tenant_id: str, to_user_id: str, title: str, due_at: str):
        # 避免重複：用唯一 key (tenant_id,to_user_id,due_at,title)
        exist = self.sb.table("saturday_reminders").select("id").eq("tenant_id", tenant_id).eq("to_user_id", to_user_id).eq("due_at", due_at).eq("title", title).limit(1).execute()
        if exist.data:
            return
        self.sb.table("saturday_reminders").insert({
            "tenant_id": tenant_id,
            "created_by": "system",
            "to_user_id": to_user_id,
            "title": title,
            "due_at": due_at,
            "status": "pending"
        }).execute()

    # ---------- morning targets / tenants / members ----------
    def list_morning_targets(self, hhmm: str) -> list[dict]:
        res = self.sb.table("saturday_profiles").select("tenant_id,user_id,home_city,zodiac_sign").eq("morning_enabled", True).eq("morning_time", hhmm).execute()
        return res.data or []

    def list_tenants(self) -> list[str]:
        res = self.sb.table("saturday_profiles").select("tenant_id").execute()
        return sorted(list(set([r["tenant_id"] for r in (res.data or [])])))

    def list_member_user_ids(self, tenant_id: str) -> list[str]:
        res = self.sb.table("saturday_profiles").select("user_id").eq("tenant_id", tenant_id).execute()
        return [r["user_id"] for r in (res.data or [])]

    # ---------- news cache for "看新聞 N" ----------
    def set_last_news_cache(self, tenant_id: str, user_id: str, items: list[dict]):
        self.sb.table("saturday_profiles").upsert({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "last_news_cache": json.dumps(items, ensure_ascii=False)
        }).execute()

    def get_last_news_cache(self, tenant_id: str, user_id: str) -> list[dict] | None:
        res = self.sb.table("saturday_profiles").select("last_news_cache").eq("tenant_id", tenant_id).eq("user_id", user_id).limit(1).execute()
        if not res.data:
            return None
        raw = res.data[0].get("last_news_cache")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
