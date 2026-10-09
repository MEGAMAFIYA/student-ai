"""Cloudflare R2 media storage for Kino.

R2 is optional: when R2 env vars are missing the application falls back to the
existing Telegram -> Render temporary cache path. When configured, movies are
uploaded once to R2 and the Mini App receives a direct public/CDN URL or a
short-lived presigned URL, so Render does not proxy video bytes.
"""
from __future__ import annotations

import logging
import os
import re
import urllib.parse
import uuid

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # optional dependency / local development without R2
    boto3 = None
    BotoConfig = None

ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID", "").strip()
SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
BUCKET = os.getenv("R2_BUCKET", "").strip()
PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
PRESIGNED_TTL = max(60, min(3600, int(os.getenv("R2_PRESIGNED_TTL_SEC", "900"))))
ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com" if ACCOUNT_ID else ""


def enabled() -> bool:
    return bool(boto3 and ACCOUNT_ID and ACCESS_KEY and SECRET_KEY and BUCKET)


def _client():
    if not enabled():
        raise RuntimeError("Cloudflare R2 sozlanmagan.")
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="auto",
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


def safe_key_part(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "")).strip("._")
    return value[:160] or uuid.uuid4().hex


def make_key(movie_id: str, file_name: str = "") -> str:
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"}:
        ext = ".mp4"
    return f"movies/{safe_key_part(movie_id)}/{safe_key_part(os.path.splitext(file_name or movie_id)[0])}{ext}"


def upload_file(path: str, key: str, content_type: str = "video/mp4") -> bool:
    client = _client()
    extra = {"ContentType": content_type or "video/mp4", "CacheControl": "public, max-age=31536000, immutable"}
    client.upload_file(path, BUCKET, key, ExtraArgs=extra)
    logger.info("☁️ R2 upload OK: bucket=%s key=%s", BUCKET, key)
    return True


def exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def media_url(key: str) -> str:
    if not key:
        return ""
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/{urllib.parse.quote(key, safe='/') }"
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=PRESIGNED_TTL,
    )
