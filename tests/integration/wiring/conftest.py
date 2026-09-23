"""Fixtures for the wiring tests: a migrated schema, buckets and the real container.

``migrated_schema`` upgrades the session database to ``head`` for this package and
downgrades it to ``base`` (dropping Alembic's version table) afterwards, so the
other integration packages still find an empty database. ``buckets`` creates an
empty private and public bucket on the session MinIO for one test and removes them
after it.
"""

import asyncio
import uuid
from argparse import Namespace
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from aiobotocore.session import get_session
from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict, PostgresDsn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import MinioServer
from yakhnama.platform.settings import Settings

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
ALEMBIC_INI: Final = REPOSITORY_ROOT / "alembic.ini"
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


def _alembic_config(database_url: str) -> Config:
    config = Config(ALEMBIC_INI, cmd_opts=Namespace(x=[f"database_url={database_url}"]))
    # env.py would otherwise call fileConfig and replace pytest's log handlers.
    config.attributes["configure_logger"] = False
    return config


async def _drop_version_table(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


@pytest.fixture(scope="package")
def migrated_schema(postgis_url: str) -> Iterator[None]:
    """Upgrade the session database to head for this package, then empty it.

    Synchronous on purpose: ``migrations/env.py`` runs its own event loop with
    ``asyncio.run``, which cannot start inside a running pytest-asyncio loop.
    """
    config = _alembic_config(postgis_url)
    command.upgrade(config, "head")

    yield

    command.downgrade(config, "base")
    asyncio.run(_drop_version_table(postgis_url))


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
async def buckets(minio_server: MinioServer) -> AsyncIterator[Buckets]:
    """Create an empty private and public bucket for one test, removed afterwards."""
    suffix = uuid.uuid4().hex[:12]
    names = Buckets(
        private=f"wiring-private-{suffix}", public=f"wiring-public-{suffix}"
    )
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


@pytest.fixture
def wiring_settings(
    migrated_schema: None,
    postgis_url: str,
    minio_server: MinioServer,
    buckets: Buckets,
) -> Settings:
    """Return test settings on the migrated database and the test's buckets.

    The in-memory task broker runs tasks in this process; the malware scanner is
    the development ``noop`` one (see the test for how it is made to answer clean).
    """
    del migrated_schema
    return Settings(
        _env_file=None,
        environment="test",
        log_format="console",
        database_url=PostgresDsn(postgis_url),
        storage_endpoint_url=minio_server.endpoint_url,
        storage_region=REGION,
        storage_access_key_id=minio_server.access_key_id,
        storage_secret_access_key=minio_server.secret_access_key,
        storage_private_bucket=buckets.private,
        storage_public_bucket=buckets.public,
        task_queue_backend="memory",
        malware_scanner="noop",
    )
