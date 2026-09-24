"""Unit tests for the Pillow EXIF reader: every fact alone, and malformed input."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import ExifTags, TiffImagePlugin

from tests.unit.modules.media.infrastructure.adapters.images import (
    GILGIT_GPS,
    MINIMAL_PDF,
    build_exif,
    gps_photo,
    image_bytes,
)
from yakhnama.modules.media.infrastructure.adapters.exif import (
    PillowExifReader,
    parse_exif,
)
from yakhnama.shared_kernel.value_objects import DatePrecision

TAKEN: Final[Mapping[int, object]] = {
    ExifTags.Base.DateTimeOriginal: "2025:07:14 09:30:00"
}


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_parse_exif_photo_with_every_fact_returns_time_place_and_camera(
    image_format: str,
) -> None:
    data = gps_photo(image_format)

    facts = parse_exif(data)

    assert facts is not None
    assert facts.taken_at is not None
    assert facts.taken_at.value == datetime(2025, 7, 14, 4, 30, tzinfo=UTC)
    assert facts.taken_at.precision is DatePrecision.EXACT
    assert facts.location is not None
    assert facts.location.latitude == pytest.approx(35.92)
    assert facts.location.longitude == pytest.approx(74.3)
    assert facts.camera == "Canon EOS 80D"


def test_parse_exif_time_without_offset_is_assumed_utc() -> None:
    data = image_bytes("JPEG", exif=build_exif(exif_ifd=TAKEN))

    facts = parse_exif(data)

    assert facts is not None
    assert facts.taken_at is not None
    assert facts.taken_at.value == datetime(2025, 7, 14, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("offset", "expected_hour"),
    [("-03:00", 12), ("+14:00", 9 - 14 + 24), ("+15:00", 9), ("garbage", 9)],
)
def test_parse_exif_offset_is_applied_or_ignored_when_invalid(
    offset: str, expected_hour: int
) -> None:
    exif = build_exif(
        exif_ifd={**TAKEN, ExifTags.Base.OffsetTimeOriginal: offset},
    )

    facts = parse_exif(image_bytes("JPEG", exif=exif))

    assert facts is not None
    assert facts.taken_at is not None
    assert facts.taken_at.value.hour == expected_hour


@pytest.mark.parametrize(
    "raw", ["0000:00:00 00:00:00", "2025-07-14 09:30:00", "    ", "0001:01:01 00:00:00"]
)
def test_parse_exif_unreadable_time_drops_only_the_time(raw: str) -> None:
    exif = build_exif(
        base={ExifTags.Base.Make: "Nikon"},
        exif_ifd={ExifTags.Base.DateTimeOriginal: raw},
    )

    facts = parse_exif(image_bytes("JPEG", exif=exif))

    assert facts is not None
    assert facts.taken_at is None
    assert facts.camera == "Nikon"


def test_parse_exif_southern_western_position_is_negative() -> None:
    gps = {
        **GILGIT_GPS,
        ExifTags.GPS.GPSLatitudeRef: "S",
        ExifTags.GPS.GPSLongitudeRef: "W",
    }

    facts = parse_exif(image_bytes("JPEG", exif=build_exif(gps=gps)))

    assert facts is not None
    assert facts.location is not None
    assert facts.location.latitude == pytest.approx(-35.92)
    assert facts.location.longitude == pytest.approx(-74.3)


@pytest.mark.parametrize(
    "broken",
    [
        {ExifTags.GPS.GPSLatitudeRef: "X"},
        {ExifTags.GPS.GPSLatitude: (35.0, 55.0)},
        {ExifTags.GPS.GPSLatitude: (95.0, 0.0, 0.0)},
        {
            ExifTags.GPS.GPSLatitude: (
                TiffImagePlugin.IFDRational(1, 0),
                0.0,
                0.0,
            )
        },
    ],
)
def test_parse_exif_malformed_position_drops_only_the_position(
    broken: dict[int, object],
) -> None:
    exif = build_exif(exif_ifd=TAKEN, gps={**GILGIT_GPS, **broken})

    facts = parse_exif(image_bytes("JPEG", exif=exif))

    assert facts is not None
    assert facts.location is None
    assert facts.taken_at is not None


def test_parse_exif_position_without_reference_is_dropped() -> None:
    gps = {
        tag: value
        for tag, value in GILGIT_GPS.items()
        if tag != ExifTags.GPS.GPSLongitudeRef
    }

    facts = parse_exif(image_bytes("JPEG", exif=build_exif(exif_ifd=TAKEN, gps=gps)))

    assert facts is not None
    assert facts.location is None


@pytest.mark.parametrize(
    ("make", "model", "expected"),
    [
        ("Canon", "EOS 80D", "Canon EOS 80D"),
        ("Canon", "Canon EOS 80D", "Canon EOS 80D"),
        (None, "Pixel 8", "Pixel 8"),
        ("Apple", None, "Apple"),
        ("  Samsung\x00", "SM-A515F  ", "Samsung SM-A515F"),
    ],
)
def test_parse_exif_camera_joins_make_and_model(
    make: str | None, model: str | None, expected: str
) -> None:
    base: dict[int, object] = {
        tag: value
        for tag, value in (
            (ExifTags.Base.Make, make),
            (ExifTags.Base.Model, model),
        )
        if value is not None
    }

    facts = parse_exif(image_bytes("JPEG", exif=build_exif(base=base)))

    assert facts is not None
    assert facts.camera == expected


def test_parse_exif_camera_with_control_characters_is_dropped() -> None:
    exif = build_exif(base={int(ExifTags.Base.Make): "Evil\x07Cam"}, exif_ifd=TAKEN)

    facts = parse_exif(image_bytes("JPEG", exif=exif))

    assert facts is not None
    assert facts.camera is None


def test_parse_exif_image_without_exif_returns_none() -> None:
    facts = parse_exif(image_bytes("PNG"))

    assert facts is None


@pytest.mark.parametrize(
    "data", [MINIMAL_PDF, b"", b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00garbage"]
)
def test_parse_exif_non_image_or_corrupt_file_returns_none(data: bytes) -> None:
    facts = parse_exif(data)

    assert facts is None


@given(st.binary(max_size=512))
def test_parse_exif_arbitrary_bytes_never_raise(data: bytes) -> None:
    facts = parse_exif(b"\xff\xd8\xff\xe1" + data)

    assert facts is None or not facts.is_empty


def test_parse_exif_truncated_photo_never_raises() -> None:
    data = gps_photo()

    facts = [parse_exif(data[:cut]) for cut in range(0, len(data), 7)]

    assert all(fact is None or not fact.is_empty for fact in facts)


async def test_pillow_exif_reader_reads_the_stored_original() -> None:
    stored = {"media/original/photo": gps_photo()}

    async def read(key: str) -> bytes | None:
        return stored.get(key)

    reader = PillowExifReader(read)

    found = await reader.read("media/original/photo")
    missing = await reader.read("media/original/absent")

    assert found is not None
    assert found.camera == "Canon EOS 80D"
    assert missing is None
