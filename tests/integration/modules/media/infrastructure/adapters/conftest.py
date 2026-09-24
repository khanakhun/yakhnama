"""Fixtures for the media adapter tests: buckets on the session MinIO and adapters.

Every test gets fresh, empty buckets (created before it, emptied and removed after
it), so no test sees another's objects.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from aiobotocore.session import get_session
from pydantic import BaseModel, ConfigDict

from tests.fakes.clock import FrozenClock
from tests.integration.conftest import MinioServer
from yakhnama.modules.media.infrastructure.adapters.s3_storage import S3StoragePort

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

STORAGE_NOW: Final = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
PRESIGN_TTL_SECONDS: Final = 300
REGION: Final = "us-east-1"


class Buckets(BaseModel):
    """The private and public bucket names of one test.

    Implements: Value Object.

    Attributes:
        private: Bucket of the originals.
        public: Bucket of the public copies.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    private: str
    public: str


def admin_client(server: MinioServer) -> AbstractAsyncContextManager["S3Client"]:
    """Return an unopened S3 client context on ``server`` for test set-up.

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


@pytest.fixture
async def buckets(
    minio_server: MinioServer, request: pytest.FixtureRequest
) -> AsyncIterator[Buckets]:
    """Create an empty private and public bucket for one test, removed afterwards."""
    # Derived from the test id, so names are unique per test and stable per run.
    suffix = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    names = Buckets(private=f"private-{suffix}", public=f"public-{suffix}")
    async with admin_client(minio_server) as client:
        for name in (names.private, names.public):
            await client.create_bucket(Bucket=name)

    yield names

    async with admin_client(minio_server) as client:
        for name in (names.private, names.public):
            listing = await client.list_objects_v2(Bucket=name)
            for item in listing.get("Contents", []):
                await client.delete_object(Bucket=name, Key=item["Key"])
            await client.delete_bucket(Bucket=name)


def build_storage(
    server: MinioServer, buckets: Buckets, *, max_object_bytes: int | None = None
) -> S3StoragePort:
    """Return the adapter on ``server`` and ``buckets`` with a frozen clock.

    Args:
        server: The MinIO container.
        buckets: The test's buckets.
        max_object_bytes: A smaller size cap, to test it without 50 MiB files.

    Returns:
        The adapter.
    """
    extra = {} if max_object_bytes is None else {"max_object_bytes": max_object_bytes}
    return S3StoragePort(
        endpoint_url=server.endpoint_url,
        region=REGION,
        access_key_id=server.access_key_id,
        secret_access_key=server.secret_access_key,
        private_bucket=buckets.private,
        public_bucket=buckets.public,
        presign_ttl_seconds=PRESIGN_TTL_SECONDS,
        clock=FrozenClock(STORAGE_NOW),
        **extra,
    )


@pytest.fixture
def storage(minio_server: MinioServer, buckets: Buckets) -> S3StoragePort:
    """Return the adapter on the test's buckets."""
    return build_storage(minio_server, buckets)
