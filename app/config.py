from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Gemini
    gemini_api_key: str = ""

    # YouTube
    youtube_api_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # App
    frontend_url: str = "http://localhost:3000"
    environment: str = "development"

    # AWS S3 (Phase 3)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"
    s3_bucket_name: str = "creator-os-media"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
