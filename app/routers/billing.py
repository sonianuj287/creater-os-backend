from fastapi import APIRouter, HTTPException, Query
from app.config import get_settings
from app.services.db_service import get_supabase

settings = get_settings()
router = APIRouter(prefix="/billing", tags=["billing"])

# ── Plans ─────────────────────────────────────────────────────

PLANS = {
    "free": {
        "name": "Free",
        "price_inr": 0,
        "limits": {
            "ideas_per_month":    2,
            "uploads_per_month":  2,
            "exports_per_upload": 1,
            "watermark":          True,
            "scheduling":         False,
        }
    },
    "creator": {
        "name": "Creator",
        "price_inr": 1599,
        "limits": {
            "ideas_per_month":    50,
            "uploads_per_month":  20,
            "exports_per_upload": 3,
            "watermark":          False,
            "scheduling":         True,
        }
    },
    "pro": {
        "name": "Pro",
        "price_inr": 3999,
        "limits": {
            "ideas_per_month":    -1,
            "uploads_per_month":  -1,
            "exports_per_upload": -1,
            "watermark":          False,
            "scheduling":         True,
        }
    },
}


# ── Public endpoints ──────────────────────────────────────────

@router.get("/plans")
async def get_plans():
    """Return all available plans and their limits."""
    return {"plans": PLANS}


@router.get("/status/{user_id}")
async def get_billing_status(user_id: str):
    """Get current plan and limits for a user."""
    supabase  = get_supabase()
    profile   = supabase.table("profiles")\
        .select("plan")\
        .eq("id", user_id)\
        .single()\
        .execute()

    if not profile.data:
        raise HTTPException(status_code=404, detail="User not found")

    plan_name = profile.data.get("plan", "free")
    plan_info = PLANS.get(plan_name, PLANS["free"])

    return {
        "plan":   plan_name,
        "name":   plan_info["name"],
        "limits": plan_info["limits"],
        "price":  plan_info["price_inr"],
    }


# ── Admin endpoints ───────────────────────────────────────────

@router.post("/admin/set-plan")
async def admin_set_plan(
    user_id:   str = Query(..., description="Supabase user UUID"),
    plan:      str = Query(..., description="free | creator | pro"),
    admin_key: str = Query(..., description="Your ADMIN_SECRET_KEY"),
):
    """
    Manually upgrade or downgrade a user's plan.
    Protected by admin_key — only you can call this.

    Usage:
    POST /billing/admin/set-plan?user_id=xxx&plan=creator&admin_key=your-secret
    """
    # Verify admin key
    if not settings.admin_secret_key or admin_key != settings.admin_secret_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    # Validate plan
    if plan not in PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan '{plan}'. Must be one of: {', '.join(PLANS.keys())}"
        )

    supabase = get_supabase()

    # Check user exists
    profile = supabase.table("profiles")\
        .select("id, plan")\
        .eq("id", user_id)\
        .single()\
        .execute()

    if not profile.data:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    old_plan = profile.data.get("plan", "free")

    # Update plan
    supabase.table("profiles")\
        .update({"plan": plan})\
        .eq("id", user_id)\
        .execute()

    return {
        "success":  True,
        "user_id":  user_id,
        "old_plan": old_plan,
        "new_plan": plan,
        "limits":   PLANS[plan]["limits"],
        "message":  f"User upgraded from {old_plan} to {plan}",
    }


@router.get("/admin/users")
async def admin_list_users(
    admin_key: str = Query(...),
    plan:      str = Query(default="all"),
):
    """
    List all users and their plans.
    Useful for seeing who's on which plan.
    """
    if not settings.admin_secret_key or admin_key != settings.admin_secret_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    supabase = get_supabase()
    query    = supabase.table("profiles").select("id, full_name, plan, created_at")

    if plan != "all":
        query = query.eq("plan", plan)

    result = query.order("created_at", desc=True).execute()
    return {"users": result.data or [], "count": len(result.data or [])}
