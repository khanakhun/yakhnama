"""Immutable value objects shared by every bounded context.

All models are frozen, reject unknown fields and bound every string and number, so a
value that exists is a valid value. Validators raise ``ValueError`` so Pydantic reports
every problem at once as a ``pydantic.ValidationError``; plain factory methods that run
outside a validator raise the kernel's ``ValidationError``.

Patterns: Value Object.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Self

from geojson_pydantic import Point
from geojson_pydantic.types import Position2D
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from yakhnama.shared_kernel.errors import ValidationError

# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #

Longitude = Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]
_TWO_DIMENSIONS = 2


class Coordinates(BaseModel):
    """A point on the WGS84 ellipsoid (EPSG:4326), in decimal degrees.

    Longitude comes first, as in GeoJSON (RFC 7946), to avoid the classic latitude and
    longitude swap at every boundary.

    Implements: Value Object.

    Attributes:
        longitude: Degrees east of Greenwich, from -180 to 180.
        latitude: Degrees north of the equator, from -90 to 90.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    longitude: Longitude
    latitude: Latitude

    def to_geojson_point(self) -> Point:
        """Return the coordinates as a GeoJSON ``Point``.

        Returns:
            A two-dimensional ``geojson_pydantic.Point``.
        """
        return Point(
            type="Point",
            coordinates=Position2D(longitude=self.longitude, latitude=self.latitude),
        )

    @classmethod
    def from_geojson_point(cls, point: Point) -> Self:
        """Build coordinates from a two-dimensional GeoJSON ``Point``.

        Args:
            point: The GeoJSON point.

        Returns:
            The equivalent coordinates.

        Raises:
            ValidationError: If the point carries an altitude, which these
                coordinates cannot hold and must not silently drop.
        """
        if len(point.coordinates) != _TWO_DIMENSIONS:
            message = "a three-dimensional point cannot become two-dimensional"
            raise ValidationError(message)
        return cls(
            longitude=point.coordinates.longitude,
            latitude=point.coordinates.latitude,
        )


class BoundingBox(BaseModel):
    """An axis-aligned WGS84 rectangle, edges included.

    Boxes crossing the antimeridian (180°) are rejected rather than modelled, because
    no Gilgit-Baltistan use case needs them yet and a half-supported wrap-around would
    silently return wrong search results; this is an open question for the maintainer.

    Implements: Value Object.

    Attributes:
        min_longitude: Western edge.
        min_latitude: Southern edge.
        max_longitude: Eastern edge, never west of ``min_longitude``.
        max_latitude: Northern edge, never south of ``min_latitude``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_longitude: Longitude
    min_latitude: Latitude
    max_longitude: Longitude
    max_latitude: Latitude

    @model_validator(mode="after")
    def _check_edges(self) -> Self:
        if self.min_longitude > self.max_longitude:
            message = (
                "min_longitude is east of max_longitude; boxes crossing the "
                "antimeridian are not supported"
            )
            raise ValueError(message)
        if self.min_latitude > self.max_latitude:
            message = "min_latitude is north of max_latitude"
            raise ValueError(message)
        return self

    def contains(self, coordinates: Coordinates) -> bool:
        """Tell whether a point lies inside the box or on its edge.

        Args:
            coordinates: The point to test.

        Returns:
            ``True`` if the point is inside or on the boundary.
        """
        return (
            self.min_longitude <= coordinates.longitude <= self.max_longitude
            and self.min_latitude <= coordinates.latitude <= self.max_latitude
        )

    @classmethod
    def from_coordinates(cls, coordinates: Iterable[Coordinates]) -> Self:
        """Return the smallest box containing every given point.

        Args:
            coordinates: At least one point.

        Returns:
            The tightest bounding box.

        Raises:
            ValidationError: If ``coordinates`` is empty.
        """
        points = list(coordinates)
        if not points:
            message = "a bounding box needs at least one point"
            raise ValidationError(message)
        longitudes = [point.longitude for point in points]
        latitudes = [point.latitude for point in points]
        return cls(
            min_longitude=min(longitudes),
            min_latitude=min(latitudes),
            max_longitude=max(longitudes),
            max_latitude=max(latitudes),
        )


# --------------------------------------------------------------------------- #
# Measurements                                                                #
# --------------------------------------------------------------------------- #


class SiUnit(StrEnum):
    """Units the platform stores measurements in: SI base or derived, plus counts.

    Values are the snake_case unit names used in storage and in the open dataset.
    Currency is deliberately absent: money is not an SI quantity and needs its own
    value object (currency, reference year) when economic losses are modelled.
    Two derived units are compound: ``cubic_metre_per_second`` for discharge (for
    example a flood's peak flow) and ``metre_per_second`` for velocity (for example a
    glacier's surge speed or a debris flow's front speed).

    Implements: Value Object.
    """

    METRE = "metre"
    SQUARE_METRE = "square_metre"
    CUBIC_METRE = "cubic_metre"
    CUBIC_METRE_PER_SECOND = "cubic_metre_per_second"
    METRE_PER_SECOND = "metre_per_second"
    KILOGRAM = "kilogram"
    SECOND = "second"
    KELVIN = "kelvin"
    COUNT = "count"


KNOWN_UNITS: frozenset[str] = frozenset(unit.value for unit in SiUnit)
"""Every unit a ``Measurement`` accepts. Extended only by adding a ``SiUnit`` member."""

_UNIT_PATTERN = r"^[a-z][a-z0-9_]*$"


def is_known_unit(unit: str) -> bool:
    """Tell whether ``unit`` is registered in ``KNOWN_UNITS``.

    Args:
        unit: A unit name such as ``"cubic_metre"``.

    Returns:
        ``True`` if measurements may be stored in this unit.
    """
    return unit in KNOWN_UNITS


def _require_known_unit(unit: str) -> str:
    if not is_known_unit(unit):
        message = f"unknown unit {unit!r}; known units: {sorted(KNOWN_UNITS)}"
        raise ValueError(message)
    return unit


# ``Unit`` is a string rather than the ``SiUnit`` enum so storage, the dataset and the
# impact metric registry carry plain names and the registry can later become data
# without changing every field type; the check against ``KNOWN_UNITS`` keeps it closed
# today.
Unit = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=_UNIT_PATTERN),
    AfterValidator(_require_known_unit),
]


class Measurement(BaseModel):
    """A finite quantity in a registered SI unit.

    ``float`` rather than ``Decimal``: measured physical quantities (volumes, areas,
    lengths) are approximate by nature and are aggregated numerically; exact decimal
    arithmetic belongs to money, which is not a ``Measurement``.

    Implements: Value Object.

    Attributes:
        value: The magnitude, finite (no NaN or infinity).
        unit: One of ``KNOWN_UNITS``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(allow_inf_nan=False)
    unit: Unit


# --------------------------------------------------------------------------- #
# Time                                                                        #
# --------------------------------------------------------------------------- #


class DatePrecision(StrEnum):
    """How precisely a moment is known.

    Implements: Value Object.
    """

    EXACT = "exact"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    SEASON = "season"
    YEAR = "year"


# Proposed default, not a domain fact: seasons are the meteorological quarters starting
# in December, March, June and September. Local Gilgit-Baltistan seasons may differ and
# are an open question for the maintainer.
_SEASON_START_MONTHS = (12, 3, 6, 9)
_MONTHS_PER_SEASON = 3
_DECEMBER = 12

# Year 1 is excluded so a season floor (January of year 2 floors to December of year
# 1) is always representable; no hazard record predates it.
EARLIEST_SUPPORTED_INSTANT = datetime(2, 1, 1, tzinfo=UTC)


class DateWithPrecision(BaseModel):
    """A UTC instant together with how precisely it is known.

    Implements: Value Object.

    Attributes:
        value: The instant, normalised to UTC; naive datetimes are rejected.
        precision: How much of ``value`` is meaningful.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: AwareDatetime
    precision: DatePrecision

    @field_validator("value", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        try:
            normalised = value.astimezone(UTC)
        except OverflowError as error:
            message = "instant is outside the representable UTC range"
            raise ValueError(message) from error
        if normalised < EARLIEST_SUPPORTED_INSTANT:
            message = f"instant must not be earlier than {EARLIEST_SUPPORTED_INSTANT}"
            raise ValueError(message)
        return normalised

    def truncate(self) -> Self:
        """Return the same precision with the instant floored to its start.

        Flooring happens in UTC (a proposed default: local Pakistan time, UTC+5, would
        change day boundaries, recorded as an open question). ``season`` floors to the
        first day of the meteorological quarter (December, March, June, September),
        also a proposed default.

        Returns:
            A new value whose instant is the start of its precision period; for
            ``exact`` the instant is unchanged.
        """
        return self.model_validate(
            {"value": _floor(self.value, self.precision), "precision": self.precision}
        )


def _floor(value: datetime, precision: DatePrecision) -> datetime:
    start_of_hour = value.replace(minute=0, second=0, microsecond=0)
    start_of_day = start_of_hour.replace(hour=0)
    start_of_month = start_of_day.replace(day=1)
    floors = {
        DatePrecision.EXACT: value,
        DatePrecision.HOUR: start_of_hour,
        DatePrecision.DAY: start_of_day,
        DatePrecision.MONTH: start_of_month,
        DatePrecision.YEAR: start_of_month.replace(month=1),
    }
    if precision is not DatePrecision.SEASON:
        return floors[precision]
    # ``month % 12`` maps December to 0, so each season is three consecutive values
    # (0-2, 3-5, 6-8, 9-11) and integer division picks its start month. January and
    # February belong to the season that started in the previous year's December.
    season_start = _SEASON_START_MONTHS[(value.month % _DECEMBER) // _MONTHS_PER_SEASON]
    is_previous_year = season_start == _DECEMBER and value.month != _DECEMBER
    year = value.year - 1 if is_previous_year else value.year
    return start_of_month.replace(year=year, month=season_start)


# --------------------------------------------------------------------------- #
# Languages                                                                   #
# --------------------------------------------------------------------------- #

_LANGUAGE_CODE_PATTERN = r"^[a-z]{2,3}(-[A-Z][a-z]{3})?$"
_LANGUAGE_CODE_MAX_LENGTH = 8


def _normalise_language_code(value: object) -> object:
    # Case is not significant in BCP 47; normalising to the canonical form (lower-case
    # language, title-case script) makes "UR-arab" and "ur-Arab" the same key.
    if not isinstance(value, str):
        return value
    language, separator, script = value.partition("-")
    return f"{language.lower()}{separator}{script.title()}"


LanguageCode = Annotated[
    str,
    BeforeValidator(_normalise_language_code),
    StringConstraints(
        min_length=2,
        max_length=_LANGUAGE_CODE_MAX_LENGTH,
        pattern=_LANGUAGE_CODE_PATTERN,
    ),
]
"""A BCP 47 subset: ISO 639 language (2-3 letters) plus an optional ISO 15924 script.

Examples: ``en``, ``ur``, ``ur-Arab``, ``scl-Latn``, ``bsk-Arab``. Regions, variants
and extensions are out of scope until a use case needs them.
"""

_LANGUAGE_CODE_REGEX = re.compile(_LANGUAGE_CODE_PATTERN)

# Labels, names and short descriptions; long-form text belongs in its own model.
LOCALIZED_TEXT_MAX_LENGTH = 4000
LOCALIZED_TEXT_MAX_LANGUAGES = 32
LocalizedString = Annotated[
    str,
    StringConstraints(min_length=1, max_length=LOCALIZED_TEXT_MAX_LENGTH),
]


class LocalizedText(BaseModel):
    """One text in several languages and scripts.

    The mapping is exposed read-only so a frozen value cannot be changed through it.

    Implements: Value Object.

    Attributes:
        texts: Text per language code, at least one entry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    texts: Mapping[LanguageCode, LocalizedString] = Field(
        min_length=1, max_length=LOCALIZED_TEXT_MAX_LANGUAGES
    )

    @field_validator("texts", mode="after")
    @classmethod
    def _freeze_texts(
        cls, texts: Mapping[LanguageCode, LocalizedString]
    ) -> Mapping[LanguageCode, LocalizedString]:
        return MappingProxyType(dict(texts))

    @field_serializer("texts")
    def _serialise_texts(
        self, texts: Mapping[LanguageCode, LocalizedString]
    ) -> dict[str, str]:
        return dict(texts)

    def __hash__(self) -> int:
        """Hash by content, as the generated hash cannot hash a mapping.

        Returns:
            A hash equal for equal texts.
        """
        return hash(frozenset(self.texts.items()))

    def get(self, language: str, fallback_order: Sequence[str] = ()) -> str | None:
        """Return the text in the first available language.

        Args:
            language: Preferred language code; normalised like ``LanguageCode``.
            fallback_order: Further codes to try, in order, when ``language`` is
                missing.

        Returns:
            The first text found, or ``None`` if no requested language is present.
        """
        for candidate in (language, *fallback_order):
            text = self.texts.get(str(_normalise_language_code(candidate)))
            if text is not None:
                return text
        return None


def is_language_code(value: str) -> bool:
    """Tell whether ``value`` is a valid ``LanguageCode`` once normalised.

    Args:
        value: A candidate such as ``"UR-arab"``.

    Returns:
        ``True`` if the normalised form matches the supported BCP 47 subset.
    """
    normalised = str(_normalise_language_code(value))
    return (
        len(normalised) <= _LANGUAGE_CODE_MAX_LENGTH
        and _LANGUAGE_CODE_REGEX.fullmatch(normalised) is not None
    )


# --------------------------------------------------------------------------- #
# Confidence                                                                  #
# --------------------------------------------------------------------------- #


class Confidence(StrEnum):
    """How much a source's figure can be trusted; always stored with the source.

    Implements: Value Object.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
