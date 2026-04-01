from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Gemini
    gemini_api_key: str = ""

    # YouTube Data API (for trending feed)
    youtube_api_key: str = ""

    # YouTube OAuth (for posting videos)
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    # Instagram
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_test_token: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Cloudflare R2 / AWS S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "auto"
    aws_endpoint_url: str = ""
    s3_bucket_name: str = "creator-os-media"

    # App
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    environment: str = "development"

    # Admin
    admin_secret_key: str = ""

    # Email (Resend — https://resend.com, free 3000 emails/mo)
    resend_api_key: str = ""


    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_creator_price_id: str = ""
    stripe_pro_price_id: str = ""
