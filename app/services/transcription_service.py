import json
import re
import os
from google import genai
from google.genai import types
from app.config import get_settings

settings = get_settings()
client = genai.Client(api_key=settings.gemini_api_key)

FLASH = "gemini-2.0-flash"


def _parse_json(text: str) -> dict:
    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(clean)


# ── Transcription ────────────────────────────────────────────

async def transcribe_audio(audio_path: str) -> dict:
    """
    Transcribe audio using Gemini's native audio understanding.
    Returns full transcript + word-level segments for caption generation.
    """
    # Upload audio file to Gemini File API
    print(f"Uploading audio to Gemini: {audio_path}")
    audio_file = client.files.upload(
        file=audio_path,
        config=types.UploadFileConfig(mime_type="audio/wav"),
    )

    prompt = """Transcribe this audio completely and accurately.
Return ONLY valid JSON with this exact structure — no markdown, no explanation:
{
  "text": "full transcript here",
  "segments": [
    {"start": 0.0, "end": 2.5, "word": "Hello"},
    {"start": 2.5, "end": 3.0, "word": "everyone"}
  ],
  "language": "en",
  "duration_seconds": 120
}
Include every word with timestamps. Be as precise as possible."""

    response = client.models.generate_content(
        model=FLASH,
        contents=[prompt, audio_file],
    )

    # Clean up uploaded file after use
    try:
        client.files.delete(name=audio_file.name)
    except Exception:
        pass

    try:
        data = _parse_json(response.text)
        return data
    except json.JSONDecodeError:
        # Fallback — return text without timestamps
        return {
            "text": response.text,
            "segments": [],
            "language": "en",
            "duration_seconds": 0,
        }


# ── Golden moment detection ──────────────────────────────────

async def extract_golden_moments(
    transcript: str,
    duration_seconds: float,
    num_clips: int = 5,
) -> list[dict]:
    """
    Use Gemini to find the most engaging moments for short clips.
    Returns list of segments with start/end times and engagement scores.
    """
    prompt = f"""You are a viral content editor. Analyze this transcript and find the {num_clips} most engaging moments that would work as short standalone clips (30-90 seconds each).

Transcript:
{transcript[:4000]}

Video duration: {duration_seconds:.0f} seconds

Find moments with: strong hooks, surprising revelations, emotional peaks, actionable tips, or quotable lines.

Return ONLY valid JSON:
{{
  "clips": [
    {{
      "title": "short clip title",
      "start_seconds": 45.0,
      "end_seconds": 90.0,
      "hook": "opening line of this clip",
      "why_viral": "one sentence explaining why this works",
      "engagement_score": 85
    }}
  ]
}}"""

    response = client.models.generate_content(
        model=FLASH,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.7),
    )

    try:
        data = _parse_json(response.text)
        return data.get("clips", [])
    except Exception:
        return []


# ── Carousel slides ──────────────────────────────────────────

async def generate_carousel_slides(
    transcript: str,
    title: str,
    num_slides: int = 5,
) -> list[dict]:
    """Generate Instagram carousel slide content from a transcript."""
    prompt = f"""Create a {num_slides}-slide Instagram carousel from this content.

Title: {title}
Transcript: {transcript[:3000]}

Each slide should have a punchy headline and 2-3 bullet points.
Slide 1 = hook/title slide. Last slide = CTA.

Return ONLY valid JSON:
{{
  "slides": [
    {{
      "slide_number": 1,
      "headline": "string (max 8 words)",
      "body": ["bullet 1", "bullet 2", "bullet 3"],
      "type": "hook|content|cta"
    }}
  ]
}}"""

    response = client.models.generate_content(
        model=FLASH,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.7),
    )

    try:
        data = _parse_json(response.text)
        return data.get("slides", [])
    except Exception:
        return []


# ── Tweet thread ─────────────────────────────────────────────

async def generate_tweet_thread(transcript: str, title: str) -> list[str]:
    """Generate a 5-tweet thread from a transcript."""
    prompt = f"""Write a 5-tweet thread based on this content.

Title: {title}
Transcript: {transcript[:3000]}

Rules:
- Tweet 1: strong hook that makes people want to read more. End with a thread symbol
- Tweets 2-4: one key insight each, concrete and specific
- Tweet 5: summary + CTA to watch the full video
- Each tweet under 280 characters
- No hashtags except tweet 5 (max 2)

Return ONLY valid JSON:
{{"tweets": ["tweet 1 text", "tweet 2 text", "tweet 3 text", "tweet 4 text", "tweet 5 text"]}}"""

    response = client.models.generate_content(
        model=FLASH,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.75),
    )

    try:
        data = _parse_json(response.text)
        return data.get("tweets", [])
    except Exception:
        return []


# ── Newsletter intro ─────────────────────────────────────────

async def generate_newsletter_intro(transcript: str, title: str) -> dict:
    """Generate a newsletter intro section from a transcript."""
    prompt = f"""Write a newsletter intro section based on this video content.

Title: {title}
Transcript: {transcript[:3000]}

Return ONLY valid JSON:
{{
  "subject_line": "string (compelling email subject)",
  "preview_text": "string (preview/subtitle, under 90 chars)",
  "intro": "string (2-3 paragraph intro, conversational tone, ~250 words)",
  "key_takeaways": ["takeaway 1", "takeaway 2", "takeaway 3", "takeaway 4"]
}}"""

    response = client.models.generate_content(
        model=FLASH,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.7),
    )

    try:
        return _parse_json(response.text)
    except Exception:
        return {
            "subject_line": title,
            "preview_text": "",
            "intro": "",
            "key_takeaways": [],
        }
