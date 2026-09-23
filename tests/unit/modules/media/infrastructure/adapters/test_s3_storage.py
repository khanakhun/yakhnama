"""Unit tests for the storage adapter parts that need no S3: stripping and guards.

The S3 calls themselves run against a real MinIO in
``tests/integration/modules/media/infrastructure/adapters``.
"""

from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import ExifTags, Image, PngImagePlugin
from pydantic import SecretStr

from tests.fakes.clock import FrozenClock
from tests.unit.modules.media.infrastructure.adapters.images import (
    MINIMAL_MP4,
    MINIMAL_PDF,
    build_exif,
    gps_photo,
    image_bytes,
)
from yakhnama.modules.media.domain.value_objects import MimeType
from yakhnama.modules.media.infrastructure.adapters.exif import parse_exif
from yakhnama.modules.media.infrastructure.adapters.s3_storage import (
    PUBLIC_KEY_PREFIX,
    S3StoragePort,
    StorageError,
    strip_metadata,
)
from yakhnama.platform.settings import Settings

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _storage() -> S3StoragePort:
    # Building the adapter makes no connection, so these tests do no I/O.
    return S3StoragePort(
        endpoint_url="http://127.0.0.1:1",
        region="us-east-1",
        access_key_id="unit",
        secret_access_key=SecretStr("unit-secret"),
        private_bucket="private-bucket",
        public_bucket="public-bucket",
        presign_ttl_seconds=300,
        clock=FrozenClock(NOW),
    )


def _open(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("JPEG", MimeType.JPEG), ("PNG", MimeType.PNG), ("WEBP", MimeType.WEBP)],
)
def test_strip_metadata_image_loses_every_exif_fact(
    image_format: str, mime_type: MimeType
) -> None:
    original = gps_photo(image_format)

    stripped = strip_metadata(original, mime_type)

    copy = _open(stripped)
    assert parse_exif(original) is not None
    assert parse_exif(stripped) is None
    assert dict(copy.getexif()) == {}
    assert "exif" not in copy.info
    assert copy.format == image_format


def test_strip_metadata_applies_orientation_before_dropping_it() -> None:
    exif = build_exif(base={ExifTags.Base.Orientation: 6})
    original = image_bytes("JPEG", exif=exif, size=(4, 2))

    stripped = strip_metadata(original, MimeType.JPEG)

    assert _open(stripped).size == (2, 4)


def test_strip_metadata_png_drops_text_chunks_and_icc_profile() -> None:
    text = PngImagePlugin.PngInfo()
    text.add_text("Author", "Reporter Name")
    output = BytesIO()
    Image.new("RGB", (2, 2)).save(
        output, format="PNG", pnginfo=text, icc_profile=b"fake-icc-profile"
    )

    stripped = strip_metadata(output.getvalue(), MimeType.PNG)

    assert _open(stripped).info == {}
    assert b"Reporter Name" not in stripped


def test_strip_metadata_palette_png_keeps_transparency_as_rgba() -> None:
    palette_image = Image.new("P", (2, 2), 1)
    palette_image.putpalette([255, 0, 0, 0, 255, 0])
    output = BytesIO()
    palette_image.save(output, format="PNG", transparency=1)

    stripped = strip_metadata(output.getvalue(), MimeType.PNG)

    copy = _open(stripped)
    assert copy.mode == "RGBA"
    assert copy.getpixel((0, 0)) == (0, 255, 0, 0)


def test_strip_metadata_animated_webp_keeps_its_frames() -> None:
    frames = [Image.new("RGB", (2, 2), colour) for colour in ("red", "blue")]
    output = BytesIO()
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        exif=build_exif(base={ExifTags.Base.Make: "Canon"}).tobytes(),
    )

    stripped = strip_metadata(output.getvalue(), MimeType.WEBP)

    copy = Image.open(BytesIO(stripped))
    assert getattr(copy, "n_frames", 1) == 2
    assert dict(copy.getexif()) == {}


@pytest.mark.parametrize(
    ("data", "mime_type"), [(MINIMAL_PDF, MimeType.PDF), (MINIMAL_MP4, MimeType.MP4)]
)
def test_strip_metadata_non_image_is_returned_byte_for_byte(
    data: bytes, mime_type: MimeType
) -> None:
    stripped = strip_metadata(data, mime_type)

    assert stripped == data


def test_strip_metadata_corrupt_image_raises_storage_error() -> None:
    corrupt = gps_photo()[:40]

    with pytest.raises(StorageError) as caught:
        strip_metadata(corrupt, MimeType.JPEG)

    assert caught.value.code == "storage_error"
    assert caught.value.details["operation"] == "strip"


def test_strip_metadata_format_mismatch_raises_storage_error() -> None:
    with pytest.raises(StorageError):
        strip_metadata(image_bytes("PNG"), MimeType.JPEG)


@pytest.mark.parametrize(
    "key", ["../etc/passwd", "UPPER/case", "media/x y", "media/../x"]
)
async def test_s3_storage_invalid_key_is_refused_before_any_request(key: str) -> None:
    storage = _storage()

    with pytest.raises(StorageError, match="not a valid media key") as caught:
        await storage.presign_put(key, MimeType.JPEG, 1024)

    assert key not in str(caught.value)
    assert key not in repr(caught.value.details)


async def test_s3_storage_public_copy_outside_public_prefix_is_refused() -> None:
    storage = _storage()

    with pytest.raises(StorageError, match="public media key"):
        await storage.copy_stripped_public("media/original/1234", "media/original/x")


def test_s3_storage_from_settings_uses_the_storage_settings() -> None:
    settings = Settings(
        _env_file=None,
        storage_private_bucket="originals",
        storage_public_bucket="copies",
    )

    storage = S3StoragePort.from_settings(settings, FrozenClock(NOW))

    assert storage._bucket_of("media/original/1") == "originals"
    assert storage._bucket_of(f"{PUBLIC_KEY_PREFIX}1") == "copies"
    assert "minioadmin-dev-only" not in repr(vars(storage))
