from datetime import datetime, date
from app.services.db_service import get_supabase

PLAN_LIMITS = {
    "free": {
        "ideas_per_month":    2,
        "uploads_per_month":  2,
        "exports_per_upload": 1,
        "scheduling":         False,
        "watermark":          True,
    },
    "creator": {
        "ideas_per_month":    50,
        "uploads_per_month":  20,
        "exports_per_upload": 3,
        "scheduling":         True,
        "watermark":          False,
    },
    "pro": {
        "ideas_per_month":    -1,
        "uploads_per_month":  -1,
        "exports_per_upload": -1,
        "scheduling":         True,
        "watermark":          False,
    },
}


def get_user_plan(user_id: str) -> str:
    supabase = get_supabase()
    result = supabase.table("profiles").select("plan").eq("id", user_id).single().execute()
    return result.data.get("plan", "free") if result.data else "free"


def get_monthly_usage(user_id: str, action: str) -> int:
    """Count how many times a user performed an action this calendar month."""
    supabase = get_supabase()
    month_start = date.today().replace(day=1).isoformat()

    result = supabase.table("usage_logs")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("action", action)\
        .gte("created_at", month_start)\
        .execute()

    return result.count or 0


def log_usage(user_id: str, action: str, metadata: dict = None):
    """Record a usage event."""
    supabase = get_supabase()
    supabase.table("usage_logs").insert({
        "user_id":  user_id,
        "action":   action,
        "metadata": metadata or {},
    }).execute()


def check_and_consume(user_id: str, action: str) -> dict:
    """
    Check if user can perform action. If yes, log it.
    Returns: { "allowed": bool, "used": int, "limit": int, "plan": str }
    """
    plan       = get_user_plan(user_id)
    limits     = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    limit_key  = f"{action}s_per_month"
    limit      = limits.get(limit_key, -1)

    # -1 means unlimited (pro)
    if limit == -1:
        log_usage(user_id, action)
        return {"allowed": True, "used": -1, "limit": -1, "plan": plan}

    used = get_monthly_usage(user_id, action)

    if used >= limit:
        return {
            "allowed": False,
            "used":    used,
            "limit":   limit,
            "plan":    plan,
            "upgrade_message": f"You've used {used}/{limit} {action}s this month. Upgrade to Creator (₹100/mo) for more.",
        }

    log_usage(user_id, action)
    return {"allowed": True, "used": used + 1, "limit": limit, "plan": plan}


def get_usage_summary(user_id: str) -> dict:
    """Get full usage summary for a user — used by the frontend."""
    plan   = get_user_plan(user_id)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    ideas_used   = get_monthly_usage(user_id, "idea")
    uploads_used = get_monthly_usage(user_id, "upload")

    return {
        "plan":   plan,
        "limits": limits,
        "usage": {
            "ideas":   {"used": ideas_used,   "limit": limits["ideas_per_month"]},
            "uploads": {"used": uploads_used, "limit": limits["uploads_per_month"]},
        },
        "reset_date": date.today().replace(day=1).replace(
            month=date.today().month % 12 + 1
        ).isoformat(),
    }
