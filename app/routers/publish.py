from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime, timedelta

from app.config import get_settings
from app.services.db_service import get_supabase
from app.services import instagram_service, youtube_service_publish

settings = get_settings()
router = APIRouter(prefix="/publish", tags=["publish"])

FRONTEND_URL = settings.frontend_url


# ── Request models ────────────────────────────────────────────

class PostReelRequest(BaseModel):
    user_id:    str
    output_id:  str
    project_id: str
    video_url:  str
    caption:    str
    platform:   str = "instagram"


class PostYouTubeRequest(BaseModel):
    user_id:    str
    output_id:  str
    project_id: str
    video_url:  str
    title:      str
    description: str
    tags:       list[str] = []
    is_short:   bool = True
    privacy:    str = "public"


class SchedulePostRequest(BaseModel):
    user_id:      str
    output_id:    str
    project_id:   str
    platform:     str
    video_url:    str
    caption:      str
    scheduled_for: str  # ISO datetime string


# ── Instagram OAuth ───────────────────────────────────────────

@router.get("/instagram/connect")
async def instagram_connect(user_id: str = Query(...)):
    """
    Step 1: Redirect user to Instagram OAuth login page.
    Frontend calls this URL — user is redirected to Instagram.
    """
    redirect_uri = f"{settings.backend_url}/publish/instagram/callback"
    scope = "instagram_basic,instagram_content_publish,instagram_manage_insights"

    oauth_url = (
        f"https://api.instagram.com/oauth/authorize"
        f"?client_id={settings.instagram_app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        f"&response_type=code"
        f"&state={user_id}"
    )
    return RedirectResponse(url=oauth_url)


@router.get("/instagram/callback")
async def instagram_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """
    Step 2: Instagram redirects here with authorization code.
    Exchange for token and save to DB.
    """
    if error:
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=instagram_denied")

    if not code or not state:
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=invalid_callback")

    user_id = state
    redirect_uri = f"{settings.backend_url}/publish/instagram/callback"

    try:
        # Exchange code for long-lived token
        token_data = await instagram_service.exchange_code_for_token(code, redirect_uri)
        access_token = token_data["access_token"]
        ig_user_id   = str(token_data["user_id"])

        # Get user info
        user_info = await instagram_service.get_user_info(access_token)
        username  = user_info.get("username", "")
        followers = user_info.get("followers_count", 0)

        # Calculate token expiry (60 days)
        expires_at = (datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 5183944))).isoformat()

        # Save to DB
        supabase = get_supabase()
        supabase.table("connected_accounts").upsert({
            "user_id":              user_id,
            "platform":             "instagram",
            "platform_user_id":     ig_user_id,
            "platform_username":    username,
            "platform_display_name": username,
            "access_token":         access_token,
            "token_expires_at":     expires_at,
            "follower_count":       followers,
            "is_active":            True,
        }, on_conflict="user_id,platform").execute()

        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?connected=instagram")

    except Exception as e:
        print(f"Instagram OAuth error: {e}")
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=instagram_failed")


# ── Instagram posting ─────────────────────────────────────────

@router.post("/instagram/post")
async def post_to_instagram(request: PostReelRequest):
    """Post a video as an Instagram Reel."""
    supabase = get_supabase()

    # Get connected account
    account = supabase.table("connected_accounts")\
        .select("*")\
        .eq("user_id", request.user_id)\
        .eq("platform", "instagram")\
        .eq("is_active", True)\
        .single()\
        .execute()

    if not account.data:
        raise HTTPException(status_code=400, detail="Instagram account not connected")

    acc = account.data

    try:
        result = await instagram_service.post_reel(
            instagram_user_id=acc["platform_user_id"],
            access_token=acc["access_token"],
            video_url=request.video_url,
            caption=request.caption,
        )

        # Save post record
        supabase.table("scheduled_posts").insert({
            "user_id":         request.user_id,
            "project_id":      request.project_id,
            "output_id":       request.output_id,
            "platform":        "instagram",
            "platform_post_id": result["media_id"],
            "caption":         request.caption,
            "scheduled_for":   datetime.utcnow().isoformat(),
            "posted_at":       datetime.utcnow().isoformat(),
            "status":          "posted",
        }).execute()

        return {
            "success":  True,
            "media_id": result["media_id"],
            "message":  "Posted to Instagram successfully",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Instagram post failed: {str(e)}")


# ── YouTube OAuth ─────────────────────────────────────────────

@router.get("/youtube/connect")
async def youtube_connect(user_id: str = Query(...)):
    """Redirect user to YouTube OAuth login."""
    redirect_uri = f"{settings.backend_url}/publish/youtube/callback"
    scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

    oauth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.youtube_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={user_id}"
    )
    return RedirectResponse(url=oauth_url)


@router.get("/youtube/callback")
async def youtube_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Handle YouTube OAuth callback."""
    if error:
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=youtube_denied")

    user_id      = state
    redirect_uri = f"{settings.backend_url}/publish/youtube/callback"

    try:
        token_data   = await youtube_service_publish.exchange_code_for_token(code, redirect_uri)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")

        channel_info = await youtube_service_publish.get_channel_info(access_token)

        update_data = {
            "user_id":              user_id,
            "platform":             "youtube",
            "platform_user_id":     channel_info["channel_id"],
            "platform_username":    channel_info["title"],
            "platform_display_name": channel_info["title"],
            "access_token":         access_token,
            "follower_count":       channel_info["subscriber_count"],
            "is_active":            True,
        }
        if refresh_token:
            update_data["refresh_token"] = refresh_token

        supabase = get_supabase()
        supabase.table("connected_accounts").upsert(
            update_data, on_conflict="user_id,platform"
        ).execute()

        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?connected=youtube")

    except Exception as e:
        print(f"YouTube OAuth error: {e}")
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=youtube_failed")


# ── YouTube posting ───────────────────────────────────────────

@router.post("/youtube/post")
async def post_to_youtube(request: PostYouTubeRequest):
    """Upload a video to YouTube. Regenerates a fresh R2 URL before downloading."""
    supabase = get_supabase()

    account = supabase.table("connected_accounts")\
        .select("*")\
        .eq("user_id", request.user_id)\
        .eq("platform", "youtube")\
        .eq("is_active", True)\
        .single()\
        .execute()

    if not account.data:
        raise HTTPException(status_code=400, detail="YouTube account not connected")

    acc = account.data

    try:
        # Always generate a fresh presigned URL — stored URLs expire after 24h
        from app.services.storage_service import create_presigned_download_url
        from urllib.parse import urlparse, unquote

        # Extract exact S3 key from the frontend URL (safely handles assembled_9x16.mp4 vs 9x16.mp4)
        path = unquote(urlparse(request.video_url).path)
        s3_key = path[path.find("outputs/"):] if "outputs/" in path else f"outputs/{request.user_id}/{request.project_id}/9x16.mp4"
        fresh_url = await create_presigned_download_url(s3_key)

        # Refresh access token
        access_token = await youtube_service_publish.refresh_access_token(acc["refresh_token"])

        result = await youtube_service_publish.upload_video(
            access_token=access_token,
            video_url=fresh_url,
            title=request.title,
            description=request.description,
            tags=request.tags,
            is_short=request.is_short,
            privacy=request.privacy,
        )

        supabase.table("scheduled_posts").insert({
            "user_id":         request.user_id,
            "project_id":      request.project_id,
            "output_id":       request.output_id,
            "platform":        "youtube",
            "platform_post_id": result["video_id"],
            "caption":         request.description,
            "scheduled_for":   datetime.utcnow().isoformat(),
            "posted_at":       datetime.utcnow().isoformat(),
            "status":          "posted",
        }).execute()

        return {
            "success":  True,
            "video_id": result["video_id"],
            "url":      result["url"],
            "message":  "Uploaded to YouTube successfully",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"YouTube upload failed: {str(e)}")


# ── Test Instagram with saved token ──────────────────────────

@router.get("/instagram/test")
async def test_instagram_token():
    """
    Test the INSTAGRAM_TEST_TOKEN from Railway env.
    Verifies the token works before building full OAuth.
    """
    token = settings.instagram_test_token
    if not token:
        raise HTTPException(status_code=400, detail="INSTAGRAM_TEST_TOKEN not set in environment")

    try:
        info = await instagram_service.get_user_info(token)
        return {
            "status":   "token_valid",
            "username": info.get("username"),
            "user_id":  info.get("id"),
            "followers": info.get("followers_count"),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token invalid: {str(e)}")



class TokenVerifyRequest(BaseModel):
    token:   str
    user_id: str


@router.post("/instagram/test-token")
async def verify_instagram_token(request: TokenVerifyRequest):
    """Verify a user-supplied Instagram access token from the frontend form."""
    try:
        info = await instagram_service.get_user_info(request.token)
        if not info.get("id"):
            raise HTTPException(status_code=400, detail="Token did not return a valid user")
        return {
            "status":    "token_valid",
            "username":  info.get("username"),
            "user_id":   info.get("id"),
            "followers": info.get("followers_count", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid token: {str(e)}")

# ── Analytics pull ────────────────────────────────────────────

@router.post("/analytics/pull")
async def pull_analytics(user_id: str):
    """
    Pull analytics for all posts made 48h+ ago.
    Call this via a daily cron job.
    """
    supabase = get_supabase()

    cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
    posts = supabase.table("scheduled_posts")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("status", "posted")\
        .lt("posted_at", cutoff)\
        .execute()

    if not posts.data:
        return {"message": "No posts ready for analytics pull", "pulled": 0}

    accounts = supabase.table("connected_accounts")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("is_active", True)\
        .execute()

    account_map = {a["platform"]: a for a in (accounts.data or [])}
    pulled = 0

    for post in posts.data:
        platform = post["platform"]
        acc      = account_map.get(platform)
        if not acc or not post.get("platform_post_id"):
            continue

        try:
            metrics = {}
            if platform == "instagram":
                metrics = await instagram_service.get_media_insights(
                    post["platform_post_id"], acc["access_token"]
                )
            elif platform == "youtube":
                access_token = await youtube_service_publish.refresh_access_token(acc["refresh_token"])
                metrics = await youtube_service_publish.get_video_analytics(
                    post["platform_post_id"], access_token
                )

            if metrics:
                supabase.table("analytics_snapshots").insert({
                    "user_id":           user_id,
                    "post_id":           post["id"],
                    "platform":          platform,
                    "views":             metrics.get("reach", metrics.get("views", 0)),
                    "likes":             metrics.get("likes_count", metrics.get("likes", 0)),
                    "saves":             metrics.get("saved", 0),
                    "comments":          metrics.get("comments_count", metrics.get("comments", 0)),
                    "shares":            metrics.get("shares", 0),
                    "followers_gained":  0,
                }).execute()
                pulled += 1

        except Exception as e:
            print(f"Analytics pull failed for post {post['id']}: {e}")

    return {"message": f"Pulled analytics for {pulled} posts", "pulled": pulled}
