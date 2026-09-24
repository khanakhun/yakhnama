"""Measuring the bytes that pass between a format strategy and object storage.

An export's checksum and size are computed here, around the ``BinarySink`` the
exporter writes to, so the sidecar describes exactly the stored bytes whatever the
exporter does; an import's file is measured on the way in and checked against the
size and digest declared when the import was requested, so a file replaced or
truncated in storage meanwhile is refused. Both enforce a byte cap as the bytes
flow, so an oversized file fails fast instead of filling memory or storage.

Patterns: Decorator.
"""

import hashlib
from typing import Final

from yakhnama.modules.exchange.application.formats import BinarySink, BinarySource
from yakhnama.modules.exchange.domain.value_objects import ArtifactRef
from yakhnama.shared_kernel.errors import ValidationError

SIZE_LIMIT_REASON: Final = "size_limit"
"""``details["reason"]`` of the error raised when a stream passes its byte cap."""

INTEGRITY_REASON: Final = "integrity"
"""``details["reason"]`` of the error raised when a file differs from its record."""

DRAIN_CHUNK_BYTES: Final = 64 * 1024


def _size_limit_error(limit: int) -> ValidationError:
    message = "the file is larger than allowed"
    return ValidationError(
        message, details={"reason": SIZE_LIMIT_REASON, "max_bytes": limit}
    )


class MeteredSink:
    """A ``BinarySink`` that hashes, counts and caps the bytes it forwards.

    Implements: Decorator (of ``BinarySink``).

    Attributes:
        byte_size: Bytes forwarded so far.
    """

    def __init__(self, inner: BinarySink, *, max_bytes: int) -> None:
        """Wrap a sink.

        Args:
            inner: The storage sink.
            max_bytes: The most bytes it may receive.
        """
        self._inner = inner
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self.byte_size = 0

    @property
    def sha256(self) -> str:
        """Return the SHA-256 of every byte forwarded so far, as 64 hex digits."""
        return self._digest.hexdigest()

    async def write(self, data: bytes) -> None:
        """Forward ``data`` after counting and hashing it.

        Args:
            data: The next bytes.

        Raises:
            ValidationError: If the total would pass ``max_bytes``; nothing of
                ``data`` is forwarded then.
        """
        if self.byte_size + len(data) > self._max_bytes:
            raise _size_limit_error(self._max_bytes)
        self._digest.update(data)
        self.byte_size += len(data)
        await self._inner.write(data)


class MeteredSource:
    """A ``BinarySource`` that hashes, counts and caps the bytes it hands out.

    Implements: Decorator (of ``BinarySource``).

    Attributes:
        byte_size: Bytes read so far.
    """

    def __init__(self, inner: BinarySource, *, max_bytes: int) -> None:
        """Wrap a source.

        Args:
            inner: The storage source.
            max_bytes: The most bytes the file may have.
        """
        self._inner = inner
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self.byte_size = 0

    @property
    def sha256(self) -> str:
        """Return the SHA-256 of every byte read so far, as 64 hex digits."""
        return self._digest.hexdigest()

    async def read(self, size: int) -> bytes:
        """Read up to ``size`` bytes from the wrapped source.

        Args:
            size: The most bytes to return, at least 1.

        Returns:
            The bytes; empty once the file is exhausted.

        Raises:
            ValidationError: If the file turns out larger than ``max_bytes``.
        """
        data = await self._inner.read(size)
        if self.byte_size + len(data) > self._max_bytes:
            raise _size_limit_error(self._max_bytes)
        self._digest.update(data)
        self.byte_size += len(data)
        return data

    async def drain(self) -> None:
        """Read whatever the importer left, so the whole file is measured.

        Raises:
            ValidationError: If the file turns out larger than ``max_bytes``.
        """
        while await self.read(DRAIN_CHUNK_BYTES):
            pass

    def verify(self, declared: ArtifactRef) -> None:
        """Check that the bytes read are exactly the declared file.

        Call after ``drain``.

        Args:
            declared: The size and digest recorded when the import was requested.

        Raises:
            ValidationError: If the size or the digest differs.
        """
        if self.byte_size != declared.byte_size or self.sha256 != declared.sha256:
            message = "the stored file differs from the one the import was asked for"
            raise ValidationError(message, details={"reason": INTEGRITY_REASON})
