"""Fixtures for the artifact store tests: a fresh bucket on the session MinIO.

Every test gets its own empty bucket, created before it and emptied (objects and
incomplete multipart uploads) and removed after it.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from aiobotocore.session import get_session

from tests.fakes.clock import FrozenClock
from tests.integration.conftest import MinioServer
from yakhnama.modules.exchange.infrastructure.adapters.s3_artifact_store import (
    S3ArtifactStore,
)

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

REGION: Final = "us-east-1"
STORE_NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
PRESIGN_TTL_SECONDS: Final = 300


def admin_client(server: MinioServer) -> AbstractAsyncContextManager["S3Client"]:
    """Return an unopened S3 client context on ``server`` for set-up and checks.

    Args:
        server: The MinIO container.

    Returns:
        The client context; use it with ``async with``.
    """
    return get_session().create_client(
        "s3",
        endpoint_url=server.endpoint_url,
        region_name=REGION,
        aws_access_key_id=server.access_key_id,
        aws_secret_access_key=server.secret_access_key.get_secret_value(),
    )


def build_store(server: MinioServer, bucket: str) -> S3ArtifactStore:
    """Return the adapter on ``server`` and ``bucket``.

    Args:
        server: The MinIO container.
        bucket: The bucket.

    Returns:
        The adapter, with the production part size.
    """
    return S3ArtifactStore(
        endpoint_url=server.endpoint_url,
        region=REGION,
        access_key_id=server.access_key_id,
        secret_access_key=server.secret_access_key,
        bucket=bucket,
        presign_ttl_seconds=PRESIGN_TTL_SECONDS,
        clock=FrozenClock(STORE_NOW),
    )


async def _empty_and_remove(client: "S3Client", bucket: str) -> None:
    uploads = await client.list_multipart_uploads(Bucket=bucket)
    for upload in uploads.get("Uploads", []):
        await client.abort_multipart_upload(
            Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
        )
    listing = await client.list_objects_v2(Bucket=bucket)
    for item in listing.get("Contents", []):
        await client.delete_object(Bucket=bucket, Key=item["Key"])
    await client.delete_bucket(Bucket=bucket)


@pytest.fixture
async def bucket(
    minio_server: MinioServer, request: pytest.FixtureRequest
) -> AsyncIterator[str]:
    """Create an empty bucket for one test, removed afterwards."""
    # Derived from the test id, so names are unique per test and stable per run.
    name = f"exchange-{hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]}"
    async with admin_client(minio_server) as client:
        await client.create_bucket(Bucket=name)

    yield name

    async with admin_client(minio_server) as client:
        await _empty_and_remove(client, name)


@pytest.fixture
def store(minio_server: MinioServer, bucket: str) -> S3ArtifactStore:
    """Return the adapter on the test's bucket."""
    return build_store(minio_server, bucket)
