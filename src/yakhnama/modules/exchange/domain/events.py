"""Domain events of the ``exchange`` bounded context.

Every event carries the job's id as ``aggregate_id``, ``export_job`` or
``import_job`` as ``aggregate_type`` and the job's ``version`` after the change.
**Payloads carry ids, codes and counts only**: never an object key (it would let a
subscriber fetch a file), never filters' values beyond codes, never an error summary
or a row value (free text), because events are relayed to subscribers and kept in
the outbox (``AGENTS.md`` §5).

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.exchange.domain.value_objects import (
    ARTIFACT_MAX_BYTES,
    EXPORT_MAX_ROWS,
    IMPORT_MAX_CREATED_IDS,
    IMPORT_MAX_ROWS,
    ExportDataset,
    ExportFormat,
    ImportFormat,
    JobVersion,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

EXPORT_JOB_AGGREGATE_TYPE: Final = "export_job"
IMPORT_JOB_AGGREGATE_TYPE: Final = "import_job"


class ExportJobEvent(DomainEvent):
    """Fields shared by every event about an export job; never published alone.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"export_job"``.
        version: The job's version after the change.
    """

    aggregate_type: Literal["export_job"] = EXPORT_JOB_AGGREGATE_TYPE
    version: JobVersion


class ExportRequested(ExportJobEvent):
    """An export was requested and queued.

    Implements: Domain Events.

    Attributes:
        requested_by: The requesting user.
        dataset: Which dataset.
        format: Which format.
    """

    event_type: ClassVar[str] = "exchange.export_requested"

    requested_by: EntityId
    dataset: ExportDataset
    format: ExportFormat


class ExportStarted(ExportJobEvent):
    """A worker started writing the export.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.export_started"


class ExportCompleted(ExportJobEvent):
    """The export file and its sidecar were stored.

    Implements: Domain Events.

    Attributes:
        row_count: Rows written.
        byte_size: Size of the file in bytes.
    """

    event_type: ClassVar[str] = "exchange.export_completed"

    row_count: int = Field(ge=0, le=EXPORT_MAX_ROWS)
    byte_size: int = Field(ge=0, le=ARTIFACT_MAX_BYTES)


class ExportFailed(ExportJobEvent):
    """The export could not be produced; the reason stays on the job.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.export_failed"


class ExportCancelled(ExportJobEvent):
    """A queued export was cancelled before it started.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.export_cancelled"


class ImportJobEvent(DomainEvent):
    """Fields shared by every event about an import job; never published alone.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"import_job"``.
        version: The job's version after the change.
    """

    aggregate_type: Literal["import_job"] = IMPORT_JOB_AGGREGATE_TYPE
    version: JobVersion


class ImportRequested(ImportJobEvent):
    """An import was requested and queued.

    Implements: Domain Events.

    Attributes:
        requested_by: The requesting moderator.
        format: Which format the file is in.
        dry_run: Whether the import only validates.
    """

    event_type: ClassVar[str] = "exchange.import_requested"

    requested_by: EntityId
    format: ImportFormat
    dry_run: bool


class ImportStarted(ImportJobEvent):
    """A worker started reading the import file.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.import_started"


class ImportOutcome(ImportJobEvent):
    """Counts shared by the events that end an import; never published alone.

    Implements: Domain Events.

    Attributes:
        dry_run: Whether the import only validated.
        rows_seen: Data rows read, ``0`` if no report was produced.
        rows_rejected: Rows with at least one error.
        created_count: Events created (always 0 for a dry run).
        batches_applied: Batches committed.
    """

    dry_run: bool
    rows_seen: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    rows_rejected: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    created_count: int = Field(ge=0, le=IMPORT_MAX_CREATED_IDS)
    batches_applied: int = Field(ge=0, le=IMPORT_MAX_ROWS)


class ImportCompleted(ImportOutcome):
    """The import finished: validated only, or validated and written.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.import_completed"


class ImportFailed(ImportOutcome):
    """The import stopped; any batches already committed stay, and are counted.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "exchange.import_failed"
