import httpx
import asyncio
from app.config import get_settings

settings = get_settings()

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchange OAuth code for YouTube access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            }
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> str:
    """Get a fresh access token using the refresh token."""
    if not refresh_token:
        raise Exception("No refresh token. Please disconnect and reconnect your YouTube account.")
        
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": refresh_token,
                "client_id":     settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "grant_type":    "refresh_token",
            }
        )
        if resp.status_code != 200:
            raise Exception(f"Google Token Error: {resp.text}")
            
        return resp.json()["access_token"]


async def get_channel_info(access_token: str) -> dict:
    """Get YouTube channel ID, title and subscriber count."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{YOUTUBE_API}/channels",
            params={
                "part": "snippet,statistics",
                "mine": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            raise Exception("No YouTube channel found for this account")

        channel = items[0]
        return {
            "channel_id":       channel["id"],
            "title":            channel["snippet"]["title"],
            "description":      channel["snippet"].get("description", ""),
            "subscriber_count": int(channel["statistics"].get("subscriberCount", 0)),
            "video_count":      int(channel["statistics"].get("videoCount", 0)),
            "view_count":       int(channel["statistics"].get("viewCount", 0)),
            "thumbnail":        channel["snippet"]["thumbnails"].get("default", {}).get("url", ""),
        }


async def upload_video(
    access_token: str,
    video_url: str,
    title: str,
    description: str,
    tags: list[str] = None,
    category_id: str = "22",      # 22 = People & Blogs
    privacy: str = "public",
    is_short: bool = False,
) -> dict:
    """
    Upload a video to YouTube.
    Downloads from R2 URL then uploads to YouTube using resumable upload.
    Returns video ID and URL.
    """
    # Add #Shorts to title/description for YouTube Shorts
    if is_short:
        if "#Shorts" not in title:
            title = f"{title} #Shorts"
        if "#Shorts" not in description:
            description = f"{description}\n\n#Shorts"

    async with httpx.AsyncClient(timeout=300.0) as client:
        # Download video from R2
        video_resp = await client.get(video_url)
        video_resp.raise_for_status()
        video_bytes = video_resp.content

        # Step 1: Initiate resumable upload
        init_resp = await client.post(
            f"{YOUTUBE_UPLOAD}/videos",
            params={
                "uploadType": "resumable",
                "part":       "snippet,status",
            },
            headers={
                "Authorization":           f"Bearer {access_token}",
                "Content-Type":            "application/json",
                "X-Upload-Content-Type":   "video/mp4",
                "X-Upload-Content-Length": str(len(video_bytes)),
            },
            json={
                "snippet": {
                    "title":       title[:100],   # YouTube max 100 chars
                    "description": description[:5000],
                    "tags":        (tags or [])[:500],
                    "categoryId":  category_id,
                },
                "status": {
                    "privacyStatus":           privacy,
                    "selfDeclaredMadeForKids": False,
                }
            }
        )
        init_resp.raise_for_status()
        upload_url = init_resp.headers["Location"]

        # Step 2: Upload video bytes
        upload_resp = await client.put(
            upload_url,
            content=video_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "video/mp4",
            }
        )
        upload_resp.raise_for_status()
        video_id = upload_resp.json()["id"]

        return {
            "video_id": video_id,
            "url":      f"https://youtube.com/watch?v={video_id}",
            "is_short": is_short,
        }


async def get_video_analytics(
    video_id: str,
    access_token: str,
) -> dict:
    """Get view count and engagement for a published video."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{YOUTUBE_API}/videos",
            params={
                "part": "statistics",
                "id":   video_id,
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if resp.status_code != 200:
            return {}

        items = resp.json().get("items", [])
        if not items:
            return {}

        stats = items[0].get("statistics", {})
        return {
            "views":    int(stats.get("viewCount", 0)),
            "likes":    int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }
