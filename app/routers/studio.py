from fastapi import APIRouter, HTTPException
import httpx
import time
import asyncio
from typing import Optional
from app.models.schemas import (
    ScriptRequest, ScriptResponse,
    ShotListRequest, ShotListResponse,
    CaptionRequest, CaptionResponse,
    IdeaGenerateRequest, IdeaGenerateResponse,
)
from app.services import ai_service, youtube_service

router = APIRouter(prefix="/studio", tags=["studio"])


@router.post("/script", response_model=ScriptResponse)
async def generate_script(request: ScriptRequest):
    """
    Generate a full script outline for a video.
    Returns hook, context, main points, CTA — all editable.
    """
    try:
        return await ai_service.generate_script(
            title=request.title,
            description=request.description,
            hook=request.hook,
            platform=request.platform,
            niche=request.niche,
            duration_minutes=request.duration_minutes,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Script generation failed: {str(e)}")


@router.post("/shot-list", response_model=ShotListResponse)
async def generate_shot_list(request: ShotListRequest):
    """
    Generate a shot list from a script outline.
    Includes B-roll keyword suggestions.
    """
    try:
        return await ai_service.generate_shot_list(
            title=request.title,
            script_sections=request.script_sections,
            format=request.format,
            platform=request.platform,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shot list generation failed: {str(e)}")


@router.post("/captions", response_model=CaptionResponse)
async def generate_captions(request: CaptionRequest):
    """
    Generate 3 caption variants + hashtag set for a given platform.
    Captions follow platform-specific rules (char limits, tone, hashtag strategy).
    """
    try:
        return await ai_service.generate_captions(
            title=request.title,
            description=request.description,
            platform=request.platform,
            niche=request.niche,
            hook=request.hook,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Caption generation failed: {str(e)}")


@router.get("/competitors")
async def get_competitor_examples(
    query: str,
    max_results: int = 3,
):
    """
    Search YouTube for similar viral videos on a topic.
    Shown on the idea detail page to inspire creators.
    """
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        results = await youtube_service.search_similar_videos(
            query=query,
            max_results=max_results,
        )
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Competitor search failed: {str(e)}")


# ── In-process cache (TTL = 6 hours) ──────────────────────────
_audio_cache: dict = {"data": None, "ts": 0.0}
AUDIO_CACHE_TTL = 6 * 3600  # seconds


async def _fetch_apple_music_india(limit: int = 25) -> list[dict]:
    """Fetch top songs from Apple Music India RSS (no auth, free)."""
    url = f"https://rss.applemarketingtools.com/api/v2/in/music/most-played/{limit}/songs.json"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            feed = resp.json().get("feed", {})
            results = feed.get("results", [])
            tracks = []
            for i, item in enumerate(results):
                tracks.append({
                    "id": f"apple-{item.get('id', i)}",
                    "title": item.get("name", ""),
                    "artist": item.get("artistName", ""),
                    "genre": item.get("genres", [{}])[0].get("name", "Pop") if item.get("genres") else "Pop",
                    "artwork": item.get("artworkUrl100", ""),
                    "apple_url": item.get("url", ""),
                    "preview_url": None,
                    "source": "apple_india",
                    "rank": i + 1,
                    "hot": i < 5,
                })
            return tracks
    except Exception:
        return []


async def _fetch_deezer_global(limit: int = 25) -> list[dict]:
    """Fetch global top tracks from Deezer chart API (no auth, free)."""
    url = f"https://api.deezer.com/chart/0/tracks?limit={limit}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            items = resp.json().get("data", [])
            tracks = []
            for i, item in enumerate(items):
                artist = item.get("artist", {})
                album = item.get("album", {})
                tracks.append({
                    "id": f"deezer-{item.get('id', i)}",
                    "title": item.get("title", ""),
                    "artist": artist.get("name", ""),
                    "genre": "Global Pop",
                    "artwork": album.get("cover_medium", ""),
                    "preview_url": item.get("preview", ""),
                    "deezer_url": item.get("link", ""),
                    "source": "deezer_global",
                    "rank": i + 1,
                    "hot": i < 5,
                })
            return tracks
    except Exception:
        return []




async def _fetch_itunes_search(terms: list[str]) -> list[dict]:
    """Targeted iTunes search for curated viral terms (fallback enrichment)."""
    tracks = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for i, term in enumerate(terms[:5]):
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={"term": term, "media": "music", "limit": 1, "country": "in"}
                )
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results", [])
                if results:
                    r = results[0]
                    tracks.append({
                        "id": f"itunes-{r.get('trackId', i)}",
                        "title": r.get("trackName", ""),
                        "artist": r.get("artistName", ""),
                        "genre": r.get("primaryGenreName", "Pop"),
                        "artwork": r.get("artworkUrl100", ""),
                        "preview_url": r.get("previewUrl", ""),
                        "apple_url": r.get("trackViewUrl", ""),
                        "source": "itunes_curated",
                        "rank": 99,
                        "hot": True,  # manually curated = always surface prominently
                    })
                await asyncio.sleep(0.1)  # Be nice to iTunes API
    except Exception:
        pass
    return tracks


CURATED_VIRAL_TERMS = [
    "Espresso Sabrina Carpenter",
    "APT Rose Bruno Mars",
    "Maan Meri Jaan King",
    "Raataan Lambiyan",
    "Kesariya Arijit Singh",
]


@router.get("/trending-audio")
async def get_trending_audio(limit: int = 30, refresh: bool = False):
    """
    Returns real-time trending tracks from:
    - Apple Music India top charts  (daily updated)
    - Deezer global top chart       (daily updated)
    - iTunes curated viral tracks   (supplemental)
    Results are cached in-process for 6 hours.
    """
    global _audio_cache

    # Serve from cache if fresh
    if not refresh and _audio_cache["data"] and (time.time() - _audio_cache["ts"]) < AUDIO_CACHE_TTL:
        tracks = _audio_cache["data"]
        return {"tracks": tracks[:limit], "source": "cache", "count": len(tracks[:limit])}

    # Fetch all three sources concurrently
    apple_tracks, deezer_tracks, curated_tracks = await asyncio.gather(
        _fetch_apple_music_india(limit=25),
        _fetch_deezer_global(limit=25),
        _fetch_itunes_search(CURATED_VIRAL_TERMS),
    )

    # Merge — deduplicate by normalized "title+artist"
    seen: set[str] = set()
    merged: list[dict] = []
    for track in [*curated_tracks, *apple_tracks, *deezer_tracks]:
        key = f"{track['title'].lower().strip()}-{track['artist'].lower().strip()}"
        if key not in seen and track["title"]:
            seen.add(key)
            merged.append(track)

    # Sort: curated first, then by rank
    merged.sort(key=lambda t: (t["source"] != "itunes_curated", t.get("rank", 99)))

    # Cache it
    _audio_cache = {"data": merged, "ts": time.time()}

    return {"tracks": merged[:limit], "source": "live", "count": len(merged[:limit])}

