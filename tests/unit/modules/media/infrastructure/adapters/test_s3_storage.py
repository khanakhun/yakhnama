"""Unit tests for the storage adapter parts that need no S3: stripping and guards.

The S3 calls themselves run against a real MinIO in
``tests/integration/modules/media/infrastructure/adapters``.
"""

import struct
import warnings
import zlib
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
from yakhnama.modules.media.infrastructure.adapters import image_limits
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
def test_strip_metadata_video_or_pdf_is_refused_as_unsupported(
    data: bytes, mime_type: MimeType
) -> None:
    with pytest.raises(StorageError) as caught:
        strip_metadata(data, mime_type)

    assert caught.value.details == {"operation": "strip", "reason": "unsupported"}


def _png_header(width: int, height: int) -> bytes:
    # A PNG that declares a canvas but carries no pixel data: the shape of a
    # decompression bomb, a few dozen bytes long.
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def test_image_limits_set_pillow_max_image_pixels_explicitly() -> None:
    assert Image.MAX_IMAGE_PIXELS == image_limits.MAX_FRAME_PIXELS


def test_strip_metadata_declared_huge_canvas_is_refused_before_decoding() -> None:
    bomb = _png_header(100_000, 100_000)

    with pytest.raises(StorageError) as caught:
        strip_metadata(bomb, MimeType.PNG)

    assert caught.value.details["operation"] == "strip"
    assert caught.value.details["reason"] == "malformed"


def test_strip_metadata_canvas_in_pillows_warning_band_is_still_refused() -> None:
    # Between MAX_IMAGE_PIXELS and twice that Pillow only warns; the explicit
    # check must refuse it even where warnings are not errors.
    bomb = _png_header(8_000, 8_000)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with pytest.raises(StorageError) as caught:
            strip_metadata(bomb, MimeType.PNG)

    assert caught.value.details["reason"] == "too_large"
    assert caught.value.details["limit"] == "frame_pixels"


def _animated_webp(frames: int, size: tuple[int, int] = (4, 4)) -> bytes:
    images = [Image.new("RGB", size, (index, 0, 0)) for index in range(frames)]
    output = BytesIO()
    images[0].save(
        output, format="WEBP", save_all=True, append_images=images[1:], lossless=True
    )
    return output.getvalue()


@pytest.mark.parametrize(
    ("limits", "data", "limit"),
    [
        ({"MAX_FRAME_PIXELS": 15}, image_bytes("PNG", size=(4, 4)), "frame_pixels"),
        ({"MAX_FRAMES": 2}, _animated_webp(3), "frames"),
        ({"MAX_TOTAL_PIXELS": 40}, _animated_webp(3), "total_pixels"),
    ],
)
def test_strip_metadata_image_above_a_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch, limits: dict[str, int], data: bytes, limit: str
) -> None:
    for name, value in limits.items():
        monkeypatch.setattr(image_limits, name, value)
    mime_type = MimeType.PNG if data.startswith(b"\x89PNG") else MimeType.WEBP

    with pytest.raises(StorageError) as caught:
        strip_metadata(data, mime_type)

    assert caught.value.details == {
        "operation": "strip",
        "reason": "too_large",
        "limit": limit,
    }


def test_exceeded_limit_small_image_is_decodable() -> None:
    with Image.open(BytesIO(_animated_webp(3))) as image:
        limit = image_limits.exceeded_limit(image)

    assert limit is None


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
        await storage.copy_stripped_public(
            "media/original/1234", "media/original/x", expected_sha256="a" * 64
        )


@pytest.mark.parametrize(
    "key", ["media/original/1234", "media/public/1234", "media/other/1234"]
)
async def test_s3_storage_presign_put_only_for_upload_keys(key: str) -> None:
    storage = _storage()

    with pytest.raises(StorageError, match="only for upload keys"):
        await storage.presign_put(key, MimeType.JPEG, 1024)


@pytest.mark.parametrize(
    ("upload_key", "original_key"),
    [
        ("media/original/1234", "media/original/1234"),
        ("media/upload/1234", "media/upload/5678"),
        ("media/upload/1234", "media/public/1234"),
    ],
)
async def test_s3_storage_seal_upload_refuses_wrong_kinds_of_keys(
    upload_key: str, original_key: str
) -> None:
    storage = _storage()

    with pytest.raises(StorageError, match="sealing needs"):
        await storage.seal_upload(upload_key, original_key)


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
