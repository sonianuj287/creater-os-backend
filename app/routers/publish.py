import httpx
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
    redirect_uri = f"{settings.backend_url}/publish/instagram/callback"
    
    # Use Facebook OAuth (not api.instagram.com) for Business Login
    oauth_url = (
        f"https://www.facebook.com/dialog/oauth"
        f"?client_id={settings.instagram_app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=instagram_basic,instagram_content_publish,instagram_manage_insights,pages_show_list,pages_read_engagement"
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
    if error:
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=instagram_denied")

    user_id      = state
    redirect_uri = f"{settings.backend_url}/publish/instagram/callback"

    try:
        async with httpx.AsyncClient() as client:
            # Exchange code for Facebook user access token
            token_resp = await client.get(
                "https://graph.facebook.com/v21.0/oauth/access_token",
                params={
                    "client_id":     settings.instagram_app_id,
                    "client_secret": settings.instagram_app_secret,
                    "redirect_uri":  redirect_uri,
                    "code":          code,
                }
            )
            token_resp.raise_for_status()
            token_data   = token_resp.json()
            fb_token     = token_data["access_token"]

            # Get Facebook pages linked to this user
            pages_resp = await client.get(
                "https://graph.facebook.com/v21.0/me/accounts",
                params={"access_token": fb_token}
            )
            pages_resp.raise_for_status()
            pages = pages_resp.json().get("data", [])

            if not pages:
                return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=no_facebook_page")

            page_token = pages[0]["access_token"]
            page_id    = pages[0]["id"]

            # Get Instagram Business Account linked to this page
            ig_resp = await client.get(
                f"https://graph.facebook.com/v21.0/{page_id}",
                params={
                    "fields":       "instagram_business_account",
                    "access_token": page_token,
                }
            )
            ig_resp.raise_for_status()
            ig_data = ig_resp.json()
            ig_account = ig_data.get("instagram_business_account")

            if not ig_account:
                return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=no_instagram_account")

            ig_user_id = ig_account["id"]

            # Get Instagram user details
            user_resp = await client.get(
                f"https://graph.facebook.com/v21.0/{ig_user_id}",
                params={
                    "fields":       "username,followers_count",
                    "access_token": page_token,
                }
            )
            user_resp.raise_for_status()
            user_info = user_resp.json()

        # Save to DB using page_token for posting
        supabase = get_supabase()
        supabase.table("connected_accounts").upsert({
            "user_id":              user_id,
            "platform":             "instagram",
            "platform_user_id":     ig_user_id,
            "platform_username":    user_info.get("username", ""),
            "access_token":         page_token,
            "follower_count":       user_info.get("followers_count", 0),
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

        supabase = get_supabase()
        supabase.table("connected_accounts").upsert({
            "user_id":              user_id,
            "platform":             "youtube",
            "platform_user_id":     channel_info["channel_id"],
            "platform_username":    channel_info["title"],
            "platform_display_name": channel_info["title"],
            "access_token":         access_token,
            "refresh_token":        refresh_token,
            "follower_count":       channel_info["subscriber_count"],
            "is_active":            True,
        }, on_conflict="user_id,platform").execute()

        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?connected=youtube")

    except Exception as e:
        print(f"YouTube OAuth error: {e}")
        return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/publish?error=youtube_failed")


# ── YouTube posting ───────────────────────────────────────────

@router.post("/youtube/post")
async def post_to_youtube(request: PostYouTubeRequest):
    """Upload a video to YouTube."""
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
        # Refresh access token first
        access_token = await youtube_service_publish.refresh_access_token(acc["refresh_token"])

        result = await youtube_service_publish.upload_video(
            access_token=access_token,
            video_url=request.video_url,
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
