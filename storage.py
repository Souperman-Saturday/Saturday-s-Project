import json
import re
from datetime import datetime, date

from supabase import create_client


class Storage:
    def __init__(self, url: str, key: str):
        self.url = (url or "").strip()
        self.key = (key or "").strip()
        self.sb = None
        if self.url and self.key:
            self.sb = create_client(self.url, self.key)

    # -----------------------
    # profile
    # -----------------------
    def ensure_profile(self, user_id: str):
        if not self.sb:
            return
        # upsert
        self.sb.table("saturday_profiles").upsert({
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat()
        }, on_conflict="user_id").execute()

    def get_profile(self, user_id: str):
        if not self.sb:
            return {}
        res = self.sb.table("saturday_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else {}

    def get_all_profiles(self):
        if not self.sb:
            return []
        res = self.sb.table("saturday_profiles").select("*").execute()
        return res.data or []

    def update_profile(self, user_id: str, fields: dict):
        if not self.sb:
            return
        fields = dict(fields)
        fields["user_id"] = user_id
        self.sb.table("saturday_profiles").upsert(fields, on_conflict="user_id").execute()

    def set_role_label_if_empty(self, user_id: str, label: str):
        p = self.get_profile(user_id)
        if not p.get("role_label"):
            self.update_profile(user_id, {"role_label": label})

    # -----------------------
    # short turns
    # -----------------------
    def save_turn(self, user_id: str, who: str, text: str, at: datetime):
        if not self.sb:
            return
        self.sb.table("saturday_turns").insert({
            "user_id": user_id,
            "who": who,
            "text": text,
            "created_at": at.isoformat()
        }).execute()

    def get_recent_turns(self, user_id: str, limit: int = 10) -> str:
        if not self.sb:
            return ""
        res = self.sb.table("saturday_turns") \
            .select("who,text") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        rows = list(reversed(res.data or []))
        lines = []
        for r in rows:
            prefix = "你" if r["who"] == "user" else "Saturday"
            lines.append(f"{prefix}：{r['text']}")
        return "\n".join(lines)

    # -----------------------
    # alias binding (family / friends)
    # -----------------------
    def bind_alias(self, alias: str, target_user_id: str, created_by: str):
        if not self.sb:
            return
        self.sb.table("saturday_bindings").upsert({
            "alias": alias,
            "user_id": target_user_id,
            "created_by": created_by
        }, on_conflict="alias").execute()

    def unbind_alias(self, alias: str):
        if not self.sb:
            return
        self.sb.table("saturday_bindings").delete().eq("alias", alias).execute()

    def get_user_id_by_alias(self, alias: str):
        if not self.sb:
            return None
        res = self.sb.table("saturday_bindings").select("user_id").eq("alias", alias).limit(1).execute()
        return res.data[0]["user_id"] if res.data else None

    def alias_list_text(self):
        if not self.sb:
            return "（記憶庫未連線）"
        res = self.sb.table("saturday_bindings").select("alias,user_id").order("alias", desc=False).execute()
        if not res.data:
            return "目前沒有綁定。用法：綁定 夫人 Uxxxx"
        lines = ["📇 名冊："]
        for r in res.data:
            lines.append(f"- {r['alias']}：{r['user_id']}")
        return "\n".join(lines)

    # 每個人對每個人的稱呼（更人性）
    def upsert_callname(self, owner_user_id: str, target_user_id: str, callname: str):
        if not self.sb:
            return
        self.sb.table("saturday_callnames").upsert({
            "owner_user_id": owner_user_id,
            "target_user_id": target_user_id,
            "callname": callname
        }, on_conflict="owner_user_id,target_user_id").execute()

    def get_preferred_callname(self, receiver_id: str, sender_id: str):
        """
        接收者(receiver)對發送者(sender)的稱呼。
        例如：夫人看到你 => 用夫人設定的你名字。
        """
        if not self.sb:
            return None
        res = self.sb.table("saturday_callnames") \
            .select("callname") \
            .eq("owner_user_id", receiver_id) \
            .eq("target_user_id", sender_id) \
            .limit(1) \
            .execute()
        if res.data:
            return res.data[0]["callname"]

        # 沒設定就退回 profile.my_name 或 role_label
        p = self.get_profile(sender_id)
        return p.get("my_name") or p.get("role_label")

    # -----------------------
    # long memory
    # -----------------------
    def save_long_memory(self, owner_user_id: str, scope: str, content: str, embedding, created_at: datetime):
        if not self.sb:
            return
        self.sb.table("saturday_memories").insert({
            "owner_user_id": owner_user_id,
            "scope": scope,
            "content": content,
            "embedding": embedding,
            "created_at": created_at.isoformat()
        }).execute()

    def list_long_memories(self, requester_id: str, limit: int = 10):
        if not self.sb:
            return []
        # 只給「自己的私密」+「所有共享」
        res = self.sb.table("saturday_memories") \
            .select("content,scope") \
            .or_(f"scope.eq.shared,owner_user_id.eq.{requester_id}") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return res.data or []

    def search_long_memory(self, query_embedding, requester_id: str, threshold: float = 0.25, count: int = 6):
        if not self.sb:
            return []
        res = self.sb.rpc("match_memories", {
            "query_embedding": query_embedding,
            "match_threshold": float(threshold),
            "match_count": int(count),
            "p_requester_id": requester_id
        }).execute()
        return res.data or []

    # 何時該做向量搜尋（省時間）
    def should_memory_search(self, text: str) -> bool:
        t = (text or "")
        # 問習慣/喜好/住哪/生日/記得/以前說過
        keys = ["習慣", "喜歡", "最愛", "住哪", "住在", "生日", "記得", "以前", "你說過", "我說過", "我剛剛說"]
        return any(k in t for k in keys)

    def is_preference_like(self, content: str) -> bool:
        t = content
        keys = ["喜歡", "最愛", "討厭", "偏好", "習慣", "常常", "每天", "都會", "不吃", "愛喝"]
        return any(k in t for k in keys)

    # -----------------------
    # tasks (reminders)
    # -----------------------
    def create_task(self, created_by: str, target_user_id: str, due_at: datetime, message: str):
        if not self.sb:
            return None
        res = self.sb.table("saturday_tasks").insert({
            "created_by": created_by,
            "target_user_id": target_user_id,
            "due_at": due_at.isoformat(),
            "message": message,
            "status": "pending"
        }).execute()
        if res.data:
            return res.data[0]["id"]
        return None

    def get_due_tasks(self, now_dt: datetime):
        if not self.sb:
            return []
        res = self.sb.table("saturday_tasks") \
            .select("*") \
            .eq("status", "pending") \
            .lte("due_at", now_dt.isoformat()) \
            .order("due_at", desc=False) \
            .limit(50) \
            .execute()
        return res.data or []

    def mark_task_sent(self, task_id: int, sent_at: datetime):
        if not self.sb:
            return
        self.sb.table("saturday_tasks").update({
            "status": "sent",
            "sent_at": sent_at.isoformat()
        }).eq("id", task_id).execute()

    def mark_task_failed(self, task_id: int, err: str):
        if not self.sb:
            return
        self.sb.table("saturday_tasks").update({
            "status": "failed",
            "last_error": err[:400]
        }).eq("id", task_id).execute()

    def cancel_task(self, task_id: int, requester_id: str) -> bool:
        if not self.sb:
            return False
        # 只能取消自己建立的
        res = self.sb.table("saturday_tasks").update({
            "status": "cancelled"
        }).eq("id", task_id).eq("created_by", requester_id).execute()
        return bool(res.data)

    def list_tasks(self, user_id: str):
        if not self.sb:
            return []
        res = self.sb.table("saturday_tasks") \
            .select("id,due_at,message,status") \
            .eq("created_by", user_id) \
            .order("due_at", desc=False) \
            .limit(50) \
            .execute()
        return res.data or []

    # -----------------------
    # morning log
    # -----------------------
    def morning_already_sent(self, user_id: str, ymd: date) -> bool:
        if not self.sb:
            return False
        res = self.sb.table("saturday_morning_logs").select("user_id").eq("user_id", user_id).eq("ymd", ymd.isoformat()).limit(1).execute()
        return bool(res.data)

    def log_morning_sent(self, user_id: str, ymd: date, at: datetime):
        if not self.sb:
            return
        self.sb.table("saturday_morning_logs").upsert({
            "user_id": user_id,
            "ymd": ymd.isoformat(),
            "sent_at": at.isoformat(),
            "status": "sent"
        }, on_conflict="user_id,ymd").execute()

    def log_morning_failed(self, user_id: str, ymd: date, err: str):
        if not self.sb:
            return
        self.sb.table("saturday_morning_logs").upsert({
            "user_id": user_id,
            "ymd": ymd.isoformat(),
            "status": "failed",
            "last_error": err[:400]
        }, on_conflict="user_id,ymd").execute()

    # -----------------------
    # news cache（看新聞 N）
    # -----------------------
    def cache_news_items(self, user_id: str, items: list, at: datetime):
        if not self.sb:
            return
        self.sb.table("saturday_cache").upsert({
            "user_id": user_id,
            "key": "news_items",
            "value": json.dumps(items, ensure_ascii=False),
            "updated_at": at.isoformat()
        }, on_conflict="user_id,key").execute()

    def get_cached_news_items(self, user_id: str):
        if not self.sb:
            return None
        res = self.sb.table("saturday_cache").select("value").eq("user_id", user_id).eq("key", "news_items").limit(1).execute()
        if not res.data:
            return None
        try:
            return json.loads(res.data[0]["value"])
        except Exception:
            return None

    # -----------------------
    # 指令表（你要給客人的）
    # -----------------------
    def help_text(self) -> str:
        return """【Saturday 指令表（V7.1）】

一、基本設定
- 設定城市 台中
- 設定星座 獅子座
- 設定晨報時間 07:00
- 開啟晨報 / 關閉晨報

二、名冊（好友/家人）
- 綁定 夫人 Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
- 解除綁定 夫人
- 名冊

三、稱呼（每個人可不同）
- 叫我 Flynn
- 我叫 夫人 叫 寶貝   （你提到「夫人」時，我用「寶貝」稱呼她）
- 預設共享 / 預設私密  （生活喜好預設記憶權限）

四、記憶（長期）
- 記住 我早上習慣喝冰美式
- 記住(共享) 小孩晚上八點睡
- 我記得什麼

五、提醒（靠 /cron + UptimeRobot）
- 2分鐘後提醒 喝水
- 2分鐘後提醒 夫人 記得買奶粉
- 2026-02-14 18:30 提醒 帶尿布
- 我的提醒
- 取消提醒 123

六、資訊
- 今天日期 / 現在時間
- 今天天氣
- 明天台中天氣
- 台中未來三天天氣 / 未來三天天氣
- 今日運勢
- 全球新聞
- 全球新聞 AI
- 看新聞 3

七、傳訊（互傳）
- 傳訊給夫人 明天記得買奶粉
- 傳訊給先生 我到家了
""".strip()
