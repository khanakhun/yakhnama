"""Value objects of the ``geography`` bounded context.

Administrative levels, stable place codes, script codes, multilingual place names and
WGS84 place geometry. Several of these encode choices that are **proposed defaults, not
domain facts** (the ``division`` level, the code scheme, the script list); each one is
marked where it is defined and listed in ``docs/data-dictionary/geography.md``.

Patterns: Value Object.
"""

import math
import unicodedata
from collections.abc import Iterator
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from geojson_pydantic import MultiPolygon, Point, Polygon
from geojson_pydantic.types import Position, Position2D
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    LanguageCode,
)

# --------------------------------------------------------------------------- #
# Administrative levels                                                       #
# --------------------------------------------------------------------------- #


class AdminLevel(StrEnum):
    """Level of a place in the administrative hierarchy (glossary: Place).

    Members are declared from the top of the hierarchy down, and that declaration order
    *is* the ordering: ``country`` is the highest level, ``village`` the lowest.

    ``DIVISION`` is a **proposed** addition to the glossary's hierarchy:
    Gilgit-Baltistan groups its districts into divisions, so the level exists between
    ``PROVINCE_OR_REGION`` and ``DISTRICT``. The maintainer has not yet confirmed it
    (open question in ``docs/data-dictionary/geography.md``).

    Implements: Value Object.
    """

    COUNTRY = "country"
    PROVINCE_OR_REGION = "province_or_region"
    DIVISION = "division"
    DISTRICT = "district"
    TEHSIL = "tehsil"
    UNION_COUNCIL = "union_council"
    VILLAGE = "village"

    @property
    def rank(self) -> int:
        """Return the depth of this level: 0 for ``country``, growing downwards.

        Returns:
            The zero-based position of the level in the hierarchy.
        """
        return _LEVEL_ORDER.index(self)

    def is_below(self, other: "AdminLevel") -> bool:
        """Tell whether this level is strictly lower in the hierarchy than ``other``.

        Args:
            other: The level to compare against.

        Returns:
            ``True`` if this level is nested somewhere under ``other``; ``False`` for
            the same level or a higher one.
        """
        return self.rank > other.rank

    def can_be_child_of(self, parent: "AdminLevel | None") -> bool:
        """Tell whether a place at this level may have a parent at ``parent``.

        The rule is a **proposed default**: a country has no parent, every other level
        has a parent at a *strictly higher* level, and gaps are allowed (a village may
        sit directly under a district) because tehsils and union councils are not
        recorded everywhere.

        Args:
            parent: The parent's level, or ``None`` for a place without a parent.

        Returns:
            ``True`` if the combination is allowed.
        """
        if parent is None:
            return self is AdminLevel.COUNTRY
        return self.is_below(parent)


# Built once so ``rank`` is an index lookup; ``StrEnum`` iterates in declaration order.
_LEVEL_ORDER: tuple[AdminLevel, ...] = tuple(AdminLevel)


# --------------------------------------------------------------------------- #
# Codes                                                                       #
# --------------------------------------------------------------------------- #

PLACE_CODE_PATTERN = r"^[a-z0-9][a-z0-9_.-]{1,63}$"

PlaceCode = Annotated[str, StringConstraints(pattern=PLACE_CODE_PATTERN)]
"""A stable machine code for a place, such as ``pk.gb.hunza`` (**proposed** scheme).

Lower-case ASCII letters, digits, ``_``, ``.`` and ``-``; 2 to 64 characters, starting
with a letter or digit. The dotted "parent path" in the example is a convention for
reference data, not a rule the domain enforces, so a place keeps its code if it is ever
re-parented. Codes are never reused for another place.
"""


class ScriptCode(StrEnum):
    """ISO 15924 script of a place name (**proposed** list).

    ``LATN`` covers English and romanised forms; ``ARAB`` the Perso-Arabic script of
    Urdu and most written forms of the regional languages; ``TIBT`` the Tibetan script
    occasionally used for Balti. Further scripts are added only when a sourced name
    needs them.

    Implements: Value Object.
    """

    LATN = "Latn"
    ARAB = "Arab"
    TIBT = "Tibt"


PLACE_VERSION_MAX = 2_147_483_647
PlaceVersion = Annotated[int, Field(ge=1, le=PLACE_VERSION_MAX)]
"""Optimistic-concurrency version of a place: 1 when created, +1 per change.

The upper bound is the largest signed 32-bit integer, so it fits a PostgreSQL
``integer`` column.
"""

REASON_MAX_LENGTH = 500
StatusReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=REASON_MAX_LENGTH
    ),
]
"""Why a place was retired or merged, 1 to 500 characters after stripping."""

PlaceStatus = Literal["active", "merged", "retired"]
"""Lifecycle of a place. ``merged`` and ``retired`` are final."""

# --------------------------------------------------------------------------- #
# Place names                                                                 #
# --------------------------------------------------------------------------- #

PLACE_NAME_MAX_LENGTH = 200


def _normalise_name_text(value: object) -> object:
    # NFC so that the same visible name typed with precomposed or combining characters
    # (common with diacritics such as "Hunzā" and with Perso-Arabic input methods) is
    # one string, which the duplicate-name invariant relies on; strip because leading
    # or trailing spaces are never part of a name.
    if not isinstance(value, str):
        return value
    return unicodedata.normalize("NFC", value).strip()


PlaceNameText = Annotated[
    str,
    BeforeValidator(_normalise_name_text),
    StringConstraints(min_length=1, max_length=PLACE_NAME_MAX_LENGTH),
]
"""Name text, NFC-normalised and stripped, 1 to 200 characters."""

PlaceNameKind = Literal["official", "alternative", "historical", "transliteration"]
"""What role a name plays: the official form, another current form, a former name, or
a transliteration of another name into a different script."""

NameKey = tuple[str, str, ScriptCode | None]
"""The identity of a name inside one place: ``(text, language, script)``."""


class PlaceName(BaseModel):
    """One name for a place in one language and script (glossary: Place name).

    Implements: Value Object.

    Attributes:
        text: The name, NFC-normalised and stripped, 1 to 200 characters.
        language: BCP 47 language code (``en``, ``ur``, ``scl``, ...), normalised.
        script: ISO 15924 script, or ``None`` when not recorded. If ``language``
            carries a script subtag (``ur-Arab``) the two must agree.
        kind: Role of the name, see ``PlaceNameKind``.
        is_preferred: Whether this is the name to display for ``language``; a place
            has at most one preferred name per language.
        source_id: Identifier of the provenance ``Source`` the name comes from, or
            ``None`` until one is linked. Only the id is stored: the provenance module
            arrives in Phase 3.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: PlaceNameText
    language: LanguageCode
    script: ScriptCode | None = None
    kind: PlaceNameKind = "official"
    is_preferred: bool = False
    source_id: EntityId | None = None

    @model_validator(mode="after")
    def _check_script_agrees_with_language(self) -> Self:
        _, separator, subtag = self.language.partition("-")
        if separator and self.script is not None and subtag != self.script.value:
            message = (
                f"script {self.script.value!r} contradicts the script subtag of "
                f"language {self.language!r}"
            )
            raise ValueError(message)
        return self

    @property
    def key(self) -> NameKey:
        """Return the ``(text, language, script)`` identity of this name.

        Returns:
            The tuple two names of one place must never share.
        """
        return (self.text, self.language, self.script)

    def with_preference(self, *, is_preferred: bool) -> Self:
        """Return this name with ``is_preferred`` set.

        Args:
            is_preferred: The new preference flag.

        Returns:
            A new ``PlaceName``; this instance is unchanged.
        """
        return self.model_validate(
            {**_field_values(self), "is_preferred": is_preferred}
        )


def _field_values(model: BaseModel) -> dict[str, object]:
    # Field values as they are (nested models stay models), so re-validation does not
    # round-trip through a JSON-like dump.
    return {name: getattr(model, name) for name in type(model).model_fields}


# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #

PlaceGeoJson = Annotated[Point | Polygon | MultiPolygon, Field(discriminator="type")]
"""A GeoJSON Point, Polygon or MultiPolygon; the ``type`` member selects the class."""

GeometryType = Literal["Point", "Polygon", "MultiPolygon"]

_MAX_POSITIONS = 1_000_000
_TWO_DIMENSIONS = 2
# WGS84 bounds in decimal degrees, the same ranges ``Coordinates`` enforces.
_MAX_ABS_LONGITUDE = 180.0
_MAX_ABS_LATITUDE = 90.0


class PlaceGeometry(BaseModel):
    """The footprint of a place in WGS84 (EPSG:4326), longitude first.

    ``geojson-pydantic`` checks the GeoJSON structure (closed rings, at least four
    positions per ring) but not that positions are finite and inside WGS84 bounds, and
    it accepts empty polygons; this wrapper adds those checks. Positions must be
    two-dimensional: altitude has no meaning for an administrative footprint and would
    be dropped silently by a two-dimensional store.

    ``geojson-pydantic`` models are mutable, so the wrapped geometry is copied on the
    way in: a caller changing its own instance afterwards cannot change a place.

    Implements: Value Object.

    Attributes:
        geojson: The geometry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    SRID: ClassVar[int] = 4326

    geojson: PlaceGeoJson

    @field_validator("geojson", mode="before")
    @classmethod
    def _copy_geometry(cls, value: object) -> object:
        if isinstance(value, Point | Polygon | MultiPolygon):
            return value.model_dump()
        return value

    @field_validator("geojson", mode="after")
    @classmethod
    def _check_positions(
        cls, geojson: Point | Polygon | MultiPolygon
    ) -> Point | Polygon | MultiPolygon:
        count = 0
        for position in _positions(geojson):
            count += 1
            _check_position(position)
        if count == 0:
            message = "geometry must contain at least one position"
            raise ValueError(message)
        if count > _MAX_POSITIONS:
            message = f"geometry has more than {_MAX_POSITIONS} positions"
            raise ValueError(message)
        return geojson

    @property
    def geometry_type(self) -> GeometryType:
        """Return the GeoJSON type name of the wrapped geometry.

        Returns:
            ``"Point"``, ``"Polygon"`` or ``"MultiPolygon"``.
        """
        return self.geojson.type

    def bounding_box(self) -> BoundingBox:
        """Return the smallest WGS84 box containing every position.

        Returns:
            The tight bounding box; for a point, a box of zero size.
        """
        return BoundingBox.from_coordinates(
            Coordinates(longitude=position.longitude, latitude=position.latitude)
            for position in _positions(self.geojson)
        )

    @classmethod
    def from_coordinates(cls, coordinates: Coordinates) -> Self:
        """Wrap a point.

        Args:
            coordinates: The WGS84 position.

        Returns:
            A point geometry.
        """
        return cls(geojson=coordinates.to_geojson_point())

    @classmethod
    def from_bounding_box(cls, box: BoundingBox) -> Self:
        """Return the rectangle ``box`` as a polygon geometry.

        Args:
            box: The rectangle; a zero-width or zero-height box gives a degenerate
                polygon, which GeoJSON allows.

        Returns:
            A polygon geometry with one closed exterior ring.
        """
        west, south = box.min_longitude, box.min_latitude
        east, north = box.max_longitude, box.max_latitude
        ring: list[Position] = [
            Position2D(longitude=west, latitude=south),
            Position2D(longitude=east, latitude=south),
            Position2D(longitude=east, latitude=north),
            Position2D(longitude=west, latitude=north),
            Position2D(longitude=west, latitude=south),
        ]
        return cls(geojson=Polygon(type="Polygon", coordinates=[ring]))


def _positions(geojson: Point | Polygon | MultiPolygon) -> Iterator[Position]:
    if isinstance(geojson, Point):
        yield geojson.coordinates
        return
    # A Polygon is a list of rings; a MultiPolygon a list of such lists.
    polygons = (
        [geojson.coordinates] if isinstance(geojson, Polygon) else geojson.coordinates
    )
    for polygon in polygons:
        for ring in polygon:
            yield from ring


def _check_position(position: Position) -> None:
    if len(position) != _TWO_DIMENSIONS:
        message = "positions must be two-dimensional (longitude, latitude)"
        raise ValueError(message)
    longitude, latitude = position.longitude, position.latitude
    if not (math.isfinite(longitude) and abs(longitude) <= _MAX_ABS_LONGITUDE):
        message = f"longitude {longitude} is outside WGS84 bounds [-180, 180]"
        raise ValueError(message)
    if not (math.isfinite(latitude) and abs(latitude) <= _MAX_ABS_LATITUDE):
        message = f"latitude {latitude} is outside WGS84 bounds [-90, 90]"
        raise ValueError(message)
