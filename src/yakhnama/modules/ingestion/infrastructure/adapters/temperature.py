"""Pipeline for the synthetic hourly air-temperature fixture CSV.

The layout is Yakhnama's own (the fixture is synthetic, ``data/reference/
datasets.yaml`` entry ``fixture.temperature_sample``), so nothing here describes a
real publisher. A real station source needs its own pipeline, with the column
contract, units, time zone, quality vocabulary and duplicate rule taken from the
publisher's documentation.

Column contract (``EXPECTED_COLUMNS``, in this order, UTF-8, comma-separated, one
header line and no comment lines):

- ``station_code`` - the station's code; required.
- ``station_name`` - the station's name; optional.
- ``longitude``, ``latitude`` - WGS84 decimal degrees; both or neither.
- ``elevation_m`` - height above sea level in metre; optional.
- ``observed_at`` - ISO 8601 instant **with** a UTC offset; stored in UTC at hour
  precision because the series is hourly.
- ``air_temperature_c`` - degrees Celsius; empty when the value is missing.
- ``quality`` - ``good``, ``suspect``, ``missing`` or ``estimated``.

Conversion: kelvin = Celsius + 273.15, computed in decimal arithmetic and rounded to
three decimals (1 mK, far below any thermometer's resolution) with round-half-even
(**proposed**; unbiased over long series), so a stored value is the exact decimal
result rather than a binary floating-point artefact.

Quality mapping: the four codes map one to one to ``QualityFlag``. An unknown code
maps to ``suspect`` with a warning (the value is kept, flagged as not to be trusted).
An empty value is stored as ``missing`` whatever the code says, with a warning if the
code said otherwise; a value with the code ``missing`` is contradictory and rejected.

Duplicates: the template default (the first row wins) applies; the fixture has none.

Patterns: Template Method, Anti-Corruption Layer.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.ingestion.application.pipeline import (
    IngestionPipeline,
    ObservationDraft,
    ValidationCollector,
)
from yakhnama.modules.ingestion.application.ports import RawPayload
from yakhnama.modules.ingestion.domain.value_objects import QualityFlag, StationRef
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

EXPECTED_COLUMNS: Final = (
    "station_code",
    "station_name",
    "longitude",
    "latitude",
    "elevation_m",
    "observed_at",
    "air_temperature_c",
    "quality",
)
"""The header the fixture must have, in order."""

VARIABLE: Final = "air_temperature"
"""The only variable in the fixture; stored in kelvin (``VARIABLES``)."""

CELSIUS_TO_KELVIN_OFFSET: Final = Decimal("273.15")
KELVIN_DECIMALS: Final = 3
_KELVIN_QUANTUM: Final = Decimal(1).scaleb(-KELVIN_DECIMALS)

QUALITY_CODES: Final[Mapping[str, QualityFlag]] = MappingProxyType(
    {flag.value: flag for flag in QualityFlag}
)
"""Source quality code to ``QualityFlag``; the fixture uses Yakhnama's own codes."""

UNKNOWN_QUALITY_FLAG: Final = QualityFlag.SUSPECT
"""What an unknown quality code maps to (**proposed**): keep the value, distrust it."""

_QUOTED_CODE_MAX_LENGTH: Final = 32


class TemperatureRow(BaseModel):
    """One CSV row as written (text, degrees Celsius) plus its mapped quality.

    Implements: Anti-Corruption Layer (source-shaped model).

    Attributes:
        row_number: 1-based data row number (the header is row 0).
        station_code: Station code text.
        station_name: Station name text.
        longitude: Longitude text.
        latitude: Latitude text.
        elevation_m: Elevation text, metre.
        observed_at: ISO 8601 instant text.
        air_temperature_c: Temperature text, degrees Celsius; empty if missing.
        quality: The quality code as written.
        mapped_quality: ``quality`` mapped by ``parse``, so the mapping and its
            warning happen once per row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int
    station_code: str
    station_name: str
    longitude: str
    latitude: str
    elevation_m: str
    observed_at: str
    air_temperature_c: str
    quality: str
    mapped_quality: QualityFlag


def _reference(row_number: int) -> str:
    return f"row {row_number}"


def _optional(text: str) -> str | None:
    stripped = text.strip()
    return stripped or None


def _decimal(text: str, field: str) -> Decimal:
    try:
        number = Decimal(text.strip())
    except InvalidOperation as error:
        message = f"{field}: not a number"
        raise ValueError(message) from error
    if not number.is_finite():
        message = f"{field}: must be a finite number"
        raise ValueError(message)
    return number


def celsius_to_kelvin(celsius: str) -> float:
    """Convert a Celsius text value to kelvin, rounded to ``KELVIN_DECIMALS``.

    Args:
        celsius: The value as written, for example ``"-4.8"``.

    Returns:
        The value in kelvin, for example ``268.35``; halves round to even.

    Raises:
        ValueError: If the text is not a finite number or lies below absolute zero.
    """
    kelvin = _decimal(celsius, "air_temperature_c") + CELSIUS_TO_KELVIN_OFFSET
    if kelvin < 0:
        message = "air_temperature_c: below absolute zero"
        raise ValueError(message)
    return float(kelvin.quantize(_KELVIN_QUANTUM, rounding=ROUND_HALF_EVEN))


def parse_observed_at(text: str) -> DateWithPrecision:
    """Parse an ISO 8601 instant with an offset into an hourly UTC moment.

    Args:
        text: The instant as written, for example ``2026-01-01T05:00:00+05:00``.

    Returns:
        The instant in UTC at ``hour`` precision.

    Raises:
        ValueError: If the text is not ISO 8601 or has no UTC offset; a local time
            without an offset is ambiguous and never guessed.
    """
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError as error:
        message = "observed_at: not an ISO 8601 date and time"
        raise ValueError(message) from error
    if moment.utcoffset() is None:
        message = "observed_at: a UTC offset is required"
        raise ValueError(message)
    return DateWithPrecision(value=moment, precision=DatePrecision.HOUR)


def _location(row: TemperatureRow) -> Coordinates | None:
    longitude, latitude = _optional(row.longitude), _optional(row.latitude)
    if longitude is None and latitude is None:
        return None
    if longitude is None or latitude is None:
        message = "longitude and latitude are given together or not at all"
        raise ValueError(message)
    return Coordinates.model_validate({"longitude": longitude, "latitude": latitude})


def _elevation(row: TemperatureRow) -> Measurement | None:
    elevation = _optional(row.elevation_m)
    if elevation is None:
        return None
    return Measurement(
        value=float(_decimal(elevation, "elevation_m")), unit=SiUnit.METRE
    )


class TemperatureCsvPipeline(IngestionPipeline[TemperatureRow]):
    """Ingest the synthetic hourly temperature CSV as ``air_temperature`` in kelvin.

    Implements: Template Method (fills ``parse`` and ``normalise``).
    """

    def parse(
        self, payload: RawPayload, collector: ValidationCollector
    ) -> Sequence[TemperatureRow]:
        """Read the CSV; a row with the wrong number of fields is rejected.

        Args:
            payload: The fetched bytes.
            collector: Where rejected rows and quality warnings go.

        Returns:
            One row per readable data row, in file order.

        Raises:
            UnicodeDecodeError: If the bytes are not UTF-8.
            ValueError: If the header is not ``EXPECTED_COLUMNS``; a changed layout
                fails the run rather than being guessed.
            csv.Error: If the CSV itself is broken (for example an unclosed quote).
        """
        reader = csv.DictReader(io.StringIO(payload.content.decode("utf-8")))
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            message = "the CSV header is not the expected temperature layout"
            raise ValueError(message)
        rows: list[TemperatureRow] = []
        for row_number, record in enumerate(reader, start=1):
            # DictReader puts surplus fields under None and fills missing ones
            # with None; either means the row does not fit the layout.
            if None in record or any(value is None for value in record.values()):
                collector.reject(
                    "parse",
                    _reference(row_number),
                    [f"expected {len(EXPECTED_COLUMNS)} fields"],
                )
                continue
            fields = {column: record[column] for column in EXPECTED_COLUMNS}
            rows.append(
                TemperatureRow(
                    row_number=row_number,
                    mapped_quality=self._map_quality(
                        fields["quality"], fields["air_temperature_c"], row_number
                    ),
                    **fields,
                )
            )
        return rows

    def normalise(self, row: TemperatureRow) -> ObservationDraft:
        """Convert a row to kelvin, UTC and a ``StationRef``.

        Args:
            row: One parsed row.

        Returns:
            The draft; ``value`` is ``None`` with quality ``missing`` when the
            temperature is empty.

        Raises:
            ValueError: If a number, the instant or the coordinates are malformed
                or out of range (``pydantic.ValidationError`` included).
        """
        value = (
            None
            if _optional(row.air_temperature_c) is None
            else Measurement(
                value=celsius_to_kelvin(row.air_temperature_c), unit=SiUnit.KELVIN
            )
        )
        station = StationRef(
            code=row.station_code.strip(),
            name=_optional(row.station_name),
            location=_location(row),
            elevation=_elevation(row),
        )
        return ObservationDraft(
            station=station,
            variable=VARIABLE,
            value=value,
            observed_at=parse_observed_at(row.observed_at),
            quality=QualityFlag.MISSING if value is None else row.mapped_quality,
        )

    def reference_for(self, row: TemperatureRow, index: int) -> str:
        """Name a row by its data row number, as ``parse`` does.

        Args:
            row: The row.
            index: Its position among parsed rows; unused, because rejected rows
                would shift it away from the file.

        Returns:
            ``row <n>``.
        """
        return _reference(row.row_number)

    def _map_quality(self, code: str, value: str, row_number: int) -> QualityFlag:
        stripped = code.strip()
        flag = QUALITY_CODES.get(stripped)
        if _optional(value) is None:
            if flag is not QualityFlag.MISSING:
                self.collector.warn(
                    "parse",
                    "the value is empty, so the quality is recorded as 'missing'",
                    reference=_reference(row_number),
                )
            return QualityFlag.MISSING
        if flag is None:
            self.collector.warn(
                "parse",
                f"unknown quality code '{stripped[:_QUOTED_CODE_MAX_LENGTH]}' "
                f"recorded as '{UNKNOWN_QUALITY_FLAG.value}'",
                reference=_reference(row_number),
            )
            return UNKNOWN_QUALITY_FLAG
        return flag
