"""S3-compatible object storage for export files, their sidecars and import files.

``ArtifactStore`` over ``aiobotocore``, following the media adapter's conventions
(ADR 0009): a short-lived client per operation, path-style addressing, explicit
timeouts and standard-mode retries behind the adapter, and no key, bucket,
credential or URL in any log line or error message.

- **Bucket.** Exchange objects live in the *private* bucket
  (``storage_private_bucket``) under ``exports/`` and ``imports/``; there is no
  separate exports bucket setting yet (open question). Media keys start with
  ``media/``, so the prefixes never overlap.
- **Keys.** Every key is validated again (``ObjectKey``) and must start with
  ``exports/`` or ``imports/``: this adapter cannot reach a media object even if
  a caller passes a media key.
- **Streaming writes.** ``open_sink`` buffers at most one part
  (``MULTIPART_PART_BYTES``, 5 MiB, S3's minimum part size) and uploads each full
  part as it fills, so an export of any size streams. The multipart upload is
  created only when the first part is full; a smaller file is stored with one
  ``PutObject`` when the block ends. The object becomes visible only on a clean
  exit (``CompleteMultipartUpload``); an error, a cancellation or a failed
  completion aborts the upload,
  so no partial object and no orphaned parts remain. If the abort itself fails,
  that is logged and the original error is raised, because it is the one the
  caller must see (a bucket lifecycle rule for incomplete uploads is the backstop,
  open question).
- **Streaming reads.** ``open_source`` hands out the object's body chunk by chunk.
- **Upload grants.** ``presign_upload`` signs a ``PUT`` of an ``imports/`` key
  only, bound to the declared ``Content-Type`` (the upload must send exactly that
  header), valid for ``storage_presign_ttl_seconds``. A presigned ``PUT`` cannot
  cap the body size (only a presigned ``POST`` policy has
  ``content-length-range``, and the port returns a ``PUT``), so ``max_bytes`` is
  the client contract, enforced after the fact: the import handler's
  ``MeteredSource`` refuses a file larger than the size recorded for the import,
  and the size must be checked against ``max_bytes`` before it is recorded (open
  question). This is the same limit the media adapter documents.
- **Download links.** ``presign_download`` signs a ``GET`` of an ``exports/`` key
  only, with ``Content-Disposition: attachment`` and the module-built file name,
  valid for ``storage_presign_ttl_seconds``.

Patterns: Adapter + Anti-Corruption Layer.
"""

import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final, Self

import structlog
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.exchange.application.formats import BinarySink, BinarySource
from yakhnama.modules.exchange.application.ports import (
    EXPORT_KEY_PREFIX,
    IMPORT_KEY_PREFIX,
)
from yakhnama.modules.exchange.domain.value_objects import ObjectKey
from yakhnama.modules.media.public import HttpHeader, PresignedUpload
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import NotFoundError, YakhnamaError

if TYPE_CHECKING:
    from aiobotocore.response import StreamingBody
    from types_aiobotocore_s3 import S3Client
    from types_aiobotocore_s3.type_defs import CompletedPartTypeDef

MULTIPART_PART_BYTES: Final = 5 * 1024 * 1024
"""Size of every uploaded part but the last: S3's minimum part size."""

MAX_PARTS: Final = 10_000
"""S3's limit on parts per multipart upload (50 GB at 5 MiB, above any artifact)."""

ALLOWED_KEY_PREFIXES: Final = (EXPORT_KEY_PREFIX, IMPORT_KEY_PREFIX)
DOWNLOAD_FILE_NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
MISSING_OBJECT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})

# Same values as the media adapter: a stalled storage call fails within seconds and
# standard-mode retries absorb transient 5xx and throttling.
CONNECT_TIMEOUT_SECONDS: Final = 5.0
READ_TIMEOUT_SECONDS: Final = 30.0
MAX_ATTEMPTS: Final = 3

_OBJECT_KEY: Final = TypeAdapter(ObjectKey)
_STORAGE_ERRORS: Final = (BotoCoreError, ClientError)


class ArtifactStorageError(YakhnamaError):
    """Object storage failed, or was asked for a key this adapter may not touch.

    Its message names the operation, never the key, bucket or credentials. The
    application treats it like any unexpected failure (a failed job, a 500).

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"storage_error"``.
    """

    code: ClassVar[str] = "storage_error"


def _storage_error(error: Exception, *, operation: str) -> ArtifactStorageError:
    return ArtifactStorageError(
        f"object storage failed during {operation}",
        details={"operation": operation, "reason": type(error).__name__},
    )


def _is_missing(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in MISSING_OBJECT_CODES


def checked_key(key: str, prefixes: tuple[str, ...] = ALLOWED_KEY_PREFIXES) -> str:
    """Return ``key`` if it is a valid exchange key under one of ``prefixes``.

    Args:
        key: The object key.
        prefixes: The prefixes the operation may use.

    Returns:
        The key, unchanged.

    Raises:
        ArtifactStorageError: If the key is malformed or outside the prefixes.
    """
    try:
        valid = _OBJECT_KEY.validate_python(key)
    except PydanticValidationError as error:
        message = "the object key is not a valid exchange key"
        raise ArtifactStorageError(message, details={"operation": "key"}) from error
    if not valid.startswith(prefixes):
        # The application builds every key under these prefixes; anything else is
        # a caller reaching for another module's objects.
        message = "the object key is outside the exchange prefixes"
        raise ArtifactStorageError(message, details={"operation": "key"})
    return valid


class _MultipartSink:
    """``BinarySink`` uploading full parts as they fill, one object per sink.

    Implements: Adapter (``BinarySink`` over an S3 multipart upload).
    """

    def __init__(
        self,
        client: "S3Client",
        *,
        bucket: str,
        key: str,
        media_type: str,
        part_bytes: int,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._key = key
        self._media_type = media_type
        self._part_bytes = part_bytes
        self._buffer = bytearray()
        self._upload_id: str | None = None
        self._parts: list[CompletedPartTypeDef] = []

    async def write(self, data: bytes) -> None:
        """Buffer ``data`` and upload every full part.

        Raises:
            ArtifactStorageError: If storage refuses a part.
        """
        self._buffer.extend(data)
        while len(self._buffer) >= self._part_bytes:
            part = bytes(self._buffer[: self._part_bytes])
            del self._buffer[: self._part_bytes]
            await self._upload_part(part)

    async def _upload_part(self, part: bytes) -> None:
        if len(self._parts) >= MAX_PARTS:
            message = "the file has more parts than object storage accepts"
            raise ArtifactStorageError(message, details={"operation": "upload_part"})
        try:
            if self._upload_id is None:
                created = await self._client.create_multipart_upload(
                    Bucket=self._bucket, Key=self._key, ContentType=self._media_type
                )
                self._upload_id = created["UploadId"]
            number = len(self._parts) + 1
            uploaded = await self._client.upload_part(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                PartNumber=number,
                Body=part,
            )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="upload_part") from error
        self._parts.append({"ETag": uploaded["ETag"], "PartNumber": number})

    async def complete(self) -> None:
        """Make the object visible: one ``PutObject``, or complete the upload.

        Raises:
            ArtifactStorageError: If storage refuses.
        """
        if self._upload_id is None:
            try:
                await self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._key,
                    Body=bytes(self._buffer),
                    ContentType=self._media_type,
                )
            except _STORAGE_ERRORS as error:
                raise _storage_error(error, operation="put") from error
            return
        if self._buffer:
            # The last part may be smaller than the minimum part size.
            await self._upload_part(bytes(self._buffer))
            self._buffer.clear()
        try:
            await self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                MultipartUpload={"Parts": self._parts},
            )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="complete_upload") from error

    async def abort(self) -> None:
        """Discard every uploaded part; nothing to do if no upload was created.

        Raises:
            ArtifactStorageError: If storage refuses.
        """
        if self._upload_id is None:
            return
        try:
            await self._client.abort_multipart_upload(
                Bucket=self._bucket, Key=self._key, UploadId=self._upload_id
            )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="abort_upload") from error


class _StreamingSource:
    """``BinarySource`` over the body of one ``GetObject`` response.

    Implements: Adapter (``BinarySource`` over an S3 object body).
    """

    def __init__(self, body: "StreamingBody") -> None:
        self._body = body

    async def read(self, size: int) -> bytes:
        """Return up to ``size`` further bytes; empty at the end.

        Raises:
            ArtifactStorageError: If the download fails.
        """
        try:
            return await self._body.read(size)
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="read") from error


class S3ArtifactStore:
    """``ArtifactStore`` over any S3-compatible service through ``aiobotocore``.

    Implements: Adapter.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per connection setting, all required
        self,
        *,
        endpoint_url: str | None,
        region: str,
        access_key_id: str,
        secret_access_key: SecretStr,
        bucket: str,
        presign_ttl_seconds: int,
        clock: Clock,
        part_bytes: int = MULTIPART_PART_BYTES,
    ) -> None:
        """Create the adapter; nothing connects until an operation runs.

        Args:
            endpoint_url: The S3 endpoint; ``None`` for AWS's regional default.
            region: The signing region.
            access_key_id: The access key id.
            secret_access_key: The secret key, unwrapped only to build a client.
            bucket: The bucket holding ``exports/`` and ``imports/``.
            presign_ttl_seconds: Lifetime of every upload grant and download link.
            clock: Source of the expiry instants reported for upload grants.
            part_bytes: Size of every uploaded part but the last; S3 refuses
                less than 5 MiB, so only a test against a lenient store lowers it.
        """
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._ttl_seconds = presign_ttl_seconds
        self._clock = clock
        self._part_bytes = part_bytes
        self._session = get_session()
        self._config = AioConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"},
        )

    @classmethod
    def from_settings(cls, settings: Settings, clock: Clock) -> Self:
        """Build the adapter from the ``storage_*`` settings, on the private bucket.

        Args:
            settings: The process settings.
            clock: Source of the expiry instants of upload grants.

        Returns:
            The adapter.
        """
        return cls(
            endpoint_url=settings.storage_endpoint_url,
            region=settings.storage_region,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
            bucket=settings.storage_private_bucket,
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

    @asynccontextmanager
    async def open_sink(self, key: str, media_type: str) -> AsyncIterator[BinarySink]:
        """Open a new object for streamed writing.

        Args:
            key: An ``exports/`` or ``imports/`` key.
            media_type: Its ``Content-Type``.

        Yields:
            The sink; the object appears only when the block exits cleanly.

        Raises:
            ArtifactStorageError: If the key is refused or storage fails.
        """
        valid = checked_key(key)
        async with self._client() as client:
            sink = _MultipartSink(
                client,
                bucket=self._bucket,
                key=valid,
                media_type=media_type,
                part_bytes=self._part_bytes,
            )
            try:
                yield sink
                # Inside the try: a failed completion must abort too, or its
                # parts would stay behind as an incomplete upload.
                await sink.complete()
            except BaseException:
                await self._abort_keeping_error(sink)
                raise

    @staticmethod
    async def _abort_keeping_error(sink: _MultipartSink) -> None:
        try:
            await sink.abort()
        except ArtifactStorageError as error:
            # Not swallowed: logged, while the error that made the export fail
            # propagates; the incomplete upload is left to the bucket lifecycle.
            structlog.get_logger(__name__).warning(
                "exchange.artifact_abort_failed",
                operation=error.details.get("operation"),
                reason=error.details.get("reason"),
            )

    @asynccontextmanager
    async def open_source(self, key: str) -> AsyncIterator[BinarySource]:
        """Open an existing object for streamed reading.

        Args:
            key: An ``exports/`` or ``imports/`` key.

        Yields:
            The source.

        Raises:
            NotFoundError: If no object has that key.
            ArtifactStorageError: If the key is refused or storage fails.
        """
        valid = checked_key(key)
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=valid)
            except ClientError as error:
                if _is_missing(error):
                    message = "no stored file has this key"
                    raise NotFoundError(message) from error
                raise _storage_error(error, operation="open") from error
            except BotoCoreError as error:
                raise _storage_error(error, operation="open") from error
            async with response["Body"] as body:
                yield _StreamingSource(body)

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        """Store a small object (a sidecar, an inline import file) in one request.

        Args:
            key: An ``exports/`` or ``imports/`` key.
            data: The whole content.
            media_type: Its ``Content-Type``.

        Raises:
            ArtifactStorageError: If the key is refused or storage fails.
        """
        valid = checked_key(key)
        try:
            async with self._client() as client:
                await client.put_object(
                    Bucket=self._bucket, Key=valid, Body=data, ContentType=media_type
                )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="put") from error

    async def presign_download(self, key: str, *, file_name: str) -> str:
        """Return a presigned ``GET`` of an export file, saved as ``file_name``.

        Args:
            key: An ``exports/`` key; import files are never offered for download.
            file_name: The download name, letters, digits, ``.``, ``_`` and ``-``
                only, so it cannot break out of the ``Content-Disposition`` header.

        Returns:
            The URL, valid for ``storage_presign_ttl_seconds``.

        Raises:
            ArtifactStorageError: If the key or file name is refused, or signing
                fails.
        """
        valid = checked_key(key, (EXPORT_KEY_PREFIX,))
        if DOWNLOAD_FILE_NAME_PATTERN.fullmatch(file_name) is None:
            message = "the download file name is not safe for a header"
            raise ArtifactStorageError(message, details={"operation": "presign"})
        try:
            async with self._client() as client:
                return await client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._bucket,
                        "Key": valid,
                        "ResponseContentDisposition": (
                            f'attachment; filename="{file_name}"'
                        ),
                    },
                    ExpiresIn=self._ttl_seconds,
                )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="presign") from error

    async def presign_upload(
        self, key: str, media_type: str, max_bytes: int
    ) -> PresignedUpload:
        """Return a presigned ``PUT`` of an import file, bound to its media type.

        Args:
            key: An ``imports/`` key; exports and every other key are refused.
            media_type: The ``Content-Type`` the upload must carry; it is signed.
            max_bytes: Largest accepted file. Not enforceable by a presigned
                ``PUT`` (module documentation); checked after the upload.

        Returns:
            The URL, the ``Content-Type`` header it requires and its expiry.

        Raises:
            ArtifactStorageError: If the key is refused or signing fails.
        """
        del max_bytes
        valid = checked_key(key, (IMPORT_KEY_PREFIX,))
        # Taken before signing, so the reported expiry is never later than the one
        # botocore signs from its own, slightly later, clock reading.
        expires_at = self._clock.now() + timedelta(seconds=self._ttl_seconds)
        try:
            async with self._client() as client:
                url = await client.generate_presigned_url(
                    "put_object",
                    Params={
                        "Bucket": self._bucket,
                        "Key": valid,
                        "ContentType": media_type,
                    },
                    ExpiresIn=self._ttl_seconds,
                )
        except _STORAGE_ERRORS as error:
            raise _storage_error(error, operation="presign_upload") from error
        return PresignedUpload(
            url=url,
            headers=(HttpHeader(name="Content-Type", value=media_type),),
            expires_at=expires_at,
        )
