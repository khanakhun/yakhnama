"""Value objects of the ``exchange`` bounded context.

Exports turn the verified record into files for researchers; imports bring historical
records in. Both run as jobs, so the values here describe what a job asked for
(dataset, format, filters), what it produced (an artifact and its metadata sidecar)
and what an import found (a row-level validation report).

Choices marked **proposed** are defaults awaiting the maintainer, listed in
``docs/data-dictionary/exchange.md``; none is a sourced domain fact. The dataset
licence in particular is ADR 0010's *proposal*: every sidecar says so in
``licence.status``.

Patterns: Value Object.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.events.public import EventPeriod
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.shared_kernel.errors import ValidationError as KernelValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
)

# --------------------------------------------------------------------------- #
# Codes                                                                       #
# --------------------------------------------------------------------------- #

FORMAT_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{1,31}$"

FormatCode = Annotated[str, StringConstraints(pattern=FORMAT_CODE_PATTERN)]
"""A format's registry key such as ``geojson``: 2 to 32 snake_case characters."""


class ExportFormat(StrEnum):
    """File formats an export can be written in.

    Implements: Value Object.
    """

    JSON = "json"
    GEOJSON = "geojson"
    CSV = "csv"
    GEOPARQUET = "geoparquet"


class ImportFormat(StrEnum):
    """File formats an import can be read from.

    Implements: Value Object.
    """

    CSV = "csv"
    GEOJSON = "geojson"


class ExportDataset(StrEnum):
    """Which part of the record an export contains.

    Who may export which dataset (reports are moderator-only, **proposed**) is an
    application policy, not a property of the value.

    Implements: Value Object.
    """

    EVENTS = "events"
    CLAIMS = "claims"
    REPORTS = "reports"


class JobStatus(StrEnum):
    """Lifecycle of an export or import job.

    ``completed``, ``failed`` and ``cancelled`` are final.

    Implements: Value Object.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        """Return ``True`` for ``completed``, ``failed`` and ``cancelled``."""
        return self in FINAL_JOB_STATUSES


FINAL_JOB_STATUSES: Final = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

JOB_VERSION_MAX: Final = 2**31 - 1

JobVersion = Annotated[int, Field(ge=1, le=JOB_VERSION_MAX)]
"""Optimistic-concurrency version of a job: 1 at creation, +1 per change."""

JOB_ERROR_SUMMARY_MAX_LENGTH: Final = 1000

JobErrorSummary = Annotated[str, *safe_text(JOB_ERROR_SUMMARY_MAX_LENGTH)]
"""Why a job failed: 1 to 1000 characters of single-line safe text.

Written by the platform, never copied into events; it must not quote row values,
which may contain personal data.
"""

# --------------------------------------------------------------------------- #
# Filters                                                                     #
# --------------------------------------------------------------------------- #

FILTER_STATUS_PATTERN: Final = r"^[a-z][a-z_]{1,31}$"

FilterStatus = Annotated[str, StringConstraints(pattern=FILTER_STATUS_PATTERN)]
"""A record status code to filter on, such as ``published``.

Only the shape is checked: which statuses a dataset has, and which of them a caller
may see, is decided by the application against the owning module (**proposed**).
"""

NO_FILTERS_FRAGMENT: Final = "no filters"
"""The citation fragment of an export without filters."""


class ExportFilters(BaseModel):
    """What an export was restricted to; every filter is optional.

    An event matches the time filters if it may have happened within
    ``[occurred_from, occurred_to]``, with each bound read at its own precision.

    Implements: Value Object.

    Attributes:
        bbox: WGS84 box the record's location must fall in.
        hazard_type: Hazard type code, for example ``glof``.
        place_code: Gazetteer place code, for example ``pk.gb.hunza``.
        occurred_from: Earliest moment of interest, with precision.
        occurred_to: Latest moment of interest, with precision; never before
            ``occurred_from`` (the same precision-aware rule as an event period).
        status: Record status code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bbox: BoundingBox | None = None
    hazard_type: HazardCode | None = None
    place_code: PlaceCode | None = None
    occurred_from: DateWithPrecision | None = None
    occurred_to: DateWithPrecision | None = None
    status: FilterStatus | None = None

    @model_validator(mode="after")
    def _check_time_window(self) -> Self:
        if self.occurred_from is not None and self.occurred_to is not None:
            # Reusing EventPeriod keeps one precision-aware ordering rule for a
            # window and an event, so a window a user can type is one an event fits.
            try:
                EventPeriod(started_at=self.occurred_from, ended_at=self.occurred_to)
            except PydanticValidationError as error:
                message = "occurred_to must not be earlier than occurred_from"
                raise ValueError(message) from error
        return self

    @property
    def is_empty(self) -> bool:
        """Tell whether no filter is set.

        Returns:
            ``True`` if every filter is ``None``.
        """
        return all(getattr(self, name) is None for name in type(self).model_fields)

    def to_citation_fragment(self) -> str:
        """Describe the filters in one deterministic, human-readable line.

        The fragment goes into the sidecar's citation, so two exports with the same
        filters cite the same subset in the same words. Only codes, numbers and
        dates appear, never free text.

        Returns:
            ``"no filters"``, or ``"; "``-separated ``name=value`` parts in the fixed
            order hazard type, place, bbox, from, to, status. Floats use ``repr``
            so the bbox is exact; dates are floored to their precision.
        """
        parts: list[str] = []
        if self.hazard_type is not None:
            parts.append(f"hazard_type={self.hazard_type}")
        if self.place_code is not None:
            parts.append(f"place_code={self.place_code}")
        if self.bbox is not None:
            edges = (
                self.bbox.min_longitude,
                self.bbox.min_latitude,
                self.bbox.max_longitude,
                self.bbox.max_latitude,
            )
            parts.append("bbox=" + ",".join(repr(edge) for edge in edges))
        if self.occurred_from is not None:
            parts.append(f"from={format_moment(self.occurred_from)}")
        if self.occurred_to is not None:
            parts.append(f"to={format_moment(self.occurred_to)}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        return "; ".join(parts) if parts else NO_FILTERS_FRAGMENT


def format_moment(moment: DateWithPrecision) -> str:
    """Write a moment as precisely as it is known, and no more.

    Args:
        moment: An instant with its precision.

    Returns:
        ``YYYY`` for a year, ``YYYY-MM`` for a month, ``YYYY-MM season`` for a
        season (its first month), ``YYYY-MM-DD`` for a day, ``YYYY-MM-DDTHHZ`` for
        an hour, and the full ISO 8601 UTC instant (``...Z``) for ``exact``.
    """
    value = moment.truncate().value
    year_month = f"{value.year:04d}-{value.month:02d}"
    day = f"{year_month}-{value.day:02d}"
    formats = {
        DatePrecision.YEAR: f"{value.year:04d}",
        DatePrecision.SEASON: f"{year_month} season",
        DatePrecision.MONTH: year_month,
        DatePrecision.DAY: day,
        DatePrecision.HOUR: f"{day}T{value.hour:02d}Z",
        DatePrecision.EXACT: value.isoformat().replace("+00:00", "Z"),
    }
    return formats[moment.precision]


class ExportRequest(BaseModel):
    """What an export is asked to contain and how it is written.

    Implements: Value Object.

    Attributes:
        dataset: Which dataset.
        format: Which file format.
        filters: Which rows to select.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters = ExportFilters()


# --------------------------------------------------------------------------- #
# Artifacts                                                                   #
# --------------------------------------------------------------------------- #

# The same key shape as media's object keys: the domain may hold no storage client,
# only the key, and one shape keeps every bucket's keys alike.
OBJECT_KEY_PATTERN: Final = r"^[a-z0-9][a-z0-9/_.-]{3,255}$"


def _reject_parent_segments(key: str) -> str:
    if ".." in key:
        message = "object key must not contain '..'"
        raise ValueError(message)
    return key


ObjectKey = Annotated[
    str,
    StringConstraints(pattern=OBJECT_KEY_PATTERN),
    AfterValidator(_reject_parent_segments),
]
"""Storage key of an artifact: 4 to 256 of ``a-z 0-9 / _ . -``, never ``..``."""

SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"

Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
"""A SHA-256 digest as 64 lower-case hexadecimal digits."""

MEDIA_TYPE_PATTERN: Final = r"^[a-z]+/[a-z0-9][a-z0-9.+-]{0,99}$"

MediaTypeName = Annotated[str, StringConstraints(pattern=MEDIA_TYPE_PATTERN)]
"""An IANA-style media type without parameters, such as ``application/geo+json``."""

ARTIFACT_MAX_BYTES: Final = 10 * 1024**3
"""Largest artifact a job may record: 10 GiB (**proposed**)."""


class ArtifactRef(BaseModel):
    """Where a job's file lives in object storage and what it contains.

    Implements: Value Object.

    Attributes:
        object_key: Storage key, built by the application from the job id, never
            from a user-supplied file name.
        byte_size: Size in bytes, 0 to ``ARTIFACT_MAX_BYTES``.
        sha256: Digest of the stored bytes.
        media_type: The file's media type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: ObjectKey
    byte_size: int = Field(ge=0, le=ARTIFACT_MAX_BYTES)
    sha256: Sha256
    media_type: MediaTypeName


# --------------------------------------------------------------------------- #
# Metadata sidecar                                                            #
# --------------------------------------------------------------------------- #

SCHEMA_VERSION_PATTERN: Final = r"^[0-9]{1,4}\.[0-9]{1,4}$"

SchemaVersion = Annotated[str, StringConstraints(pattern=SCHEMA_VERSION_PATTERN)]
"""``MAJOR.MINOR`` of an exchange layout; a major bump breaks readers."""

EXPORT_SCHEMA_VERSION: Final = "1.0"
"""Version of the export layouts written by this code (**proposed** start)."""

LICENCE_ID_PATTERN: Final = r"^[A-Za-z0-9.+-]{2,64}$"
LICENCE_URL_PATTERN: Final = r"^https://[\x21-\x7e]{1,2040}$"

LicenceStatus = Literal["proposed", "accepted"]
"""Whether the dataset licence is ADR 0010's proposal or the maintainer's decision."""


class LicenceStatement(BaseModel):
    """The licence the exported data is offered under.

    Implements: Value Object.

    Attributes:
        spdx_id: SPDX identifier, for example ``CC-BY-4.0``.
        status: ``proposed`` until ADR 0010 is accepted, then ``accepted``.
        url: The licence's canonical ``https`` URL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spdx_id: str = Field(pattern=LICENCE_ID_PATTERN)
    status: LicenceStatus
    url: str = Field(pattern=LICENCE_URL_PATTERN)

    @property
    def label(self) -> str:
        """Return the identifier with its status, such as ``CC-BY-4.0 (proposed)``."""
        return f"{self.spdx_id} ({self.status})"


PROPOSED_DATASET_LICENCE: Final = LicenceStatement(
    spdx_id="CC-BY-4.0",
    status="proposed",
    url="https://creativecommons.org/licenses/by/4.0/",
)
"""ADR 0010's proposed data licence (status ``proposed`` until it is accepted).

The application should prefer the licence configured in settings; this constant is
the value the Phase 4 plan names while the ADR is open.
"""

GENERATOR_PATTERN: Final = r"^yakhnama/[0-9A-Za-z.+-]{1,64}$"
CITATION_MAX_LENGTH: Final = 2000
EXPORT_MAX_ROWS: Final = 10**9

SidecarCitation = Annotated[str, *safe_text(CITATION_MAX_LENGTH)]
"""How to cite an export: 1 to 2000 characters of single-line safe text."""


class MetadataSidecar(BaseModel):
    """The metadata written next to every export file.

    ``to_json_dict`` and ``to_json_bytes`` are the boundary: exporters and the API
    use them and never dump the model themselves, so the sidecar has one JSON
    shape (``model_validate`` of ``to_json_dict()`` returns an equal sidecar).

    Implements: Value Object.

    Attributes:
        licence: The data licence and whether it is still proposed.
        generated_at: When the file was generated, UTC.
        dataset: Which dataset the file holds.
        format: The file's format.
        filters: The filters the rows were selected with.
        schema_version: Version of the file layout.
        citation: How to cite the file.
        row_count: Number of data rows (records) in the file.
        checksum: SHA-256 of the file's bytes.
        generator: ``yakhnama/<version>`` of the software that wrote it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    licence: LicenceStatement
    generated_at: AwareDatetime
    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters = ExportFilters()
    schema_version: SchemaVersion
    citation: SidecarCitation
    row_count: int = Field(ge=0, le=EXPORT_MAX_ROWS)
    checksum: Sha256
    generator: str = Field(pattern=GENERATOR_PATTERN)

    @field_validator("generated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def to_json_dict(self) -> dict[str, object]:
        """Return the sidecar as JSON-compatible values: the serialisation boundary.

        Unset filters are left out, so a reader sees only the filters applied.

        Returns:
            Plain JSON values (strings, numbers, lists, objects); timestamps are
            ISO 8601 UTC strings ending in ``Z``.
        """
        return self.model_dump(mode="json", exclude_none=True)

    def to_json_bytes(self) -> bytes:
        """Return the sidecar file's content.

        Returns:
            ``to_json_dict()`` as UTF-8 JSON, indented by two spaces, with
            non-ASCII characters written as themselves.
        """
        return json.dumps(self.to_json_dict(), indent=2, ensure_ascii=False).encode(
            "utf-8"
        )


# --------------------------------------------------------------------------- #
# Validation report                                                           #
# --------------------------------------------------------------------------- #

IMPORT_MAX_ROWS: Final = 10_000
"""Most data rows one import may contain (**proposed**).

Equal to ``IMPORT_MAX_CREATED_IDS`` because every accepted row creates exactly one
event, whose id the job records.
"""

IMPORT_MAX_CREATED_IDS: Final = IMPORT_MAX_ROWS
"""Most event ids one import job records."""

REPORT_MAX_ISSUES: Final = 10_000
"""Most issues a report keeps; later ones are counted but dropped."""

ISSUE_MESSAGE_MAX_LENGTH: Final = 500
ISSUE_FIELD_PATTERN: Final = r"^[a-z][a-z0-9_.]{0,99}$"

IssueMessage = Annotated[str, *safe_text(ISSUE_MESSAGE_MAX_LENGTH)]
"""What is wrong: 1 to 500 characters of safe text that never quotes the value."""

IssueField = Annotated[str, StringConstraints(pattern=ISSUE_FIELD_PATTERN)]
"""The column or property a problem is in, such as ``claim_1_value``."""


class ValidationSeverity(StrEnum):
    """Whether a problem rejects its row.

    Implements: Value Object.
    """

    ERROR = "error"
    WARNING = "warning"


class RowIssue(BaseModel):
    """One problem found in one row of an import file.

    Implements: Value Object.

    Attributes:
        row_number: 1-based number of the data row (the header is not counted).
        field: The column at fault, or ``None`` for a problem of the whole row.
        message: What is wrong, without quoting the value.
        severity: ``error`` rejects the row; ``warning`` does not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int = Field(ge=1, le=IMPORT_MAX_ROWS)
    field: IssueField | None = None
    message: IssueMessage
    severity: ValidationSeverity = ValidationSeverity.ERROR

    @property
    def is_blocking(self) -> bool:
        """Tell whether the issue rejects its row.

        Returns:
            ``True`` for ``error``.
        """
        return self.severity is ValidationSeverity.ERROR


class ValidationReport(BaseModel):
    """Everything an import found, row by row, whether or not it was a dry run.

    A row is rejected if it has at least one ``error``. At most
    ``REPORT_MAX_ISSUES`` issues are kept, in row order; ``is_truncated`` says
    whether more were found. The row counts always cover every row.

    Implements: Value Object.

    Attributes:
        issues: The kept issues, ordered by row number.
        rows_seen: Data rows read.
        rows_valid: Rows without errors.
        rows_rejected: Rows with at least one error.
        is_truncated: Whether issues beyond the cap were dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issues: tuple[RowIssue, ...] = Field(default=(), max_length=REPORT_MAX_ISSUES)
    rows_seen: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    rows_valid: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    rows_rejected: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    is_truncated: bool = False

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.rows_valid + self.rows_rejected != self.rows_seen:
            message = "rows_valid + rows_rejected must equal rows_seen"
            raise ValueError(message)
        if any(issue.row_number > self.rows_seen for issue in self.issues):
            message = "an issue refers to a row that was not seen"
            raise ValueError(message)
        rows_with_errors = len(
            {issue.row_number for issue in self.issues if issue.is_blocking}
        )
        is_consistent = (
            rows_with_errors <= self.rows_rejected
            if self.is_truncated
            else rows_with_errors == self.rows_rejected
        )
        if not is_consistent:
            message = "rows_rejected must count the rows that have an error"
            raise ValueError(message)
        if self.is_truncated and len(self.issues) != REPORT_MAX_ISSUES:
            message = "only a report holding the maximum of issues is truncated"
            raise ValueError(message)
        return self

    @property
    def has_blocking_errors(self) -> bool:
        """Tell whether any row was rejected.

        Returns:
            ``True`` if ``rows_rejected`` is positive.
        """
        return self.rows_rejected > 0

    @property
    def error_count(self) -> int:
        """Return how many kept issues are errors."""
        return sum(1 for issue in self.issues if issue.is_blocking)

    @property
    def warning_count(self) -> int:
        """Return how many kept issues are warnings."""
        return len(self.issues) - self.error_count

    @classmethod
    def from_issues(cls, rows_seen: int, issues: Iterable[RowIssue]) -> Self:
        """Build the report of ``rows_seen`` rows from every issue found in them.

        Args:
            rows_seen: How many data rows were read.
            issues: Every issue, in any order.

        Returns:
            The report: issues sorted by row number (stable within a row) and cut
            to ``REPORT_MAX_ISSUES``, with counts over all of them.

        Raises:
            pydantic.ValidationError: If an issue's row number exceeds
                ``rows_seen``, or ``rows_seen`` is out of range.
        """
        ordered = sorted(issues, key=lambda issue: issue.row_number)
        rejected = len({issue.row_number for issue in ordered if issue.is_blocking})
        return cls(
            issues=tuple(ordered[:REPORT_MAX_ISSUES]),
            rows_seen=rows_seen,
            rows_valid=rows_seen - rejected,
            rows_rejected=rejected,
            is_truncated=len(ordered) > REPORT_MAX_ISSUES,
        )


# --------------------------------------------------------------------------- #
# Batches                                                                     #
# --------------------------------------------------------------------------- #

DEFAULT_IMPORT_BATCH_SIZE: Final = 500
"""Rows written per transaction by a real import (**proposed**)."""


class ImportBatch(BaseModel):
    """A contiguous range of data rows written in one transaction.

    Implements: Value Object.

    Attributes:
        number: 1-based position of the batch in the import.
        first_row: First data row, inclusive.
        last_row: Last data row, inclusive, never before ``first_row``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1, le=IMPORT_MAX_ROWS)
    first_row: int = Field(ge=1, le=IMPORT_MAX_ROWS)
    last_row: int = Field(ge=1, le=IMPORT_MAX_ROWS)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.last_row < self.first_row:
            message = "last_row must not be before first_row"
            raise ValueError(message)
        return self

    @property
    def size(self) -> int:
        """Return how many rows the batch covers."""
        return self.last_row - self.first_row + 1

    def contains(self, row_number: int) -> bool:
        """Tell whether a data row belongs to this batch.

        Args:
            row_number: 1-based data row number.

        Returns:
            ``True`` if ``first_row <= row_number <= last_row``.
        """
        return self.first_row <= row_number <= self.last_row


def plan_import_batches(
    row_count: int, batch_size: int = DEFAULT_IMPORT_BATCH_SIZE
) -> tuple[ImportBatch, ...]:
    """Cut rows ``1..row_count`` into consecutive batches of ``batch_size`` rows.

    Args:
        row_count: Number of data rows, 0 to ``IMPORT_MAX_ROWS``.
        batch_size: Rows per batch, at least 1; the last batch may be smaller.

    Returns:
        The batches in order; empty when ``row_count`` is 0.

    Raises:
        ValidationError: If ``row_count`` or ``batch_size`` is out of range.
    """
    if not 0 <= row_count <= IMPORT_MAX_ROWS or batch_size < 1:
        message = (
            f"need 0 <= row_count <= {IMPORT_MAX_ROWS} and batch_size >= 1, "
            f"got row_count={row_count}, batch_size={batch_size}"
        )
        raise KernelValidationError(message)
    return tuple(
        ImportBatch(
            number=index + 1,
            first_row=first,
            last_row=min(first + batch_size - 1, row_count),
        )
        for index, first in enumerate(range(1, row_count + 1, batch_size))
    )


class ImportWrites(BaseModel):
    """What a real import wrote: the created events, their lineage and batch count.

    Implements: Value Object.

    Attributes:
        created_ids: Ids of the events created, in creation order, distinct.
        lineage_source_id: The ``dataset`` source every created record cites;
            required when anything was created.
        batches_applied: Batches committed; at least 1 when anything was created.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    created_ids: tuple[EntityId, ...] = Field(
        default=(), max_length=IMPORT_MAX_CREATED_IDS
    )
    lineage_source_id: EntityId | None = None
    batches_applied: int = Field(default=0, ge=0, le=IMPORT_MAX_ROWS)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if len(set(self.created_ids)) != len(self.created_ids):
            message = "created_ids must be distinct"
            raise ValueError(message)
        if self.created_ids and (
            self.lineage_source_id is None or self.batches_applied == 0
        ):
            message = "created records need a lineage source and an applied batch"
            raise ValueError(message)
        return self

    @property
    def is_empty(self) -> bool:
        """Tell whether nothing was written.

        Returns:
            ``True`` without ids, batches or a lineage source.
        """
        return (
            not self.created_ids
            and self.batches_applied == 0
            and self.lineage_source_id is None
        )


NO_WRITES: Final = ImportWrites()
"""The writes of a dry run, or of an import that stopped before its first batch."""


class ImportRequest(BaseModel):
    """Which stored file an import reads, in which format, and whether it writes.

    Implements: Value Object.

    Attributes:
        format: The file's format.
        source_artifact: The stored file.
        dry_run: ``True`` to validate only; no default, so a caller always says.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: ImportFormat
    source_artifact: ArtifactRef
    dry_run: bool
