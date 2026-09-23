"""Magic-byte media type detection with ``filetype``.

The ``Content-Type`` an uploader declares is chosen by the client, so the platform
never trusts it; the type of an original is read from its first bytes instead and
must be on the ``MimeType`` allow-list.

Patterns: Adapter + Anti-Corruption Layer.
"""

from collections.abc import Awaitable, Callable
from typing import Final

import filetype  # type: ignore[import-untyped]  # reason: filetype 1.2 ships no type information

from yakhnama.modules.media.domain.value_objects import MimeType

SNIFF_BYTES: Final = 8 * 1024
"""Bytes read from the start of a file: every signature ``filetype`` knows (MP4's
``ftyp`` box included) lies within the first 8 KiB."""

type PrefixReader = Callable[[str, int], Awaitable[bytes | None]]
"""Returns up to ``n`` leading bytes of the original at ``key``, ``None`` if absent."""


def detect_mime_type(data: bytes) -> MimeType | None:
    """Return the allowed media type the leading bytes ``data`` belong to.

    Args:
        data: The first bytes of a file, ideally ``SNIFF_BYTES`` of them.

    Returns:
        The matching ``MimeType``, or ``None`` if the signature is unknown or its
        type is not on the allow-list (for example ``video/quicktime``).
    """
    if not data:
        return None
    # filetype.guess returns an untyped Kind object or None; only its mime string
    # crosses into our code, and it is checked against the allow-list at once.
    kind: object = filetype.guess(data)
    mime: object = getattr(kind, "mime", None)
    if not isinstance(mime, str):
        return None
    try:
        return MimeType(mime)
    except ValueError:
        return None


class FiletypeMimeSniffer:
    """``MimeSniffer`` reading a stored original's magic bytes with ``filetype``.

    Implements: Adapter.
    """

    def __init__(self, read_prefix: PrefixReader) -> None:
        """Create the sniffer.

        Args:
            read_prefix: Reads leading bytes of an original, for example
                ``S3StoragePort.read_original_prefix``.
        """
        self._read_prefix = read_prefix

    async def sniff(self, key: str) -> MimeType | None:
        """Return the media type of the original at ``key``.

        Args:
            key: The original's object key.

        Returns:
            The detected type, or ``None`` if the object is absent, empty or not an
            allowed type.
        """
        data = await self._read_prefix(key, SNIFF_BYTES)
        return None if data is None else detect_mime_type(data)
