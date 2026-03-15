from supabase import create_client, Client
from app.config import get_settings

settings = get_settings()

_client: Client | None = None

def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,  # Service role = full access
        )
    return _client


async def save_trending_ideas(ideas: list[dict]) -> int:
    """Save AI-generated trending ideas to the ideas table. Returns count saved."""
    supabase = get_supabase()
    saved = 0

    for idea in ideas:
        try:
            # Upsert to avoid duplicates on repeated cron runs
            supabase.table("ideas").insert({
                "source":            "trending",
                "title":             idea.get("title", ""),
                "description":       idea.get("description", ""),
                "hook_preview":      idea.get("hook_preview", ""),
                "niche":             idea.get("niche", "other"),
                "platforms":         idea.get("platforms", []),
                "recommended_format": idea.get("recommended_format", "reel"),
                "difficulty":        idea.get("difficulty", "medium"),
                "estimated_minutes": idea.get("estimated_minutes", 30),
                "viral_score":       idea.get("viral_score", 70),
                "trending_reason":   idea.get("trending_reason", ""),
                "similar_views_avg": idea.get("similar_views_avg", 0),
                "is_active":         True,
            }).execute()
            saved += 1
        except Exception as e:
            print(f"Error saving idea: {e}")

    return saved


async def get_active_ideas(niche: str | None = None, limit: int = 20) -> list[dict]:
    """Fetch active ideas from DB, optionally filtered by niche."""
    supabase = get_supabase()
    query = supabase.table("ideas").select("*").eq("is_active", True)

    if niche and niche != "all":
        query = query.eq("niche", niche)

    result = query.order("viral_score", desc=True).limit(limit).execute()
    return result.data or []


async def save_project(user_id: str, project_data: dict) -> dict:
    """Create a new project for a user."""
    supabase = get_supabase()
    result = supabase.table("projects").insert({
        "user_id": user_id,
        **project_data,
    }).execute()
    return result.data[0] if result.data else {}


async def update_project(project_id: str, updates: dict) -> dict:
    """Update project fields (hook_variants, script_outline, shot_list, etc.)"""
    supabase = get_supabase()
    result = (
        supabase.table("projects")
        .update(updates)
        .eq("id", project_id)
        .execute()
    )
    return result.data[0] if result.data else {}
