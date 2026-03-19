import json
import re
from google import genai
from google.genai import types
from app.config import get_settings

settings = get_settings()
client = genai.Client(api_key=settings.gemini_api_key)

FLASH = "gemini-1.5-flash"
PRO   = "gemini-1.5-flash"


def _parse_json(text: str) -> dict:
    """Strip markdown fences and parse JSON from Gemini response."""
    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(clean)


def _generate(prompt: str, model: str = FLASH, temperature: float = 0.8) -> str:
    """Single helper for all Gemini text generation calls."""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return response.text


# ── Idea generation ──────────────────────────────────────────

async def generate_ideas(
    prompt: str,
    niche: str,
    platforms: list[str],
    num_ideas: int = 5,
):
    from app.models.schemas import IdeaGenerateResponse, IdeaVariant

    platform_str = ", ".join(platforms)

    full_prompt = f"""You are an expert content strategist for social media creators.
Generate {num_ideas} unique content ideas for a creator in the '{niche}' niche.
They post on: {platform_str}.
Their topic/prompt: "{prompt}"

For each idea, generate 3 hook variants.
Return ONLY valid JSON — no markdown, no explanation, no preamble:
{{
  "ideas": [
    {{
      "title": "string (compelling, specific, under 60 chars)",
      "angle": "string (unique perspective e.g. beginner story, myth-busting, data-driven)",
      "description": "string (2-3 sentences explaining what the video covers)",
      "hooks": [
        {{"text": "string (opening line, under 15 words)", "style": "question|shock_stat|story|bold_claim", "score": 85}},
        {{"text": "string", "style": "question|shock_stat|story|bold_claim", "score": 78}},
        {{"text": "string", "style": "question|shock_stat|story|bold_claim", "score": 72}}
      ],
      "recommended_format": "reel|short|carousel|long_form|thread",
      "estimated_minutes": 30,
      "difficulty": "easy|medium|hard"
    }}
  ]
}}"""

    text = _generate(full_prompt, temperature=0.8)
    data = _parse_json(text)
    ideas = [IdeaVariant(**i) for i in data["ideas"]]
    return IdeaGenerateResponse(ideas=ideas)


# ── Script outline ───────────────────────────────────────────

async def generate_script(
    title: str,
    description: str,
    hook: str,
    platform: str,
    niche: str,
    duration_minutes: int = 3,
):
    from app.models.schemas import ScriptResponse, ScriptSection

    word_target = duration_minutes * 130

    prompt = f"""You are a professional scriptwriter for viral {platform} content in the {niche} niche.

Write a complete script outline for this video:
Title: {title}
Description: {description}
Opening hook: {hook}
Target duration: {duration_minutes} minutes (~{word_target} words)

Return ONLY valid JSON:
{{
  "sections": [
    {{"section": "hook",         "content": "string (exact opening words, 10-20 seconds)", "tips": "string (delivery tip)"}},
    {{"section": "context",      "content": "string (set up why this matters, 20-30 seconds)", "tips": "string"}},
    {{"section": "main_point_1", "content": "string (first key point with example)", "tips": "string"}},
    {{"section": "main_point_2", "content": "string (second key point with example)", "tips": "string"}},
    {{"section": "main_point_3", "content": "string (third key point with example)", "tips": "string"}},
    {{"section": "cta",          "content": "string (call to action)", "tips": "string"}}
  ],
  "total_words": {word_target},
  "estimated_duration_seconds": {duration_minutes * 60}
}}"""

    text = _generate(prompt, temperature=0.7)
    data = _parse_json(text)
    sections = [ScriptSection(**s) for s in data["sections"]]
    return ScriptResponse(
        sections=sections,
        total_words=data.get("total_words", word_target),
        estimated_duration_seconds=data.get("estimated_duration_seconds", duration_minutes * 60),
    )


# ── Shot list ────────────────────────────────────────────────

async def generate_shot_list(
    title: str,
    script_sections,
    format: str,
    platform: str,
):
    from app.models.schemas import ShotListResponse, ShotItem, BrollSuggestion

    script_text = "\n".join(
        f"[{s.section.upper()}] {s.content}" for s in script_sections
    )
    aspect = "9:16 vertical" if format in ("reel", "short") else "16:9 horizontal"

    prompt = f"""You are a video director creating a shot list for a {platform} {format} video.
Aspect ratio: {aspect}
Title: {title}

Script:
{script_text}

Create a practical shot list a solo creator can film alone.
Also suggest 3 B-roll keyword searches for free stock footage.

Return ONLY valid JSON:
{{
  "shots": [
    {{
      "order": 1,
      "shot_type": "talking_head|broll|screen_record|text_slide",
      "description": "string (exactly what to film or show)",
      "duration_seconds": 5,
      "tips": "string (practical filming tip)"
    }}
  ],
  "broll_suggestions": [
    {{"keyword": "string (Pexels search term)", "pexels_url": "https://www.pexels.com/search/KEYWORD/"}}
  ],
  "total_duration_seconds": 60
}}"""

    text = _generate(prompt, temperature=0.6)
    data = _parse_json(text)
    shots = [ShotItem(**s) for s in data["shots"]]
    broll = [BrollSuggestion(**b) for b in data.get("broll_suggestions", [])]
    return ShotListResponse(
        shots=shots,
        broll_suggestions=broll,
        total_duration_seconds=data.get("total_duration_seconds", 60),
    )


# ── Captions & hashtags ──────────────────────────────────────

PLATFORM_CAPTION_RULES = {
    "instagram": "Max 2200 chars. First line is critical. Use line breaks. End with a question.",
    "youtube":   "First 100 chars show in search. Include keywords naturally.",
    "tiktok":    "Keep under 300 chars. Casual tone. 1-2 sentences max.",
    "twitter":   "Under 280 chars total. Punchy. Max 2 hashtags.",
    "linkedin":  "Professional tone. Up to 3000 chars. Start with a hook. Max 5 hashtags.",
}

async def generate_captions(
    title: str,
    description: str,
    platform: str,
    niche: str,
    hook: str | None = None,
):
    from app.models.schemas import CaptionResponse, CaptionVariant, HashtagSet

    rules = PLATFORM_CAPTION_RULES.get(platform, "Keep it concise and engaging.")

    prompt = f"""You are a social media copywriter for {platform} in the {niche} niche.

Video: {title}
Description: {description}
Opening hook: {hook or 'not provided'}
Platform rules: {rules}

Generate 3 caption variants and a hashtag strategy.

Return ONLY valid JSON:
{{
  "variants": [
    {{"style": "curiosity_gap", "caption": "string (full caption ready to copy-paste)", "char_count": 150}},
    {{"style": "storytelling",  "caption": "string (full caption ready to copy-paste)", "char_count": 200}},
    {{"style": "listicle",      "caption": "string (full caption ready to copy-paste)", "char_count": 180}}
  ],
  "hashtags": {{
    "big":   ["hashtag1", "hashtag2", "hashtag3"],
    "niche": ["hashtag4", "hashtag5", "hashtag6", "hashtag7", "hashtag8"],
    "micro": ["hashtag9", "hashtag10", "hashtag11", "hashtag12"]
  }},
  "best_posting_time": "string e.g. Tuesday-Thursday 7-9 PM IST"
}}"""

    text = _generate(prompt, temperature=0.75)
    data = _parse_json(text)
    variants = [CaptionVariant(**v) for v in data["variants"]]
    hashtags = HashtagSet(**data["hashtags"])
    return CaptionResponse(
        variants=variants,
        hashtags=hashtags,
        best_posting_time=data.get("best_posting_time", ""),
    )


# ── Trending ideas from YouTube signals ─────────────────────

async def generate_trending_ideas_from_signals(
    niche: str,
    trending_titles: list[str],
    platforms: list[str] = None,
) -> list[dict]:
    platform_str = ", ".join(platforms or ["instagram", "youtube"])
    titles_str   = "\n".join(f"- {t}" for t in trending_titles[:15])

    prompt = f"""You are a viral content strategist. Based on these currently trending YouTube videos in the '{niche}' niche:

{titles_str}

Generate 6 unique content ideas a creator could make for {platform_str}.
Ideas should be INSPIRED by the trends but NOT copies.

Return ONLY valid JSON:
{{
  "ideas": [
    {{
      "title": "string",
      "description": "string (2 sentences)",
      "hook_preview": "string (opening line in quotes, punchy)",
      "niche": "{niche}",
      "platforms": ["{(platforms or ['instagram', 'youtube'])[0]}"],
      "recommended_format": "reel|short|carousel|long_form|thread",
      "difficulty": "easy|medium|hard",
      "estimated_minutes": 30,
      "viral_score": 85,
      "trending_reason": "string (why this will work right now)",
      "similar_views_avg": 500000
    }}
  ]
}}"""

    text = _generate(prompt, temperature=0.85)
    data = _parse_json(text)
    return data.get("ideas", [])
