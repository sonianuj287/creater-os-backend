import json
import re
import google.generativeai as genai
from app.config import get_settings
from app.models.schemas import (
    IdeaGenerateResponse, IdeaVariant, HookVariant,
    ScriptResponse, ScriptSection,
    ShotListResponse, ShotItem, BrollSuggestion,
    CaptionResponse, CaptionVariant, HashtagSet,
)

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)

# Use Flash for speed + cost, Pro for complex tasks
flash = genai.GenerativeModel("gemini-flash-latest")
pro   = genai.GenerativeModel("gemini-pro-latest")


def _parse_json(text: str) -> dict:
    """Strip markdown fences and parse JSON from Gemini response."""
    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(clean)


# ── Idea generation ──────────────────────────────────────────

async def generate_ideas(
    prompt: str,
    niche: str,
    platforms: list[str],
    num_ideas: int = 5,
) -> IdeaGenerateResponse:
    platform_str = ", ".join(platforms)
    system = f"""You are an expert content strategist for social media creators.
Your job is to generate viral content ideas tailored to a specific niche and platform.
Always return valid JSON only — no explanation, no markdown, no preamble."""

    user = f"""Generate {num_ideas} unique content ideas for a creator in the '{niche}' niche.
They post on: {platform_str}.
Their topic/prompt: "{prompt}"

For each idea, generate 3 hook variants.

Return this exact JSON structure:
{{
  "ideas": [
    {{
      "title": "string (compelling, specific, under 60 chars)",
      "angle": "string (unique perspective e.g. 'beginner story', 'myth-busting', 'data-driven')",
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

    response = await flash.generate_content_async(
        f"{system}\n\n{user}",
        generation_config=genai.GenerationConfig(temperature=0.8)
    )
    data = _parse_json(response.text)
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
) -> ScriptResponse:
    word_target = duration_minutes * 130  # ~130 words/min speaking pace

    prompt = f"""You are a professional scriptwriter for viral {platform} content in the {niche} niche.

Write a complete script outline for this video:
Title: {title}
Description: {description}
Opening hook: {hook}
Target duration: {duration_minutes} minutes (~{word_target} words)

Return this exact JSON only:
{{
  "sections": [
    {{"section": "hook", "content": "string (exact opening words, 10-20 seconds)", "tips": "string (delivery tip)"}},
    {{"section": "context", "content": "string (set up why this matters, 20-30 seconds)", "tips": "string"}},
    {{"section": "main_point_1", "content": "string (first key point with example)", "tips": "string"}},
    {{"section": "main_point_2", "content": "string (second key point with example)", "tips": "string"}},
    {{"section": "main_point_3", "content": "string (third key point with example)", "tips": "string"}},
    {{"section": "cta", "content": "string (call to action — subscribe/follow/comment prompt)", "tips": "string"}}
  ],
  "total_words": {word_target},
  "estimated_duration_seconds": {duration_minutes * 60}
}}"""

    response = await pro.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(temperature=0.7)
    )
    data = _parse_json(response.text)
    sections = [ScriptSection(**s) for s in data["sections"]]
    return ScriptResponse(
        sections=sections,
        total_words=data.get("total_words", word_target),
        estimated_duration_seconds=data.get("estimated_duration_seconds", duration_minutes * 60),
    )


# ── Shot list ────────────────────────────────────────────────

async def generate_shot_list(
    title: str,
    script_sections: list[ScriptSection],
    format: str,
    platform: str,
) -> ShotListResponse:
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

Return this exact JSON only:
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

    response = await flash.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(temperature=0.6)
    )
    data = _parse_json(response.text)
    shots = [ShotItem(**s) for s in data["shots"]]
    broll = [BrollSuggestion(**b) for b in data.get("broll_suggestions", [])]
    return ShotListResponse(
        shots=shots,
        broll_suggestions=broll,
        total_duration_seconds=data.get("total_duration_seconds", 60),
    )


# ── Captions & hashtags ──────────────────────────────────────

PLATFORM_CAPTION_RULES = {
    "instagram": "Max 2200 chars. First line is critical — it shows before 'more'. Use line breaks. End with a question to boost comments.",
    "youtube":   "First 100 chars show in search. Include keywords naturally. Add chapter timestamps format if long-form.",
    "tiktok":    "Keep under 300 chars. Casual tone. 1-2 sentences max. Hook immediately.",
    "twitter":   "Under 280 chars total including hashtags. Punchy. Max 2 hashtags.",
    "linkedin":  "Professional tone. Up to 3000 chars. Start with a hook line. No hashtag spam — max 5.",
}

async def generate_captions(
    title: str,
    description: str,
    platform: str,
    niche: str,
    hook: str | None = None,
) -> CaptionResponse:
    rules = PLATFORM_CAPTION_RULES.get(platform, "Keep it concise and engaging.")

    prompt = f"""You are a social media copywriter specialising in {platform} content for the {niche} niche.

Video: {title}
Description: {description}
Opening hook: {hook or 'not provided'}

Platform rules: {rules}

Generate 3 caption variants and a hashtag strategy.

Return this exact JSON only:
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
  "best_posting_time": "string e.g. 'Tuesday–Thursday, 7–9 PM IST'"
}}"""

    response = await flash.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(temperature=0.75)
    )
    data = _parse_json(response.text)
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
Ideas should be INSPIRED by the trends but NOT copies — find the underlying theme and make something original.

Return this exact JSON only:
{{
  "ideas": [
    {{
      "title": "string",
      "description": "string (2 sentences)",
      "hook_preview": "string (opening line in quotes, punchy)",
      "niche": "{niche}",
      "platforms": ["{(platforms or ['instagram','youtube'])[0]}"],
      "recommended_format": "reel|short|carousel|long_form|thread",
      "difficulty": "easy|medium|hard",
      "estimated_minutes": 30,
      "viral_score": 85,
      "trending_reason": "string (why this will work right now)",
      "similar_views_avg": 500000
    }}
  ]
}}"""

    response = await flash.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(temperature=0.85)
    )
    data = _parse_json(response.text)
    return data.get("ideas", [])
