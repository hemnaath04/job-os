"""Cloudflare R2 (S3-compatible) blob storage.

We use a synchronous boto3 client wrapped in `run_in_threadpool` — the boto
async client is heavy and we only use R2 for occasional resume artifact
uploads, not the request hot path.

When R2 credentials are absent, uploads return None and the route reports
"not configured" instead of failing.
"""
from __future__ import annotations

from dataclasses import dataclass

import boto3
import structlog
from fastapi.concurrency import run_in_threadpool

from job_os.settings import get_settings

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class UploadResult:
    key: str
    public_url: str | None


def _client():
    s = get_settings()
    if not (s.r2_account_id and s.r2_access_key_id and s.r2_secret_access_key and s.r2_bucket):
        return None, None
    endpoint = f"https://{s.r2_account_id}.r2.cloudflarestorage.com"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name="auto",
    )
    return client, s.r2_bucket


async def upload(key: str, content: bytes, content_type: str) -> UploadResult | None:
    client, bucket = _client()
    if not client:
        log.warning("r2.skipped.no_config", key=key)
        return None
    s = get_settings()

    def _put():
        client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)

    await run_in_threadpool(_put)
    public_url = None
    if s.r2_public_base_url:
        public_url = f"{s.r2_public_base_url.rstrip('/')}/{key}"
    return UploadResult(key=key, public_url=public_url)


async def download(key: str) -> bytes | None:
    client, bucket = _client()
    if not client:
        return None

    def _get():
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()

    return await run_in_threadpool(_get)


async def presign_get(key: str, expires_seconds: int = 3600) -> str | None:
    client, bucket = _client()
    if not client:
        return None

    def _sign():
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    return await run_in_threadpool(_sign)
