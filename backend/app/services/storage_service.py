"""Object storage (S3 / MinIO) access. Credentials never leave the backend."""

import asyncio

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import Settings


class ObjectStorage:
    """Thin async wrapper over a boto3 S3 client bound to the configured bucket."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value() or None,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
            region_name=settings.s3_region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=2,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    async def check_bucket(self) -> None:
        """Raise if the bucket is unreachable or the credentials are rejected."""
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
