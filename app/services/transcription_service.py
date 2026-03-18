import httpx
import json
import os
import google.generativeai as genai
from app.config import get_settings

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)


async def transcribe_audio(audio_path: str) -> dict:
    """
    Transcribe audio using Gemini's audio understanding.
    Returns segments with timestamps for caption generation.
    """
    model = genai.GenerativeModel("gemini-1.5-flash")

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    audio_file = genai.upload_file(audio_path, mime_type="audio/wav")

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
Include every word with timestamps. Be precise."""

    response = model.generate_content([prompt, audio_file])

    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return data
    except json.JSONDecodeError:
        # Fallback: return just the text without timestamps
        return {
            "text": response.text,
            "segments": [],
            "language": "en",
            "duration_seconds": 0,
        }


async def extract_golden_moments(
    transcript: str,
    duration_seconds: float,
    num_clips: int = 5,
) -> list[dict]:
    """
    Use Gemini to identify the most engaging moments in a transcript.
    Returns segments with timestamps and engagement scores.
    """
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""You are a viral content editor. Analyze this transcript and find the {num_clips} most engaging moments that would work as short standalone clips (30–90 seconds each).

Transcript:
{transcript[:4000]}

Video duration: {duration_seconds:.0f} seconds

Find moments that have: strong hooks, surprising revelations, emotional peaks, actionable tips, or quotable lines.

Return ONLY valid JSON — no markdown:
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

    response = model.generate_content(prompt)
    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return data.get("clips", [])
    except Exception:
        return []


async def generate_carousel_slides(transcript: str, title: str, num_slides: int = 5) -> list[dict]:
    """Generate carousel slide content from a transcript."""
    model = genai.GenerativeModel("gemini-1.5-flash")

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

    response = model.generate_content(prompt)
    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return data.get("slides", [])
    except Exception:
        return []


async def generate_tweet_thread(transcript: str, title: str) -> list[str]:
    """Generate a 5-tweet thread from a transcript."""
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""Write a 5-tweet thread based on this content.

Title: {title}
Transcript: {transcript[:3000]}

Rules:
- Tweet 1: strong hook that makes people want to read more. End with "🧵"
- Tweets 2-4: one key insight each, concrete and specific
- Tweet 5: summary + CTA to watch the full video
- Each tweet under 280 characters
- No hashtags except tweet 5 (max 2)

Return ONLY valid JSON:
{{"tweets": ["tweet 1 text", "tweet 2 text", "tweet 3 text", "tweet 4 text", "tweet 5 text"]}}"""

    response = model.generate_content(prompt)
    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return data.get("tweets", [])
    except Exception:
        return []


async def generate_newsletter_intro(transcript: str, title: str) -> dict:
    """Generate a newsletter intro from a transcript."""
    model = genai.GenerativeModel("gemini-1.5-flash")

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

    response = model.generate_content(prompt)
    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception:
        return {"subject_line": title, "preview_text": "", "intro": "", "key_takeaways": []}
