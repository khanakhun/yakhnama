"""Builders of small image files with EXIF blocks, for the adapter tests.

Everything is made in memory with Pillow's own ``Image.Exif``, so no fixture file
or third-party EXIF writer is needed.
"""

from collections.abc import Mapping
from io import BytesIO
from typing import Final

from PIL import ExifTags, Image

GILGIT_GPS: Final[Mapping[int, object]] = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (35.0, 55.0, 12.0),
    ExifTags.GPS.GPSLongitudeRef: "E",
    ExifTags.GPS.GPSLongitude: (74.0, 18.0, 0.0),
}
"""A position near Gilgit: 35.92 N, 74.30 E."""

MINIMAL_PDF: Final = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"/Author (Reporter Name)\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)
"""A tiny PDF whose body mentions an author, to show it is copied unchanged."""

MINIMAL_MP4: Final = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
"""The leading ``ftyp`` box of an MP4 file, enough for magic-byte detection."""


def build_exif(
    *,
    base: Mapping[int, object] | None = None,
    exif_ifd: Mapping[int, object] | None = None,
    gps: Mapping[int, object] | None = None,
) -> Image.Exif:
    """Return an EXIF block with the given IFD0, Exif-IFD and GPS entries.

    Args:
        base: IFD0 tags, for example ``Make`` and ``Orientation``.
        exif_ifd: Exif sub-IFD tags, for example ``DateTimeOriginal``.
        gps: GPS sub-IFD tags.

    Returns:
        The block, ready for ``tobytes``.
    """
    exif = Image.Exif()
    for tag, value in (base or {}).items():
        exif[tag] = value
    if exif_ifd:
        exif[ExifTags.IFD.Exif] = dict(exif_ifd)
    if gps:
        exif[ExifTags.IFD.GPSInfo] = dict(gps)
    return exif


def image_bytes(
    image_format: str,
    *,
    exif: Image.Exif | None = None,
    size: tuple[int, int] = (4, 2),
    mode: str = "RGB",
) -> bytes:
    """Return a solid-colour image file in ``image_format``.

    Args:
        image_format: A Pillow format name: ``JPEG``, ``PNG`` or ``WEBP``.
        exif: The EXIF block to embed, if any.
        size: Width and height in pixels.
        mode: The Pillow image mode.

    Returns:
        The encoded file.
    """
    output = BytesIO()
    image = Image.new(mode, size, "red" if mode != "P" else 1)
    if exif is None:
        image.save(output, format=image_format)
    else:
        image.save(output, format=image_format, exif=exif.tobytes())
    return output.getvalue()


def gps_photo(image_format: str = "JPEG") -> bytes:
    """Return a photo with a capture time, a camera and a GPS position.

    Args:
        image_format: A Pillow format name.

    Returns:
        The encoded file.
    """
    exif = build_exif(
        base={ExifTags.Base.Make: "Canon", ExifTags.Base.Model: "Canon EOS 80D"},
        exif_ifd={
            ExifTags.Base.DateTimeOriginal: "2025:07:14 09:30:00",
            ExifTags.Base.OffsetTimeOriginal: "+05:00",
        },
        gps=GILGIT_GPS,
    )
    return image_bytes(image_format, exif=exif)
