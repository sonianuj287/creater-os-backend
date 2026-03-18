from pydantic_settings import BaseSettings
from pydantic import ConfigDict
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

    # Cloudflare R2 / AWS S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "auto"
    aws_endpoint_url: str = ""
    s3_bucket_name: str = "creator-os-media"

    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="allow"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
