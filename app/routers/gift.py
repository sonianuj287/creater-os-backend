from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from app.services.db_service import get_supabase

router = APIRouter(prefix="/gift", tags=["gift"])


class RedeemRequest(BaseModel):
    user_id: str
    code:    str


@router.post("/redeem")
async def redeem_gift_card(request: RedeemRequest):
    """
    Redeem a gift card code to upgrade a user's plan.
    Validates: code exists, is active, not already redeemed.
    """
    supabase = get_supabase()
    code = request.code.strip().upper()

    # 1. Find the gift card
    result = supabase.table("gift_cards")\
        .select("*")\
        .eq("code", code)\
        .single()\
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail={
            "error":   "invalid_code",
            "message": "This code doesn't exist. Check for typos.",
        })

    card = result.data

    # 2. Check if active
    if not card["is_active"]:
        raise HTTPException(status_code=400, detail={
            "error":   "code_disabled",
            "message": "This code has been disabled.",
        })

    # 3. Check if already redeemed
    if card["redeemed_by"]:
        raise HTTPException(status_code=400, detail={
            "error":   "already_redeemed",
            "message": "This code has already been used.",
        })

    # 4. Calculate expiry
    expires_at = (datetime.utcnow() + timedelta(days=card["duration_days"])).isoformat()

    # 5. Mark card as redeemed
    supabase.table("gift_cards").update({
        "redeemed_by":  request.user_id,
        "redeemed_at":  datetime.utcnow().isoformat(),
        "is_active":    False,
    }).eq("id", card["id"]).execute()

    # 6. Upgrade user plan
    supabase.table("profiles").update({
        "plan":           card["plan"],
        "plan_expires_at": expires_at,
    }).eq("id", request.user_id).execute()

    return {
        "success":      True,
        "plan":         card["plan"],
        "expires_at":   expires_at,
        "duration_days": card["duration_days"],
        "message":      f"Welcome to {card['plan'].title()} plan! Valid for {card['duration_days']} days.",
    }


@router.get("/admin/list")
async def list_gift_cards(admin_key: str):
    """Admin: view all gift cards and redemption status."""
    from app.config import get_settings
    settings = get_settings()
    if admin_key != settings.admin_secret_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

    supabase = get_supabase()
    result = supabase.table("gift_cards").select("*").order("created_at", desc=True).execute()
    return {"cards": result.data, "total": len(result.data)}


@router.post("/admin/create")
async def create_gift_cards(
    admin_key: str,
    plan: str = "creator",
    count: int = 5,
    duration_days: int = 30,
    note: str = "",
):
    """Admin: generate new gift card codes."""
    from app.config import get_settings
    import random, string
    settings = get_settings()
    if admin_key != settings.admin_secret_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if plan not in ("creator", "pro"):
        raise HTTPException(status_code=400, detail="plan must be creator or pro")
    if count > 100:
        raise HTTPException(status_code=400, detail="max 100 codes at a time")

    supabase = get_supabase()
    prefix = "CREATOR" if plan == "creator" else "PRO"
    codes  = []

    for _ in range(count):
        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        code   = f"{prefix}-{suffix}"
        codes.append({
            "code":         code,
            "plan":         plan,
            "duration_days": duration_days,
            "is_active":    True,
            "note":         note,
        })

    supabase.table("gift_cards").insert(codes).execute()
    return {
        "created": count,
        "codes":   [c["code"] for c in codes],
        "plan":    plan,
        "days":    duration_days,
    }


@router.post("/admin/disable")
async def disable_code(admin_key: str, code: str):
    """Admin: disable a specific gift card code."""
    from app.config import get_settings
    settings = get_settings()
    if admin_key != settings.admin_secret_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

    supabase = get_supabase()
    supabase.table("gift_cards").update({"is_active": False}).eq("code", code.upper()).execute()
    return {"disabled": code.upper()}
