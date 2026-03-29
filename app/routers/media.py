from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import uuid
from app.services.storage_service import create_presigned_upload_url
from app.services.db_service import get_supabase
from app.workers.video_tasks import celery_app, process_video, generate_thumbnail

router = APIRouter(prefix="/media", tags=["media"])


# ── Request models ────────────────────────────────────────────

class UploadUrlRequest(BaseModel):
    filename: str
    content_type: str
    user_id: str
    project_id: str


class ProcessVideoRequest(BaseModel):
    project_id: str
    user_id: str
    s3_key: str
    title: str
    options: dict = {
        "cut_silences": True,
        "burn_captions": True,
        "caption_style": "bold",
        "export_formats": ["9x16", "1x1", "16x9"],
        "extract_clips": True,
        "generate_carousel": True,
        "generate_thread": True,
    }


class ThumbnailRequest(BaseModel):
    project_id: str
    user_id: str
    s3_key: str


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/upload-url")
async def get_upload_url(request: UploadUrlRequest):
    from app.services.usage_service import check_and_consume
    from fastapi import HTTPException as _HTTPException
    _usage = check_and_consume(request.user_id, "upload")
    if not _usage["allowed"]:
        raise _HTTPException(status_code=402, detail={
            "error": "limit_reached",
            "message": _usage.get("upgrade_message", "Upload limit reached"),
            "used": _usage["used"], "limit": _usage["limit"], "plan": _usage["plan"],
        })
    """
    Generate a presigned URL for direct browser-to-R2 upload.
    Frontend calls this first, then uploads directly to R2.
    """
    allowed_types = [
        "video/mp4", "video/mov", "video/quicktime",
        "video/avi", "video/webm", "audio/mp3",
        "audio/wav", "audio/mpeg",
    ]
    if request.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type {request.content_type} not allowed"
        )

    result = await create_presigned_upload_url(
        user_id=request.user_id,
        filename=request.filename,
        content_type=request.content_type,
        folder="uploads",
    )
    return result


@router.post("/process")
async def start_processing(request: ProcessVideoRequest):
    """
    Start async video processing job.
    Creates a DB record then dispatches to Celery worker.
    Returns job_id for polling.
    """
    supabase = get_supabase()

    # Create output record in DB
    output_id = str(uuid.uuid4())
    supabase.table("generated_outputs").insert({
        "id":          output_id,
        "project_id":  request.project_id,
        "user_id":     request.user_id,
        "output_type": "processed_video",
        "status":      "pending",
        "metadata":    '{"s3_key": "' + request.s3_key + '"}',
    }).execute()

    # Update project status
    supabase.table("projects").update({
        "status": "editing",
        "source_s3_key": request.s3_key,
        "title": request.title,
    }).eq("id", request.project_id).execute()

    # Dispatch Celery job
    task = process_video.delay(
        output_id=output_id,
        project_id=request.project_id,
        user_id=request.user_id,
        s3_key=request.s3_key,
        options=request.options,
    )

    return {
        "output_id": output_id,
        "task_id":   task.id,
        "status":    "pending",
        "message":   "Video processing started",
    }


@router.get("/status/{output_id}")
async def get_job_status(output_id: str):
    """
    Poll this endpoint every 2–3 seconds from the frontend.
    Returns current processing status and results when done.
    """
    supabase = get_supabase()
    result = (
        supabase.table("generated_outputs")
        .select("*")
        .eq("id", output_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Job not found")

    data = result.data

    import json
    response = {
        "output_id":  output_id,
        "status":     data.get("status"),
        "error":      data.get("error_message"),
    }

    if data.get("status") == "completed":
        # Parse JSON fields
        for field in ["format_urls", "clips", "carousel_slides", "tweet_thread", "newsletter", "metadata"]:
            val = data.get(field)
            if val:
                try:
                    response[field] = json.loads(val) if isinstance(val, str) else val
                except Exception:
                    response[field] = val

    return response


@router.post("/thumbnail")
async def request_thumbnail(request: ThumbnailRequest):
    """Start thumbnail extraction job."""
    supabase = get_supabase()
    output_id = str(uuid.uuid4())

    supabase.table("generated_outputs").insert({
        "id":          output_id,
        "project_id":  request.project_id,
        "user_id":     request.user_id,
        "output_type": "thumbnail",
        "status":      "pending",
    }).execute()

    generate_thumbnail.delay(
        output_id=output_id,
        s3_key=request.s3_key,
        user_id=request.user_id,
        project_id=request.project_id,
    )

    return {"output_id": output_id, "status": "pending"}


@router.get("/project/{project_id}/outputs")
async def get_project_outputs(project_id: str):
    """Get all outputs for a project."""
    supabase = get_supabase()
    result = (
        supabase.table("generated_outputs")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"outputs": result.data or []}


# ── Scene assembler ───────────────────────────────────────────

class AssembleRequest(BaseModel):
    user_id:    str
    project_id: str
    title:      str
    scene_keys: list[str]   # Ordered list of S3 keys
    options:    dict = {}


@router.post("/assemble")
async def assemble_scenes(request: AssembleRequest):
    """
    Assemble multiple scene clips into one final video.
    scene_keys must be in the order they should appear.
    """
    supabase = get_supabase()

    # Ensure project exists to appease foreign key constraint
    proj_res = supabase.table("projects").select("id").eq("id", request.project_id).execute()
    if not proj_res.data:
        supabase.table("projects").insert({
            "id": request.project_id,
            "user_id": request.user_id,
            "title": request.title or "Assembled Video",
            "status": "editing"
        }).execute()

    output_id = str(uuid.uuid4())
    supabase.table("generated_outputs").insert({
        "id":          output_id,
        "project_id":  request.project_id,
        "user_id":     request.user_id,
        "output_type": "assembled_video",
        "status":      "pending",
        "metadata":    '{"scene_count": ' + str(len(request.scene_keys)) + '}',
    }).execute()

    from app.services.usage_service import get_usage_summary
    usage = get_usage_summary(request.user_id)
    limits = usage.get("limits", {})
    
    # Enforce Plan Constraints
    if limits.get("exports_per_upload") == 1:
        request.options["export_formats"] = ["9x16"]  # Force 1 format
    if limits.get("watermark") is True:
        request.options["watermark"] = True

    from app.workers.video_tasks import assemble_scenes_task
    task = assemble_scenes_task.delay(
        output_id=output_id,
        project_id=request.project_id,
        user_id=request.user_id,
        scene_keys=request.scene_keys,
        title=request.title,
        options=request.options,
    )

    return {
        "output_id": output_id,
        "task_id":   task.id,
        "status":    "pending",
        "scenes":    len(request.scene_keys),
    }
