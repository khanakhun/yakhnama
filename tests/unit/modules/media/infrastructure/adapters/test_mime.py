"""Unit tests for magic-byte media type detection."""

import pytest

from tests.unit.modules.media.infrastructure.adapters.images import (
    MINIMAL_MP4,
    MINIMAL_PDF,
    image_bytes,
)
from yakhnama.modules.media.domain.value_objects import MimeType
from yakhnama.modules.media.infrastructure.adapters.mime import (
    SNIFF_BYTES,
    FiletypeMimeSniffer,
    detect_mime_type,
)

QUICKTIME = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 32
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (image_bytes("JPEG"), MimeType.JPEG),
        (image_bytes("PNG"), MimeType.PNG),
        (image_bytes("WEBP"), MimeType.WEBP),
        (MINIMAL_PDF, MimeType.PDF),
        (MINIMAL_MP4, MimeType.MP4),
    ],
)
def test_detect_mime_type_allowed_signature_returns_its_type(
    data: bytes, expected: MimeType
) -> None:
    detected = detect_mime_type(data)

    assert detected is expected


@pytest.mark.parametrize("data", [b"", b"plain text, no magic", QUICKTIME, GIF])
def test_detect_mime_type_unknown_or_disallowed_signature_returns_none(
    data: bytes,
) -> None:
    detected = detect_mime_type(data)

    assert detected is None


async def test_filetype_mime_sniffer_reads_only_the_leading_bytes() -> None:
    requested: list[tuple[str, int]] = []
    stored = {"media/original/doc": MINIMAL_PDF}

    async def read_prefix(key: str, length: int) -> bytes | None:
        requested.append((key, length))
        data = stored.get(key)
        return None if data is None else data[:length]

    sniffer = FiletypeMimeSniffer(read_prefix)

    found = await sniffer.sniff("media/original/doc")
    missing = await sniffer.sniff("media/original/absent")

    assert found is MimeType.PDF
    assert missing is None
    assert requested == [
        ("media/original/doc", SNIFF_BYTES),
        ("media/original/absent", SNIFF_BYTES),
    ]
