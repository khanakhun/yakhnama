"""Integration tests of the media adapters against a real MinIO.

Uploads go through the presigned URLs exactly as a client would send them, with
``httpx`` on the loopback address of the test container, and always to the upload
key; ``seal_upload`` then copies them to the original key.
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
from yakhnama.modules.media.application.dto import StoredObject
from yakhnama.modules.media.domain.errors import MediaContentChangedError
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

UPLOAD = "media/upload/0197a000-0000-7000-8000-000000000001"
ORIGINAL = "media/original/0197a000-0000-7000-8000-000000000001"
PUBLIC = "media/public/0197a000-0000-7000-8000-000000000001"


async def put_through_presigned_url(
    storage: S3StoragePort, body: bytes, mime_type: MimeType, key: str = UPLOAD
) -> httpx.Response:
    """PUT ``body`` to ``key`` through a presigned URL, as a client does."""
    grant = await storage.presign_put(key, mime_type, len(body))
    headers = {header.name: header.value for header in grant.headers}
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.put(grant.url, content=body, headers=headers)


async def upload_and_seal(
    storage: S3StoragePort, body: bytes, mime_type: MimeType
) -> StoredObject:
    """Upload ``body`` and seal it into the original, as the handlers do."""
    response = await put_through_presigned_url(storage, body, mime_type)
    assert response.status_code == httpx.codes.OK
    sealed = await storage.seal_upload(UPLOAD, ORIGINAL)
    assert sealed is not None
    return sealed


async def _download(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.get(url)


async def _public_object_count(server: MinioServer, buckets: Buckets) -> int:
    async with admin_client(server) as client:
        listing = await client.list_objects_v2(Bucket=buckets.public)
    return listing.get("KeyCount", 0)


async def test_s3_presigned_put_then_seal_reports_the_sha256_of_the_upload(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()

    response = await put_through_presigned_url(storage, body, MimeType.JPEG)
    sealed = await storage.seal_upload(UPLOAD, ORIGINAL)

    assert response.status_code == httpx.codes.OK
    assert sealed is not None
    assert sealed.sha256 == hashlib.sha256(body).hexdigest()
    assert sealed.byte_size == len(body)
    assert sealed.content_type == MimeType.JPEG.value
    assert await storage.head(ORIGINAL) == sealed
    assert await storage.head(UPLOAD) is None


async def test_s3_seal_repeated_after_the_upload_is_gone_describes_the_original(
    storage: S3StoragePort,
) -> None:
    first = await upload_and_seal(storage, gps_photo(), MimeType.JPEG)

    again = await storage.seal_upload(UPLOAD, ORIGINAL)

    assert again == first


async def test_s3_seal_without_any_upload_returns_none(
    storage: S3StoragePort,
) -> None:
    sealed = await storage.seal_upload(UPLOAD, ORIGINAL)

    assert sealed is None


async def test_s3_overwriting_the_upload_after_sealing_changes_nothing_read(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()
    sealed = await upload_and_seal(storage, body, MimeType.JPEG)

    replaced = await put_through_presigned_url(
        storage, gps_photo("JPEG") + b"replacement", MimeType.JPEG
    )

    assert replaced.status_code == httpx.codes.OK
    assert await storage.read_original(ORIGINAL) == body
    assert await storage.head(ORIGINAL) == sealed


async def test_s3_presign_put_expiry_is_now_plus_ttl_and_url_is_the_upload_key(
    storage: S3StoragePort, buckets: Buckets
) -> None:
    grant = await storage.presign_put(UPLOAD, MimeType.PNG, 1024)

    assert grant.expires_at == STORAGE_NOW + timedelta(seconds=PRESIGN_TTL_SECONDS)
    assert f"/{buckets.private}/{UPLOAD}" in grant.url
    assert [(header.name, header.value) for header in grant.headers] == [
        ("Content-Type", "image/png")
    ]


async def test_s3_presigned_put_with_another_content_type_is_refused(
    storage: S3StoragePort,
) -> None:
    grant = await storage.presign_put(UPLOAD, MimeType.JPEG, 1024)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            grant.url, content=b"<html>", headers={"Content-Type": "text/html"}
        )

    assert response.status_code == httpx.codes.FORBIDDEN
    assert await storage.head(UPLOAD) is None


async def test_s3_head_missing_object_returns_none(storage: S3StoragePort) -> None:
    stored = await storage.head(ORIGINAL)

    assert stored is None


async def test_s3_oversize_upload_is_described_without_hashing_or_sealing(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    storage = build_storage(minio_server, buckets, max_object_bytes=1024)
    await put_through_presigned_url(storage, b"\x00" * 2048, MimeType.PDF)

    stored = await storage.seal_upload(UPLOAD, ORIGINAL)

    assert stored is not None
    assert stored.byte_size == 2048
    assert stored.sha256 == OVERSIZE_SHA256
    assert await storage.head(ORIGINAL) is None


async def test_s3_oversize_original_is_never_loaded(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    storage = build_storage(minio_server, buckets, max_object_bytes=1024)
    async with admin_client(minio_server) as client:
        await client.put_object(
            Bucket=buckets.private, Key=ORIGINAL, Body=b"\x00" * 2048
        )

    with pytest.raises(StorageError, match="larger than the size cap"):
        await storage.read_original(ORIGINAL)
    with pytest.raises(StorageError, match="larger than the size cap"):
        await storage.copy_stripped_public(
            ORIGINAL, PUBLIC, expected_sha256=OVERSIZE_SHA256
        )


async def test_s3_copy_stripped_public_removes_exif_and_keeps_the_original(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()
    sealed = await upload_and_seal(storage, body, MimeType.JPEG)

    await storage.copy_stripped_public(ORIGINAL, PUBLIC, expected_sha256=sealed.sha256)

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
    sealed = await upload_and_seal(storage, gps_photo("PNG"), MimeType.PNG)

    await storage.copy_stripped_public(ORIGINAL, PUBLIC, expected_sha256=sealed.sha256)
    first = await _download((await storage.presign_get(PUBLIC)).url)
    await storage.copy_stripped_public(ORIGINAL, PUBLIC, expected_sha256=sealed.sha256)
    second = await _download((await storage.presign_get(PUBLIC)).url)

    assert first.content == second.content


async def test_s3_copy_of_a_pdf_is_refused_and_nothing_is_public(
    storage: S3StoragePort, minio_server: MinioServer, buckets: Buckets
) -> None:
    sealed = await upload_and_seal(storage, MINIMAL_PDF, MimeType.PDF)

    with pytest.raises(StorageError) as caught:
        await storage.copy_stripped_public(
            ORIGINAL, PUBLIC, expected_sha256=sealed.sha256
        )

    assert caught.value.details["reason"] == "unsupported"
    assert await _public_object_count(minio_server, buckets) == 0


async def test_s3_copy_of_a_changed_original_is_refused_and_nothing_is_public(
    storage: S3StoragePort, minio_server: MinioServer, buckets: Buckets
) -> None:
    sealed = await upload_and_seal(storage, gps_photo(), MimeType.JPEG)
    async with admin_client(minio_server) as client:
        await client.put_object(
            Bucket=buckets.private, Key=ORIGINAL, Body=gps_photo("PNG")
        )

    with pytest.raises(MediaContentChangedError):
        await storage.copy_stripped_public(
            ORIGINAL, PUBLIC, expected_sha256=sealed.sha256
        )

    assert await _public_object_count(minio_server, buckets) == 0


async def test_s3_copy_missing_or_disallowed_original_raises_storage_error(
    storage: S3StoragePort, minio_server: MinioServer, buckets: Buckets
) -> None:
    digest = hashlib.sha256(b"text").hexdigest()
    with pytest.raises(StorageError, match="missing"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC, expected_sha256=digest)
    async with admin_client(minio_server) as client:
        await client.put_object(Bucket=buckets.private, Key=ORIGINAL, Body=b"text")

    with pytest.raises(StorageError, match="not an allowed media type"):
        await storage.copy_stripped_public(ORIGINAL, PUBLIC, expected_sha256=digest)


async def test_s3_presign_get_downloads_the_private_original(
    storage: S3StoragePort, buckets: Buckets
) -> None:
    await upload_and_seal(storage, MINIMAL_PDF, MimeType.PDF)

    link = await storage.presign_get(ORIGINAL)
    response = await _download(link.url)

    assert f"/{buckets.private}/{ORIGINAL}" in link.url
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


async def test_s3_iter_original_checks_the_expected_digest(
    storage: S3StoragePort,
) -> None:
    body = gps_photo()
    sealed = await upload_and_seal(storage, body, MimeType.JPEG)

    streamed = b"".join(
        [chunk async for chunk in storage.iter_original(ORIGINAL, sealed.sha256)]
    )
    with pytest.raises(MediaContentChangedError):
        async for _ in storage.iter_original(ORIGINAL, "0" * 64):
            pass

    assert streamed == body


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
        await storage.read_original_prefix(ORIGINAL, 8)
    with pytest.raises(StorageError, match="during read"):
        await storage.read_original(ORIGINAL)
    with pytest.raises(StorageError, match="during read_prefix"):
        await unreachable.read_original_prefix(ORIGINAL, 8)
    with pytest.raises(StorageError, match="during head"):
        await unreachable.head(ORIGINAL)
    with pytest.raises(StorageError, match="during seal"):
        await unreachable.seal_upload(UPLOAD, ORIGINAL)


async def test_s3_copy_into_missing_public_bucket_raises_storage_error(
    minio_server: MinioServer, buckets: Buckets
) -> None:
    storage = build_storage(
        minio_server, Buckets(private=buckets.private, public="absent-public")
    )
    sealed = await upload_and_seal(storage, gps_photo(), MimeType.JPEG)

    with pytest.raises(StorageError, match="during copy"):
        await storage.copy_stripped_public(
            ORIGINAL, PUBLIC, expected_sha256=sealed.sha256
        )


async def test_exif_reader_and_mime_sniffer_read_from_storage(
    storage: S3StoragePort,
) -> None:
    await upload_and_seal(storage, gps_photo("WEBP"), MimeType.WEBP)
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
    sealed = await upload_and_seal(storage, body, MimeType.JPEG)
    received: list[bytes] = []

    async def fake_clamd(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # A loopback stand-in for clamd: reads the INSTREAM framing, answers OK.
        stream = bytearray(await reader.readexactly(len(INSTREAM_COMMAND)))
        while True:
            (length,) = struct.unpack(">I", await reader.readexactly(4))
            if length == 0:
                break
            stream.extend(await reader.readexactly(length))
        received.append(bytes(stream))
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
        verdict = await scanner.scan(ORIGINAL, expected_sha256=sealed.sha256)
        with pytest.raises(MediaContentChangedError):
            await scanner.scan(ORIGINAL, expected_sha256="0" * 64)

    assert verdict is ScanStatus.CLEAN
    assert received[0] == INSTREAM_COMMAND + body
