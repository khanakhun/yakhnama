"""The ``ExportJob`` and ``ImportJob`` aggregates: asynchronous exchange work.

Both are jobs with one lifecycle, driven by an explicit transition table:

- export: ``queued`` → ``running`` → ``completed`` | ``failed``; a queued export
  may also fail (it could not be dispatched) or be ``cancelled``;
- import: ``queued`` → ``running`` → ``completed`` | ``failed``; a queued import
  may also fail. Imports cannot be cancelled: once batches commit, stopping half
  way is a failure that records what was written.

A completed export holds its artifact and a metadata sidecar that describes exactly
that artifact (same dataset, format, filters and checksum). An import records its
validation report, the ids of the events it created (never for a dry run) and the
``dataset`` source that records their lineage.

Every change validates the whole new state, bumps ``version`` by one, stamps the
relevant timestamp from the injected ``Clock`` and draws event ids from the injected
``IdGenerator``.

Patterns: Entity, Aggregate Root, State, Domain Events.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from yakhnama.modules.exchange.domain.errors import JobKind, JobStateError
from yakhnama.modules.exchange.domain.events import (
    ExportCancelled,
    ExportCompleted,
    ExportFailed,
    ExportJobEvent,
    ExportStarted,
    ImportCompleted,
    ImportFailed,
    ImportJobEvent,
    ImportStarted,
)
from yakhnama.modules.exchange.domain.registry import DEFAULT_FORMAT_REGISTRY
from yakhnama.modules.exchange.domain.value_objects import (
    NO_WRITES,
    ArtifactRef,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ExportVisibility,
    ImportFormat,
    ImportWrites,
    JobErrorSummary,
    JobStatus,
    JobVersion,
    MetadataSidecar,
    ValidationReport,
    require_visibility_fits,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

type TransitionTable = Mapping[JobStatus, frozenset[JobStatus]]

EXPORT_TRANSITIONS: Final[TransitionTable] = {
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED}),
}
"""Which status an export may move to from each non-final status."""

IMPORT_TRANSITIONS: Final[TransitionTable] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED}),
}
"""Which status an import may move to from each non-final status."""


class _Shape(BaseModel):
    """Which optional facts a status requires and which it allows.

    Implements: Value Object.

    Attributes:
        required: Fields that must be set.
        allowed: Fields that may be set; a superset of ``required``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    required: frozenset[str]
    allowed: frozenset[str]


_EXPORT_FACTS: Final = ("started_at", "finished_at", "artifact", "sidecar")
_EXPORT_SHAPES: Final = {
    JobStatus.QUEUED: _Shape(required=frozenset(), allowed=frozenset()),
    JobStatus.RUNNING: _Shape(
        required=frozenset({"started_at"}), allowed=frozenset({"started_at"})
    ),
    JobStatus.COMPLETED: _Shape(
        required=frozenset(_EXPORT_FACTS), allowed=frozenset(_EXPORT_FACTS)
    ),
    JobStatus.FAILED: _Shape(
        required=frozenset({"finished_at", "error_summary"}),
        allowed=frozenset({"started_at", "finished_at", "error_summary"}),
    ),
    JobStatus.CANCELLED: _Shape(
        required=frozenset({"finished_at"}), allowed=frozenset({"finished_at"})
    ),
}

_IMPORT_FACTS: Final = ("started_at", "finished_at", "report")
_IMPORT_SHAPES: Final = {
    JobStatus.QUEUED: _Shape(required=frozenset(), allowed=frozenset()),
    JobStatus.RUNNING: _Shape(
        required=frozenset({"started_at"}), allowed=frozenset({"started_at"})
    ),
    JobStatus.COMPLETED: _Shape(
        required=frozenset(_IMPORT_FACTS), allowed=frozenset(_IMPORT_FACTS)
    ),
    JobStatus.FAILED: _Shape(
        required=frozenset({"finished_at", "error_summary"}),
        allowed=frozenset({"started_at", "finished_at", "report", "error_summary"}),
    ),
}
"""No ``cancelled`` shape: imports are never cancelled, so that status is refused."""


def _check_shape(
    model: BaseModel,
    status: JobStatus,
    shapes: Mapping[JobStatus, _Shape],
    optional: Sequence[str],
) -> None:
    present = {name for name in optional if getattr(model, name) is not None}
    shape = shapes.get(status)
    if shape is None:
        message = f"this kind of job is never {status.value}"
        raise ValueError(message)
    if not shape.required <= present <= shape.allowed:
        message = (
            f"a {status.value} job needs {sorted(shape.required)} and allows only "
            f"{sorted(shape.allowed)}; it has {sorted(present)}"
        )
        raise ValueError(message)


def _check_order(
    requested_at: datetime, started_at: datetime | None, finished_at: datetime | None
) -> None:
    moments = [requested_at, started_at, finished_at]
    known = [moment for moment in moments if moment is not None]
    if known != sorted(known):
        message = "requested_at <= started_at <= finished_at must hold"
        raise ValueError(message)


def _to_utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


class ExportJob(BaseModel):
    """One requested export of a dataset in one format.

    Invariants, checked on every construction:

    - each status has exactly its facts (see the module docs): only a completed
      job has an artifact and a sidecar; only a failed job has an error summary;
    - the sidecar describes the artifact: same dataset, format, filters and
      visibility, its checksum is the artifact's digest, and the artifact's media
      type is the format's;
    - a ``reports`` export has ``moderation`` visibility;
    - ``requested_at <= started_at <= finished_at`` where set.

    Implements: Entity / Aggregate Root, State.

    Attributes:
        id: Stable identity (UUIDv7).
        requested_by: The requesting user.
        dataset: Which dataset.
        format: Which format.
        filters: The filters the rows are selected with.
        visibility: Whose view of the record the file holds, fixed at request
            time: ``moderation`` if the requester could moderate, else ``public``.
        status: Where the job is in its lifecycle.
        artifact: The stored file, once completed.
        sidecar: The file's metadata, once completed.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it completed, failed or was cancelled, UTC.
        version: Optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    requested_by: EntityId
    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters = ExportFilters()
    visibility: ExportVisibility = "public"
    status: JobStatus = JobStatus.QUEUED
    artifact: ArtifactRef | None = None
    sidecar: MetadataSidecar | None = None
    error_summary: JobErrorSummary | None = None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    version: JobVersion = 1

    @field_validator("requested_at", "started_at", "finished_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime | None) -> datetime | None:
        return _to_utc(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _check_shape(
            self, self.status, _EXPORT_SHAPES, (*_EXPORT_FACTS, "error_summary")
        )
        _check_order(self.requested_at, self.started_at, self.finished_at)
        require_visibility_fits(self.dataset, self.visibility)
        if self.artifact is not None and self.sidecar is not None:
            self._check_sidecar(self.artifact, self.sidecar)
        return self

    def _check_sidecar(self, artifact: ArtifactRef, sidecar: MetadataSidecar) -> None:
        expected_media_type = DEFAULT_FORMAT_REGISTRY.export_descriptor(
            self.format.value
        ).media_type
        if (
            sidecar.dataset is not self.dataset
            or sidecar.format is not self.format
            or sidecar.filters != self.filters
            or sidecar.visibility != self.visibility
        ):
            message = (
                "the sidecar must describe this job's dataset, format, filters "
                "and visibility"
            )
            raise ValueError(message)
        if sidecar.checksum != artifact.sha256:
            message = "the sidecar checksum must be the artifact's sha256"
            raise ValueError(message)
        if artifact.media_type != expected_media_type:
            message = (
                f"a {self.format.value} artifact has media type {expected_media_type}"
            )
            raise ValueError(message)

    def start(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["ExportJob"]:
        """Record that a worker picked the job up.

        Args:
            clock: Source of ``started_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The running job and ``ExportStarted``.

        Raises:
            JobStateError: If the job is not queued.
        """
        self._require_transition(JobStatus.RUNNING, "start")
        now = clock.now()
        state = self._evolve(status=JobStatus.RUNNING, started_at=now)
        return AggregateChange[ExportJob](
            state=state, events=(state._event(ExportStarted, ids, now),)
        )

    def complete(
        self,
        artifact: ArtifactRef,
        sidecar: MetadataSidecar,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["ExportJob"]:
        """Record the stored file and its sidecar.

        Args:
            artifact: Where the file is and its digest, size and media type.
            sidecar: The metadata written next to it.
            clock: Source of ``finished_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The completed job and ``ExportCompleted``.

        Raises:
            JobStateError: If the job is not running.
            pydantic.ValidationError: If the sidecar does not describe the
                artifact and this job.
        """
        self._require_transition(JobStatus.COMPLETED, "complete")
        now = clock.now()
        state = self._evolve(
            status=JobStatus.COMPLETED,
            artifact=artifact,
            sidecar=sidecar,
            finished_at=now,
        )
        event = state._event(
            ExportCompleted,
            ids,
            now,
            row_count=sidecar.row_count,
            byte_size=artifact.byte_size,
        )
        return AggregateChange[ExportJob](state=state, events=(event,))

    def fail(
        self, summary: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["ExportJob"]:
        """Record that the export could not be produced.

        Args:
            summary: Why, 1 to 1000 characters of single-line safe text; it must
                not quote row values.
            clock: Source of ``finished_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The failed job and ``ExportFailed``.

        Raises:
            JobStateError: If the job is already final.
            pydantic.ValidationError: If ``summary`` is empty, too long or unsafe.
        """
        self._require_transition(JobStatus.FAILED, "fail")
        now = clock.now()
        state = self._evolve(
            status=JobStatus.FAILED, error_summary=summary, finished_at=now
        )
        return AggregateChange[ExportJob](
            state=state, events=(state._event(ExportFailed, ids, now),)
        )

    def cancel(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["ExportJob"]:
        """Cancel a job no worker has started.

        Args:
            clock: Source of ``finished_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The cancelled job and ``ExportCancelled``.

        Raises:
            JobStateError: If the job is not queued.
        """
        self._require_transition(JobStatus.CANCELLED, "cancel")
        now = clock.now()
        state = self._evolve(status=JobStatus.CANCELLED, finished_at=now)
        return AggregateChange[ExportJob](
            state=state, events=(state._event(ExportCancelled, ids, now),)
        )

    def _require_transition(self, target: JobStatus, action: str) -> None:
        if not can_transition(EXPORT_TRANSITIONS, self.status, target):
            kind: JobKind = "export_job"
            raise JobStateError.for_job(kind, self.id, self.status.value, action)

    def _evolve(self, **updates: object) -> "ExportJob":
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant.
        return self.model_validate(
            {**_fields_of(self), **updates, "version": self.version + 1}
        )

    def _event[EventT: ExportJobEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "version": self.version,
                **fields,
            }
        )


class ImportJob(BaseModel):
    """One import of a stored file, validated row by row and written in batches.

    Invariants, checked on every construction:

    - each status has exactly its facts (see the module docs); a completed job has
      a report; a failed job has an error summary and may have a report; an
      import is never ``cancelled``;
    - a dry run never records created ids, batches or a lineage source;
    - created ids are distinct, at most ``IMPORT_MAX_CREATED_IDS``, and exist only
      with a lineage source and at least one applied batch (``ImportWrites``);
    - a completed real import whose report has blocking errors created nothing
      (it must fail instead, or complete with zero created);
    - a completed import created at most one event per valid row;
    - ``requested_at <= started_at <= finished_at`` where set.

    Implements: Entity / Aggregate Root, State.

    Attributes:
        id: Stable identity (UUIDv7).
        requested_by: The requesting moderator.
        format: The file's format.
        dry_run: Whether the import only validates.
        source_artifact: The stored file to import.
        status: Where the job is in its lifecycle.
        report: The row-level validation report, once produced.
        writes: The created event ids, their ``dataset`` lineage source and the
            batches committed; empty for a dry run.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it completed or failed, UTC.
        version: Optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    requested_by: EntityId
    format: ImportFormat
    dry_run: bool
    source_artifact: ArtifactRef
    status: JobStatus = JobStatus.QUEUED
    report: ValidationReport | None = None
    writes: ImportWrites = NO_WRITES
    error_summary: JobErrorSummary | None = None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    version: JobVersion = 1

    @field_validator("requested_at", "started_at", "finished_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime | None) -> datetime | None:
        return _to_utc(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _check_shape(
            self, self.status, _IMPORT_SHAPES, (*_IMPORT_FACTS, "error_summary")
        )
        _check_order(self.requested_at, self.started_at, self.finished_at)
        if self.dry_run and not self.writes.is_empty:
            message = "a dry run never records created ids, batches or lineage"
            raise ValueError(message)
        if self.status is JobStatus.COMPLETED and self.report is not None:
            if self.report.has_blocking_errors and self.writes.created_ids:
                message = "an import with blocking errors must fail or create nothing"
                raise ValueError(message)
            if len(self.writes.created_ids) > self.report.rows_valid:
                message = "an import creates at most one event per valid row"
                raise ValueError(message)
        return self

    @property
    def created_ids(self) -> tuple[EntityId, ...]:
        """Return the ids of the events the import created, in creation order."""
        return self.writes.created_ids

    @property
    def created_count(self) -> int:
        """Return how many events the import created."""
        return len(self.writes.created_ids)

    def start(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["ImportJob"]:
        """Record that a worker picked the job up.

        Args:
            clock: Source of ``started_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The running job and ``ImportStarted``.

        Raises:
            JobStateError: If the job is not queued.
        """
        self._require_transition(JobStatus.RUNNING, "start")
        now = clock.now()
        state = self._evolve(status=JobStatus.RUNNING, started_at=now)
        return AggregateChange[ImportJob](
            state=state, events=(state._event(ImportStarted, ids, now),)
        )

    def complete(
        self,
        report: ValidationReport,
        writes: ImportWrites = NO_WRITES,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["ImportJob"]:
        """Record the validation report and, for a real import, what was written.

        Args:
            report: The row-level validation report.
            writes: The events created, their lineage source and the batches
                committed; ``NO_WRITES`` for a dry run or when the report has
                blocking errors.
            clock: Source of ``finished_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The completed job and ``ImportCompleted``.

        Raises:
            JobStateError: If the job is not running.
            pydantic.ValidationError: If the outcome breaks an invariant, for
                example writes for a dry run or for a report with blocking errors.
        """
        self._require_transition(JobStatus.COMPLETED, "complete")
        now = clock.now()
        state = self._evolve(
            status=JobStatus.COMPLETED, report=report, writes=writes, finished_at=now
        )
        return AggregateChange[ImportJob](
            state=state, events=(state._outcome_event(ImportCompleted, ids, now),)
        )

    def fail(
        self,
        summary: str,
        report: ValidationReport | None = None,
        writes: ImportWrites = NO_WRITES,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["ImportJob"]:
        """Record that the import stopped, keeping whatever batches committed.

        Args:
            summary: Why, 1 to 1000 characters of single-line safe text; it must
                not quote row values.
            report: The validation report, if one was produced.
            writes: What batches committed before the failure wrote.
            clock: Source of ``finished_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The failed job and ``ImportFailed``.

        Raises:
            JobStateError: If the job is already final.
            pydantic.ValidationError: If ``summary`` is invalid or the outcome
                breaks an invariant.
        """
        self._require_transition(JobStatus.FAILED, "fail")
        now = clock.now()
        state = self._evolve(
            status=JobStatus.FAILED,
            error_summary=summary,
            report=report,
            writes=writes,
            finished_at=now,
        )
        return AggregateChange[ImportJob](
            state=state, events=(state._outcome_event(ImportFailed, ids, now),)
        )

    def _require_transition(self, target: JobStatus, action: str) -> None:
        if not can_transition(IMPORT_TRANSITIONS, self.status, target):
            kind: JobKind = "import_job"
            raise JobStateError.for_job(kind, self.id, self.status.value, action)

    def _evolve(self, **updates: object) -> "ImportJob":
        return self.model_validate(
            {**_fields_of(self), **updates, "version": self.version + 1}
        )

    def _outcome_event[EventT: ImportJobEvent](
        self, event_class: type[EventT], ids: IdGenerator, now: datetime
    ) -> EventT:
        report = self.report
        return self._event(
            event_class,
            ids,
            now,
            dry_run=self.dry_run,
            rows_seen=0 if report is None else report.rows_seen,
            rows_rejected=0 if report is None else report.rows_rejected,
            created_count=self.created_count,
            batches_applied=self.writes.batches_applied,
        )

    def _event[EventT: ImportJobEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "version": self.version,
                **fields,
            }
        )


def _fields_of(model: BaseModel) -> dict[str, object]:
    return {name: getattr(model, name) for name in type(model).model_fields}


def can_transition(
    table: TransitionTable, current: JobStatus, target: JobStatus
) -> bool:
    """Tell whether a job may move from ``current`` to ``target``.

    Args:
        table: ``EXPORT_TRANSITIONS`` or ``IMPORT_TRANSITIONS``.
        current: The job's status now.
        target: The status asked for.

    Returns:
        ``True`` if the table lists the move.
    """
    return target in table.get(current, frozenset())
