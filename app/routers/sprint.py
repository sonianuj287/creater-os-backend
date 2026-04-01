"""
Creator Sprint Router — 30-day niche-specific content challenge.

Endpoints:
  POST /sprint/enroll        — enroll user, generate 30 ideas, send welcome email
  GET  /sprint/me            — get user's active sprint + progress
  POST /sprint/complete-day  — mark a day done (links to a project)
  POST /sprint/send-reminder — manually trigger today's reminder email
  DELETE /sprint/cancel      — cancel active sprint
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import httpx
import asyncio
from datetime import date, timedelta, datetime

from app.services.db_service import get_supabase
from app.services import ai_service
from app.config import get_settings

router = APIRouter(prefix="/sprint", tags=["sprint"])
settings = get_settings()

# ── Schemas ────────────────────────────────────────────────────

class EnrollRequest(BaseModel):
    user_id: str
    niche: str
    email: str
    name: str
    email_notifications: bool = True

class CompleteDayRequest(BaseModel):
    user_id: str
    sprint_id: str
    day_number: int
    project_id: Optional[str] = None

class ReminderRequest(BaseModel):
    user_id: str

# ── Email via Resend ───────────────────────────────────────────

RESEND_API_URL = "https://api.resend.com/emails"

async def _send_email(to: str, subject: str, html: str) -> bool:
    """Send email via Resend API. Returns True if sent."""
    api_key = getattr(settings, "resend_api_key", "")
    if not api_key:
        print(f"[Sprint] Email skipped — RESEND_API_KEY not set. Would send: {subject} → {to}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "from": "CreaterOS <sprint@createros.in>",
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[Sprint] Email error: {e}")
        return False


def _welcome_email_html(name: str, niche: str, day1_title: str, app_url: str) -> str:
    return f"""
<div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;background:#0a0a0f;color:#fff;border-radius:16px;overflow:hidden;border:1px solid #1e1e2e">
  <div style="background:linear-gradient(135deg,#7c6af5,#ec4899);padding:40px 32px;text-align:center">
    <div style="font-size:48px;margin-bottom:12px">🔥</div>
    <h1 style="margin:0;font-size:28px;font-weight:900;letter-spacing:-0.5px">You're in, {name}!</h1>
    <p style="margin:8px 0 0;opacity:.85;font-size:15px">Your 30-Day Creator Sprint starts NOW</p>
  </div>
  <div style="padding:32px">
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;margin-bottom:24px;border:1px solid #2a2a4a">
      <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#7c6af5;font-weight:700">Day 1 Idea · {niche.capitalize()}</p>
      <p style="margin:0;font-size:18px;font-weight:700;line-height:1.4">{day1_title}</p>
    </div>
    <p style="color:#9ca3af;font-size:14px;line-height:1.6">
      You've committed to 30 days of consistent content creation. Every day you'll get one high-impact video idea tailored to your <strong style="color:#fff">{niche}</strong> niche — with hooks, script angles, and format recommendations.
    </p>
    <a href="{app_url}/dashboard/sprint" style="display:block;text-align:center;background:linear-gradient(135deg,#7c6af5,#ec4899);color:#fff;text-decoration:none;padding:16px 32px;border-radius:12px;font-weight:700;font-size:15px;margin:24px 0">
      🚀 Open My Sprint Dashboard
    </a>
    <p style="color:#4b5563;font-size:12px;text-align:center">You'll get a daily reminder at 9 AM. No spam — unsubscribe anytime from settings.</p>
  </div>
</div>"""


def _daily_reminder_html(name: str, day: int, idea_title: str, hook: str, niche: str, app_url: str) -> str:
    emoji_map = {1:"🌱",5:"⚡",10:"🔥",15:"💪",20:"🚀",25:"💎",30:"👑"}
    emoji = emoji_map.get(day, "🎬")
    return f"""
<div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;background:#0a0a0f;color:#fff;border-radius:16px;overflow:hidden;border:1px solid #1e1e2e">
  <div style="background:#1a1a2e;padding:20px 32px;border-bottom:1px solid #2a2a4a;display:flex;align-items:center;gap:12px">
    <div style="font-size:24px">{emoji}</div>
    <div>
      <p style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#7c6af5;font-weight:700">Day {day} of 30</p>
      <p style="margin:2px 0 0;font-size:13px;color:#9ca3af">Creator Sprint · {niche.capitalize()}</p>
    </div>
    <div style="margin-left:auto;background:#7c6af5;color:#fff;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700">{day}/30</div>
  </div>
  <div style="padding:32px">
    <p style="color:#9ca3af;font-size:14px;margin:0 0 20px">Hey {name} 👋 Here's your content for today:</p>
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;border:1px solid #2a2a4a;margin-bottom:24px">
      <p style="margin:0 0 12px;font-size:18px;font-weight:700;line-height:1.4">{idea_title}</p>
      <p style="margin:0;color:#a78bfa;font-size:14px;font-style:italic;border-left:3px solid #7c6af5;padding-left:12px">"{hook}"</p>
    </div>
    <a href="{app_url}/dashboard/sprint" style="display:block;text-align:center;background:linear-gradient(135deg,#7c6af5,#ec4899);color:#fff;text-decoration:none;padding:16px 32px;border-radius:12px;font-weight:700;font-size:15px;margin-bottom:16px">
      🎬 Create Today's Video
    </a>
    <p style="color:#4b5563;font-size:12px;text-align:center">Miss a day? No stress — it stays in your calendar. Just keep the streak going!</p>
  </div>
</div>"""


# ── Generate 30 sprint ideas via Gemini ───────────────────────

async def _generate_sprint_ideas(niche: str) -> list[dict]:
    """Ask Gemini for a coherent 30-day progressive content roadmap for a niche."""
    prompt = f"""You are a viral content strategist. Create a 30-day progressive content roadmap for a {niche} creator on Instagram/YouTube.

Rules:
- Days 1-5: Easy, relatable, personal story angles
- Days 6-15: Educational/informative hooks 
- Days 16-22: Controversial/opinion-based for engagement
- Days 23-28: Collaboration and trend-jacking ideas
- Days 29-30: Series finale / call-to-action posts

FOR EACH DAY return exactly this JSON object:
{{"day": 1, "title": "...", "hook": "...", "format": "reel|short|long_form", "difficulty": "easy|medium|hard", "angle": "..."}}

Return ONLY a valid JSON array of 30 objects. No markdown, no extra text."""

    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Clean up markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        import json
        ideas = json.loads(text)
        return ideas[:30]  # Ensure max 30
    except Exception as e:
        print(f"[Sprint] Gemini failed: {e} — using fallback ideas")
        return _fallback_ideas(niche)


def _fallback_ideas(niche: str) -> list[dict]:
    """Minimal fallback if AI fails."""
    templates = [
        "Why I started my {niche} journey",
        "The biggest myth about {niche}",
        "My honest review after 30 days of {niche}",
        "What nobody tells you about {niche}",
        "The {niche} mistake I made so you don't have to",
    ]
    ideas = []
    for i in range(30):
        title = templates[i % len(templates)].format(niche=niche)
        ideas.append({
            "day": i + 1,
            "title": title,
            "hook": f"If you're into {niche}, you need to see this...",
            "format": "reel",
            "difficulty": "easy" if i < 10 else "medium",
            "angle": "personal story",
        })
    return ideas


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/enroll")
async def enroll_sprint(req: EnrollRequest, background: BackgroundTasks):
    """Enroll user in a 30-day Creator Sprint."""
    db = get_supabase()

    # Cancel any existing active sprint
    db.table("creator_sprints")\
        .update({"status": "cancelled"})\
        .eq("user_id", req.user_id)\
        .eq("status", "active")\
        .execute()

    # Generate 30 ideas
    ideas = await _generate_sprint_ideas(req.niche)

    # Insert sprint record
    start = date.today().isoformat()
    end   = (date.today() + timedelta(days=29)).isoformat()

    result = db.table("creator_sprints").insert({
        "user_id":              req.user_id,
        "niche":                req.niche,
        "status":               "active",
        "start_date":           start,
        "end_date":             end,
        "ideas":                ideas,
        "email_notifications":  req.email_notifications,
        "enrollee_email":       req.email,
        "enrollee_name":        req.name,
        "streak":               0,
        "days_completed":       0,
    }).execute()

    sprint_id = result.data[0]["id"] if result.data else None

    # Send welcome email in background
    if req.email_notifications and ideas:
        day1 = ideas[0]
        html = _welcome_email_html(req.name, req.niche, day1["title"], settings.frontend_url)
        background.add_task(_send_email, req.email, "🔥 Your 30-Day Creator Sprint begins NOW!", html)

    return {
        "sprint_id": sprint_id,
        "start_date": start,
        "end_date": end,
        "ideas_count": len(ideas),
        "message": "Sprint enrolled! Check your email for Day 1 idea.",
    }


@router.get("/me/{user_id}")
async def get_my_sprint(user_id: str):
    """Get user's active sprint with ideas and progress."""
    try:
        db = get_supabase()

        # Get active sprint
        sprint_res = db.table("creator_sprints")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("status", "active")\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if not sprint_res.data:
            return {"sprint": None}

        sprint = sprint_res.data[0]

        # Get completed days
        progress_res = db.table("sprint_progress")\
            .select("day_number, completed_at, project_id")\
            .eq("sprint_id", sprint["id"])\
            .execute()

        completed_days = {p["day_number"]: p for p in (progress_res.data or [])}

        # Calculate current day
        start = date.fromisoformat(sprint["start_date"])
        current_day = min(30, (date.today() - start).days + 1)

        # Calculate streak
        streak = 0
        for d in range(current_day, 0, -1):
            if d in completed_days:
                streak += 1
            else:
                break

        return {
            "sprint": {
                **sprint,
                "current_day": current_day,
                "streak": streak,
                "completed_days": completed_days,
                "days_remaining": 30 - current_day,
                "completion_pct": round((len(completed_days) / 30) * 100),
            }
        }
    except Exception as e:
        return {"sprint": None, "error": str(e)}


@router.post("/complete-day")
async def complete_day(req: CompleteDayRequest, background: BackgroundTasks):
    """Mark a sprint day as completed."""
    db = get_supabase()

    # Check if already done
    existing = db.table("sprint_progress")\
        .select("id")\
        .eq("sprint_id", req.sprint_id)\
        .eq("day_number", req.day_number)\
        .execute()

    if existing.data:
        return {"status": "already_completed", "day": req.day_number}

    db.table("sprint_progress").insert({
        "sprint_id":    req.sprint_id,
        "user_id":      req.user_id,
        "day_number":   req.day_number,
        "project_id":   req.project_id,
        "completed_at": datetime.utcnow().isoformat(),
    }).execute()

    # Update days_completed + streak on sprint
    sprint_res = db.table("creator_sprints").select("ideas,days_completed,enrollee_email,enrollee_name,niche,email_notifications").eq("id", req.sprint_id).single().execute()
    sprint = sprint_res.data or {}
    new_count = sprint.get("days_completed", 0) + 1
    db.table("creator_sprints").update({"days_completed": new_count}).eq("id", req.sprint_id).execute()

    # If day 30 — mark complete
    if req.day_number == 30:
        db.table("creator_sprints").update({"status": "completed"}).eq("id", req.sprint_id).execute()

    # Send tomorrow's reminder email
    next_day = req.day_number + 1
    if sprint.get("email_notifications") and next_day <= 30:
        ideas = sprint.get("ideas", [])
        if len(ideas) >= next_day:
            next_idea = ideas[next_day - 1]
            html = _daily_reminder_html(
                sprint.get("enrollee_name", "Creator"),
                next_day,
                next_idea["title"],
                next_idea.get("hook", ""),
                sprint.get("niche", ""),
                settings.frontend_url,
            )
            background.add_task(
                _send_email,
                sprint.get("enrollee_email", ""),
                f"🎬 Day {next_day}: Your content idea is ready!",
                html,
            )

    return {"status": "completed", "day": req.day_number, "total_completed": new_count}


@router.delete("/cancel/{user_id}")
async def cancel_sprint(user_id: str):
    """Cancel active sprint."""
    db = get_supabase()
    db.table("creator_sprints")\
        .update({"status": "cancelled"})\
        .eq("user_id", user_id)\
        .eq("status", "active")\
        .execute()
    return {"status": "cancelled"}
