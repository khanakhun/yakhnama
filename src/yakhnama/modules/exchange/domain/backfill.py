"""The historical backfill import contract: one event and its claims per row.

Importers only tokenise. A CSV importer hands every record to
``ImportedEventDraft.from_flat_row`` as a mapping of column name to cell text; a
GeoJSON importer does the same with a feature's properties (as text) and its
geometry serialised as JSON under ``geometry``. The draft either comes back valid or
the row's issues come back, one per faulty column, each with the 1-based row number;
``from_flat_row`` never raises for bad cell content.

The column contract (names, meaning, format, required or not) is documented in
``docs/architecture/exchange.md`` and in ``docs/data-dictionary/exchange.md``. It is
**proposed** (Phase 4 plan §5) and versioned as ``BACKFILL_SCHEMA_VERSION``.

Rules shared by every cell: surrounding whitespace is ignored and an empty cell is
an absent value; timestamps are ISO 8601 with a UTC offset (never a bare number,
which Pydantic would read as a Unix time); numbers are plain decimals (no ``nan``,
``inf``, underscores or hexadecimal); free text follows the shared safe-text rules.
Issue messages never quote a cell, because cells are untrusted and may carry
personal data.

Patterns: Value Object.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from geojson_pydantic import Point
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.events.public import EventGeometry, EventPeriod
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import (
    ISSUE_FIELD_PATTERN,
    ISSUE_MESSAGE_MAX_LENGTH,
    RowIssue,
    SchemaVersion,
)
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.modules.impacts.public import (
    ClaimValue,
    CountValue,
    MeasurementValue,
    MetricCode,
    MonetaryValue,
    ValueKind,
)
from yakhnama.modules.provenance.public import Licence
from yakhnama.shared_kernel.text import is_forbidden_character, safe_text
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
    Latitude,
    Longitude,
    SiUnit,
)

BACKFILL_SCHEMA_VERSION: Final[SchemaVersion] = "1.0"
"""Version of the column contract below (**proposed** start)."""

# --------------------------------------------------------------------------- #
# Limits and text types                                                       #
# --------------------------------------------------------------------------- #

MAX_CLAIMS_PER_ROW: Final = 5
"""Claim slots per row, ``claim_1_*`` to ``claim_5_*`` (**proposed**)."""

MAX_PLACE_CODES_PER_ROW: Final = 20
PLACE_CODE_SEPARATOR: Final = ";"

CELL_MAX_LENGTH: Final = 10_000
NUMBER_CELL_MAX_LENGTH: Final = 64
GEOMETRY_CELL_MAX_LENGTH: Final = 1_000_000
"""Longest ``geometry`` cell: 1 MB of GeoJSON text (**proposed**)."""

# The bounds mirror the events and provenance types a draft becomes (EventTitle,
# EventSummary, Citation, SourceUrl, ClaimNote); their facades do not export the
# text aliases, so the numbers are repeated here and pinned by the unit tests.
IMPORT_TITLE_MIN_LENGTH: Final = 3
IMPORT_TITLE_MAX_LENGTH: Final = 200
IMPORT_SUMMARY_MAX_LENGTH: Final = 2000
IMPORT_CITATION_MAX_LENGTH: Final = 1000
IMPORT_NOTE_MAX_LENGTH: Final = 1000
IMPORT_URL_MAX_LENGTH: Final = 2048

ImportTitle = Annotated[
    str, *safe_text(IMPORT_TITLE_MAX_LENGTH, IMPORT_TITLE_MIN_LENGTH)
]
"""An imported event's title: 3 to 200 characters of single-line safe text."""

ImportSummary = Annotated[
    str, *safe_text(IMPORT_SUMMARY_MAX_LENGTH, allow_line_breaks=True)
]
"""An imported event's summary: 1 to 2000 characters, line breaks allowed."""

ImportCitation = Annotated[str, *safe_text(IMPORT_CITATION_MAX_LENGTH)]
"""How to cite a row's source: 1 to 1000 characters of single-line safe text."""

ImportClaimNote = Annotated[
    str, *safe_text(IMPORT_NOTE_MAX_LENGTH, allow_line_breaks=True)
]
"""A note on an imported claim: 1 to 1000 characters, line breaks allowed."""

_ALLOWED_URL_SCHEMES: Final = frozenset({"http", "https"})


def _check_url(value: str) -> str:
    # The same rules as provenance's SourceUrl, which the row's source becomes.
    if any(not "\x21" <= character <= "\x7e" for character in value):
        message = "url must be printable ASCII without spaces"
        raise ValueError(message)
    parts = urlsplit(value)
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        message = "url must use http or https"
        raise ValueError(message)
    if "@" in parts.netloc:
        message = "url must not contain user information (credentials)"
        raise ValueError(message)
    if not parts.hostname:
        message = "url must have a host"
        raise ValueError(message)
    try:
        _ = parts.port
    except ValueError as error:
        message = "url has an invalid port"
        raise ValueError(message) from error
    return value


ImportSourceUrl = Annotated[
    str,
    StringConstraints(min_length=1, max_length=IMPORT_URL_MAX_LENGTH),
    AfterValidator(_check_url),
]
"""Where a row's source is online: ``http`` or ``https``, at most 2048 characters."""

# --------------------------------------------------------------------------- #
# Columns                                                                     #
# --------------------------------------------------------------------------- #

EVENT_COLUMNS: Final = (
    "title",
    "hazard_type",
    "started_at",
    "started_at_precision",
    "ended_at",
    "ended_at_precision",
    "longitude",
    "latitude",
    "geometry",
    "place_codes",
    "summary",
    "source_citation",
    "source_url",
    "source_licence",
    "source_licence_text",
)
"""Event and source columns, in contract order."""

CLAIM_FIELDS: Final = (
    "metric_code",
    "value_kind",
    "value",
    "unit",
    "currency",
    "price_year",
    "confidence",
    "claimed_at",
    "claimed_at_precision",
    "note",
)
"""Fields of one claim slot; column ``claim_<n>_<field>`` for n = 1 to 5."""


def claim_column(slot: int, field: str) -> str:
    """Return the column name of one field of one claim slot.

    Args:
        slot: The claim slot, 1 to ``MAX_CLAIMS_PER_ROW``.
        field: One of ``CLAIM_FIELDS``.

    Returns:
        ``claim_<slot>_<field>``, for example ``claim_1_metric_code``.
    """
    return f"claim_{slot}_{field}"


CLAIM_COLUMNS: Final = tuple(
    claim_column(slot, field)
    for slot in range(1, MAX_CLAIMS_PER_ROW + 1)
    for field in CLAIM_FIELDS
)

BACKFILL_COLUMNS: Final = (*EVENT_COLUMNS, *CLAIM_COLUMNS)
"""Every contract column in order; exporters of the backfill layout write these."""

REQUIRED_COLUMNS: Final = (
    "title",
    "hazard_type",
    "started_at",
    "started_at_precision",
    "source_citation",
)
"""Columns every file header must contain; the others may be left out."""

_KNOWN_COLUMNS: Final = frozenset(BACKFILL_COLUMNS)


def require_backfill_header(columns: Sequence[str]) -> None:
    """Check a file's header against the column contract before any row is read.

    Args:
        columns: The header names, in file order.

    Raises:
        ImportContractError: If a required column is missing, a name is not a
            contract column, or a column appears more than once.
    """
    seen: set[str] = set()
    duplicated: list[str] = []
    for column in columns:
        if column in seen and column in _KNOWN_COLUMNS and column not in duplicated:
            duplicated.append(column)
        seen.add(column)
    missing = [column for column in REQUIRED_COLUMNS if column not in seen]
    unknown_count = sum(1 for column in seen if column not in _KNOWN_COLUMNS)
    if missing or unknown_count or duplicated:
        raise ImportContractError.for_header(missing, unknown_count, duplicated)


# --------------------------------------------------------------------------- #
# Drafts                                                                      #
# --------------------------------------------------------------------------- #


class ImportedSourceDraft(BaseModel):
    """The source one imported row cites, before it is registered in provenance.

    Implements: Value Object.

    Attributes:
        citation: How to cite it.
        url: Where it is online, if anywhere.
        licence: Its reuse terms, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: ImportCitation
    url: ImportSourceUrl | None = None
    licence: Licence | None = None


class ImportedClaimDraft(BaseModel):
    """One impact figure of an imported row, before it becomes an impact claim.

    Whether ``value`` suits the metric (kind, unit, currency) is checked by the
    application against the impact metric registry, not here.

    Implements: Value Object.

    Attributes:
        metric_code: The impact metric, for example ``deaths``.
        value: The figure with its unit or currency.
        confidence: How far the source's figure can be trusted.
        claimed_at: When the source stated the figure, with precision.
        note: A curator's note, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_code: MetricCode
    value: ClaimValue
    confidence: Confidence
    claimed_at: DateWithPrecision
    note: ImportClaimNote | None = None


class ImportedEventDraft(BaseModel):
    """One historical event with its source and claims, read from one import row.

    Invariants: a draft is located, by a geometry or at least one place code
    (**proposed**), and its place codes are distinct.

    Implements: Value Object.

    Attributes:
        title: Short name of the event.
        hazard_type: Hazard type code; whether it exists is checked by the
            application against the hazards registry.
        period: When it happened, each bound with its precision.
        geometry: Where it happened, if mapped.
        place_codes: Gazetteer places it concerns, as ``impacted`` places.
        summary: A curator's summary, if any.
        source: The source the row cites.
        claims: Up to ``MAX_CLAIMS_PER_ROW`` impact figures.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: ImportTitle
    hazard_type: HazardCode
    period: EventPeriod
    geometry: EventGeometry | None = None
    place_codes: tuple[PlaceCode, ...] = Field(
        default=(), max_length=MAX_PLACE_CODES_PER_ROW
    )
    summary: ImportSummary | None = None
    source: ImportedSourceDraft
    claims: tuple[ImportedClaimDraft, ...] = Field(
        default=(), max_length=MAX_CLAIMS_PER_ROW
    )

    @field_validator("place_codes", mode="after")
    @classmethod
    def _require_distinct_places(cls, codes: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(codes)) != len(codes):
            message = "place codes must be distinct"
            raise ValueError(message)
        return codes

    @model_validator(mode="after")
    def _check_location(self) -> Self:
        if self.geometry is None and not self.place_codes:
            message = "an imported event needs a geometry or a place code"
            raise ValueError(message)
        return self

    @classmethod
    def from_flat_row(
        cls, row_number: int, cells: Mapping[str, str]
    ) -> "ImportedEventDraft | tuple[RowIssue, ...]":
        """Read one row of the column contract.

        Args:
            row_number: 1-based number of the data row, used in every issue.
            cells: Column name to cell text; absent columns count as empty.

        Returns:
            The draft if the row is valid, otherwise every issue found, in column
            order, each naming its column (``None`` for a row-level problem).
        """
        return _RowReader(row_number, cells).read_event()

    def to_flat_row(self) -> dict[str, str]:
        """Write the draft as one row of the column contract.

        ``from_flat_row`` of the result returns an equal draft. A point is written
        as ``longitude`` and ``latitude``, any other geometry as GeoJSON in
        ``geometry``. Floats use ``repr`` so nothing is lost.

        Returns:
            Every column of ``BACKFILL_COLUMNS``, in order; absent values are
            empty strings.
        """
        cells = dict.fromkeys(BACKFILL_COLUMNS, "")
        cells |= {
            "title": self.title,
            "hazard_type": self.hazard_type,
            "place_codes": PLACE_CODE_SEPARATOR.join(self.place_codes),
            "summary": self.summary or "",
            **_moment_cells("started_at", self.period.started_at),
            **_moment_cells("ended_at", self.period.ended_at),
            **_geometry_cells(self.geometry),
            **_source_cells(self.source),
        }
        for slot, claim in enumerate(self.claims, start=1):
            cells |= _claim_cells(slot, claim)
        return cells


def _moment_cells(column: str, moment: DateWithPrecision | None) -> dict[str, str]:
    if moment is None:
        return {}
    return {column: moment.value.isoformat(), f"{column}_precision": moment.precision}


def _geometry_cells(geometry: EventGeometry | None) -> dict[str, str]:
    if geometry is None:
        return {}
    if isinstance(geometry.geojson, Point):
        point = geometry.centroid()
        return {"longitude": repr(point.longitude), "latitude": repr(point.latitude)}
    return {
        "geometry": json.dumps(
            geometry.geojson.model_dump(mode="json", exclude_none=True)
        )
    }


def _source_cells(source: ImportedSourceDraft) -> dict[str, str]:
    licence = source.licence
    return {
        "source_citation": source.citation,
        "source_url": source.url or "",
        "source_licence": "" if licence is None else licence.spdx_id or "",
        "source_licence_text": "" if licence is None else licence.custom_text or "",
    }


def _claim_cells(slot: int, claim: ImportedClaimDraft) -> dict[str, str]:
    value = claim.value
    cells = {
        "metric_code": claim.metric_code,
        "value_kind": value.value_kind.value,
        "confidence": claim.confidence.value,
        "note": claim.note or "",
        **_moment_cells("claimed_at", claim.claimed_at),
    }
    if isinstance(value, CountValue):
        cells |= {"value": str(value.count), "unit": value.unit_or_currency}
    elif isinstance(value, MeasurementValue):
        cells |= {
            "value": repr(value.measurement.value),
            "unit": value.measurement.unit,
        }
    else:
        cells |= {
            "value": str(value.amount),
            "currency": value.currency,
            "price_year": str(value.price_year),
        }
    return {claim_column(slot, field): text for field, text in cells.items()}


# --------------------------------------------------------------------------- #
# Row reader                                                                  #
# --------------------------------------------------------------------------- #

_TIMESTAMP_PATTERN: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(:[0-9]{2}(\.[0-9]{1,6})?)?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_DECIMAL_PATTERN: Final = re.compile(
    r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?"
)
_INTEGER_PATTERN: Final = re.compile(r"[0-9]+")
_ISSUE_FIELD_REGEX: Final = re.compile(ISSUE_FIELD_PATTERN)

_TITLE: Final[TypeAdapter[str]] = TypeAdapter(ImportTitle)
_HAZARD_CODE: Final[TypeAdapter[str]] = TypeAdapter(HazardCode)
_PLACE_CODE: Final[TypeAdapter[str]] = TypeAdapter(PlaceCode)
_SUMMARY: Final[TypeAdapter[str]] = TypeAdapter(ImportSummary)
_CITATION: Final[TypeAdapter[str]] = TypeAdapter(ImportCitation)
_URL: Final[TypeAdapter[str]] = TypeAdapter(ImportSourceUrl)
_METRIC_CODE: Final[TypeAdapter[str]] = TypeAdapter(MetricCode)
_VALUE_KIND: Final[TypeAdapter[ValueKind]] = TypeAdapter(ValueKind)
_CONFIDENCE: Final[TypeAdapter[Confidence]] = TypeAdapter(Confidence)
_NOTE: Final[TypeAdapter[str]] = TypeAdapter(ImportClaimNote)
_LONGITUDE: Final[TypeAdapter[float]] = TypeAdapter(Longitude)
_LATITUDE: Final[TypeAdapter[float]] = TypeAdapter(Latitude)
_CLAIM_VALUE: Final[TypeAdapter[CountValue | MeasurementValue | MonetaryValue]] = (
    TypeAdapter(ClaimValue)
)

_REQUIRED: Final = "a value is required"
_FALLBACK_MESSAGE: Final = "the value is invalid"

type LocationMapper = Callable[[tuple[int | str, ...]], str | None]


def _issue_message(text: str) -> str:
    # Pydantic's messages are fixed texts, but they are cleaned and cut anyway so a
    # RowIssue can always be built: from_flat_row must never raise.
    cleaned = "".join(
        " " if is_forbidden_character(character) else character for character in text
    ).strip()
    return cleaned[:ISSUE_MESSAGE_MAX_LENGTH] or _FALLBACK_MESSAGE


def _describe(error: PydanticValidationError) -> str:
    return "; ".join(detail["msg"] for detail in error.errors())


class _RowReader:
    """Reads one flat row cell by cell, collecting an issue per faulty column.

    Implements: Value Object (a short-lived parser of one row's values).
    """

    def __init__(self, row_number: int, cells: Mapping[str, str]) -> None:
        self._row_number = row_number
        self._cells = cells
        self._reported: set[str] = set()
        self.issues: list[RowIssue] = []

    # -- primitives ----------------------------------------------------------

    def report(self, column: str | None, message: str) -> None:
        if column is not None:
            self._reported.add(column)
        self.issues.append(
            RowIssue(
                row_number=self._row_number,
                field=column,
                message=_issue_message(message),
            )
        )

    def has_reported(self, column: str) -> bool:
        return column in self._reported

    def is_blank(self, column: str) -> bool:
        return not self._cells.get(column, "").strip()

    def cell(self, column: str, max_length: int = CELL_MAX_LENGTH) -> str | None:
        value = self._cells.get(column, "").strip()
        if not value:
            return None
        if len(value) > max_length:
            self.report(column, f"the value is longer than {max_length} characters")
            return None
        return value

    def required_cell(
        self, column: str, max_length: int = CELL_MAX_LENGTH
    ) -> str | None:
        value = self.cell(column, max_length)
        if value is None and column not in self._reported:
            self.report(column, _REQUIRED)
        return value

    def forbid(self, column: str, reason: str) -> None:
        if not self.is_blank(column):
            self.report(column, reason)

    def check[ValueT](
        self, column: str, adapter: TypeAdapter[ValueT], value: str | None
    ) -> ValueT | None:
        if value is None:
            return None
        try:
            return adapter.validate_python(value)
        except PydanticValidationError as error:
            self.report(column, _describe(error))
            return None

    def required[ValueT](
        self, column: str, adapter: TypeAdapter[ValueT]
    ) -> ValueT | None:
        return self.check(column, adapter, self.required_cell(column))

    def optional[ValueT](
        self, column: str, adapter: TypeAdapter[ValueT]
    ) -> ValueT | None:
        return self.check(column, adapter, self.cell(column))

    def report_model_errors(
        self, error: PydanticValidationError, column_of: LocationMapper
    ) -> None:
        for detail in error.errors():
            self.report(column_of(tuple(detail["loc"])), detail["msg"])

    def decimal(self, column: str, *, is_required: bool) -> str | None:
        value = (
            self.required_cell(column, NUMBER_CELL_MAX_LENGTH)
            if is_required
            else self.cell(column, NUMBER_CELL_MAX_LENGTH)
        )
        if value is not None and _DECIMAL_PATTERN.fullmatch(value) is None:
            self.report(column, "the value must be a plain decimal number")
            return None
        return value

    def integer(self, column: str) -> str | None:
        value = self.required_cell(column, NUMBER_CELL_MAX_LENGTH)
        if value is not None and _INTEGER_PATTERN.fullmatch(value) is None:
            self.report(column, "the value must be a whole number without a sign")
            return None
        return value

    def moment(self, column: str, *, is_required: bool) -> DateWithPrecision | None:
        precision_column = f"{column}_precision"
        value = self.cell(column)
        precision = self.cell(precision_column)
        if value is None and precision is None:
            if is_required and column not in self._reported:
                self.report(column, _REQUIRED)
            return None
        if value is None:
            if column not in self._reported:
                self.report(column, f"a value is required with {precision_column}")
            return None
        if precision is None:
            if precision_column not in self._reported:
                self.report(precision_column, f"a value is required with {column}")
            return None
        if _TIMESTAMP_PATTERN.fullmatch(value) is None:
            self.report(
                column,
                "the value must be an ISO 8601 timestamp with a UTC offset, "
                "such as 2022-07-15T06:00:00Z",
            )
            return None
        try:
            return DateWithPrecision.model_validate(
                {"value": value, "precision": precision}
            )
        except PydanticValidationError as error:
            self.report_model_errors(
                error,
                lambda loc: precision_column if loc[:1] == ("precision",) else column,
            )
            return None

    # -- event ---------------------------------------------------------------

    def read_event(self) -> ImportedEventDraft | tuple[RowIssue, ...]:
        self._report_unknown_columns()
        title = self.required("title", _TITLE)
        hazard_type = self.required("hazard_type", _HAZARD_CODE)
        period = self._read_period()
        geometry = self._read_geometry()
        place_codes = self._read_place_codes()
        if (
            geometry is None
            and not place_codes
            and all(
                self.is_blank(column)
                for column in ("longitude", "latitude", "geometry", "place_codes")
            )
        ):
            self.report(
                None,
                "a row needs longitude and latitude, a geometry or a place code",
            )
        summary = self.optional("summary", _SUMMARY)
        source = self._read_source()
        claims = [
            claim
            for slot in range(1, MAX_CLAIMS_PER_ROW + 1)
            if (claim := self._read_claim(slot)) is not None
        ]
        if self.issues:
            return tuple(self.issues)
        try:
            return ImportedEventDraft.model_validate(
                {
                    "title": title,
                    "hazard_type": hazard_type,
                    "period": period,
                    "geometry": geometry,
                    "place_codes": place_codes,
                    "summary": summary,
                    "source": source,
                    "claims": tuple(claims),
                }
            )
        except PydanticValidationError as error:
            self.report_model_errors(error, _event_column_of)
            return tuple(self.issues)

    def _report_unknown_columns(self) -> None:
        for column in self._cells:
            if column not in _KNOWN_COLUMNS:
                # Only a name that fits the field pattern is echoed: the header is
                # untrusted text and may be anything.
                is_echoable = _ISSUE_FIELD_REGEX.fullmatch(column) is not None
                self.report(
                    column if is_echoable else None,
                    "the column is not part of the import contract",
                )

    def _read_period(self) -> EventPeriod | None:
        started_at = self.moment("started_at", is_required=True)
        ended_at = self.moment("ended_at", is_required=False)
        if started_at is None or (ended_at is None and not self.is_blank("ended_at")):
            return None
        try:
            return EventPeriod(started_at=started_at, ended_at=ended_at)
        except PydanticValidationError:
            self.report("ended_at", "ended_at must not be earlier than started_at")
            return None

    def _read_geometry(self) -> EventGeometry | None:
        longitude_text = self.decimal("longitude", is_required=False)
        latitude_text = self.decimal("latitude", is_required=False)
        has_point = not (self.is_blank("longitude") and self.is_blank("latitude"))
        geometry_text = self.cell("geometry", GEOMETRY_CELL_MAX_LENGTH)
        if has_point and not self.is_blank("geometry"):
            self.report(
                "geometry", "give longitude and latitude or a geometry, not both"
            )
            return None
        if has_point:
            return self._read_point(longitude_text, latitude_text)
        if geometry_text is None:
            return None
        try:
            geojson = json.loads(geometry_text)
        except (ValueError, RecursionError):
            self.report("geometry", "the value must be a GeoJSON geometry object")
            return None
        try:
            return EventGeometry.model_validate({"geojson": geojson})
        except PydanticValidationError as error:
            self.report("geometry", _describe(error))
            return None

    def _read_point(
        self, longitude_text: str | None, latitude_text: str | None
    ) -> EventGeometry | None:
        for column in ("longitude", "latitude"):
            if self.is_blank(column):
                self.report(column, "longitude and latitude must be given together")
        longitude = self.check("longitude", _LONGITUDE, longitude_text)
        latitude = self.check("latitude", _LATITUDE, latitude_text)
        if longitude is None or latitude is None:
            return None
        return EventGeometry.from_coordinates(
            Coordinates(longitude=longitude, latitude=latitude)
        )

    def _read_place_codes(self) -> tuple[str, ...]:
        text = self.cell("place_codes")
        if text is None:
            return ()
        parts = [part.strip() for part in text.split(PLACE_CODE_SEPARATOR)]
        if len(parts) > MAX_PLACE_CODES_PER_ROW:
            self.report("place_codes", f"at most {MAX_PLACE_CODES_PER_ROW} place codes")
            return ()
        codes: list[str] = []
        for part in parts:
            code = self.check("place_codes", _PLACE_CODE, part)
            if code is None:
                return ()
            codes.append(code)
        return tuple(codes)

    def _read_source(self) -> ImportedSourceDraft | None:
        citation = self.required("source_citation", _CITATION)
        url = self.optional("source_url", _URL)
        licence = self._read_licence()
        if citation is None:
            return None
        return ImportedSourceDraft(citation=citation, url=url, licence=licence)

    def _read_licence(self) -> Licence | None:
        spdx_id = self.cell("source_licence")
        text = self.cell("source_licence_text")
        if spdx_id is not None and text is not None:
            self.report(
                "source_licence_text",
                "give source_licence or source_licence_text, not both",
            )
            return None
        column, payload = (
            ("source_licence", {"spdx_id": spdx_id})
            if spdx_id is not None
            else ("source_licence_text", {"custom_text": text})
        )
        if spdx_id is None and text is None:
            return None
        try:
            return Licence.model_validate(payload)
        except PydanticValidationError as error:
            self.report(column, _describe(error))
            return None

    # -- claims --------------------------------------------------------------

    def _read_claim(self, slot: int) -> ImportedClaimDraft | None:
        columns = {field: claim_column(slot, field) for field in CLAIM_FIELDS}
        if all(self.is_blank(column) for column in columns.values()):
            return None
        metric_code = self.required(columns["metric_code"], _METRIC_CODE)
        value_kind = self.required(columns["value_kind"], _VALUE_KIND)
        value = None if value_kind is None else self._read_value(value_kind, columns)
        confidence = self.required(columns["confidence"], _CONFIDENCE)
        claimed_at = self.moment(columns["claimed_at"], is_required=True)
        note = self.optional(columns["note"], _NOTE)
        if (
            metric_code is None
            or value is None
            or confidence is None
            or claimed_at is None
        ):
            return None
        return ImportedClaimDraft(
            metric_code=metric_code,
            value=value,
            confidence=confidence,
            claimed_at=claimed_at,
            note=note,
        )

    def _read_value(
        self, value_kind: ValueKind, columns: Mapping[str, str]
    ) -> CountValue | MeasurementValue | MonetaryValue | None:
        value_column = columns["value"]
        payload, column_of = _VALUE_READERS[value_kind](self, columns)
        if payload is None:
            return None
        try:
            return _CLAIM_VALUE.validate_python(payload)
        except PydanticValidationError as error:
            self.report_model_errors(
                error, lambda loc: _value_column_of(loc, column_of, value_column)
            )
            return None


type ValuePayload = tuple[dict[str, object] | None, Mapping[str, str]]


def _count_payload(reader: _RowReader, columns: Mapping[str, str]) -> ValuePayload:
    reader.forbid(columns["currency"], "a count claim has no currency")
    reader.forbid(columns["price_year"], "a count claim has no price year")
    unit = reader.cell(columns["unit"])
    if unit is not None and unit != SiUnit.COUNT.value:
        reader.report(columns["unit"], "a count claim uses unit 'count' or none")
    value = reader.integer(columns["value"])
    if value is None or reader.has_reported(columns["unit"]):
        return None, {}
    return {"kind": ValueKind.COUNT.value, "count": value}, {}


def _measurement_payload(
    reader: _RowReader, columns: Mapping[str, str]
) -> ValuePayload:
    reader.forbid(columns["currency"], "a measurement claim has no currency")
    reader.forbid(columns["price_year"], "a measurement claim has no price year")
    value = reader.decimal(columns["value"], is_required=True)
    unit = reader.required_cell(columns["unit"])
    if value is None or unit is None:
        return None, {}
    payload: dict[str, object] = {
        "kind": ValueKind.MEASUREMENT.value,
        "measurement": {"value": value, "unit": unit},
    }
    return payload, {"unit": columns["unit"]}


def _monetary_payload(reader: _RowReader, columns: Mapping[str, str]) -> ValuePayload:
    reader.forbid(columns["unit"], "a monetary claim has a currency, not a unit")
    value = reader.decimal(columns["value"], is_required=True)
    currency = reader.required_cell(columns["currency"])
    price_year = reader.integer(columns["price_year"])
    if value is None or currency is None or price_year is None:
        return None, {}
    if reader.has_reported(columns["unit"]):
        return None, {}
    payload: dict[str, object] = {
        "kind": ValueKind.MONETARY.value,
        "amount": value,
        "currency": currency,
        "price_year": price_year,
    }
    return payload, {
        "currency": columns["currency"],
        "price_year": columns["price_year"],
    }


_VALUE_READERS: Final[
    Mapping[ValueKind, Callable[[_RowReader, Mapping[str, str]], ValuePayload]]
] = {
    ValueKind.COUNT: _count_payload,
    ValueKind.MEASUREMENT: _measurement_payload,
    ValueKind.MONETARY: _monetary_payload,
}


def _value_column_of(
    loc: tuple[int | str, ...], column_of: Mapping[str, str], value_column: str
) -> str:
    # The innermost named part decides: ("measurement", "measurement", "unit") is
    # the unit column, anything not named in column_of is the value itself.
    for part in reversed(loc):
        if str(part) in column_of:
            return column_of[str(part)]
    return value_column


def _event_column_of(loc: tuple[int | str, ...]) -> str | None:
    head = str(loc[0]) if loc else ""
    return head if head in _KNOWN_COLUMNS else None


def read_flat_rows(
    rows: Iterable[Mapping[str, str]],
) -> tuple[tuple[ImportedEventDraft, ...], tuple[RowIssue, ...], int]:
    """Read every row of a file, numbering data rows from 1.

    A convenience for importers and tests: it applies ``from_flat_row`` to each
    row in order.

    Args:
        rows: The data rows (header excluded), in file order.

    Returns:
        The valid drafts in file order, every issue found, and the number of rows
        read.
    """
    drafts: list[ImportedEventDraft] = []
    issues: list[RowIssue] = []
    count = 0
    for count, cells in enumerate(rows, start=1):
        outcome = ImportedEventDraft.from_flat_row(count, cells)
        if isinstance(outcome, ImportedEventDraft):
            drafts.append(outcome)
        else:
            issues.extend(outcome)
    return tuple(drafts), tuple(issues), count
