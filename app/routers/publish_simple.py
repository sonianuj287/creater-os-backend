from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import httpx

from app.config import get_settings
from app.services.db_service import get_supabase
from app.services.instagram_service import post_reel, get_user_info

settings = get_settings()
router = APIRouter(prefix="/post", tags=["post"])

GRAPH_URL = "https://graph.instagram.com/v21.0"


# ── Request models ────────────────────────────────────────────

class InstagramPostRequest(BaseModel):
    user_id:    str
    project_id: str
    output_id:  str
    video_url:  str       # Public R2 presigned URL
    caption:    str


class InstagramTestRequest(BaseModel):
    video_url: str
    caption:   str


# ── Verify test token on startup ──────────────────────────────

@router.get("/instagram/me")
async def get_instagram_me():
    """
    Returns the Instagram account info for the test token.
    Use this to verify the token is valid and get the user ID.
    """
    token = settings.instagram_test_token
    if not token:
        raise HTTPException(status_code=400, detail="INSTAGRAM_TEST_TOKEN not set")

    try:
        info = await get_user_info(token)
        return {
            "username":  info.get("username"),
            "user_id":   info.get("id"),
            "followers": info.get("followers_count", 0),
            "token_status": "valid",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token error: {str(e)}")


# ── Post Reel using test token ────────────────────────────────

@router.post("/instagram/reel")
async def post_instagram_reel(request: InstagramPostRequest):
    """
    Post a video as an Instagram Reel using the saved test token.
    video_url must be a publicly accessible URL (R2 presigned URL).
    """
    token = settings.instagram_test_token
    if not token:
        raise HTTPException(status_code=400, detail="INSTAGRAM_TEST_TOKEN not set in Railway")

    # Get Instagram user ID from token
    try:
        info = await get_user_info(token)
        ig_user_id = info["id"]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not get Instagram user: {str(e)}")

    # Always regenerate a fresh presigned URL — stored URLs expire after 24h
    from app.services.storage_service import create_presigned_download_url
    s3_key = f"outputs/{request.user_id}/{request.project_id}/9x16.mp4"
    try:
        fresh_video_url = await create_presigned_download_url(s3_key)
    except Exception:
        fresh_video_url = request.video_url  # fallback to provided URL

    try:
        result = await post_reel(
            instagram_user_id=ig_user_id,
            access_token=token,
            video_url=fresh_video_url,
            caption=request.caption,
        )

        # Save post record to DB
        supabase = get_supabase()
        supabase.table("scheduled_posts").insert({
            "user_id":          request.user_id,
            "project_id":       request.project_id,
            "output_id":        request.output_id,
            "platform":         "instagram",
            "platform_post_id": result["media_id"],
            "caption":          request.caption,
            "scheduled_for":    datetime.utcnow().isoformat(),
            "posted_at":        datetime.utcnow().isoformat(),
            "status":           "posted",
        }).execute()

        return {
            "success":  True,
            "media_id": result["media_id"],
            "message":  "Posted to Instagram successfully",
            "profile":  f"https://instagram.com/{info.get('username')}",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Post failed: {str(e)}")


# ── Quick test post (no DB, just test the API) ────────────────

@router.post("/instagram/test-post")
async def test_instagram_post(request: InstagramTestRequest):
    """
    Test posting without saving to DB.
    Use this to verify the full pipeline works before wiring to UI.
    """
    token = settings.instagram_test_token
    if not token:
        raise HTTPException(status_code=400, detail="INSTAGRAM_TEST_TOKEN not set")

    info = await get_user_info(token)
    ig_user_id = info["id"]

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Step 1: Create container
        container_resp = await client.post(
            f"{GRAPH_URL}/{ig_user_id}/media",
            params={
                "media_type":   "REELS",
                "video_url":    request.video_url,
                "caption":      request.caption,
                "access_token": token,
            }
        )

        if container_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Container creation failed: {container_resp.text}"
            )

        container_id = container_resp.json()["id"]

        # Step 2: Poll for processing
        import asyncio
        for i in range(20):
            await asyncio.sleep(10)
            status_resp = await client.get(
                f"{GRAPH_URL}/{container_id}",
                params={
                    "fields":       "status_code,status",
                    "access_token": token,
                }
            )
            status = status_resp.json()
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") == "ERROR":
                raise HTTPException(status_code=400, detail=f"Video processing error: {status}")

        # Step 3: Publish
        publish_resp = await client.post(
            f"{GRAPH_URL}/{ig_user_id}/media_publish",
            params={
                "creation_id":  container_id,
                "access_token": token,
            }
        )

        if publish_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Publish failed: {publish_resp.text}"
            )

        media_id = publish_resp.json()["id"]
        return {
            "success":  True,
            "media_id": media_id,
            "username": info.get("username"),
        }
