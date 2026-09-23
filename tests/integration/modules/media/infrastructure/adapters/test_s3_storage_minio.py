"""Integration tests of the media adapters against a real MinIO.

Uploads go through the presigned URLs exactly as a client would send them, with
``httpx`` on the loopback address of the test container.
"""

import asyncio
import hashlib
import struct
from datetime import timedelta
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from tests.fakes.clock import FrozenClock
from tests.integration.conftest import MinioServer
from tests.integration.modules.media.infrastructure.adapters.conftest import (
    PRESIGN_TTL_SECONDS,
    STORAGE_NOW,
    Buckets,
    admin_client,
    build_storage,
)
from tests.unit.modules.media.infrastructure.adapters.images import (
    MINIMAL_PDF,
    gps_photo,
)
from yakhnama.modules.media.domain.value_objects import MimeType, ScanStatus
from yakhnama.modules.media.infrastructure.adapters.exif import (
    PillowExifReader,
    parse_exif,
)
from yakhnama.modules.media.infrastructure.adapters.mime import FiletypeMimeSniffer
from yakhnama.modules.media.infrastructure.adapters.s3_storage import (
    OVERSIZE_SHA256,
    S3StoragePort,
    StorageError,
)
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    INSTREAM_COMMAND,
    ClamAvScanner,
    tcp_connector,
)

pytestmark = pytest.mark.integration

ORIGINAL = "media/original/0197a000-0000-7000-8000-000000000001"
PUBLIC = "media/public/0197a000-0000-7000-8000-000000000001"


async def _upload(
    storage: S3StoragePort, key: str, body: bytes, mime_type: MimeType
) -> httpx.Response:
    grant = await storage.presign_put(key, mime_type, len(body))
    headers = {header.name: header.value for header in grant.headers}
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.put(grant.url, content=body, headers=headers)


async def _download(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.get(url)


async def test_s3_presigned_put_then_head_reports_the_sha256_of_the_upload(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()

    response = await _upload(storage, ORIGINAL, body, MimeType.JPEG)
    stored = await storage.head(ORIGINAL)

    assert response.status_code == httpx.codes.OK
    assert stored is not None
    assert stored.sha256 == hashlib.sha256(body).hexdigest()
    assert stored.byte_size == len(body)
    assert stored.content_type == MimeType.JPEG.value


async def test_s3_presign_put_expiry_is_now_plus_ttl_and_url_is_private(
    storage: S3StoragePort, buckets: Buckets
) -> None:
    grant = await storage.presign_put(ORIGINAL, MimeType.PNG, 1024)

    assert grant.expires_at == STORAGE_NOW + timedelta(seconds=PRESIGN_TTL_SECONDS)
    assert f"/{buckets.private}/{ORIGINAL}" in grant.url
    assert [(header.name, header.value) for header in grant.headers] == [
        ("Content-Type", "image/png")
    ]


async def test_s3_presigned_put_with_another_content_type_is_refused(
    storage: S3StoragePort,
) -> None:
    grant = await storage.presign_put(ORIGINAL, MimeType.JPEG, 1024)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            grant.url, content=b"<html>", headers={"Content-Type": "text/html"}
        )

    assert response.status_code == httpx.codes.FORBIDDEN
    assert await storage.head(ORIGINAL) is None


async def test_s3_head_missing_object_returns_none(storage: S3StoragePort) -> None:
    stored = await storage.head(ORIGINAL)

    assert stored is None


async def test_s3_oversize_object_is_reported_without_hashing_and_not_loaded(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    storage = build_storage(minio_server, buckets, max_object_bytes=1024)
    await _upload(storage, ORIGINAL, b"\x00" * 2048, MimeType.PDF)

    stored = await storage.head(ORIGINAL)

    assert stored is not None
    assert stored.byte_size == 2048
    assert stored.sha256 == OVERSIZE_SHA256
    with pytest.raises(StorageError, match="larger than the size cap"):
        await storage.read_original(ORIGINAL)
    with pytest.raises(StorageError, match="larger than the size cap"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC)


async def test_s3_copy_stripped_public_removes_exif_and_keeps_the_original(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()
    await _upload(storage, ORIGINAL, body, MimeType.JPEG)

    await storage.copy_stripped_public(ORIGINAL, PUBLIC)

    original = await storage.read_original(ORIGINAL)
    public = await _download((await storage.presign_get(PUBLIC)).url)
    assert original == body
    original_facts = parse_exif(body)
    assert original_facts is not None
    assert original_facts.location is not None
    assert public.status_code == httpx.codes.OK
    assert public.headers["content-type"] == MimeType.JPEG.value
    assert parse_exif(public.content) is None
    copy = Image.open(BytesIO(public.content))
    assert dict(copy.getexif()) == {}
    assert b"Canon" not in public.content


async def test_s3_copy_stripped_public_is_idempotent(storage: S3StoragePort) -> None:
    await _upload(storage, ORIGINAL, gps_photo("PNG"), MimeType.PNG)

    await storage.copy_stripped_public(ORIGINAL, PUBLIC)
    first = await _download((await storage.presign_get(PUBLIC)).url)
    await storage.copy_stripped_public(ORIGINAL, PUBLIC)
    second = await _download((await storage.presign_get(PUBLIC)).url)

    assert first.content == second.content


async def test_s3_copy_non_image_is_byte_identical(storage: S3StoragePort) -> None:
    await _upload(storage, ORIGINAL, MINIMAL_PDF, MimeType.PDF)

    await storage.copy_stripped_public(ORIGINAL, PUBLIC)

    public = await _download((await storage.presign_get(PUBLIC)).url)
    assert public.content == MINIMAL_PDF
    assert public.headers["content-type"] == MimeType.PDF.value


async def test_s3_copy_missing_or_disallowed_original_raises_storage_error(
    storage: S3StoragePort, minio_server: MinioServer, buckets: Buckets
) -> None:
    with pytest.raises(StorageError, match="missing"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC)
    async with admin_client(minio_server) as client:
        await client.put_object(Bucket=buckets.private, Key=ORIGINAL, Body=b"text")

    with pytest.raises(StorageError, match="not an allowed media type"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC)


async def test_s3_presign_get_downloads_the_private_original(
    storage: S3StoragePort, buckets: Buckets
) -> None:
    await _upload(storage, ORIGINAL, MINIMAL_PDF, MimeType.PDF)

    link = await storage.presign_get(ORIGINAL)
    response = await _download(link.url)

    assert f"/{buckets.private}/" in link.url
    assert link.expires_at == STORAGE_NOW + timedelta(seconds=PRESIGN_TTL_SECONDS)
    assert response.content == MINIMAL_PDF


async def test_s3_reads_of_empty_and_missing_objects(
    storage: S3StoragePort, minio_server: MinioServer, buckets: Buckets
) -> None:
    async with admin_client(minio_server) as client:
        await client.put_object(Bucket=buckets.private, Key=ORIGINAL, Body=b"")

    empty_prefix = await storage.read_original_prefix(ORIGINAL, 8)
    empty_whole = await storage.read_original(ORIGINAL)
    empty_head = await storage.head(ORIGINAL)
    missing_prefix = await storage.read_original_prefix(PUBLIC, 8)
    missing_whole = await storage.read_original(PUBLIC)

    assert empty_prefix == b""
    assert empty_whole == b""
    assert empty_head is not None
    assert empty_head.byte_size == 0
    assert empty_head.sha256 == hashlib.sha256(b"").hexdigest()
    assert missing_prefix is None
    assert missing_whole is None


async def test_s3_iter_original_of_missing_object_raises_storage_error(
    storage: S3StoragePort,
) -> None:
    with pytest.raises(StorageError, match="during read"):
        async for _ in storage.iter_original(ORIGINAL):
            pass


async def test_s3_wrong_credentials_raise_storage_error_without_the_secret(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    wrong = MinioServer(
        endpoint_url=minio_server.endpoint_url,
        access_key_id=minio_server.access_key_id,
        secret_access_key=SecretStr("not-the-password"),
    )
    storage = build_storage(wrong, buckets)

    with pytest.raises(StorageError) as caught:
        await storage.head(ORIGINAL)

    assert "not-the-password" not in str(caught.value)
    assert ORIGINAL not in str(caught.value)
    assert caught.value.details == {"operation": "head", "reason": "ClientError"}


async def test_s3_failures_of_every_operation_map_to_storage_error(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    missing_buckets = Buckets(private="absent-private", public="absent-public")
    storage = build_storage(minio_server, missing_buckets)
    await _upload(
        build_storage(minio_server, buckets), ORIGINAL, MINIMAL_PDF, MimeType.PDF
    )

    with pytest.raises(StorageError, match="during read_prefix"):
        await storage.read_original_prefix(ORIGINAL, 8)
    with pytest.raises(StorageError, match="during read"):
        await storage.read_original(ORIGINAL)
    unreachable = S3StoragePort(
        endpoint_url="http://127.0.0.1:9",
        region="us-east-1",
        access_key_id="x",
        secret_access_key=SecretStr("y"),
        private_bucket=buckets.private,
        public_bucket=buckets.public,
        presign_ttl_seconds=60,
        clock=FrozenClock(STORAGE_NOW),
    )
    with pytest.raises(StorageError, match="during read_prefix"):
        await unreachable.read_original_prefix(ORIGINAL, 8)


async def test_s3_copy_into_missing_public_bucket_raises_storage_error(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    storage = build_storage(
        minio_server, Buckets(private=buckets.private, public="absent-public")
    )
    await _upload(storage, ORIGINAL, MINIMAL_PDF, MimeType.PDF)

    with pytest.raises(StorageError, match="during copy"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC)


async def test_exif_reader_and_mime_sniffer_read_from_storage(
    storage: S3StoragePort,
) -> None:
    await _upload(storage, ORIGINAL, gps_photo("WEBP"), MimeType.WEBP)
    reader = PillowExifReader(storage.read_original)
    sniffer = FiletypeMimeSniffer(storage.read_original_prefix)

    facts = await reader.read(ORIGINAL)
    mime_type = await sniffer.sniff(ORIGINAL)

    assert facts is not None
    assert facts.camera == "Canon EOS 80D"
    assert mime_type is MimeType.WEBP


async def test_clamav_scanner_streams_the_original_to_a_clamd_over_tcp(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()
    await _upload(storage, ORIGINAL, body, MimeType.JPEG)
    received = bytearray()

    async def fake_clamd(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # A loopback stand-in for clamd: reads the INSTREAM framing, answers OK.
        received.extend(await reader.readexactly(len(INSTREAM_COMMAND)))
        while True:
            (length,) = struct.unpack(">I", await reader.readexactly(4))
            if length == 0:
                break
            received.extend(await reader.readexactly(length))
        writer.write(b"stream: OK\x00")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fake_clamd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    scanner = ClamAvScanner(
        connect=tcp_connector("127.0.0.1", port),
        read_chunks=storage.iter_original,
        timeout_seconds=10.0,
    )

    async with server:
        verdict = await scanner.scan(ORIGINAL)

    assert verdict is ScanStatus.CLEAN
    assert bytes(received) == INSTREAM_COMMAND + body
