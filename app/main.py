from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.routers import ideas, studio

settings = get_settings()

app = FastAPI(
    title="Creator OS API",
    description="AI backend for Creator OS — idea generation, scripts, shot lists, captions",
    version="0.2.0",
)

# CORS — allow requests from your Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3000",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(ideas.router)
app.include_router(studio.router)


@app.get("/")
async def root():
    return {
        "service": "Creator OS API",
        "version": "0.2.0",
        "status": "running",
        "environment": settings.environment,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
