"""EXIF facts (capture time, GPS position, camera) read from an original with Pillow.

EXIF is written by whatever device or software produced the file, so it is
untrusted and often malformed. Every fact is read on its own and a broken one is
dropped, never raised: a photo with a corrupt GPS block still yields its capture
time. The facts are private (``ExifFacts``); they are never logged.

**Time zone (proposed, Q-M11).** ``DateTimeOriginal`` is a local wall-clock
time without a zone. When ``OffsetTimeOriginal`` (EXIF 2.31) is present it gives the
zone; otherwise the time is *assumed to be UTC*. That default is a proposal, not a
fact: a phone in Gilgit-Baltistan usually records Pakistan Standard Time (UTC+5),
so an assumed-UTC time can be five hours late. Either way the precision recorded is
``exact``, because EXIF gives seconds; whether an unknown zone should lower it is
part of the same open question.

Patterns: Adapter + Anti-Corruption Layer.
"""

import math
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from typing import Final

from PIL import ExifTags, Image
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.media.domain.value_objects import (
    CAMERA_MAX_LENGTH,
    CameraModel,
    ExifFacts,
)
from yakhnama.modules.media.infrastructure.adapters.image_limits import (
    MALFORMED_FILE_ERRORS,
)
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

type ObjectReader = Callable[[str], Awaitable[bytes | None]]
"""Returns every byte of the original at ``key``, or ``None`` if it is absent."""

EXIF_DATETIME_FORMAT: Final = "%Y:%m:%d %H:%M:%S"
OFFSET_PATTERN: Final = re.compile(
    r"^(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})$"
)
# EXIF offsets run from -12:00 to +14:00; anything outside is a corrupt value.
MAX_OFFSET: Final = timedelta(hours=14)
MINUTES_PER_DEGREE: Final = 60
SECONDS_PER_DEGREE: Final = 3600
DMS_PARTS: Final = 3

_CAMERA_ADAPTER: Final = TypeAdapter(CameraModel)
_TAKEN_AT_ADAPTER: Final = TypeAdapter(DateWithPrecision)


def parse_exif(data: bytes) -> ExifFacts | None:
    """Return the EXIF facts of the image file ``data``.

    Args:
        data: A whole file; JPEG, PNG and WebP carry EXIF, other formats yield
            ``None``.

    Returns:
        The facts that could be read, or ``None`` if the file is not an image,
        has no EXIF block, or none of its facts is readable.
    """
    try:
        with Image.open(BytesIO(data)) as image:
            exif = image.getexif()
            base = dict(exif)
            exif_ifd = dict(exif.get_ifd(ExifTags.IFD.Exif))
            gps_ifd = dict(exif.get_ifd(ExifTags.IFD.GPSInfo))
    except MALFORMED_FILE_ERRORS:
        return None
    facts = ExifFacts(
        taken_at=_taken_at(exif_ifd),
        location=_location(gps_ifd),
        camera=_camera(base),
    )
    return None if facts.is_empty else facts


def _taken_at(exif_ifd: Mapping[int, object]) -> DateWithPrecision | None:
    raw = _text(exif_ifd.get(ExifTags.Base.DateTimeOriginal))
    if raw is None:
        return None
    try:
        # strptime without a %z yields a naive value; the zone is attached below.
        local = datetime.strptime(raw, EXIF_DATETIME_FORMAT)  # noqa: DTZ007  # reason: EXIF time is zone-less; tzinfo is set on the next line
    except ValueError:
        return None
    zone = _zone(_text(exif_ifd.get(ExifTags.Base.OffsetTimeOriginal)))
    try:
        return _TAKEN_AT_ADAPTER.validate_python(
            {"value": local.replace(tzinfo=zone), "precision": DatePrecision.EXACT}
        )
    except PydanticValidationError:
        return None


def _zone(raw: str | None) -> timezone:
    # Assumed UTC when the offset is absent or unreadable: proposed, Q-M11.
    match = OFFSET_PATTERN.match(raw) if raw is not None else None
    if match is None:
        return UTC
    offset = timedelta(hours=int(match["hours"]), minutes=int(match["minutes"]))
    if offset > MAX_OFFSET:
        return UTC
    return timezone(-offset if match["sign"] == "-" else offset)


def _location(gps_ifd: Mapping[int, object]) -> Coordinates | None:
    latitude = _degrees(
        gps_ifd.get(ExifTags.GPS.GPSLatitude),
        gps_ifd.get(ExifTags.GPS.GPSLatitudeRef),
        negative_ref="S",
        positive_ref="N",
    )
    longitude = _degrees(
        gps_ifd.get(ExifTags.GPS.GPSLongitude),
        gps_ifd.get(ExifTags.GPS.GPSLongitudeRef),
        negative_ref="W",
        positive_ref="E",
    )
    if latitude is None or longitude is None:
        return None
    try:
        return Coordinates(longitude=longitude, latitude=latitude)
    except PydanticValidationError:
        return None


def _degrees(
    value: object, reference: object, *, negative_ref: str, positive_ref: str
) -> float | None:
    ref = _text(reference)
    if ref is None or ref.upper() not in {negative_ref, positive_ref}:
        # Without a hemisphere the sign is unknown, so the position is unusable.
        return None
    if not isinstance(value, tuple) or len(value) != DMS_PARTS:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    total = degrees + minutes / MINUTES_PER_DEGREE + seconds / SECONDS_PER_DEGREE
    if not math.isfinite(total):
        return None
    return -total if ref.upper() == negative_ref else total


def _camera(base: Mapping[int, object]) -> str | None:
    make = _text(base.get(ExifTags.Base.Make))
    model = _text(base.get(ExifTags.Base.Model))
    parts = [part for part in (make, model) if part]
    if make and model and model.lower().startswith(make.lower()):
        # Many vendors repeat the make in the model ("Canon" + "Canon EOS 80D").
        parts = [model]
    if not parts:
        return None
    try:
        return _CAMERA_ADAPTER.validate_python(" ".join(parts)[:CAMERA_MAX_LENGTH])
    except PydanticValidationError:
        return None


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    if not isinstance(value, str):
        return None
    # EXIF ASCII values are NUL-terminated and often space-padded.
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


class PillowExifReader:
    """``ExifReader`` parsing a stored original's EXIF block with Pillow.

    Implements: Adapter.
    """

    def __init__(self, read_object: ObjectReader) -> None:
        """Create the reader.

        Args:
            read_object: Reads a whole original, for example
                ``S3StoragePort.read_original``.
        """
        self._read_object = read_object

    async def read(self, key: str) -> ExifFacts | None:
        """Return the EXIF facts of the original at ``key``.

        Parsing is CPU-bound but bounded by ``MAX_MEDIA_BYTES`` and only touches
        the header, so it runs inline rather than in a thread.

        Args:
            key: The original's object key.

        Returns:
            The facts, or ``None`` if the object is absent or has no readable EXIF.
        """
        data = await self._read_object(key)
        return None if data is None else parse_exif(data)
