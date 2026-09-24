"""Unit tests of the artifact store's key rules; storage itself is tested on MinIO."""

from datetime import UTC, datetime
from typing import Final

import pytest
from pydantic import SecretStr

from tests.fakes.clock import FrozenClock
from yakhnama.modules.exchange.infrastructure.adapters.s3_artifact_store import (
    ArtifactStorageError,
    S3ArtifactStore,
    checked_key,
)
from yakhnama.platform.settings import Settings

NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "key", ["exports/job-1/events.csv", "imports/upload-1/source.geojson"]
)
def test_checked_key_accepts_exchange_prefixes(key: str) -> None:
    valid = checked_key(key)

    assert valid == key


@pytest.mark.parametrize(
    "key",
    [
        "media/original/abc",
        "exports/../media/original/abc",
        "Exports/UPPER",
        "",
        "exportsx/file",
    ],
)
def test_checked_key_refuses_other_or_malformed_keys(key: str) -> None:
    with pytest.raises(ArtifactStorageError) as caught:
        checked_key(key)

    assert caught.value.details == {"operation": "key"}
    assert caught.value.code == "storage_error"
    assert key not in caught.value.message or not key


def test_checked_key_with_narrower_prefixes_refuses_imports() -> None:
    with pytest.raises(ArtifactStorageError):
        checked_key("imports/upload-1/source.csv", ("exports/",))


def _store() -> S3ArtifactStore:
    # Never connects: every test here fails before a storage call.
    return S3ArtifactStore(
        endpoint_url="http://storage.invalid",
        region="us-east-1",
        access_key_id="test",
        secret_access_key=SecretStr("test-only"),
        bucket="bucket",
        presign_ttl_seconds=60,
        clock=FrozenClock(NOW),
    )


async def test_presign_download_refuses_unsafe_file_name_before_signing() -> None:
    store = _store()

    with pytest.raises(ArtifactStorageError) as caught:
        await store.presign_download(
            "exports/job-1/events.csv", file_name='a"; filename=evil.exe'
        )

    assert caught.value.details == {"operation": "presign"}


def test_from_settings_uses_the_private_bucket() -> None:
    settings = Settings(
        _env_file=None,
        storage_private_bucket="originals",
        storage_public_bucket="copies",
    )

    store = S3ArtifactStore.from_settings(settings, FrozenClock(NOW))

    assert store._bucket == "originals"
    assert "minioadmin-dev-only" not in repr(vars(store))


@pytest.mark.parametrize(
    "key", ["exports/job-1/events.csv", "media/upload/abc", "imports/../exports/x"]
)
async def test_presign_upload_refuses_keys_other_than_imports(key: str) -> None:
    store = _store()

    with pytest.raises(ArtifactStorageError) as caught:
        await store.presign_upload(key, "text/csv", 1024)

    assert caught.value.details == {"operation": "key"}
