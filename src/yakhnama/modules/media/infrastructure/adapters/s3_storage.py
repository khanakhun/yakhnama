"""S3-compatible object storage for media originals and their public copies.

The only code that knows the S3 API (ADR 0009). Originals live in the private
bucket and are never made public; ``copy_stripped_public`` writes a metadata-free
re-encoding to the public bucket. Every operation opens a short-lived
``aiobotocore`` client, so the adapter holds no connection between calls and needs
no lifecycle hooks; the cost is one TLS handshake per operation, acceptable at
media volumes and revisited if profiling says otherwise.

What the adapter can and cannot enforce:

- **Size.** S3 cannot cap the size of a presigned ``PUT`` (only a presigned
  ``POST`` policy has ``content-length-range``, and the port returns a ``PUT``).
  The cap is therefore a documented client contract, enforced after the fact:
  ``head`` reports the real size and ``CompleteUploadHandler`` rejects anything
  above ``MAX_MEDIA_BYTES`` without the adapter ever downloading it.
- **Type.** The presigned ``PUT`` signs ``Content-Type``, so the upload must carry
  exactly the declared type; the real type is still sniffed from the bytes.
- **Digest.** An S3 ``ETag`` is not a SHA-256 (it is an MD5, or a hash of part
  hashes for multipart uploads), so ``head`` streams the object through
  ``hashlib.sha256``: one full read per completion, at most ``MAX_MEDIA_BYTES``.
- **Upload versus original.** Clients only ever get a URL for the upload key
  (``media/upload/<id>``). A presigned ``PUT`` stays valid until it expires and S3
  overwrites, so ``seal_upload`` copies the upload server-side to the original key
  (``media/original/<id>``), which no client URL covers, and hashes the original;
  every later read uses the original. The copy is pinned to the ``ETag`` the
  ``HEAD`` saw, and the upload key is deleted afterwards (a still-valid URL can
  recreate it; nothing ever reads it again, and a bucket lifecycle rule should
  expire the prefix, Q-M20).
- **Integrity.** ``copy_stripped_public`` and ``iter_original`` take the digest
  recorded at completion and raise ``MediaContentChangedError`` if the bytes they
  read hash differently.
- **Metadata.** JPEG, PNG and WebP are decoded and re-encoded from pixels alone,
  so EXIF, XMP, IPTC, ICC profiles and text chunks are all dropped; the EXIF
  orientation is applied to the pixels first so the copy displays upright. JPEG
  and lossy WebP re-encoding loses a little quality (quality 95). No other type
  is ever copied to the public bucket: ``strip_metadata`` refuses PDF and MP4
  (``unsupported``), whose document information or ``udta`` box may carry an
  author or a GPS position (Q-M13), and the domain never makes them publishable.
- **Decompression bombs.** Before any pixel is decoded, the declared canvas,
  frame count and total pixels are checked against ``image_limits``.

No credential, presigned URL or object key is ever logged or put in an error
message.

Patterns: Adapter + Anti-Corruption Layer.
"""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, aclosing
from datetime import timedelta
from io import BytesIO
from typing import TYPE_CHECKING, ClassVar, Final, Self

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, ImageSequence
from pydantic import SecretStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.media.application.dto import (
    HttpHeader,
    PresignedDownload,
    PresignedUpload,
    StoredObject,
)
from yakhnama.modules.media.domain.errors import MediaContentChangedError
from yakhnama.modules.media.domain.value_objects import (
    MAX_MEDIA_BYTES,
    UPLOAD_KEY_PREFIX,
    MimeType,
    ObjectKey,
    Variant,
)
from yakhnama.modules.media.infrastructure.adapters.image_limits import (
    MALFORMED_FILE_ERRORS,
    exceeded_limit,
)
from yakhnama.modules.media.infrastructure.adapters.mime import (
    SNIFF_BYTES,
    detect_mime_type,
)
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import YakhnamaError

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client
    from types_aiobotocore_s3.type_defs import HeadObjectOutputTypeDef

STREAM_CHUNK_BYTES: Final = 1024 * 1024
"""Bytes read per step when hashing or loading an object."""

OVERSIZE_SHA256: Final = "0" * 64
"""Placeholder digest ``head`` reports for an object above the size cap.

Hashing a file that will be rejected for its size would cost a full download of
an arbitrarily large object, so it is not hashed. ``StoredObject.sha256`` cannot
say "not computed"; the caller rejects on ``byte_size`` before it looks at the
digest (open question Q-M14)."""

# public_object_key() builds "media/public/<id>"; presign_get uses the prefix to
# pick the bucket, so the layout is read from the same Variant enum.
PUBLIC_KEY_PREFIX: Final = f"media/{Variant.PUBLIC.value}/"
CONTENT_TYPE_MAX_LENGTH: Final = 255
MISSING_OBJECT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})
EMPTY_RANGE_CODE: Final = "InvalidRange"

# Timeouts and retries live here, behind the adapter: a stalled storage call must
# fail within seconds, and standard-mode retries absorb transient 5xx and throttling.
CONNECT_TIMEOUT_SECONDS: Final = 5.0
READ_TIMEOUT_SECONDS: Final = 30.0
MAX_ATTEMPTS: Final = 3
JPEG_WEBP_QUALITY: Final = 95

_REENCODED_FORMATS: Final = {
    MimeType.JPEG: "JPEG",
    MimeType.PNG: "PNG",
    MimeType.WEBP: "WEBP",
}
_OBJECT_KEY: Final = TypeAdapter(ObjectKey)


class StorageError(YakhnamaError):
    """Object storage failed or returned something unusable.

    Its message names the operation, never the key, bucket or credentials. The
    application treats it like any unexpected failure (a retry for tasks, a 500
    for requests).

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"storage_error"``.
    """

    code: ClassVar[str] = "storage_error"


def strip_metadata(data: bytes, mime_type: MimeType) -> bytes:
    """Return ``data`` without embedded metadata, as far as its format allows.

    Args:
        data: The whole original.
        mime_type: Its sniffed media type.

    Returns:
        A re-encoding from pixels alone for JPEG, PNG and WebP.

    Raises:
        StorageError: If the type has no stripper (``unsupported``: PDF, MP4),
            the image exceeds the decoding limits (``too_large``), or it cannot
            be decoded or re-encoded (``malformed``).
    """
    image_format = _REENCODED_FORMATS.get(mime_type)
    if image_format is None:
        # Copying unstripped bytes would publish whatever metadata they carry.
        message = "this media type cannot be stripped of metadata"
        raise StorageError(
            message, details={"operation": "strip", "reason": "unsupported"}
        )
    try:
        with Image.open(BytesIO(data), formats=[image_format]) as image:
            limit = exceeded_limit(image)
            if limit is not None:
                message = "the image exceeds the decoding limits"
                raise StorageError(
                    message,
                    details={
                        "operation": "strip",
                        "reason": "too_large",
                        "limit": limit,
                    },
                )
            return _reencode(image, image_format)
    except MALFORMED_FILE_ERRORS as error:
        message = "the original image could not be re-encoded"
        raise StorageError(
            message, details={"operation": "strip", "reason": "malformed"}
        ) from error


def _reencode(image: Image.Image, image_format: str) -> bytes:
    frames = [_pixels_only(frame) for frame in ImageSequence.Iterator(image)]
    # PNG is lossless and ignores quality; JPEG and WebP need it set explicitly.
    quality = JPEG_WEBP_QUALITY if image_format != "PNG" else None
    output = BytesIO()
    if len(frames) > 1:
        # Animated PNG and WebP keep their frames and timing, nothing else.
        frames[0].save(
            output,
            format=image_format,
            save_all=True,
            append_images=frames[1:],
            duration=image.info.get("duration", 0),
            loop=image.info.get("loop", 0),
        )
    elif quality is None:
        frames[0].save(output, format=image_format)
    else:
        frames[0].save(output, format=image_format, quality=quality)
    return output.getvalue()


def _pixels_only(frame: Image.Image) -> Image.Image:
    upright = ImageOps.exif_transpose(frame)
    if upright.mode in {"P", "PA"}:
        # A palette image keeps transparency in ``info``, which is discarded; RGBA
        # carries it in the pixels instead.
        upright = upright.convert("RGBA")
    # A new image from raw pixels has an empty ``info`` and no EXIF, so no plugin
    # can copy metadata from the original, whatever its defaults.
    return Image.frombytes(upright.mode, upright.size, upright.tobytes())


def _is_same_digest(actual: str, expected: str) -> bool:
    # Digests are stored lower-case (Sha256); comparing case-insensitively keeps
    # a hand-built upper-case value from reading as a change.
    return actual == expected.lower()


def _is_missing(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in MISSING_OBJECT_CODES


def _storage_error(error: Exception, *, operation: str) -> StorageError:
    return StorageError(
        f"object storage failed during {operation}",
        details={"operation": operation, "reason": type(error).__name__},
    )


class S3StoragePort:
    """``StoragePort`` over any S3-compatible service through ``aiobotocore``.

    Besides the port it offers ``read_original``, ``read_original_prefix`` and
    ``iter_original``, which the EXIF reader, MIME sniffer and malware scanner are
    built with in the composition root.

    Implements: Adapter.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per connection setting, all required
        self,
        *,
        endpoint_url: str | None,
        region: str,
        access_key_id: str,
        secret_access_key: SecretStr,
        private_bucket: str,
        public_bucket: str,
        presign_ttl_seconds: int,
        clock: Clock,
        max_object_bytes: int = MAX_MEDIA_BYTES,
    ) -> None:
        """Create the adapter; nothing connects until an operation runs.

        Args:
            endpoint_url: The S3 endpoint; ``None`` for AWS's regional default.
            region: The signing region.
            access_key_id: The access key id.
            secret_access_key: The secret key, unwrapped only to build a client.
            private_bucket: Bucket of the originals.
            public_bucket: Bucket of the public copies.
            presign_ttl_seconds: Lifetime of every presigned URL.
            clock: Source of the expiry instants reported to callers.
            max_object_bytes: Largest object hashed or loaded into memory.
        """
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._private_bucket = private_bucket
        self._public_bucket = public_bucket
        self._ttl = timedelta(seconds=presign_ttl_seconds)
        self._clock = clock
        self._max_object_bytes = max_object_bytes
        self._session = get_session()
        self._config = AioConfig(
            signature_version="s3v4",
            # Path-style addressing works with MinIO and every S3 clone; AWS still
            # accepts it (open question Q-M15 for a production switch).
            s3={"addressing_style": "path"},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"},
        )

    @classmethod
    def from_settings(cls, settings: Settings, clock: Clock) -> Self:
        """Build the adapter from the ``storage_*`` settings.

        Args:
            settings: The process settings.
            clock: Source of expiry instants.

        Returns:
            The adapter.
        """
        return cls(
            endpoint_url=settings.storage_endpoint_url,
            region=settings.storage_region,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
            private_bucket=settings.storage_private_bucket,
            public_bucket=settings.storage_public_bucket,
            presign_ttl_seconds=settings.storage_presign_ttl_seconds,
            clock=clock,
        )

    def _client(self) -> AbstractAsyncContextManager["S3Client"]:
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key.get_secret_value(),
            config=self._config,
        )

    @staticmethod
    def _checked_key(key: str) -> str:
        # Keys come from the domain already validated; checking again here keeps a
        # future caller from reaching another prefix through this adapter.
        try:
            return _OBJECT_KEY.validate_python(key)
        except PydanticValidationError as error:
            message = "the object key is not a valid media key"
            raise StorageError(message, details={"operation": "key"}) from error

    def _bucket_of(self, key: str) -> str:
        if key.startswith(PUBLIC_KEY_PREFIX):
            return self._public_bucket
        return self._private_bucket

    async def presign_put(
        self, key: str, mime_type: MimeType, max_bytes: int
    ) -> PresignedUpload:
        """Return a presigned ``PUT`` of ``key`` into the private bucket.

        The signature covers ``Content-Type``, so the upload must send exactly
        ``mime_type``. ``max_bytes`` cannot be signed into a ``PUT``; it is the
        client contract, enforced when the upload is completed (module docstring).

        Args:
            key: The upload key (``media/upload/<id>``); nothing else is signed.
            mime_type: The declared media type.
            max_bytes: Largest accepted file, checked on completion.

        Returns:
            The URL, the ``Content-Type`` header it requires and its expiry.

        Raises:
            StorageError: If the key is invalid or not an upload key, or signing
                fails.
        """
        del max_bytes
        checked = self._checked_key(key)
        if not checked.startswith(UPLOAD_KEY_PREFIX):
            # A write URL for an original or a public copy would let a client
            # replace a file after the platform recorded its digest.
            message = "uploads are presigned only for upload keys"
            raise StorageError(message, details={"operation": "presign_put"})
        # Taken before signing, so the reported expiry is never later than the
        # one botocore signs from its own, slightly later, clock reading.
        expires_at = self._clock.now() + self._ttl
        try:
            async with self._client() as client:
                url = await client.generate_presigned_url(
                    "put_object",
                    Params={
                        "Bucket": self._private_bucket,
                        "Key": checked,
                        "ContentType": mime_type.value,
                    },
                    ExpiresIn=int(self._ttl.total_seconds()),
                )
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="presign_put") from error
        return PresignedUpload(
            url=url,
            headers=(HttpHeader(name="Content-Type", value=mime_type.value),),
            expires_at=expires_at,
        )

    async def presign_get(self, key: str) -> PresignedDownload:
        """Return a presigned ``GET`` of ``key`` from the bucket that holds it.

        Args:
            key: An original (private bucket) or public (public bucket) key.

        Returns:
            The URL and its expiry.

        Raises:
            StorageError: If the key is invalid or signing fails.
        """
        checked = self._checked_key(key)
        expires_at = self._clock.now() + self._ttl
        try:
            async with self._client() as client:
                url = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket_of(checked), "Key": checked},
                    ExpiresIn=int(self._ttl.total_seconds()),
                )
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="presign_get") from error
        return PresignedDownload(url=url, expires_at=expires_at)

    async def head(self, key: str) -> StoredObject | None:
        """Return the size, stored type and SHA-256 of ``key`` in the private bucket.

        Args:
            key: An object key in the private bucket.

        Returns:
            The object's facts, ``None`` if it is absent. Above the size cap the
            digest is ``OVERSIZE_SHA256`` and nothing is downloaded.

        Raises:
            StorageError: If storage fails or the object changes while hashed.
        """
        checked = self._checked_key(key)
        try:
            async with self._client() as client:
                return await self._describe(client, checked)
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="head") from error

    async def seal_upload(
        self, upload_key: str, original_key: str
    ) -> StoredObject | None:
        """Copy the upload to the original key and describe the original.

        Args:
            upload_key: Where the client uploaded (``media/upload/<id>``).
            original_key: The private original to write.

        Returns:
            The original's facts; for an upload above the size cap, the upload's
            facts with ``OVERSIZE_SHA256`` and nothing copied; if the upload key
            is gone, the original's facts (a completion that sealed but did not
            commit); ``None`` if neither exists.

        Raises:
            StorageError: If a key is invalid or of the wrong kind, the upload
                changed during the copy, or storage fails.
        """
        upload = self._checked_key(upload_key)
        original = self._checked_key(original_key)
        if not upload.startswith(UPLOAD_KEY_PREFIX) or original.startswith(
            (UPLOAD_KEY_PREFIX, PUBLIC_KEY_PREFIX)
        ):
            message = "sealing needs an upload key and an original key"
            raise StorageError(message, details={"operation": "seal"})
        try:
            async with self._client() as client:
                meta = await self._head_or_none(client, upload)
                if meta is None:
                    return await self._describe(client, original)
                if meta["ContentLength"] > self._max_object_bytes:
                    return await self._describe(client, upload)
                # Server-side copy pinned to the ETag HEAD saw: a replacement in
                # between fails with 412 instead of sealing an unmeasured file.
                await client.copy_object(
                    Bucket=self._private_bucket,
                    Key=original,
                    CopySource={"Bucket": self._private_bucket, "Key": upload},
                    CopySourceIfMatch=meta["ETag"],
                )
                await client.delete_object(Bucket=self._private_bucket, Key=upload)
                return await self._describe(client, original)
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="seal") from error

    async def _head_or_none(
        self, client: "S3Client", key: str
    ) -> "HeadObjectOutputTypeDef | None":
        try:
            return await client.head_object(Bucket=self._private_bucket, Key=key)
        except ClientError as error:
            if _is_missing(error):
                return None
            raise

    async def _describe(self, client: "S3Client", key: str) -> StoredObject | None:
        meta = await self._head_or_none(client, key)
        if meta is None:
            return None
        size = meta["ContentLength"]
        content_type: str | None = meta.get("ContentType")
        if content_type is not None and len(content_type) > CONTENT_TYPE_MAX_LENGTH:
            content_type = None
        if size > self._max_object_bytes:
            return StoredObject(
                sha256=OVERSIZE_SHA256, byte_size=size, content_type=content_type
            )
        digest, read = await self._digest(client, key, meta["ETag"])
        return StoredObject(sha256=digest, byte_size=read, content_type=content_type)

    async def _digest(self, client: "S3Client", key: str, etag: str) -> tuple[str, int]:
        # IfMatch pins the read to the object HEAD saw; a replacement in between
        # fails with 412 instead of hashing a different file than was measured.
        response = await client.get_object(
            Bucket=self._private_bucket, Key=key, IfMatch=etag
        )
        digest = hashlib.sha256()
        read = 0
        async with response["Body"] as body:
            while chunk := await body.read(STREAM_CHUNK_BYTES):
                read += len(chunk)
                if read > self._max_object_bytes:
                    message = "the object grew beyond the size cap while read"
                    raise StorageError(message, details={"operation": "head"})
                digest.update(chunk)
        return digest.hexdigest(), read

    async def read_original(self, key: str) -> bytes | None:
        """Return every byte of the original at ``key``.

        Args:
            key: The original's object key.

        Returns:
            The bytes, or ``None`` if the object is absent.

        Raises:
            StorageError: If storage fails or the object is above the size cap.
        """
        # An absent object yields nothing; an empty one yields a single b"".
        chunks = [chunk async for chunk in self._stream(key, missing_ok=True)]
        return b"".join(chunks) if chunks else None

    async def read_original_prefix(self, key: str, length: int) -> bytes | None:
        """Return up to ``length`` leading bytes of the original at ``key``.

        Args:
            key: The original's object key.
            length: How many bytes, at least 1.

        Returns:
            The bytes (empty for an empty object), or ``None`` if it is absent.

        Raises:
            StorageError: If storage fails.
        """
        checked = self._checked_key(key)
        try:
            async with self._client() as client:
                response = await client.get_object(
                    Bucket=self._private_bucket,
                    Key=checked,
                    Range=f"bytes=0-{max(length, 1) - 1}",
                )
                async with response["Body"] as body:
                    return await body.read(length)
        except ClientError as error:
            if _is_missing(error):
                return None
            if error.response.get("Error", {}).get("Code") == EMPTY_RANGE_CODE:
                # A range on an empty object is unsatisfiable: there are no bytes.
                return b""
            raise _storage_error(error, operation="read_prefix") from error
        except BotoCoreError as error:
            raise _storage_error(error, operation="read_prefix") from error

    def iter_original(
        self, key: str, expected_sha256: str | None = None
    ) -> AsyncGenerator[bytes]:
        """Stream the original at ``key`` in chunks of ``STREAM_CHUNK_BYTES``.

        Args:
            key: The original's object key.
            expected_sha256: The digest recorded at completion; when given, the
                streamed bytes are hashed and checked once the stream ends.

        Returns:
            An async generator of the object's bytes; close it (``aclosing``) if
            it is abandoned early.

        Raises:
            StorageError: When iterated, if the object is absent, above the size
                cap, or storage fails.
            MediaContentChangedError: After the last chunk, if the bytes hash
                differently from ``expected_sha256``.
        """
        return self._verified_stream(key, expected_sha256)

    async def _verified_stream(
        self, key: str, expected_sha256: str | None
    ) -> AsyncGenerator[bytes]:
        digest = hashlib.sha256()
        async with aclosing(self._stream(key, missing_ok=False)) as chunks:
            async for chunk in chunks:
                digest.update(chunk)
                yield chunk
        if expected_sha256 is not None and not _is_same_digest(
            digest.hexdigest(), expected_sha256
        ):
            raise MediaContentChangedError.detected()

    async def _stream(self, key: str, *, missing_ok: bool) -> AsyncGenerator[bytes]:
        checked = self._checked_key(key)
        try:
            async with self._client() as client:
                try:
                    response = await client.get_object(
                        Bucket=self._private_bucket, Key=checked
                    )
                except ClientError as error:
                    if missing_ok and _is_missing(error):
                        return
                    raise
                if response["ContentLength"] > self._max_object_bytes:
                    message = "the object is larger than the size cap"
                    raise StorageError(message, details={"operation": "read"})
                if response["ContentLength"] == 0:
                    yield b""
                    return
                async with response["Body"] as body:
                    while chunk := await body.read(STREAM_CHUNK_BYTES):
                        yield chunk
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="read") from error

    async def copy_stripped_public(
        self, original_key: str, public_key: str, *, expected_sha256: str
    ) -> None:
        """Write a metadata-free copy of the original to the public bucket.

        Idempotent: the same ``public_key`` is overwritten with the same bytes.

        Args:
            original_key: The private original.
            public_key: Where the public copy goes.
            expected_sha256: The digest recorded at completion.

        Raises:
            MediaContentChangedError: If the original hashes differently from
                ``expected_sha256``; nothing is written.
            StorageError: If the original is absent, is not an allowed type, has
                no metadata stripper, exceeds the decoding limits, cannot be
                re-encoded, or storage fails.
        """
        checked_public = self._checked_key(public_key)
        if not checked_public.startswith(PUBLIC_KEY_PREFIX):
            # An original must never land in the public bucket under its own key.
            message = "a public copy needs a public media key"
            raise StorageError(message, details={"operation": "copy"})
        data = await self.read_original(original_key)
        if data is None:
            message = "the original is missing"
            raise StorageError(message, details={"operation": "copy"})
        if not _is_same_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
            raise MediaContentChangedError.detected()
        mime_type = detect_mime_type(data[:SNIFF_BYTES])
        if mime_type is None:
            message = "the original is not an allowed media type"
            raise StorageError(message, details={"operation": "copy"})
        # Decoding a 50 MiB image takes long enough to stall the event loop.
        stripped = await asyncio.to_thread(strip_metadata, data, mime_type)
        try:
            async with self._client() as client:
                await client.put_object(
                    Bucket=self._public_bucket,
                    Key=checked_public,
                    Body=stripped,
                    ContentType=mime_type.value,
                )
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error, operation="copy") from error
