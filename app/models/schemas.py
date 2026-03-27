from pydantic import BaseModel
from typing import Optional


# ── Idea generation ──────────────────────────────────────────

class IdeaGenerateRequest(BaseModel):
    user_id: str
    prompt: str                        # "I want to talk about passive income"
    niche: str                         # "finance"
    platforms: list[str]               # ["instagram", "youtube"]
    num_ideas: int = 5


class HookVariant(BaseModel):
    text: str
    style: str                         # question | shock_stat | story | bold_claim
    score: int                         # 1-100 estimated engagement


class IdeaVariant(BaseModel):
    title: str
    angle: str                         # e.g. "beginner perspective"
    description: str
    hooks: list[HookVariant]
    recommended_format: str
    estimated_minutes: int
    difficulty: str


class IdeaGenerateResponse(BaseModel):
    ideas: list[IdeaVariant]


# ── Production guide ─────────────────────────────────────────

class ScriptRequest(BaseModel):
    title: str
    description: str
    hook: str
    platform: str
    niche: str
    duration_minutes: int = 3


class ScriptSection(BaseModel):
    section: str                       # hook | context | main_point | cta
    content: str
    tips: str


class ScriptResponse(BaseModel):
    sections: list[ScriptSection]
    total_words: int
    estimated_duration_seconds: int


class ShotListRequest(BaseModel):
    title: str
    script_sections: list[ScriptSection]
    format: str                        # reel | short | long_form
    platform: str


class ShotItem(BaseModel):
    order: int
    shot_type: str                     # talking_head | broll | screen_record | text_slide
    description: str
    duration_seconds: int
    tips: str


class BrollSuggestion(BaseModel):
    keyword: str
    pexels_url: str


class ShotListResponse(BaseModel):
    shots: list[ShotItem]
    broll_suggestions: list[BrollSuggestion]
    total_duration_seconds: int


# ── Captions & hashtags ──────────────────────────────────────

class CaptionRequest(BaseModel):
    title: str
    description: str
    platform: str
    niche: str
    hook: Optional[str] = None


class CaptionVariant(BaseModel):
    style: str                         # curiosity_gap | storytelling | listicle
    caption: str
    char_count: int


class HashtagSet(BaseModel):
    big: list[str]                     # 1M+ posts
    niche: list[str]                   # 100K–1M posts
    micro: list[str]                   # under 100K posts


class CaptionResponse(BaseModel):
    variants: list[CaptionVariant]
    hashtags: HashtagSet
    best_posting_time: str


# ── Trending ─────────────────────────────────────────────────

class TrendingRequest(BaseModel):
    niche: str
    region_code: str = "IN"
    max_results: int = 10


class TrendingIdea(BaseModel):
    title: str
    description: str
    hook_preview: str
    niche: str
    platforms: list[str]
    recommended_format: str
    difficulty: str
    estimated_minutes: int
    viral_score: int
    trending_reason: str
    similar_views_avg: int
