"""Value objects of the ``ingestion`` bounded context.

The ingestion module records what Yakhnama takes in from other publishers: the dataset
catalog (``Dataset`` → ``DatasetVersion`` → ``IngestionRun``), the narrow observation
time series and the STAC-aligned raster asset catalog (``AGENTS.md`` §7, STAC and COG).
This module holds their parts:

- catalog: ``DatasetCode``, ``DatasetLicence`` (required on every dataset), the
  coverages, ``UpdateFrequency`` and ``DatasetStatus`` with its transition table;
- versions and runs: ``VersionLabel``, ``InputChecksum``, ``RunStatus`` with its
  transition table, ``RunCounts``, ``IngestionIssue`` and ``IngestionReport``;
- observations: ``VariableCode`` and the ``VARIABLES`` registry, ``StationRef``,
  ``GridCellRef``, ``QualityFlag`` and ``ObservationKey``;
- rasters: ``RasterFootprint``, ``StacBand``, ``StacAsset``, ``Platform`` and
  ``CloudCover``.

``DatasetLicence`` and ``DatasetUrl`` mirror ``provenance``'s ``Licence`` and
``SourceUrl`` rules rather than importing them: a domain layer imports only the
kernel (``AGENTS.md`` §2.1), and a dataset licence also carries the attribution text
the publisher requires. Every free-text field uses the kernel's safe-text rules.

Many rules below are **proposed defaults, not domain facts** (length limits, the
variable registry, the quality flags, the href rules, the extra count invariants);
each is marked where it is defined and listed in ``docs/data-dictionary/ingestion.md``.

Patterns: Value Object, State.
"""

import math
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import MAXYEAR, UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, Literal, Self
from urllib.parse import urlsplit

from geojson_pydantic import MultiPolygon, Polygon
from geojson_pydantic.types import Position
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from yakhnama.modules.ingestion.domain.errors import (
    ObservationUnitMismatchError,
    UnknownVariableError,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
    Unit,
)

# --------------------------------------------------------------------------- #
# Dataset identity and descriptive text                                       #
# --------------------------------------------------------------------------- #

DATASET_CODE_PATTERN: Final = r"^[a-z][a-z0-9_.-]{1,63}$"
DATASET_TITLE_MAX_LENGTH: Final = 300
PUBLISHER_MAX_LENGTH: Final = 200
DATASET_DESCRIPTION_MAX_LENGTH: Final = 4000

DatasetCode = Annotated[str, StringConstraints(pattern=DATASET_CODE_PATTERN)]
"""Stable catalog code of a dataset, for example ``pmd_daily_temperature``.

2 to 64 characters, a lower-case letter first, then ``a-z 0-9 _ . -``. Codes are the
key of ``data/reference/datasets.yaml`` and are never reused for another dataset.
"""

DatasetTitle = Annotated[str, *safe_text(DATASET_TITLE_MAX_LENGTH)]
"""Human-readable title of a dataset, 1 to 300 characters (**proposed** bound)."""

Publisher = Annotated[str, *safe_text(PUBLISHER_MAX_LENGTH)]
"""Who publishes the dataset (an agency, research group or data provider), 1 to 200."""

DatasetDescription = Annotated[
    str, *safe_text(DATASET_DESCRIPTION_MAX_LENGTH, allow_line_breaks=True)
]
"""Long-form description of a dataset, 1 to 4000 characters, line breaks allowed."""

# --------------------------------------------------------------------------- #
# URL (mirrors provenance ``SourceUrl``)                                      #
# --------------------------------------------------------------------------- #

DATASET_URL_MAX_LENGTH: Final = 2048
ALLOWED_URL_SCHEMES: Final = frozenset({"http", "https"})


def _check_http_url(value: str) -> str:
    # The same rules as provenance ``SourceUrl``, restated here because a domain layer
    # must not import another module (AGENTS.md §2.1). Printable ASCII only, so a
    # stored link is unambiguous and safe to render.
    if any(not "\x21" <= character <= "\x7e" for character in value):
        message = "url must be printable ASCII without spaces"
        raise ValueError(message)
    parts = urlsplit(value)
    # The scheme allow-list is what rejects ``javascript:``, ``data:`` and ``file:``.
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        message = "url must use http or https"
        raise ValueError(message)
    # Credentials in a published catalog entry would leak them to every reader.
    if "@" in parts.netloc:
        message = "url must not contain user information (credentials)"
        raise ValueError(message)
    if not parts.hostname:
        message = "url must have a host"
        raise ValueError(message)
    try:
        # Accessing ``port`` validates it (digits, 0-65535); the value is unused.
        _ = parts.port
    except ValueError as error:
        message = "url has an invalid port"
        raise ValueError(message) from error
    return value


DatasetUrl = Annotated[
    str,
    StringConstraints(min_length=1, max_length=DATASET_URL_MAX_LENGTH),
    AfterValidator(_check_http_url),
]
"""An ``http`` or ``https`` URL of at most 2048 characters, stored verbatim.

It must have a host, a valid port if any, no user information and only printable
ASCII: the provenance ``SourceUrl`` rules (**proposed**).
"""

# --------------------------------------------------------------------------- #
# Licence                                                                     #
# --------------------------------------------------------------------------- #

SPDX_ID_PATTERN: Final = r"^[A-Za-z0-9.+-]{2,64}$"
LICENCE_TEXT_MAX_LENGTH: Final = 500
ATTRIBUTION_MAX_LENGTH: Final = 500

SpdxLicenceId = Annotated[str, StringConstraints(pattern=SPDX_ID_PATTERN)]
"""An SPDX licence identifier such as ``CC-BY-4.0``; only the shape is checked."""

LicenceText = Annotated[str, *safe_text(LICENCE_TEXT_MAX_LENGTH)]
"""The terms of a licence without an SPDX identifier, 1 to 500 characters."""

Attribution = Annotated[str, *safe_text(ATTRIBUTION_MAX_LENGTH)]
"""The attribution or citation text the publisher requires, 1 to 500 characters."""


class DatasetLicence(BaseModel):
    """The terms under which an ingested dataset may be stored and republished.

    Exactly one of ``spdx_id`` and ``custom_text`` is set, as in provenance's
    ``Licence``. ``attribution`` is always required: even a public-domain dataset is
    cited in the open dataset's metadata, so the text is recorded at registration
    rather than reconstructed later (**proposed**).

    Implements: Value Object.

    Attributes:
        spdx_id: SPDX identifier, or ``None`` for a custom licence.
        custom_text: The custom terms, or ``None`` for an SPDX licence.
        url: Where the publisher states the licence, if online.
        attribution: How the publisher asks to be credited.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spdx_id: SpdxLicenceId | None = None
    custom_text: LicenceText | None = None
    url: DatasetUrl | None = None
    attribution: Attribution

    @model_validator(mode="after")
    def _require_exactly_one(self) -> Self:
        if (self.spdx_id is None) == (self.custom_text is None):
            message = "a dataset licence has exactly one of spdx_id and custom_text"
            raise ValueError(message)
        return self

    @property
    def is_custom(self) -> bool:
        """Tell whether the licence is custom rather than an SPDX licence.

        Returns:
            ``True`` if ``custom_text`` is set.
        """
        return self.custom_text is not None


# --------------------------------------------------------------------------- #
# Catalog: frequency, status, coverage                                        #
# --------------------------------------------------------------------------- #


class UpdateFrequency(StrEnum):
    """How often the publisher releases new data for a dataset.

    Implements: Value Object.
    """

    STATIC = "static"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"


class DatasetStatus(StrEnum):
    """Lifecycle of a dataset in the catalog; datasets are never deleted.

    Implements: State.
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


DATASET_TRANSITIONS: Final[Mapping[DatasetStatus, frozenset[DatasetStatus]]] = (
    MappingProxyType(
        {
            DatasetStatus.ACTIVE: frozenset(
                {DatasetStatus.DEPRECATED, DatasetStatus.RETIRED}
            ),
            DatasetStatus.DEPRECATED: frozenset({DatasetStatus.RETIRED}),
            DatasetStatus.RETIRED: frozenset(),
        }
    )
)
"""Allowed dataset status moves (**proposed**): retired is terminal, and a deprecated
dataset is not reactivated (a replacement is a new dataset code)."""


def can_move_dataset(from_status: DatasetStatus, to_status: DatasetStatus) -> bool:
    """Tell whether the dataset transition table allows a move.

    Args:
        from_status: The current status.
        to_status: The requested status.

    Returns:
        ``True`` if ``to_status`` is in ``DATASET_TRANSITIONS[from_status]``.
    """
    return to_status in DATASET_TRANSITIONS[from_status]


class SpatialCoverage(BaseModel):
    """The area a dataset covers, as a WGS84 bounding box.

    Implements: Value Object.

    Attributes:
        bbox: The covered rectangle; antimeridian-crossing boxes are refused by the
            kernel's ``BoundingBox``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bbox: BoundingBox


_LATEST_INSTANT: Final = datetime.max.replace(tzinfo=UTC)
_ONE_MICROSECOND: Final = timedelta(microseconds=1)
_MONTHS_PER_YEAR: Final = 12
_MONTHS_PER_SEASON: Final = 3
_FIXED_PERIODS: Final[Mapping[DatePrecision, timedelta]] = MappingProxyType(
    {DatePrecision.HOUR: timedelta(hours=1), DatePrecision.DAY: timedelta(days=1)}
)
_MONTH_PERIODS: Final[Mapping[DatePrecision, int]] = MappingProxyType(
    {
        DatePrecision.MONTH: 1,
        DatePrecision.SEASON: _MONTHS_PER_SEASON,
        DatePrecision.YEAR: _MONTHS_PER_YEAR,
    }
)


def _add_months(start: datetime, months: int) -> datetime | None:
    # ``start`` is truncated to the first of a month, so the day always exists.
    index = start.year * _MONTHS_PER_YEAR + start.month - 1 + months
    year, month_index = divmod(index, _MONTHS_PER_YEAR)
    if year > MAXYEAR:
        return None
    return start.replace(year=year, month=month_index + 1)


def latest_instant(moment: DateWithPrecision) -> datetime:
    """Return the last instant of the period ``moment`` stands for.

    ``2024-07`` at ``month`` precision stands for all of July 2024, so its latest
    instant is ``2024-07-31T23:59:59.999999Z``; an ``exact`` moment is itself.

    Args:
        moment: The imprecise moment.

    Returns:
        The latest UTC instant of its precision period, capped at ``datetime.max``.
    """
    start = moment.truncate().value
    if moment.precision is DatePrecision.EXACT:
        return start
    if moment.precision in _FIXED_PERIODS:
        try:
            return start + _FIXED_PERIODS[moment.precision] - _ONE_MICROSECOND
        except OverflowError:
            return _LATEST_INSTANT
    following = _add_months(start, _MONTH_PERIODS[moment.precision])
    return _LATEST_INSTANT if following is None else following - _ONE_MICROSECOND


class TemporalCoverage(BaseModel):
    """The period a dataset covers: a start and an optional end, each with precision.

    ``end`` is ``None`` for a dataset that is still being extended. The order check is
    precision-aware (**proposed**, as for event periods): the end's period must not
    finish before the start's period begins, so ``2024-07-15`` (day) to ``2024-07``
    (month) is accepted and ``2024-07-15`` to ``2024-06`` is refused.

    Implements: Value Object.

    Attributes:
        start: First moment covered.
        end: Last moment covered, or ``None`` if open-ended.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: DateWithPrecision
    end: DateWithPrecision | None = None

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.end is not None and latest_instant(self.end) < (
            self.start.truncate().value
        ):
            message = "temporal coverage must not end before it starts"
            raise ValueError(message)
        return self


# --------------------------------------------------------------------------- #
# Versions and records                                                        #
# --------------------------------------------------------------------------- #

VERSION_LABEL_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
NOTES_MAX_LENGTH: Final = 2000
RECORD_VERSION_MAX: Final = 2_147_483_647

VersionLabel = Annotated[str, StringConstraints(pattern=VERSION_LABEL_PATTERN)]
"""The publisher's or our label for one release, for example ``v2.1`` or ``2024-08-01``.

1 to 64 characters, a letter or digit first, then ``A-Z a-z 0-9 . _ -``.
"""


def _lower_if_text(value: object) -> object:
    # Hex digests are case-insensitive; one canonical case makes equal checksums equal.
    return value.lower() if isinstance(value, str) else value


InputChecksum = Annotated[
    str, BeforeValidator(_lower_if_text), StringConstraints(pattern=SHA256_PATTERN)
]
"""SHA-256 of the exact input bytes, 64 hex digits, normalised to lower case."""

Notes = Annotated[str, *safe_text(NOTES_MAX_LENGTH, allow_line_breaks=True)]
"""Free-text remarks by a curator, 1 to 2000 characters, line breaks allowed."""

RecordVersion = Annotated[int, Field(ge=1, le=RECORD_VERSION_MAX)]
"""Optimistic-concurrency version: 1 when created, +1 per change with an effect."""

# --------------------------------------------------------------------------- #
# Runs                                                                        #
# --------------------------------------------------------------------------- #

ADAPTER_NAME_PATTERN: Final = r"^[a-z][a-z0-9_.]{1,63}$"

AdapterName = Annotated[str, StringConstraints(pattern=ADAPTER_NAME_PATTERN)]
"""Registry name of the source adapter a run used, such as ``local_csv_temperature``."""


class RunStatus(StrEnum):
    """Where an ingestion run is in its lifecycle.

    Implements: State.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


RUN_TRANSITIONS: Final[Mapping[RunStatus, frozenset[RunStatus]]] = MappingProxyType(
    {
        # A queued run can fail before it starts, for example when the worker finds
        # the dataset retired in the meantime.
        RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
        RunStatus.RUNNING: frozenset(
            {RunStatus.SUCCEEDED, RunStatus.PARTIALLY_SUCCEEDED, RunStatus.FAILED}
        ),
        RunStatus.SUCCEEDED: frozenset(),
        RunStatus.PARTIALLY_SUCCEEDED: frozenset(),
        RunStatus.FAILED: frozenset(),
    }
)
"""Allowed run status moves. Finished runs are terminal: a retry is a new run, so the
record of what happened the first time is never overwritten."""

FINISHED_RUN_STATUSES: Final = frozenset(
    status for status, targets in RUN_TRANSITIONS.items() if not targets
)
"""The terminal statuses: ``succeeded``, ``partially_succeeded`` and ``failed``."""


def can_move_run(from_status: RunStatus, to_status: RunStatus) -> bool:
    """Tell whether the run transition table allows a move.

    Args:
        from_status: The current status.
        to_status: The requested status.

    Returns:
        ``True`` if ``to_status`` is in ``RUN_TRANSITIONS[from_status]``.
    """
    return to_status in RUN_TRANSITIONS[from_status]


COUNT_MAX: Final = 9_223_372_036_854_775_807
Count = Annotated[int, Field(ge=0, le=COUNT_MAX)]
"""A non-negative record count that fits a PostgreSQL ``bigint``."""


class RunCounts(BaseModel):
    """How many records a run saw at each stage of the pipeline.

    Invariants: ``persisted ≤ valid ≤ parsed ≤ fetched``, and additionally
    (**proposed**, they follow from what each stage does)
    ``valid + invalid ≤ parsed``, because a parsed record is judged at most once, and
    ``deduplicated + persisted ≤ valid``, because a record dropped as a duplicate is
    not persisted. The sums are ``≤`` rather than ``=`` because a failed run may stop
    part-way through a stage.

    Implements: Value Object.

    Attributes:
        fetched: Raw records received from the source.
        parsed: Records the parser turned into source-shaped rows.
        valid: Rows that passed validation.
        invalid: Rows that failed validation.
        deduplicated: Valid records dropped as duplicates.
        persisted: Records written to storage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fetched: Count = 0
    parsed: Count = 0
    valid: Count = 0
    invalid: Count = 0
    deduplicated: Count = 0
    persisted: Count = 0

    @model_validator(mode="after")
    def _check_stages(self) -> Self:
        if not self.persisted <= self.valid <= self.parsed <= self.fetched:
            message = "counts must satisfy persisted <= valid <= parsed <= fetched"
            raise ValueError(message)
        if self.valid + self.invalid > self.parsed:
            message = "valid + invalid must not exceed parsed"
            raise ValueError(message)
        if self.deduplicated + self.persisted > self.valid:
            message = "deduplicated + persisted must not exceed valid"
            raise ValueError(message)
        return self


IssueStage = Literal[
    "fetch",
    "parse",
    "validate",
    "normalise",
    "deduplicate",
    "persist",
    "record_lineage",
]
"""The pipeline hook an issue was raised in, one per Template Method hook."""


class IssueSeverity(StrEnum):
    """How serious an ingestion issue is.

    Implements: Value Object.
    """

    ERROR = "error"
    WARNING = "warning"


ISSUE_REFERENCE_MAX_LENGTH: Final = 200
ISSUE_MESSAGE_MAX_LENGTH: Final = 500
MAX_REPORT_ISSUES: Final = 10_000

IssueReference = Annotated[str, *safe_text(ISSUE_REFERENCE_MAX_LENGTH)]
"""Where in the input the issue is, for example ``row 12`` or ``station 41530``."""

IssueMessage = Annotated[str, *safe_text(ISSUE_MESSAGE_MAX_LENGTH)]
"""What went wrong, 1 to 500 characters; never personal data."""


class IngestionIssue(BaseModel):
    """One problem or warning found while a run processed its input.

    Implements: Value Object.

    Attributes:
        stage: The pipeline hook that found it.
        reference: Where in the input it is, if it concerns one record.
        message: What went wrong.
        severity: ``error`` if a record or the run was affected, else ``warning``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: IssueStage
    reference: IssueReference | None = None
    message: IssueMessage
    severity: IssueSeverity = IssueSeverity.ERROR


class IngestionReport(BaseModel):
    """Everything a run found wrong, and the counts it reached.

    At most ``MAX_REPORT_ISSUES`` issues are kept, so one malformed source cannot fill
    the database; the rest are counted by severity in ``omitted_error_count`` and
    ``omitted_warning_count``, so nothing is dropped silently. ``collect`` applies the
    cap.

    Implements: Value Object.

    Attributes:
        issues: Kept issues, in the order they were found.
        counts: Record counts per stage.
        omitted_error_count: Errors found after the cap was reached.
        omitted_warning_count: Warnings found after the cap was reached.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issues: tuple[IngestionIssue, ...] = Field(default=(), max_length=MAX_REPORT_ISSUES)
    counts: RunCounts = RunCounts()
    omitted_error_count: Count = 0
    omitted_warning_count: Count = 0

    @model_validator(mode="after")
    def _check_omissions(self) -> Self:
        has_omissions = self.omitted_error_count + self.omitted_warning_count > 0
        if has_omissions and len(self.issues) < MAX_REPORT_ISSUES:
            message = "issues may be omitted only once the report is full"
            raise ValueError(message)
        return self

    @classmethod
    def collect(cls, issues: Iterable[IngestionIssue], counts: RunCounts) -> Self:
        """Build a report, keeping the first ``MAX_REPORT_ISSUES`` issues.

        Args:
            issues: Every issue found, in order; consumed once.
            counts: The counts the run reached.

        Returns:
            The report, with any further issues counted by severity.
        """
        kept: list[IngestionIssue] = []
        omitted = {IssueSeverity.ERROR: 0, IssueSeverity.WARNING: 0}
        for issue in issues:
            if len(kept) < MAX_REPORT_ISSUES:
                kept.append(issue)
            else:
                omitted[issue.severity] += 1
        return cls(
            issues=tuple(kept),
            counts=counts,
            omitted_error_count=omitted[IssueSeverity.ERROR],
            omitted_warning_count=omitted[IssueSeverity.WARNING],
        )

    @property
    def error_count(self) -> int:
        """Return how many errors were found, kept and omitted.

        Returns:
            The number of ``error`` issues.
        """
        kept = sum(1 for issue in self.issues if issue.severity is IssueSeverity.ERROR)
        return kept + self.omitted_error_count

    @property
    def warning_count(self) -> int:
        """Return how many warnings were found, kept and omitted.

        Returns:
            The number of ``warning`` issues.
        """
        kept = sum(
            1 for issue in self.issues if issue.severity is IssueSeverity.WARNING
        )
        return kept + self.omitted_warning_count

    @property
    def has_errors(self) -> bool:
        """Tell whether any error was found.

        Returns:
            ``True`` if ``error_count`` is positive.
        """
        return self.error_count > 0


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #

VARIABLE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{1,63}$"
VARIABLE_DESCRIPTION_MAX_LENGTH: Final = 300

VariableCode = Annotated[str, StringConstraints(pattern=VARIABLE_CODE_PATTERN)]
"""Code of an observed variable, for example ``air_temperature``; see ``VARIABLES``."""

VariableDescription = Annotated[str, *safe_text(VARIABLE_DESCRIPTION_MAX_LENGTH)]


class VariableDefinition(BaseModel):
    """One registry entry: a variable and the SI unit it is stored in.

    Implements: Value Object.

    Attributes:
        code: The variable code.
        unit: The only unit observations of this variable are stored in.
        description: What the variable measures.
        is_proposed: ``True`` until the maintainer confirms the entry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: VariableCode
    unit: Unit
    description: VariableDescription
    is_proposed: bool = True


def _registry(*definitions: VariableDefinition) -> Mapping[str, VariableDefinition]:
    return MappingProxyType({definition.code: definition for definition in definitions})


VARIABLES: Final[Mapping[str, VariableDefinition]] = _registry(
    VariableDefinition(
        code="air_temperature",
        unit=SiUnit.KELVIN,
        description="Air temperature.",
    ),
    VariableDefinition(
        code="precipitation_depth",
        unit=SiUnit.METRE,
        description="Precipitation expressed as a depth of liquid water.",
    ),
    VariableDefinition(
        code="discharge",
        unit=SiUnit.CUBIC_METRE_PER_SECOND,
        description="Volume of water flowing past a point per unit of time.",
    ),
    VariableDefinition(
        code="snow_depth",
        unit=SiUnit.METRE,
        description="Depth of snow on the ground.",
    ),
)
"""The variable registry (**proposed**, Phase 4 plan §5). Every entry is stored in SI;
the pipeline converts source units (for example °C) in ``normalise``. Measurement
heights, accumulation periods and averaging windows are not defined here: they are
source facts to be confirmed per dataset."""


def is_known_variable(variable: str) -> bool:
    """Tell whether ``variable`` is registered in ``VARIABLES``.

    Args:
        variable: A candidate code.

    Returns:
        ``True`` if the registry has an entry for it.
    """
    return variable in VARIABLES


def variable_definition(variable: str) -> VariableDefinition:
    """Return the registry entry for ``variable``.

    Args:
        variable: A variable code.

    Returns:
        Its definition.

    Raises:
        UnknownVariableError: If the code is not registered.
    """
    definition = VARIABLES.get(variable)
    if definition is None:
        raise UnknownVariableError.for_code(variable)
    return definition


def require_variable_unit(variable: str, unit: str) -> None:
    """Check that ``unit`` is the unit ``variable`` is stored in.

    Args:
        variable: A variable code.
        unit: The unit an observation carries.

    Raises:
        UnknownVariableError: If the code is not registered.
        ObservationUnitMismatchError: If ``unit`` differs from the registry unit.
    """
    expected = variable_definition(variable).unit
    if unit != expected:
        raise ObservationUnitMismatchError.for_units(variable, expected, unit)


SITE_CODE_MAX_LENGTH: Final = 64
STATION_NAME_MAX_LENGTH: Final = 200

SiteCode = Annotated[str, *safe_text(SITE_CODE_MAX_LENGTH)]
"""The publisher's code for a station or grid cell, 1 to 64 characters."""

StationName = Annotated[str, *safe_text(STATION_NAME_MAX_LENGTH)]
"""The publisher's name for a station, 1 to 200 characters."""

SiteKind = Literal["station", "grid_cell"]


def _require_metres(measurement: Measurement | None, name: str) -> None:
    if measurement is not None and measurement.unit != SiUnit.METRE:
        message = f"{name} must be in metre"
        raise ValueError(message)


class StationRef(BaseModel):
    """The station an observation was made at, as the publisher identifies it.

    Implements: Value Object.

    Attributes:
        code: The publisher's station code; unique within one dataset version.
        name: The publisher's station name, if given.
        location: Where the station is, if published.
        elevation: Height above sea level in metre, if published. The vertical
            datum is the publisher's and is recorded per dataset (open question).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: SiteCode
    name: StationName | None = None
    location: Coordinates | None = None
    elevation: Measurement | None = None

    @model_validator(mode="after")
    def _check_elevation_unit(self) -> Self:
        _require_metres(self.elevation, "elevation")
        return self

    @property
    def site_ref(self) -> str:
        """Return the site part of the observation natural key.

        Returns:
            ``"station:<code>"``.
        """
        return f"station:{self.code}"


class GridCellRef(BaseModel):
    """The grid cell a gridded value belongs to.

    Implements: Value Object.

    Attributes:
        cell_id: The publisher's cell identifier; unique within one dataset version.
        centroid: Centre of the cell, WGS84.
        resolution: Edge length of the cell in metre, strictly positive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: SiteCode
    centroid: Coordinates
    resolution: Measurement

    @model_validator(mode="after")
    def _check_resolution(self) -> Self:
        _require_metres(self.resolution, "resolution")
        if self.resolution.value <= 0:
            message = "resolution must be positive"
            raise ValueError(message)
        return self

    @property
    def site_ref(self) -> str:
        """Return the site part of the observation natural key.

        Returns:
            ``"grid_cell:<cell_id>"``.
        """
        return f"grid_cell:{self.cell_id}"


class QualityFlag(StrEnum):
    """How far an observed value can be trusted (**proposed** flag set).

    Publishers use their own flag vocabularies; the pipeline maps them to these four
    in ``normalise`` and documents the mapping per source.

    Implements: Value Object.
    """

    GOOD = "good"
    SUSPECT = "suspect"
    MISSING = "missing"
    ESTIMATED = "estimated"


SITE_REF_MAX_LENGTH: Final = len("grid_cell:") + SITE_CODE_MAX_LENGTH


class ObservationKey(BaseModel):
    """The natural key of an observation.

    ``(observed_at, dataset_version_id, variable, site_ref)`` with ``observed_at``
    first so the table can become a TimescaleDB hypertable partitioned by time
    (Phase 4 plan §5, **proposed**). ``site_ref`` prefixes the code with its kind, so
    a station and a grid cell with the same code never collide. The precision of
    ``observed_at`` is not part of the key: two values for the same instant are the
    same observation.

    Implements: Value Object.

    Attributes:
        observed_at: The observed instant, UTC.
        dataset_version_id: The dataset version it came from.
        variable: The variable code.
        site_ref: ``station:<code>`` or ``grid_cell:<id>``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: AwareDatetime
    dataset_version_id: EntityId
    variable: VariableCode
    site_ref: Annotated[str, StringConstraints(max_length=SITE_REF_MAX_LENGTH)]

    @field_validator("observed_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Rasters, STAC-aligned                                                       #
# --------------------------------------------------------------------------- #

STAC_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
ASSET_KEY_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
ASSET_ROLE_PATTERN: Final = r"^[a-z][a-z0-9_-]{0,31}$"
COMMON_NAME_PATTERN: Final = r"^[a-z][a-z0-9]{0,31}$"
MEDIA_TYPE_PATTERN: Final = (
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
    r"(; ?[a-z0-9_.-]+=[A-Za-z0-9_.+-]+)*$"
)
MEDIA_TYPE_MAX_LENGTH: Final = 255
ASSET_HREF_MAX_LENGTH: Final = 2048
PLATFORM_MAX_LENGTH: Final = 64
BAND_NAME_MAX_LENGTH: Final = 64
BAND_DESCRIPTION_MAX_LENGTH: Final = 500
MAX_ASSET_ROLES: Final = 16
COG_MEDIA_TYPE: Final = "image/tiff; application=geotiff; profile=cloud-optimized"
"""The media type STAC uses for a Cloud-Optimised GeoTIFF."""

StacId = Annotated[str, StringConstraints(pattern=STAC_ID_PATTERN)]
"""STAC item id, 1 to 128 of ``A-Z a-z 0-9 . _ : -`` (**proposed** shape)."""

AssetKey = Annotated[str, StringConstraints(pattern=ASSET_KEY_PATTERN)]
"""Key of an asset in the STAC ``assets`` object, for example ``visual`` or ``B04``."""

AssetRole = Annotated[str, StringConstraints(pattern=ASSET_ROLE_PATTERN)]
"""A STAC asset role such as ``data``, ``thumbnail`` or ``overview``."""

CommonBandName = Annotated[str, StringConstraints(pattern=COMMON_NAME_PATTERN)]
"""A STAC ``eo:common_name`` such as ``red`` or ``nir``; only the shape is checked."""

MediaType = Annotated[
    str,
    StringConstraints(max_length=MEDIA_TYPE_MAX_LENGTH, pattern=MEDIA_TYPE_PATTERN),
]
"""A lower-case media type with optional parameters, for example ``COG_MEDIA_TYPE``."""

Platform = Annotated[str, *safe_text(PLATFORM_MAX_LENGTH)]
"""The satellite or instrument platform, for example ``sentinel-2a``, 1 to 64."""

BandName = Annotated[str, *safe_text(BAND_NAME_MAX_LENGTH)]
BandDescription = Annotated[str, *safe_text(BAND_DESCRIPTION_MAX_LENGTH)]

CloudCover = Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)]
"""Percentage of the scene covered by cloud, 0 to 100 (STAC ``eo:cloud_cover``)."""

ALLOWED_HREF_SCHEMES: Final = frozenset({"http", "https", "s3"})


def _check_asset_href(value: str) -> str:
    # Hrefs end up as links in published STAC items, so they are held to link rules
    # (**proposed**): no whitespace, and either an http(s) or s3 URL without
    # credentials, or a relative object key that cannot climb out of its prefix.
    if any(character.isspace() for character in value):
        message = "href must not contain whitespace"
        raise ValueError(message)
    parts = urlsplit(value)
    if parts.scheme:
        if parts.scheme.lower() not in ALLOWED_HREF_SCHEMES:
            message = "href must be an http, https or s3 URL, or an object key"
            raise ValueError(message)
        if "@" in parts.netloc or not parts.netloc:
            message = "href URL must have a host and no user information"
            raise ValueError(message)
        return value
    segments = value.split("/")
    if value.startswith("/") or "\\" in value or {".", ".."} & set(segments):
        message = "object key must be relative, without '.', '..' or backslashes"
        raise ValueError(message)
    return value


AssetHref = Annotated[
    str, *safe_text(ASSET_HREF_MAX_LENGTH), AfterValidator(_check_asset_href)
]
"""Where an asset's bytes live: an object-storage key or an http(s)/s3 URL.

Raster bytes are never stored in PostgreSQL (Phase 4 plan §1); only this pointer is.
"""


class StacBand(BaseModel):
    """One band of a raster, as STAC 1.1 ``bands`` describes it.

    Implements: Value Object.

    Attributes:
        name: The band's name in the asset, for example ``B04``.
        common_name: The ``eo:common_name``, if the band has one.
        description: What the band holds, if described.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: BandName
    common_name: CommonBandName | None = None
    description: BandDescription | None = None


class StacAsset(BaseModel):
    """One file of a raster item, as STAC describes it.

    Implements: Value Object.

    Attributes:
        key: The asset's key in the item's ``assets`` object.
        href: Object key or URL of the bytes.
        media_type: The file's media type, for example ``COG_MEDIA_TYPE``.
        roles: STAC roles, unique, at most 16.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: AssetKey
    href: AssetHref
    media_type: MediaType
    roles: tuple[AssetRole, ...] = Field(default=(), max_length=MAX_ASSET_ROLES)

    @field_validator("roles", mode="after")
    @classmethod
    def _require_unique_roles(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(roles)) != len(roles):
            message = "asset roles must be unique"
            raise ValueError(message)
        return roles


FootprintGeoJson = Annotated[Polygon | MultiPolygon, Field(discriminator="type")]
"""The GeoJSON geometry types a raster footprint may take."""

_TWO_DIMENSIONS: Final = 2
_MAX_ABS_LONGITUDE: Final = 180.0
_MAX_ABS_LATITUDE: Final = 90.0
MAX_FOOTPRINT_POSITIONS: Final = 100_000
"""Bound on footprint positions (**proposed**): a scene outline, not a flood extent."""


def _polygons(
    geojson: Polygon | MultiPolygon,
) -> Sequence[Sequence[Sequence[Position]]]:
    # A Polygon is a list of rings; a MultiPolygon a list of such lists.
    return (
        [geojson.coordinates] if isinstance(geojson, Polygon) else geojson.coordinates
    )


def _positions(geojson: Polygon | MultiPolygon) -> Iterator[Position]:
    for polygon in _polygons(geojson):
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


class RasterFootprint(BaseModel):
    """The area a raster covers: a WGS84 polygon or multipolygon, longitude first.

    ``geojson-pydantic`` checks the GeoJSON structure (closed rings of at least four
    positions); this wrapper adds finite, two-dimensional, in-bounds positions and a
    size bound, as the events and geography geometries do. The wrapped geometry is
    copied on the way in, because ``geojson-pydantic`` models are mutable.

    Implements: Value Object.

    Attributes:
        geojson: The footprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    SRID: ClassVar[int] = 4326

    geojson: FootprintGeoJson

    @field_validator("geojson", mode="before")
    @classmethod
    def _copy_geometry(cls, value: object) -> object:
        if isinstance(value, Polygon | MultiPolygon):
            return value.model_dump()
        return value

    @field_validator("geojson", mode="after")
    @classmethod
    def _check_positions(
        cls, geojson: Polygon | MultiPolygon
    ) -> Polygon | MultiPolygon:
        count = 0
        for position in _positions(geojson):
            count += 1
            _check_position(position)
        if count > MAX_FOOTPRINT_POSITIONS:
            message = f"footprint has more than {MAX_FOOTPRINT_POSITIONS} positions"
            raise ValueError(message)
        return geojson

    def bounding_box(self) -> BoundingBox:
        """Return the smallest WGS84 box containing every position.

        Returns:
            The tight bounding box.
        """
        return BoundingBox.from_coordinates(
            Coordinates(longitude=position.longitude, latitude=position.latitude)
            for position in _positions(self.geojson)
        )


_STAC_ID_REGEX: Final = re.compile(STAC_ID_PATTERN)


def is_stac_id(value: str) -> bool:
    """Tell whether ``value`` has the shape of a ``StacId``.

    Args:
        value: A candidate id.

    Returns:
        ``True`` if it matches ``STAC_ID_PATTERN``.
    """
    return _STAC_ID_REGEX.fullmatch(value) is not None


MAX_RASTER_BANDS: Final = 256
MAX_RASTER_ASSETS: Final = 32


def require_unique_raster_parts(
    bands: Sequence[StacBand], assets: Sequence[StacAsset]
) -> None:
    """Check that band names and asset keys are unique, inside a model validator.

    Args:
        bands: A raster's bands.
        assets: A raster's assets.

    Raises:
        ValueError: If two bands share a name or two assets share a key; a
            ``ValueError`` so Pydantic reports it with the other field errors.
    """
    if len({band.name for band in bands}) != len(bands):
        message = "band names must be unique"
        raise ValueError(message)
    if len({asset.key for asset in assets}) != len(assets):
        message = "asset keys must be unique"
        raise ValueError(message)


class RasterAssetDescription(BaseModel):
    """The STAC-aligned description of one raster, catalogued together.

    Implements: Value Object.

    Attributes:
        stac_id: The STAC item id.
        footprint: The area the raster covers.
        acquired_at: When the scene was acquired, with precision (STAC
            ``datetime``).
        platform: The satellite or instrument platform.
        cloud_cover: Percentage of cloud, if known.
        bands: The bands, unique by name, at most 256.
        assets: The files, unique by key, 1 to 32; bytes live in object storage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stac_id: StacId
    footprint: RasterFootprint
    acquired_at: DateWithPrecision
    platform: Platform
    cloud_cover: CloudCover | None = None
    bands: tuple[StacBand, ...] = Field(default=(), max_length=MAX_RASTER_BANDS)
    assets: tuple[StacAsset, ...] = Field(min_length=1, max_length=MAX_RASTER_ASSETS)

    @model_validator(mode="after")
    def _check_parts(self) -> Self:
        require_unique_raster_parts(self.bands, self.assets)
        return self

    def as_fields(self) -> dict[str, object]:
        """Return the description as field values, keeping nested objects intact.

        Returns:
            A new mapping from field name to value.
        """
        return {name: getattr(self, name) for name in type(self).model_fields}


class DatasetDetails(BaseModel):
    """What a curator supplies to register a dataset.

    ``licence`` is optional here only so that ``DatasetFactory.register`` can refuse
    a missing licence with ``LicenceRequiredError`` instead of a generic validation
    error; a ``Dataset`` itself cannot exist without one.

    Implements: Value Object.

    Attributes:
        code: Catalog code.
        title: Human-readable title.
        publisher: Who publishes the data.
        licence: The publisher's terms; required by registration.
        update_frequency: How often the publisher releases data.
        description: Long-form description, if any.
        homepage_url: The dataset's page, if online.
        spatial_coverage: The covered area, if known.
        temporal_coverage: The covered period, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DatasetCode
    title: DatasetTitle
    publisher: Publisher
    licence: DatasetLicence | None = None
    update_frequency: UpdateFrequency
    description: DatasetDescription | None = None
    homepage_url: DatasetUrl | None = None
    spatial_coverage: SpatialCoverage | None = None
    temporal_coverage: TemporalCoverage | None = None

    def as_fields(self) -> dict[str, object]:
        """Return the details as field values, keeping nested objects intact.

        Returns:
            A new mapping from field name to value.
        """
        return {name: getattr(self, name) for name in type(self).model_fields}


class DatasetVersionDetails(BaseModel):
    """What a curator or pipeline supplies to record a dataset version.

    Implements: Value Object.

    Attributes:
        label: The release label.
        retrieved_at: When the input was retrieved, with precision.
        input_checksum: SHA-256 of the input bytes.
        notes: Curator remarks, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: VersionLabel
    retrieved_at: DateWithPrecision
    input_checksum: InputChecksum
    notes: Notes | None = None


class RunRequest(BaseModel):
    """Who asked for an ingestion run and through which source adapter.

    Implements: Value Object.

    Attributes:
        adapter_name: The registered source adapter to use.
        triggered_by: The requesting user, or ``None`` for a system schedule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter_name: AdapterName
    triggered_by: EntityId | None = None
