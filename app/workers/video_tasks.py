import os
import tempfile
import json
from celery import Celery
from app.config import get_settings
from app.services import media_service, storage_service, transcription_service
from app.services.db_service import get_supabase

settings = get_settings()

# Celery app using Redis as broker + backend
celery_app = Celery(
    "creator_os",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    worker_concurrency=1,
    worker_max_memory_per_child=400000,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # Process one job at a time (video processing is heavy)
)


def update_output_status(output_id: str, status: str, updates: dict = None):
    """Helper to update job status in Supabase."""
    supabase = get_supabase()
    data = {"status": status}
    if updates:
        data.update(updates)
    supabase.table("generated_outputs").update(data).eq("id", output_id).execute()


# ── Main video processing job ────────────────────────────────

@celery_app.task(bind=True, name="process_video")
def process_video(
    self,
    output_id: str,
    project_id: str,
    user_id: str,
    s3_key: str,
    options: dict,
):
    """
    Master video processing job.
    options = {
        cut_silences: bool,
        burn_captions: bool,
        caption_style: str,
        export_formats: list[str],
        extract_clips: bool,
        generate_carousel: bool,
        generate_thread: bool,
    }
    """
    update_output_status(output_id, "processing")
    supabase = get_supabase()

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            # ── Step 1: Download original video ──────────────
            local_video = os.path.join(tmp_dir, "original.mp4")
            self.update_state(state="PROGRESS", meta={"step": "Downloading video", "progress": 5})
            storage_service.download_file_from_s3(s3_key, local_video)

            # ── Step 2: Extract audio + transcribe ───────────
            self.update_state(state="PROGRESS", meta={"step": "Transcribing audio", "progress": 15})
            audio_path = os.path.join(tmp_dir, "audio.wav")
            media_service.extract_audio(local_video, audio_path)

            import asyncio
            transcript_data = asyncio.run(
                transcription_service.transcribe_audio(audio_path)
            )
            transcript_text = transcript_data.get("text", "")
            segments = transcript_data.get("segments", [])

            # Save transcript to project
            srt_content = media_service.timestamps_to_srt(segments)
            srt_path = os.path.join(tmp_dir, "captions.srt")
            with open(srt_path, "w") as f:
                f.write(srt_content)

            supabase.table("projects").update({
                "transcript": transcript_text,
                "transcript_segments": json.dumps(segments),
                "srt_content": srt_content,
            }).eq("id", project_id).execute()

            # ── Step 3: Cut silences (optional) ──────────────
            working_video = local_video
            if options.get("cut_silences", True):
                self.update_state(state="PROGRESS", meta={"step": "Cutting silences", "progress": 30})
                cut_path = os.path.join(tmp_dir, "cut.mp4")
                media_service.cut_silences(local_video, cut_path)
                working_video = cut_path

            # ── Step 4: Burn captions (optional) ─────────────
            if options.get("burn_captions", True) and srt_content:
                self.update_state(state="PROGRESS", meta={"step": "Burning captions", "progress": 45})
                captioned_path = os.path.join(tmp_dir, "captioned.mp4")
                caption_style = options.get("caption_style", "bold")
                media_service.burn_captions(working_video, srt_path, captioned_path, caption_style)
                working_video = captioned_path

            # ── Step 5: Multi-format export ───────────────────
            self.update_state(state="PROGRESS", meta={"step": "Exporting formats", "progress": 60})
            export_dir = os.path.join(tmp_dir, "exports")
            format_outputs = media_service.export_multi_format(working_video, export_dir)

            # Upload each format to R2
            format_urls = {}
            for fmt, local_path in format_outputs.items():
                export_key = f"outputs/{user_id}/{project_id}/{fmt}.mp4"
                storage_service.upload_file_to_s3(local_path, export_key)
                url = asyncio.run(storage_service.create_presigned_download_url(export_key))
                format_urls[fmt] = url

            # ── Step 6: Extract short clips ───────────────────
            clips = []
            if options.get("extract_clips", True) and transcript_text:
                self.update_state(state="PROGRESS", meta={"step": "Finding best clips", "progress": 70})
                duration = media_service.get_video_duration(working_video)
                golden_moments = asyncio.run(
                    transcription_service.extract_golden_moments(transcript_text, duration)
                )
                for i, moment in enumerate(golden_moments[:5]):
                    clip_path = os.path.join(tmp_dir, f"clip_{i}.mp4")
                    media_service.trim_clip(
                        working_video,
                        moment["start_seconds"],
                        moment["end_seconds"],
                        clip_path,
                    )
                    clip_key = f"outputs/{user_id}/{project_id}/clip_{i}.mp4"
                    storage_service.upload_file_to_s3(clip_path, clip_key)
                    clip_url = asyncio.run(storage_service.create_presigned_download_url(clip_key))
                    clips.append({
                        **moment,
                        "url": clip_url,
                        "s3_key": clip_key,
                    })

            # ── Step 7: Carousel + thread ─────────────────────
            carousel_slides = []
            tweet_thread = []
            newsletter = {}

            if options.get("generate_carousel", True) and transcript_text:
                self.update_state(state="PROGRESS", meta={"step": "Generating carousel", "progress": 82})
                project_data = supabase.table("projects").select("title").eq("id", project_id).single().execute()
                title = project_data.data.get("title", "")
                carousel_slides = asyncio.run(
                    transcription_service.generate_carousel_slides(transcript_text, title)
                )

            if options.get("generate_thread", True) and transcript_text:
                self.update_state(state="PROGRESS", meta={"step": "Writing tweet thread", "progress": 90})
                project_data = supabase.table("projects").select("title").eq("id", project_id).single().execute()
                title = project_data.data.get("title", "")
                tweet_thread = asyncio.run(
                    transcription_service.generate_tweet_thread(transcript_text, title)
                )
                newsletter = asyncio.run(
                    transcription_service.generate_newsletter_intro(transcript_text, title)
                )

            # ── Step 8: Save all results ──────────────────────
            self.update_state(state="PROGRESS", meta={"step": "Saving results", "progress": 96})
            update_output_status(output_id, "completed", {
                "format_urls":     json.dumps(format_urls),
                "clips":           json.dumps(clips),
                "carousel_slides": json.dumps(carousel_slides),
                "tweet_thread":    json.dumps(tweet_thread),
                "newsletter":      json.dumps(newsletter),
                "metadata": json.dumps({
                    "transcript_words": len(transcript_text.split()),
                    "clips_found":      len(clips),
                    "formats_exported": list(format_urls.keys()),
                }),
            })

            return {"status": "completed", "output_id": output_id}

        except Exception as e:
            error_msg = str(e)
            print(f"Video processing error: {error_msg}")
            update_output_status(output_id, "failed", {"error_message": error_msg[:500]})
            raise


# ── Thumbnail extraction (quick job) ─────────────────────────

@celery_app.task(name="generate_thumbnail")
def generate_thumbnail(output_id: str, s3_key: str, user_id: str, project_id: str):
    """Extract best frame from video for thumbnail."""
    update_output_status(output_id, "processing")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            local_video = os.path.join(tmp_dir, "video.mp4")
            storage_service.download_file_from_s3(s3_key, local_video)

            duration = media_service.get_video_duration(local_video)
            # Try 3 offsets and keep first one that works
            for offset in [3.0, duration * 0.2, duration * 0.5]:
                frame_path = os.path.join(tmp_dir, "thumb.jpg")
                try:
                    media_service.extract_best_frame(local_video, frame_path, offset)
                    thumb_key = f"outputs/{user_id}/{project_id}/thumbnail.jpg"
                    storage_service.upload_file_to_s3(frame_path, thumb_key, "image/jpeg")
                    import asyncio
                    thumb_url = asyncio.run(storage_service.create_presigned_download_url(thumb_key))
                    update_output_status(output_id, "completed", {
                        "file_url": thumb_url,
                        "metadata": json.dumps({"s3_key": thumb_key}),
                    })
                    return {"status": "completed", "url": thumb_url}
                except Exception:
                    continue

            raise Exception("Could not extract frame from video")

        except Exception as e:
            update_output_status(output_id, "failed", {"error_message": str(e)[:500]})
            raise
