import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import uuid
import os
from app.config import get_settings

settings = get_settings()


def get_s3_client():
    """
    Returns a boto3 S3 client.
    Works with both AWS S3 and Cloudflare R2 (S3-compatible).
    R2 requires endpoint_url. S3 does not.
    """
    kwargs = dict(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=Config(signature_version="s3v4"),
    )
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url

    return boto3.client("s3", **kwargs)


def generate_upload_key(user_id: str, filename: str, folder: str = "uploads") -> str:
    """Generate a unique S3 key for a file."""
    ext = os.path.splitext(filename)[-1].lower()
    unique_id = uuid.uuid4().hex[:12]
    return f"{folder}/{user_id}/{unique_id}{ext}"


async def create_presigned_upload_url(
    user_id: str,
    filename: str,
    content_type: str,
    folder: str = "uploads",
    expires_in: int = 3600,
) -> dict:
    """
    Generate a presigned URL for direct browser-to-R2 upload.
    The frontend uploads directly — no proxying through Railway.
    """
    s3 = get_s3_client()
    key = generate_upload_key(user_id, filename, folder)

    try:
        presigned_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_bucket_name,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return {
            "upload_url": presigned_url,
            "key": key,
            "bucket": settings.s3_bucket_name,
        }
    except ClientError as e:
        raise Exception(f"Failed to generate presigned URL: {e}")


async def create_presigned_download_url(key: str, expires_in: int = 86400) -> str:
    """Generate a presigned URL for downloading/viewing a processed file."""
    s3 = get_s3_client()
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
        return url
    except ClientError as e:
        raise Exception(f"Failed to generate download URL: {e}")


def download_file_from_s3(key: str, local_path: str) -> str:
    """Download a file from S3/R2 to local filesystem for processing."""
    s3 = get_s3_client()
    s3.download_file(settings.s3_bucket_name, key, local_path)
    return local_path


def upload_file_to_s3(local_path: str, key: str, content_type: str = "video/mp4") -> str:
    """Upload a processed file back to S3/R2."""
    s3 = get_s3_client()
    s3.upload_file(
        local_path,
        settings.s3_bucket_name,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return key


def delete_file_from_s3(key: str):
    """Delete a file from S3/R2."""
    s3 = get_s3_client()
    s3.delete_object(Bucket=settings.s3_bucket_name, Key=key)
