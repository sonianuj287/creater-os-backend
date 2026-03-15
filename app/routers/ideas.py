from fastapi import APIRouter, HTTPException, Query
from app.models.schemas import IdeaGenerateRequest, IdeaGenerateResponse, TrendingRequest
from app.services import ai_service, youtube_service, db_service

router = APIRouter(prefix="/ideas", tags=["ideas"])


@router.get("/trending")
async def get_trending_ideas(
    niche: str = Query(default="all", description="Filter by niche"),
    limit: int = Query(default=20, le=50),
):
    """
    Returns active trending ideas from DB.
    The DB is populated by the /ideas/refresh endpoint (run via cron).
    Falls back to generating fresh ones if DB is empty.
    """
    ideas = await db_service.get_active_ideas(niche=niche, limit=limit)

    if not ideas:
        # DB empty — generate on-demand for first load
        return await _generate_fresh_trending(niche)

    return {"ideas": ideas, "source": "db", "count": len(ideas)}


@router.post("/refresh")
async def refresh_trending_ideas(request: TrendingRequest):
    """
    Pulls fresh YouTube trending data and generates new ideas via Gemini.
    Call this via a daily cron job in Railway.
    """
    # 1. Fetch trending YouTube videos for this niche
    trending_videos = await youtube_service.fetch_trending_videos(
        niche=request.niche,
        region_code=request.region_code,
        max_results=request.max_results,
    )

    if not trending_videos:
        raise HTTPException(status_code=503, detail="Could not fetch YouTube trending data")

    # 2. Extract titles to feed into Gemini
    titles = [v["title"] for v in trending_videos]

    # 3. Generate ideas from the trending signals
    ideas = await ai_service.generate_trending_ideas_from_signals(
        niche=request.niche,
        trending_titles=titles,
    )

    # 4. Save to Supabase
    saved = await db_service.save_trending_ideas(ideas)

    return {
        "message": f"Refreshed {saved} ideas for '{request.niche}' niche",
        "youtube_videos_fetched": len(trending_videos),
        "ideas_generated": len(ideas),
        "ideas_saved": saved,
    }


@router.post("/generate", response_model=IdeaGenerateResponse)
async def generate_ideas(request: IdeaGenerateRequest):
    """
    Idea Studio: user provides a prompt → Gemini generates ideas.
    This is the core AI feature gated behind the Creator plan.
    """
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    try:
        result = await ai_service.generate_ideas(
            prompt=request.prompt,
            niche=request.niche,
            platforms=request.platforms,
            num_ideas=request.num_ideas,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")


async def _generate_fresh_trending(niche: str) -> dict:
    """Generate ideas on-the-fly when DB is empty."""
    niches_to_try = [niche] if niche != "all" else [
        "finance", "tech", "fitness", "lifestyle", "food"
    ]
    all_ideas = []

    for n in niches_to_try[:3]:
        trending = await youtube_service.fetch_trending_videos(niche=n)
        if trending:
            titles = [v["title"] for v in trending]
            ideas = await ai_service.generate_trending_ideas_from_signals(
                niche=n, trending_titles=titles
            )
            all_ideas.extend(ideas)

    await db_service.save_trending_ideas(all_ideas)
    return {"ideas": all_ideas, "source": "fresh", "count": len(all_ideas)}
