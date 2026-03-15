from fastapi import APIRouter, HTTPException
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
