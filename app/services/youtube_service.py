from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.config import get_settings

settings = get_settings()

# YouTube category IDs mapped to our niches
NICHE_CATEGORY_MAP = {
    "finance":   ["22", "25"],   # People & Blogs, News
    "tech":      ["28"],         # Science & Technology
    "fitness":   ["17", "22"],   # Sports, People & Blogs
    "lifestyle": ["22", "26"],   # People & Blogs, Howto
    "food":      ["26", "22"],   # Howto & Style, People & Blogs
    "travel":    ["19", "22"],   # Travel & Events, People & Blogs
    "education": ["27", "28"],   # Education, Science & Tech
    "gaming":    ["20"],         # Gaming
    "beauty":    ["26"],         # Howto & Style
    "other":     ["22"],         # People & Blogs
}

# Search keywords per niche for more targeted results
NICHE_KEYWORDS = {
    "finance":   "money saving investing income",
    "tech":      "AI tools technology gadgets",
    "fitness":   "workout fitness gym health",
    "lifestyle": "lifestyle routine productivity",
    "food":      "recipe cooking food",
    "travel":    "travel vlog trip destination",
    "education": "learning tutorial how to",
    "gaming":    "gaming gameplay tips",
    "beauty":    "makeup skincare beauty tutorial",
    "other":     "trending viral",
}


def get_youtube_client():
    return build("youtube", "v3", developerKey=settings.youtube_api_key)


async def fetch_trending_videos(
    niche: str,
    region_code: str = "IN",
    max_results: int = 20,
) -> list[dict]:
    """Fetch trending YouTube videos for a given niche."""
    try:
        youtube = get_youtube_client()
        category_ids = NICHE_CATEGORY_MAP.get(niche, ["22"])
        all_videos = []

        for category_id in category_ids[:1]:  # Use first category to save quota
            request = youtube.videos().list(
                part="snippet,statistics",
                chart="mostPopular",
                regionCode=region_code,
                videoCategoryId=category_id,
                maxResults=max_results,
            )
            response = request.execute()

            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                stats   = item.get("statistics", {})
                all_videos.append({
                    "title":       snippet.get("title", ""),
                    "description": snippet.get("description", "")[:200],
                    "view_count":  int(stats.get("viewCount", 0)),
                    "like_count":  int(stats.get("likeCount", 0)),
                    "channel":     snippet.get("channelTitle", ""),
                    "published":   snippet.get("publishedAt", ""),
                    "video_id":    item.get("id", ""),
                })

        # Sort by view count descending
        all_videos.sort(key=lambda x: x["view_count"], reverse=True)
        return all_videos[:max_results]

    except HttpError as e:
        print(f"YouTube API error: {e}")
        return []
    except Exception as e:
        print(f"YouTube fetch error: {e}")
        return []


async def search_similar_videos(
    query: str,
    max_results: int = 3,
) -> list[dict]:
    """Search YouTube for videos similar to a given topic."""
    try:
        youtube = get_youtube_client()

        search_response = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            order="viewCount",
            maxResults=max_results,
        ).execute()

        results = []
        for item in search_response.get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            results.append({
                "title":     snippet.get("title", ""),
                "channel":   snippet.get("channelTitle", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "url":       f"https://youtube.com/watch?v={video_id}",
                "video_id":  video_id,
            })

        # Fetch view counts for search results
        if results:
            video_ids = [r["video_id"] for r in results if r["video_id"]]
            stats_response = youtube.videos().list(
                part="statistics",
                id=",".join(video_ids),
            ).execute()

            stats_map = {
                item["id"]: int(item.get("statistics", {}).get("viewCount", 0))
                for item in stats_response.get("items", [])
            }
            for r in results:
                r["views"] = stats_map.get(r["video_id"], 0)

        return results

    except HttpError as e:
        print(f"YouTube search API error: {e}")
        return []
    except Exception as e:
        print(f"YouTube search error: {e}")
        return []
