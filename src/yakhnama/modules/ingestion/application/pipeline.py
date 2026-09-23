"""The ingestion pipeline: one fixed order of stages, each a hook a source fills.

``IngestionPipeline.run`` is the template. For one running ``IngestionRun`` it calls,
in this order and never another:

1. ``fetch`` - the bytes, through the ``SourceAdapter`` (default);
2. ``parse`` - bytes to source-shaped rows (**abstract**);
3. ``validate`` - per row, the problems that make it unusable (default: normalise
   it and check the site, the variable and its unit, and missing values);
4. ``normalise`` - a valid row to an ``ObservationDraft`` in SI units and WGS84
   (**abstract**);
5. ``deduplicate`` - drop repeated natural keys within the batch (default: the
   first record wins);
6. ``persist`` - append through ``ObservationRepository.append_many``, which skips
   keys stored before, so a re-run stores nothing twice (default);
7. ``record_lineage`` - finish the run with its outcome, report, counts and the
   checksum of the bytes read (default).

Between ``fetch`` and ``parse`` the template checks that the bytes hash to the
version's ``input_checksum``: observations point at a version, and a version pins
exactly one set of bytes, so a mismatch fails the run before anything is stored.

**Problems in the data never raise.** Every hook reports them to the run's
``ValidationCollector`` (``reject`` for a record, ``warn`` or ``error`` for the
input as a whole), which caps what it keeps like ``IngestionReport`` and derives
``RunCounts``. Only failures of the machinery (transport, storage, a broken hook)
escape ``run``; the handler then rolls back, asks ``failure_report`` for the
report and marks the run ``failed``.

Patterns: Template Method, Value Object.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Final, Literal, final

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.ingestion.application.dto import IngestionOutcome
from yakhnama.modules.ingestion.application.ports import (
    IngestionUnitOfWork,
    RawPayload,
    SourceAdapter,
)
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
)
from yakhnama.modules.ingestion.domain.errors import VersionDatasetMismatchError
from yakhnama.modules.ingestion.domain.value_objects import (
    ISSUE_MESSAGE_MAX_LENGTH,
    ISSUE_REFERENCE_MAX_LENGTH,
    MAX_REPORT_ISSUES,
    GridCellRef,
    IngestionIssue,
    IngestionReport,
    InputChecksum,
    IssueSeverity,
    IssueStage,
    ObservationKey,
    QualityFlag,
    RunCounts,
    RunStatus,
    StationRef,
    require_variable_unit,
    variable_definition,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    InvariantViolationError,
    ValidationError,
    YakhnamaError,
)
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.text import is_forbidden_character, normalise_text
from yakhnama.shared_kernel.value_objects import DateWithPrecision, Measurement

PERSIST_BATCH_SIZE: Final = 1000
"""Observations per ``append_many`` call (**proposed** operational default): bounds
one statement's size; every batch still shares the run's transaction."""

VARIABLE_TEXT_MAX_LENGTH: Final = 64

RejectStage = Literal["parse", "validate", "normalise"]
"""The stages that judge single records; a rejection there makes a record invalid
(``validate``, ``normalise``) or unparsed (``parse``)."""

_FALLBACK_MESSAGE: Final = "the problem could not be described"
_CHECKSUM_MISMATCH: Final = (
    "the fetched bytes do not match the checksum recorded for the dataset version"
)
_NO_RECORDS: Final = "the source returned no records"
_CONFLICTING_DUPLICATE: Final = (
    "a later record with the same key and a different value or quality was "
    "dropped; the first one is kept"
)


def _printable(text: str, max_length: int) -> str | None:
    # Issue text often quotes source data; forbidden characters become spaces and
    # the result is cut to the field's bound, so building an issue never raises.
    cleaned = normalise_text(
        "".join(" " if is_forbidden_character(char) else char for char in text)
    )
    cleaned = cleaned[:max_length].strip()
    return cleaned or None


def issue_message(text: str) -> str:
    """Return ``text`` as a valid ``IssueMessage``.

    Args:
        text: Any description, possibly quoting source data.

    Returns:
        The text without control or bidirectional characters, cut to
        ``ISSUE_MESSAGE_MAX_LENGTH``; a fixed sentence if nothing is left.
    """
    return _printable(text, ISSUE_MESSAGE_MAX_LENGTH) or _FALLBACK_MESSAGE


def issue_reference(text: str | None) -> str | None:
    """Return ``text`` as a valid ``IssueReference``, or ``None``.

    Args:
        text: Where in the input a problem is, if it concerns one record.

    Returns:
        The cleaned reference cut to ``ISSUE_REFERENCE_MAX_LENGTH``, or ``None``.
    """
    return None if text is None else _printable(text, ISSUE_REFERENCE_MAX_LENGTH)


def error_messages(error: ValueError | ValidationError) -> tuple[str, ...]:
    """Turn a validation failure into one message per problem.

    Args:
        error: A Pydantic ``ValidationError`` (one message per field error), a
            kernel ``ValidationError`` (its message) or another ``ValueError`` (its
            text).

    Returns:
        At least one message; field errors read ``"<field path>: <message>"``.
    """
    if isinstance(error, PydanticValidationError):
        # A Pydantic ValidationError always carries at least one error.
        return tuple(
            f"{'.'.join(str(part) for part in detail['loc']) or 'value'}: "
            f"{detail['msg']}"
            for detail in error.errors(include_url=False, include_input=False)
        )
    if isinstance(error, YakhnamaError):
        return (error.message,)
    return (str(error),)


def _with_details(error: YakhnamaError) -> str:
    # Domain error messages are fixed sentences; the (already sanitised) details
    # say which variable or unit, which a curator needs to fix the source.
    details = ", ".join(f"{key}={value}" for key, value in error.details.items())
    return f"{error.message} ({details})" if details else error.message


class ValidationCollector:
    """Collects every issue of one run and derives its counts.

    Hooks report problems here instead of raising. At most ``MAX_REPORT_ISSUES``
    issues are kept; later ones are counted by severity, exactly as
    ``IngestionReport.collect`` does, so memory stays bounded however broken the
    source is. Counts follow the ``RunCounts`` invariants by construction:
    ``fetched = parsed + unparsed``, ``valid + invalid <= parsed`` and
    ``deduplicated + persisted = valid`` once persisted.

    Implements: Template Method (the state every hook reports into).
    """

    def __init__(self) -> None:
        """Create an empty collector."""
        self._issues: list[IngestionIssue] = []
        self._omitted = {IssueSeverity.ERROR: 0, IssueSeverity.WARNING: 0}
        self._unparsed = 0
        self._parsed = 0
        self._valid = 0
        self._invalid = 0
        self._persisted: int | None = None

    def add(self, issue: IngestionIssue) -> None:
        """Keep ``issue``, or count it once the cap is reached.

        Args:
            issue: The issue.
        """
        if len(self._issues) < MAX_REPORT_ISSUES:
            self._issues.append(issue)
        else:
            self._omitted[issue.severity] += 1

    def error(
        self, stage: IssueStage, message: str, *, reference: str | None = None
    ) -> None:
        """Record an error that concerns the input or the run, not one record.

        Args:
            stage: The hook that found it.
            message: What went wrong; cleaned with ``issue_message``.
            reference: Where, if anywhere in particular.
        """
        self._record(stage, message, reference, IssueSeverity.ERROR)

    def warn(
        self, stage: IssueStage, message: str, *, reference: str | None = None
    ) -> None:
        """Record a warning; warnings never make a run fail or a record invalid.

        Args:
            stage: The hook that found it.
            message: What is worth knowing; cleaned with ``issue_message``.
            reference: Where, if anywhere in particular.
        """
        self._record(stage, message, reference, IssueSeverity.WARNING)

    def reject(
        self, stage: RejectStage, reference: str, messages: Sequence[str]
    ) -> None:
        """Record one record as unusable, with one error per message.

        A record rejected in ``parse`` counts as fetched but not parsed; in
        ``validate`` or ``normalise`` as invalid.

        Args:
            stage: Where it was rejected.
            reference: Which record, for example ``record 12``.
            messages: Why; an empty sequence records one generic error.
        """
        for message in messages or (_FALLBACK_MESSAGE,):
            self._record(stage, message, reference, IssueSeverity.ERROR)
        if stage == "parse":
            self._unparsed += 1
        else:
            self._invalid += 1

    def record_parsed(self, count: int) -> None:
        """Record how many rows ``parse`` returned.

        Args:
            count: The number of rows.
        """
        self._parsed = count

    def record_valid(self, count: int) -> None:
        """Record how many rows became observations.

        Args:
            count: The number of observations before deduplication.
        """
        self._valid = count

    def record_persisted(self, count: int) -> None:
        """Record how many observations were stored; the rest were duplicates.

        Args:
            count: The number ``persist`` returned.
        """
        self._persisted = count

    @property
    def issues(self) -> tuple[IngestionIssue, ...]:
        """Return the kept issues, in the order they were found."""
        return tuple(self._issues)

    @property
    def counts(self) -> RunCounts:
        """Return the counts reached so far.

        Returns:
            The counts; ``deduplicated`` and ``persisted`` stay 0 until
            ``record_persisted``.
        """
        persisted = self._persisted or 0
        deduplicated = 0 if self._persisted is None else self._valid - persisted
        return RunCounts(
            fetched=self._parsed + self._unparsed,
            parsed=self._parsed,
            valid=self._valid,
            invalid=self._invalid,
            deduplicated=deduplicated,
            persisted=persisted,
        )

    def report(self) -> IngestionReport:
        """Return the report with the counts reached so far.

        Returns:
            The report.
        """
        return self._build(self.counts)

    def report_after_rollback(self) -> IngestionReport:
        """Return the report of a run whose transaction was rolled back.

        Nothing the run staged was stored, so ``persisted`` and ``deduplicated``
        (which is derived from it) are 0; the earlier counts stay as reached.

        Returns:
            The report.
        """
        counts = self.counts
        return self._build(
            RunCounts(
                fetched=counts.fetched,
                parsed=counts.parsed,
                valid=counts.valid,
                invalid=counts.invalid,
            )
        )

    def _record(
        self,
        stage: IssueStage,
        message: str,
        reference: str | None,
        severity: IssueSeverity,
    ) -> None:
        self.add(
            IngestionIssue(
                stage=stage,
                reference=issue_reference(reference),
                message=issue_message(message),
                severity=severity,
            )
        )

    def _build(self, counts: RunCounts) -> IngestionReport:
        return IngestionReport(
            issues=tuple(self._issues),
            counts=counts,
            omitted_error_count=self._omitted[IssueSeverity.ERROR],
            omitted_warning_count=self._omitted[IssueSeverity.WARNING],
        )


class ObservationDraft(BaseModel):
    """A normalised record before it becomes an ``Observation``.

    ``normalise`` returns drafts so a source never has to know the run's lineage
    ids, and so problems can be reported as sentences rather than as an
    ``Observation`` construction error; ``variable`` is plain text here for the
    same reason.

    Implements: Value Object.

    Attributes:
        station: The station, for station data.
        grid_cell: The grid cell, for gridded data.
        variable: The variable code, checked against ``VARIABLES`` by
            ``problems``.
        value: The value in the registry unit, or ``None`` when missing.
        observed_at: When it was observed, with precision.
        quality: The mapped quality flag.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    station: StationRef | None = None
    grid_cell: GridCellRef | None = None
    variable: str = Field(min_length=1, max_length=VARIABLE_TEXT_MAX_LENGTH)
    value: Measurement | None
    observed_at: DateWithPrecision
    quality: QualityFlag

    def problems(self) -> tuple[str, ...]:
        """Return why the draft cannot become an observation.

        Checks exactly one site, a registered variable in its registry unit
        (``require_variable_unit``), and a value absent exactly when the quality
        is ``missing``.

        Returns:
            One sentence per problem; empty if the draft is usable.
        """
        problems: list[str] = []
        if (self.station is None) == (self.grid_cell is None):
            problems.append("a record needs exactly one of a station and a grid cell")
        try:
            if self.value is None:
                variable_definition(self.variable)
            else:
                require_variable_unit(self.variable, self.value.unit)
        except ValidationError as error:
            problems.append(_with_details(error))
        if (self.value is None) != (self.quality is QualityFlag.MISSING):
            problems.append("a value is absent exactly when the quality is 'missing'")
        return tuple(problems)

    def to_observation(
        self, *, dataset_version_id: EntityId, run_id: EntityId
    ) -> Observation:
        """Attach the lineage ids and build the observation.

        Args:
            dataset_version_id: The version being ingested.
            run_id: The run ingesting it.

        Returns:
            The observation.

        Raises:
            pydantic.ValidationError: If the draft breaks an ``Observation``
                invariant (see ``problems``).
        """
        return Observation(
            dataset_version_id=dataset_version_id,
            station=self.station,
            grid_cell=self.grid_cell,
            variable=self.variable,
            value=self.value,
            observed_at=self.observed_at,
            quality=self.quality,
            ingested_run_id=run_id,
        )


def select_outcome(report: IngestionReport) -> RunStatus:
    """Choose the terminal status a finished report calls for.

    The rules mirror ``outcome_problem`` in the domain: no errors and no invalid
    records is a success (warnings allowed, nothing persisted allowed, as when a
    version is ingested again); problems with at least one record stored is a
    partial success; problems with nothing stored is a failure.

    Args:
        report: The run's report.

    Returns:
        ``succeeded``, ``partially_succeeded`` or ``failed``.
    """
    if not (report.has_errors or report.counts.invalid > 0):
        return RunStatus.SUCCEEDED
    if report.counts.persisted > 0:
        return RunStatus.PARTIALLY_SUCCEEDED
    return RunStatus.FAILED


def _require_consistent(
    dataset: Dataset, version: DatasetVersion, run: IngestionRun
) -> None:
    if version.dataset_id != dataset.id:
        raise VersionDatasetMismatchError.for_ids(dataset.id, version.id)
    if run.dataset_version_id != version.id:
        message = "the run does not ingest this dataset version"
        raise InvariantViolationError(message, details={"run_id": str(run.id)})
    if run.status is not RunStatus.RUNNING:
        message = "a pipeline runs only a running run"
        raise InvariantViolationError(message, details={"status": run.status.value})


class IngestionPipeline[RowT](ABC):
    """Ingest one dataset version through the seven hooks, in a fixed order.

    Subclass per source: implement ``parse`` and ``normalise``, and override any
    other hook whose default does not fit (each says what it does). A pipeline is
    single-use; the ``PipelineFactory`` builds one per run.

    Implements: Template Method.

    Attributes:
        adapter: The source adapter ``fetch`` reads through.
        collector: The run's issues and counts.
    """

    def __init__(
        self, adapter: SourceAdapter, *, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the pipeline.

        Args:
            adapter: The source adapter to fetch through.
            clock: Source of the run's timestamps.
            ids: Source of event ids.
        """
        self.adapter = adapter
        self.collector = ValidationCollector()
        self._clock = clock
        self._ids = ids
        self._stage: IssueStage = "fetch"
        self._payload: RawPayload | None = None
        self._has_run = False

    @property
    def stage(self) -> IssueStage:
        """Return the stage running now, or the last one reached."""
        return self._stage

    @property
    def input_checksum(self) -> InputChecksum | None:
        """Return the checksum of the fetched bytes, or ``None`` before fetching."""
        return None if self._payload is None else self._payload.checksum

    @final
    async def run(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        run: IngestionRun,
        uow: IngestionUnitOfWork,
    ) -> IngestionOutcome:
        """Run every stage in order and finish ``run`` inside ``uow``.

        Args:
            dataset: The dataset.
            version: The version to ingest; it belongs to ``dataset``.
            run: The run, ``running``, for ``version``.
            uow: The open unit of work; observations and the finished run are
                staged in it and the caller commits.

        Returns:
            The finished run.

        Raises:
            InvariantViolationError: If the pipeline already ran, or ``run`` is not
                a running run of ``version``.
            VersionDatasetMismatchError: If ``version`` belongs to another dataset.
        """
        if self._has_run:
            message = "a pipeline runs once; build a new one for each run"
            raise InvariantViolationError(message)
        self._has_run = True
        _require_consistent(dataset, version, run)
        self._stage = "fetch"
        payload = await self.fetch(dataset, version)
        self._payload = payload
        if payload.checksum == version.input_checksum:
            await self._ingest(payload, run, uow)
        else:
            self.collector.error("fetch", _CHECKSUM_MISMATCH)
        self._stage = "record_lineage"
        finished = await self.record_lineage(run, self.collector.report(), payload, uow)
        return IngestionOutcome(run=finished)

    def failure_report(self, error: Exception) -> IngestionReport:
        """Return the report of a run that ``error`` aborted.

        The message names the stage and, for a Yakhnama error, its (safe) message;
        for any other exception only its type, because an arbitrary exception's
        text may carry URLs, credentials or source data.

        Args:
            error: The exception that escaped ``run``.

        Returns:
            The issues found so far plus that error, with the counts of a
            rolled-back transaction.
        """
        if isinstance(error, YakhnamaError):
            message = f"the {self._stage} stage failed: {error.message}"
        else:
            message = f"the {self._stage} stage failed with {type(error).__name__}"
        self.collector.error(self._stage, message)
        return self.collector.report_after_rollback()

    # ------------------------------------------------------------------ hooks

    async def fetch(self, dataset: Dataset, version: DatasetVersion) -> RawPayload:
        """Fetch the bytes of ``version``; by default through the adapter.

        Args:
            dataset: The dataset.
            version: The version.

        Returns:
            The payload.
        """
        return await self.adapter.fetch(dataset, version)

    @abstractmethod
    def parse(
        self, payload: RawPayload, collector: ValidationCollector
    ) -> Sequence[RowT]:
        """Split the payload into source-shaped rows.

        Reject a record that cannot be read with ``collector.reject("parse",
        ...)`` and go on; raise only if the input as a whole is unreadable (an
        unexpected header, an unknown encoding), which fails the run.

        Args:
            payload: The fetched bytes.
            collector: Where to report unreadable records.

        Returns:
            One row per readable record, in source order.
        """

    def validate(self, row: RowT) -> Sequence[str]:
        """Return every problem that makes ``row`` unusable; empty means valid.

        The default normalises the row, so the checks and the conversion never
        drift apart, and then applies ``ObservationDraft.problems``: exactly one
        site, a registered variable in its registry unit, and a value absent
        exactly when the quality is ``missing``.

        Args:
            row: One parsed row.

        Returns:
            One message per problem.
        """
        try:
            draft = self.normalise(row)
        except (ValueError, ValidationError) as error:
            return error_messages(error)
        return draft.problems()

    @abstractmethod
    def normalise(self, row: RowT) -> ObservationDraft:
        """Convert a row to SI units, WGS84 and the Yakhnama quality flags.

        Args:
            row: One parsed row.

        Returns:
            The draft.

        Raises:
            ValueError: If a value is missing, malformed or out of range
                (``pydantic.ValidationError`` included); the row is rejected.
            ValidationError: For the same reasons, as a kernel error.
        """

    def deduplicate(
        self, observations: Sequence[Observation], collector: ValidationCollector
    ) -> Sequence[Observation]:
        """Keep one observation per natural key within the batch.

        The default keeps the **first** record in source order and warns when a
        dropped one disagrees on value or quality. Which record a publisher means
        to win is a source fact: a subclass that knows it overrides this hook and
        says so. Keys stored by earlier runs are skipped by ``persist``.

        Args:
            observations: Valid observations, in source order.
            collector: Where to report conflicting duplicates.

        Returns:
            Observations with unique keys, in first-seen order; never more than
            were given.
        """
        kept: dict[ObservationKey, Observation] = {}
        for observation in observations:
            first = kept.setdefault(observation.key, observation)
            if first is not observation and (first.value, first.quality) != (
                observation.value,
                observation.quality,
            ):
                collector.warn(
                    "deduplicate",
                    _CONFLICTING_DUPLICATE,
                    reference=(
                        f"{observation.site_ref} at "
                        f"{observation.observed_at.value.isoformat()}"
                    ),
                )
        return list(kept.values())

    async def persist(
        self, observations: Sequence[Observation], uow: IngestionUnitOfWork
    ) -> int:
        """Append the observations in batches of ``PERSIST_BATCH_SIZE``.

        ``append_many`` skips keys already stored, so this is the deduplication
        against earlier runs and versions ingested again.

        Args:
            observations: Deduplicated observations.
            uow: The open unit of work.

        Returns:
            How many were stored.
        """
        stored = 0
        for start in range(0, len(observations), PERSIST_BATCH_SIZE):
            batch = observations[start : start + PERSIST_BATCH_SIZE]
            stored += await uow.observations.append_many(batch)
        return stored

    async def record_lineage(
        self,
        run: IngestionRun,
        report: IngestionReport,
        payload: RawPayload,
        uow: IngestionUnitOfWork,
    ) -> IngestionRun:
        """Finish the run with the outcome ``select_outcome`` picks, and stage it.

        The run then records the checksum of the bytes it read and its counts;
        every stored observation already names the version and the run.

        Args:
            run: The running run.
            report: The final report.
            payload: The fetched bytes.
            uow: The open unit of work.

        Returns:
            The finished run.
        """
        finished = self._finish(run, report, payload.checksum).record_into(uow)
        await uow.ingestion_runs.save(finished)
        return finished

    # --------------------------------------------------------------- template

    async def _ingest(
        self, payload: RawPayload, run: IngestionRun, uow: IngestionUnitOfWork
    ) -> None:
        self._stage = "parse"
        rows = self.parse(payload, self.collector)
        self.collector.record_parsed(len(rows))
        if not rows:
            self.collector.warn("parse", _NO_RECORDS)
        observations = self._judge(rows, run)
        self.collector.record_valid(len(observations))
        self._stage = "deduplicate"
        unique = self.deduplicate(observations, self.collector)
        if len(unique) > len(observations):
            message = "deduplicate returned more observations than it was given"
            raise InvariantViolationError(message)
        self._stage = "persist"
        stored = await self.persist(unique, uow)
        if not 0 <= stored <= len(unique):
            message = "persist reported more observations than it was given"
            raise InvariantViolationError(message)
        self.collector.record_persisted(stored)

    def _judge(self, rows: Sequence[RowT], run: IngestionRun) -> list[Observation]:
        observations: list[Observation] = []
        for index, row in enumerate(rows):
            reference = self.reference_for(row, index)
            self._stage = "validate"
            problems = self.validate(row)
            if problems:
                self.collector.reject("validate", reference, problems)
                continue
            self._stage = "normalise"
            try:
                observation = self.normalise(row).to_observation(
                    dataset_version_id=run.dataset_version_id, run_id=run.id
                )
            except (ValueError, ValidationError) as error:
                self.collector.reject("normalise", reference, error_messages(error))
                continue
            observations.append(observation)
        return observations

    def reference_for(self, row: RowT, index: int) -> str:
        """Return how issues name ``row``; by default ``record <n>``, 1-based.

        Args:
            row: The row.
            index: Its 0-based position among the parsed rows.

        Returns:
            A short reference; never personal data.
        """
        return f"record {index + 1}"

    def _finish(
        self, run: IngestionRun, report: IngestionReport, checksum: InputChecksum
    ) -> AggregateChange[IngestionRun]:
        status = select_outcome(report)
        if status is RunStatus.SUCCEEDED:
            return run.succeed(
                report, input_checksum=checksum, clock=self._clock, ids=self._ids
            )
        if status is RunStatus.PARTIALLY_SUCCEEDED:
            return run.partially_succeed(
                report, input_checksum=checksum, clock=self._clock, ids=self._ids
            )
        return run.fail(
            report, input_checksum=checksum, clock=self._clock, ids=self._ids
        )
