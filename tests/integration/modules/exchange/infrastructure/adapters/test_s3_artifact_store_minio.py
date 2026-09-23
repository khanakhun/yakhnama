"""The S3 artifact store against a real MinIO: streaming, prefixes and links."""

import hashlib
import random
from collections.abc import Coroutine
from datetime import timedelta
from typing import Final

import httpx
import pytest
from structlog.testing import capture_logs

from tests.integration.conftest import MinioServer
from tests.integration.modules.exchange.infrastructure.adapters.conftest import (
    PRESIGN_TTL_SECONDS,
    STORE_NOW,
    admin_client,
    build_store,
)
from tests.unit.modules.exchange.application.support import event_row
from tests.unit.modules.exchange.infrastructure.adapters.support import rows_of
from yakhnama.modules.exchange.application.streams import MeteredSink
from yakhnama.modules.exchange.domain.value_objects import ExportDataset
from yakhnama.modules.exchange.infrastructure.adapters import s3_artifact_store
from yakhnama.modules.exchange.infrastructure.adapters.csv_exporter import CsvExporter
from yakhnama.modules.exchange.infrastructure.adapters.csv_importer import CsvImporter
from yakhnama.modules.exchange.infrastructure.adapters.s3_artifact_store import (
    MULTIPART_PART_BYTES,
    ArtifactStorageError,
    S3ArtifactStore,
)
from yakhnama.shared_kernel.errors import NotFoundError

pytestmark = pytest.mark.integration

MIB: Final = 1024 * 1024
EXPORT_KEY: Final = "exports/job-1/events.csv"
SIDECAR_KEY: Final = "exports/job-1/events.sidecar.json"
IMPORT_KEY: Final = "imports/upload-1/source.csv"


class _ExportFailedError(Exception):
    """Raised inside a sink block to simulate an exporter failing mid-way."""


def _payload(size: int) -> bytes:
    # Seeded pseudo-random bytes: incompressible, reproducible, no real data.
    return random.Random(size).randbytes(size)  # noqa: S311  # reason: test data, not security


async def _object(server: MinioServer, bucket: str, key: str) -> tuple[bytes, str, str]:
    async with admin_client(server) as client:
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as body:
            data = await body.read()
        return data, response["ContentType"], response["ETag"]


async def _keys(server: MinioServer, bucket: str) -> list[str]:
    async with admin_client(server) as client:
        listing = await client.list_objects_v2(Bucket=bucket)
        return [item["Key"] for item in listing.get("Contents", [])]


async def _pending_uploads(server: MinioServer, bucket: str) -> int:
    async with admin_client(server) as client:
        uploads = await client.list_multipart_uploads(Bucket=bucket)
        return len(uploads.get("Uploads", []))


async def _write_in_chunks(store: S3ArtifactStore, key: str, data: bytes) -> None:
    async with store.open_sink(key, "text/csv") as sink:
        for start in range(0, len(data), MIB):
            await sink.write(data[start : start + MIB])


async def _remove_bucket(server: MinioServer, bucket: str) -> None:
    # MinIO answers a repeated abort with success, so the bucket itself goes:
    # the adapter's own abort must then fail.
    async with admin_client(server) as client:
        uploads = await client.list_multipart_uploads(Bucket=bucket)
        for upload in uploads.get("Uploads", []):
            await client.abort_multipart_upload(
                Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
        await client.delete_bucket(Bucket=bucket)


async def _fail_after_writing(
    store: S3ArtifactStore,
    data: bytes,
    before_failing: Coroutine[None, None, None] | None = None,
) -> None:
    async with store.open_sink(EXPORT_KEY, "text/csv") as sink:
        await sink.write(data)
        if before_failing is not None:
            await before_failing
        raise _ExportFailedError


async def _complete_after(
    store: S3ArtifactStore, data: bytes, before_exit: Coroutine[None, None, None]
) -> None:
    async with store.open_sink(EXPORT_KEY, "text/csv") as sink:
        # Exactly one full part: it is uploaded here, nothing is left buffered.
        await sink.write(data)
        await before_exit


async def test_open_sink_above_part_size_uploads_multipart_object(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    data = _payload(2 * MULTIPART_PART_BYTES + MIB)

    await _write_in_chunks(store, EXPORT_KEY, data)

    stored, media_type, etag = await _object(minio_server, bucket, EXPORT_KEY)
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(data).hexdigest()
    assert media_type == "text/csv"
    # A multipart ETag ends in "-<number of parts>": 5 MiB, 5 MiB, 1 MiB.
    assert etag.strip('"').endswith("-3")
    assert await _pending_uploads(minio_server, bucket) == 0


async def test_open_sink_below_part_size_stores_single_object(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    data = b"title,hazard_type\r\nSynthetic,glof\r\n"

    await _write_in_chunks(store, EXPORT_KEY, data)

    stored, _, etag = await _object(minio_server, bucket, EXPORT_KEY)
    assert stored == data
    assert "-" not in etag


async def test_open_sink_without_writes_stores_empty_object(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    async with store.open_sink(EXPORT_KEY, "text/csv"):
        pass

    stored, _, _ = await _object(minio_server, bucket, EXPORT_KEY)
    assert stored == b""


@pytest.mark.parametrize("size", [MIB, MULTIPART_PART_BYTES + MIB])
async def test_open_sink_error_in_block_leaves_no_object_or_parts(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str, size: int
) -> None:
    with pytest.raises(_ExportFailedError):
        await _fail_after_writing(store, _payload(size))

    assert await _keys(minio_server, bucket) == []
    assert await _pending_uploads(minio_server, bucket) == 0


async def test_open_sink_failed_abort_is_logged_and_original_error_raised(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    with capture_logs() as logs, pytest.raises(_ExportFailedError):
        await _fail_after_writing(
            store,
            _payload(MULTIPART_PART_BYTES),
            before_failing=_remove_bucket(minio_server, bucket),
        )

    async with admin_client(minio_server) as client:
        # Recreated so the fixture can remove it as usual.
        await client.create_bucket(Bucket=bucket)
    assert [entry["event"] for entry in logs] == ["exchange.artifact_abort_failed"]
    assert logs[0]["operation"] == "abort_upload"
    assert EXPORT_KEY not in repr(logs)
    assert bucket not in repr(logs)


async def test_open_sink_failed_completion_raises_storage_error_and_aborts(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    with capture_logs() as logs, pytest.raises(ArtifactStorageError) as caught:
        await _complete_after(
            store,
            _payload(MULTIPART_PART_BYTES),
            _remove_bucket(minio_server, bucket),
        )

    async with admin_client(minio_server) as client:
        # Recreated so the fixture can remove it as usual.
        await client.create_bucket(Bucket=bucket)
    assert caught.value.details["operation"] == "complete_upload"
    assert [entry["event"] for entry in logs] == ["exchange.artifact_abort_failed"]


async def test_open_sink_beyond_part_limit_raises_and_aborts(
    store: S3ArtifactStore,
    minio_server: MinioServer,
    bucket: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(s3_artifact_store, "MAX_PARTS", 1)

    with pytest.raises(ArtifactStorageError) as caught:
        await _write_in_chunks(store, EXPORT_KEY, _payload(2 * MULTIPART_PART_BYTES))

    assert caught.value.details == {"operation": "upload_part"}
    assert await _keys(minio_server, bucket) == []
    assert await _pending_uploads(minio_server, bucket) == 0


async def test_put_bytes_stores_sidecar_with_media_type(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    sidecar = b'{"licence": "CC-BY-4.0 (proposed)"}'

    await store.put_bytes(SIDECAR_KEY, sidecar, "application/json")

    assert await _object(minio_server, bucket, SIDECAR_KEY) == (
        sidecar,
        "application/json",
        f'"{hashlib.md5(sidecar).hexdigest()}"',  # noqa: S324  # reason: S3 ETag of a single-part object is its MD5
    )


async def test_presign_download_link_downloads_as_attachment(
    store: S3ArtifactStore,
) -> None:
    await store.put_bytes(EXPORT_KEY, b"a,b\r\n1,2\r\n", "text/csv")

    url = await store.presign_download(
        EXPORT_KEY, file_name="yakhnama-job-1-events.csv"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
    assert response.status_code == httpx.codes.OK
    assert response.content == b"a,b\r\n1,2\r\n"
    assert response.headers["content-disposition"] == (
        'attachment; filename="yakhnama-job-1-events.csv"'
    )
    assert "X-Amz-Expires=300" in url


async def test_presign_download_of_import_file_is_refused(
    store: S3ArtifactStore,
) -> None:
    with pytest.raises(ArtifactStorageError) as caught:
        await store.presign_download(IMPORT_KEY, file_name="source.csv")

    assert caught.value.details == {"operation": "key"}


@pytest.mark.parametrize("key", ["media/original/abc", "exports/../media/x"])
async def test_every_operation_refuses_keys_outside_exchange_prefixes(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str, key: str
) -> None:
    with pytest.raises(ArtifactStorageError):
        await store.put_bytes(key, b"x", "text/csv")
    with pytest.raises(ArtifactStorageError):
        async with store.open_sink(key, "text/csv"):
            pass
    with pytest.raises(ArtifactStorageError):
        async with store.open_source(key):
            pass

    assert await _keys(minio_server, bucket) == []


async def test_open_source_streams_object_in_requested_sizes(
    store: S3ArtifactStore,
) -> None:
    data = _payload(3 * MIB + 17)
    await _write_in_chunks(store, IMPORT_KEY, data)

    chunks: list[bytes] = []
    async with store.open_source(IMPORT_KEY) as source:
        while chunk := await source.read(256 * 1024):
            chunks.append(chunk)

    assert b"".join(chunks) == data
    assert max(len(chunk) for chunk in chunks) <= 256 * 1024
    assert len(chunks) >= 13


async def test_open_source_of_missing_key_raises_not_found(
    store: S3ArtifactStore,
) -> None:
    with pytest.raises(NotFoundError):
        async with store.open_source(IMPORT_KEY):
            pass


async def test_operations_on_missing_bucket_raise_storage_error(
    minio_server: MinioServer,
) -> None:
    store = build_store(minio_server, "exchange-missing-bucket")

    with pytest.raises(ArtifactStorageError) as put:
        await store.put_bytes(SIDECAR_KEY, b"{}", "application/json")
    with pytest.raises(ArtifactStorageError) as opened:
        async with store.open_source(IMPORT_KEY):
            pass
    with pytest.raises(ArtifactStorageError) as small:
        async with store.open_sink(EXPORT_KEY, "text/csv") as sink:
            await sink.write(b"x")
    with pytest.raises(ArtifactStorageError) as large:
        await _write_in_chunks(store, EXPORT_KEY, _payload(MULTIPART_PART_BYTES))

    assert put.value.details["operation"] == "put"
    assert opened.value.details["operation"] == "open"
    assert small.value.details["operation"] == "put"
    assert large.value.details["operation"] == "upload_part"
    assert "exchange-missing-bucket" not in str(put.value)


async def test_csv_export_streamed_to_storage_imports_back(
    store: S3ArtifactStore,
) -> None:
    rows = [event_row(title=f"Synthetic event {index}, گلگت") for index in range(50)]
    async with store.open_sink(EXPORT_KEY, "text/csv") as raw:
        metered = MeteredSink(raw, max_bytes=10 * MIB)
        await CsvExporter().write(ExportDataset.EVENTS, rows_of(rows), metered)

    importer = CsvImporter(chunk_bytes=1000)
    async with store.open_source(EXPORT_KEY) as source:
        header = await importer.header(source)
        records = [cells async for _, cells in importer.read(source)]

    assert header[0] == "event_id"
    assert [record["title"] for record in records] == [row.title for row in rows]
    assert metered.byte_size > 0


async def test_presign_upload_grant_accepts_put_with_declared_content_type(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    content = b"title,hazard_type\r\nSynthetic,glof\r\n"
    grant = await store.presign_upload(IMPORT_KEY, "text/csv", len(content))

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            grant.url,
            content=content,
            headers={header.name: header.value for header in grant.headers},
        )

    assert response.status_code == httpx.codes.OK
    assert (await _object(minio_server, bucket, IMPORT_KEY))[:2] == (
        content,
        "text/csv",
    )
    assert grant.expires_at == STORE_NOW + timedelta(seconds=PRESIGN_TTL_SECONDS)
    assert f"X-Amz-Expires={PRESIGN_TTL_SECONDS}" in grant.url


async def test_presign_upload_grant_refuses_other_content_type(
    store: S3ArtifactStore, minio_server: MinioServer, bucket: str
) -> None:
    grant = await store.presign_upload(IMPORT_KEY, "text/csv", 1024)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            grant.url,
            content=b"<html></html>",
            headers={"Content-Type": "text/html"},
        )

    assert response.status_code == httpx.codes.FORBIDDEN
    assert await _keys(minio_server, bucket) == []


async def test_presign_upload_of_export_key_is_refused(
    store: S3ArtifactStore,
) -> None:
    with pytest.raises(ArtifactStorageError) as caught:
        await store.presign_upload(EXPORT_KEY, "text/csv", 1024)

    assert caught.value.details == {"operation": "key"}
