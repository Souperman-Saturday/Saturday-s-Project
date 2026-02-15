import os
from datetime import datetime
from zoneinfo import ZoneInfo
from supabase import create_client
import google.generativeai as genai

TZ = os.getenv("TZ", "Asia/Taipei")

def _now():
    return datetime.now(ZoneInfo(TZ))

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 未設定")
    return create_client(url, key)

def get_gemini():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未設定")
    genai.configure(api_key=api_key)
    return genai

def embed_text(text: str):
    g = get_gemini()
    try:
        res = g.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document"
        )
        return res["embedding"]
    except Exception as e:
        # embedding 失敗就回 None（仍可存，但向量搜尋會少一點效果）
        return None

def ensure_profile(line_user_id: str, display_name: str | None = None):
    sb = get_supabase()
    existing = sb.table("saturday_profiles").select("*").eq("line_user_id", line_user_id).execute()
    if existing.data:
        return existing.data[0]
    payload = {
        "line_user_id": line_user_id,
        "display_name": display_name,
        "home_city": "台中市",
        "zodiac": "獅子座",
        "morning_time": "07:00",
        "news_lang": "zh-TW",
        "news_region": "TW",
        "family_share_default": False
    }
    ins = sb.table("saturday_profiles").insert(payload).execute()
    return ins.data[0]

def update_profile(line_user_id: str, **kwargs):
    sb = get_supabase()
    kwargs["updated_at"] = _now().isoformat()
    upd = sb.table("saturday_profiles").update(kwargs).eq("line_user_id", line_user_id).execute()
    return upd.data[0] if upd.data else None

def add_memory(owner_user_id: str, content: str, memory_type: str = "long", scope: str = "private", importance: int = 1):
    sb = get_supabase()
    emb = None
    if memory_type in ("long", "pref"):
        emb = embed_text(content)
    row = {
        "owner_user_id": owner_user_id,
        "content": content,
        "memory_type": memory_type,
        "scope": scope,
        "importance": importance,
        "embedding": emb
    }
    ins = sb.table("saturday_memories").insert(row).execute()
    return ins.data[0] if ins.data else None

def search_memories(owner_user_id: str, query: str, scope: str = "private", k: int = 6, threshold: float = 0.68):
    sb = get_supabase()
    qemb = embed_text(query)
    if not qemb:
        return []
    res = sb.rpc("match_memories", {
        "query_embedding": qemb,
        "match_threshold": threshold,
        "match_count": k,
        "p_owner_user_id": owner_user_id,
        "p_scope": scope
    }).execute()
    return res.data or []

def add_task(owner_user_id: str, due_at_iso: str, payload: str, scope: str = "private"):
    sb = get_supabase()
    row = {
        "owner_user_id": owner_user_id,
        "due_at": due_at_iso,
        "payload": payload,
        "scope": scope,
        "status": "pending"
    }
    ins = sb.table("saturday_tasks").insert(row).execute()
    return ins.data[0] if ins.data else None

def fetch_due_tasks(limit: int = 50):
    sb = get_supabase()
    now_iso = _now().isoformat()
    res = (sb.table("saturday_tasks")
           .select("*")
           .eq("status", "pending")
           .lte("due_at", now_iso)
           .order("due_at", desc=False)
           .limit(limit)
           .execute())
    return res.data or []

def mark_task_sent(task_id: str):
    sb = get_supabase()
    upd = sb.table("saturday_tasks").update({"status": "sent", "sent_at": _now().isoformat()}).eq("id", task_id).execute()
    return upd.data[0] if upd.data else None

def morning_already_sent(owner_user_id: str, run_date: str) -> bool:
    sb = get_supabase()
    res = (sb.table("saturday_runs")
           .select("*")
           .eq("owner_user_id", owner_user_id)
           .eq("run_date", run_date)
           .eq("run_type", "morning")
           .execute())
    return bool(res.data)

def mark_morning_sent(owner_user_id: str, run_date: str):
    sb = get_supabase()
    sb.table("saturday_runs").insert({
        "owner_user_id": owner_user_id,
        "run_date": run_date,
        "run_type": "morning"
    }).execute()
