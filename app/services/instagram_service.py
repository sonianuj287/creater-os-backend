import httpx
import asyncio
from app.config import get_settings

settings = get_settings()

GRAPH_URL = "https://graph.instagram.com/v21.0"


async def get_user_info(access_token: str) -> dict:
    """Get Instagram user ID and username from access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_URL}/me",
            params={
                "fields": "id,username,account_type,followers_count",
                "access_token": access_token,
            }
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_long_lived_token(long_lived_token: str) -> dict:
    """
    Refresh a long-lived token before it expires (60 days).
    Returns new token + expiry.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.instagram.com/refresh_access_token",
            params={
                "grant_type":   "ig_refresh_token",
                "access_token": long_lived_token,
            }
        )
        resp.raise_for_status()
        return resp.json()


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """
    Exchange OAuth authorization code for a short-lived token,
    then upgrade to a long-lived token (60 days).
    """
    async with httpx.AsyncClient() as client:
        # Step 1: Short-lived token
        short_resp = await client.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id":     settings.instagram_app_id,
                "client_secret": settings.instagram_app_secret,
                "grant_type":    "authorization_code",
                "redirect_uri":  redirect_uri,
                "code":          code,
            }
        )
        short_resp.raise_for_status()
        short_data = short_resp.json()
        short_token = short_data["access_token"]
        user_id     = short_data["user_id"]

        # Step 2: Exchange for long-lived token
        long_resp = await client.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type":        "ig_exchange_token",
                "client_secret":     settings.instagram_app_secret,
                "access_token":      short_token,
            }
        )
        long_resp.raise_for_status()
        long_data = long_resp.json()

        return {
            "user_id":      user_id,
            "access_token": long_data["access_token"],
            "expires_in":   long_data.get("expires_in", 5183944),
        }


async def post_reel(
    instagram_user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
) -> dict:
    """
    Post a video as an Instagram Reel.
    video_url must be a publicly accessible URL (R2 presigned URL works).
    Returns the media ID of the published post.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:

        # Step 1: Create media container
        container_resp = await client.post(
            f"{GRAPH_URL}/{instagram_user_id}/media",
            params={
                "media_type":  "REELS",
                "video_url":   video_url,
                "caption":     caption,
                "access_token": access_token,
            }
        )
        container_resp.raise_for_status()
        container_id = container_resp.json()["id"]

        # Step 2: Wait for video processing (poll status)
        for attempt in range(20):  # max 3 min wait
            await asyncio.sleep(10)
            status_resp = await client.get(
                f"{GRAPH_URL}/{container_id}",
                params={
                    "fields":       "status_code,status",
                    "access_token": access_token,
                }
            )
            status_data = status_resp.json()
            status_code = status_data.get("status_code", "")

            if status_code == "FINISHED":
                break
            elif status_code == "ERROR":
                raise Exception(f"Instagram video processing failed: {status_data}")

        # Step 3: Publish the container
        publish_resp = await client.post(
            f"{GRAPH_URL}/{instagram_user_id}/media_publish",
            params={
                "creation_id":  container_id,
                "access_token": access_token,
            }
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json()["id"]

        return {"media_id": media_id, "container_id": container_id}


async def post_carousel(
    instagram_user_id: str,
    access_token: str,
    image_urls: list[str],
    caption: str,
) -> dict:
    """
    Post multiple images as an Instagram carousel.
    image_urls must be publicly accessible URLs.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:

        # Step 1: Create item containers for each image
        item_ids = []
        for img_url in image_urls[:10]:  # Instagram max 10 slides
            item_resp = await client.post(
                f"{GRAPH_URL}/{instagram_user_id}/media",
                params={
                    "image_url":    img_url,
                    "is_carousel_item": "true",
                    "access_token": access_token,
                }
            )
            item_resp.raise_for_status()
            item_ids.append(item_resp.json()["id"])

        # Step 2: Create carousel container
        children = ",".join(item_ids)
        carousel_resp = await client.post(
            f"{GRAPH_URL}/{instagram_user_id}/media",
            params={
                "media_type":   "CAROUSEL",
                "children":     children,
                "caption":      caption,
                "access_token": access_token,
            }
        )
        carousel_resp.raise_for_status()
        container_id = carousel_resp.json()["id"]

        # Step 3: Publish
        publish_resp = await client.post(
            f"{GRAPH_URL}/{instagram_user_id}/media_publish",
            params={
                "creation_id":  container_id,
                "access_token": access_token,
            }
        )
        publish_resp.raise_for_status()

        return {"media_id": publish_resp.json()["id"], "container_id": container_id}


async def get_media_insights(
    media_id: str,
    access_token: str,
) -> dict:
    """
    Get performance metrics for a published post.
    Call this 48h after posting.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_URL}/{media_id}/insights",
            params={
                "metric":       "reach,likes_count,comments_count,saved,shares",
                "access_token": access_token,
            }
        )
        if resp.status_code != 200:
            return {}

        data = resp.json().get("data", [])
        metrics = {}
        for item in data:
            metrics[item["name"]] = item.get("values", [{}])[0].get("value", 0)
        return metrics


async def get_follower_count(
    instagram_user_id: str,
    access_token: str,
) -> int:
    """Get current follower count for the connected account."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_URL}/{instagram_user_id}",
            params={
                "fields":       "followers_count",
                "access_token": access_token,
            }
        )
        if resp.status_code != 200:
            return 0
        return resp.json().get("followers_count", 0)
