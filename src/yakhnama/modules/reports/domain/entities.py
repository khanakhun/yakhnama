"""The ``Report`` aggregate: one raw observation, immutable once submitted.

A report is never edited after submission (``AGENTS.md`` §5, §7). What the reporter
stated (``ReportContent``: time, position, description, language, hazard guess, place
hint, media) has no setter on a submitted report. The only changes a submitted report
accepts are about the record, not the observation:

- ``attach_triage`` stores the triage chain's suggestions; the status never changes;
- ``mark_superseded`` records that a newer revision replaced it;
- ``withdraw`` records that the reporter took it back, with a reason.

**Corrections are revisions, in two steps.** ``revise`` does not touch the report it
is called on; it returns an ``AggregateChange`` whose state is a *new* ``Report`` (new
id, ``revision + 1``, ``supersedes_id`` = the old id) and a ``ReportRevised`` event.
The handler then calls ``mark_superseded`` on the old report with the new one, which
returns a second change (the old report as ``superseded`` and ``ReportSuperseded``).
The handler records and saves both in the **same unit of work**, so the chain is never
left with two current revisions or none::

    revision = report.revise(content, clock=clock, ids=ids)
    new_report = revision.record_into(uow)
    superseded = report.mark_superseded(new_report, clock=clock, ids=ids)
    old_report = superseded.record_into(uow)
    await uow.reports.add(new_report)
    await uow.reports.save(old_report)

Every change validates the whole new state, bumps ``version`` by one, stamps
``updated_at`` from the injected ``Clock`` and draws event ids from the injected
``IdGenerator``. A change already in effect returns the report unchanged with no
events, so a retried command is harmless.

Patterns: Entity, Aggregate Root, Domain Events.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from yakhnama.modules.reports.domain.errors import (
    ReportAlreadySubmittedError,
    ReportImmutableError,
    ReportNotSubmittedError,
    ReportRevisionUnchangedError,
    ReportSupersessionMismatchError,
    ReportWithdrawnError,
)
from yakhnama.modules.reports.domain.events import (
    ReportEvent,
    ReportRevised,
    ReportSubmitted,
    ReportSuperseded,
    ReportTriaged,
    ReportWithdrawn,
)
from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    ClientReportId,
    Description,
    HazardGuess,
    ObservationPoint,
    PlaceHint,
    ReportAttribution,
    ReportContent,
    ReportStatus,
    ReportVersion,
    RevisionNumber,
    TriageResult,
    WithdrawalReason,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import DateWithPrecision, LanguageCode

_WITHDRAWAL_REASON: TypeAdapter[str] = TypeAdapter(WithdrawalReason)

# The fields that make up ``ReportContent``; a revision replaces exactly these.
_CONTENT_FIELDS = tuple(ReportContent.model_fields)
_SUBMITTED_STATUSES = frozenset({ReportStatus.SUBMITTED, ReportStatus.SUPERSEDED})


class Report(BaseModel):
    """One raw observation from one person or organisation; never trusted by default.

    Invariants, checked on every construction:

    - ``submitted_at`` is unset for a draft, set once submitted or superseded, and
      either for a withdrawn report (a draft can be withdrawn); when set it lies
      between ``created_at`` and ``updated_at``;
    - ``supersedes_id`` is set exactly when ``revision`` is above 1, and never
      equals ``id``;
    - ``superseded_by_id`` is set exactly when the status is ``superseded``;
    - ``withdrawal_reason`` is set exactly when the status is ``withdrawn``;
    - a draft carries no triage result;
    - ``media_ids`` are unique;
    - ``updated_at`` is never before ``created_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: The client-generated UUIDv7 (``ClientReportId``); a revision gets a new
            id from the platform's ``IdGenerator``.
        reporter_id: The user who reported.
        organization_id: The organisation the user reported for, or ``None``.
        source_id: The ``provenance`` source record the report is attributed to.
        observed_at: When the observation was made, with its precision.
        observation: Where the reporter was (private; rounded in public payloads).
        description: What the reporter saw, in their words.
        original_language: The language and script of ``description``.
        hazard_guess: What the reporter thinks it was, or ``None``.
        place_hint: A place the reporter picked, or ``None``.
        media_ids: Attached media assets, unique.
        status: Lifecycle status.
        revision: Position in the revision chain, 1 for the original.
        supersedes_id: The revision this one replaces, or ``None`` for revision 1.
        superseded_by_id: The revision that replaced this one, while superseded.
        withdrawal_reason: Why the reporter withdrew it, while withdrawn.
        triage: The latest triage result, or ``None`` before triage ran.
        submitted_at: When the platform accepted the submission, UTC.
        version: Optimistic-concurrency version.
        created_at: When the record was created, UTC.
        updated_at: When the record last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ClientReportId
    reporter_id: EntityId
    organization_id: EntityId | None = None
    source_id: EntityId
    observed_at: DateWithPrecision
    observation: ObservationPoint
    description: Description
    original_language: LanguageCode
    hazard_guess: HazardGuess | None = None
    place_hint: PlaceHint | None = None
    media_ids: tuple[EntityId, ...] = Field(default=(), max_length=MEDIA_PER_REPORT_MAX)
    status: ReportStatus = ReportStatus.DRAFT
    revision: RevisionNumber = 1
    supersedes_id: EntityId | None = None
    superseded_by_id: EntityId | None = None
    withdrawal_reason: WithdrawalReason | None = None
    triage: TriageResult | None = None
    submitted_at: AwareDatetime | None = None
    version: ReportVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("submitted_at", "created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if len(set(self.media_ids)) != len(self.media_ids):
            message = "a media asset may be attached to a report only once"
            raise ValueError(message)
        self._check_lifecycle_fields()
        self._check_revision_chain()
        self._check_timestamps()
        return self

    def _check_lifecycle_fields(self) -> None:
        # A withdrawn report may or may not have been submitted before, so only the
        # other statuses pin submitted_at down.
        if self.status is ReportStatus.DRAFT and self.submitted_at is not None:
            message = "a draft report has no submitted_at"
            raise ValueError(message)
        if self.status in _SUBMITTED_STATUSES and self.submitted_at is None:
            message = "a submitted or superseded report needs submitted_at"
            raise ValueError(message)
        if (self.superseded_by_id is None) == (self.status is ReportStatus.SUPERSEDED):
            message = "superseded_by_id must be set exactly when superseded"
            raise ValueError(message)
        if (self.withdrawal_reason is None) == (self.status is ReportStatus.WITHDRAWN):
            message = "withdrawal_reason must be set exactly when withdrawn"
            raise ValueError(message)
        if self.status is ReportStatus.DRAFT and self.triage is not None:
            message = "a draft report cannot carry a triage result"
            raise ValueError(message)

    def _check_revision_chain(self) -> None:
        if (self.supersedes_id is None) != (self.revision == 1):
            message = "supersedes_id must be set exactly when revision is above 1"
            raise ValueError(message)
        if self.id in {self.supersedes_id, self.superseded_by_id}:
            message = "a report cannot supersede itself"
            raise ValueError(message)

    def _check_timestamps(self) -> None:
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        if self.submitted_at is not None and not (
            self.created_at <= self.submitted_at <= self.updated_at
        ):
            message = "submitted_at must lie between created_at and updated_at"
            raise ValueError(message)

    def _content_fields(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in _CONTENT_FIELDS}

    # ----------------------------------------------------------------------- #
    # Queries                                                                 #
    # ----------------------------------------------------------------------- #

    @property
    def content(self) -> ReportContent:
        """Return what the reporter stated in this revision.

        Returns:
            The content value object.
        """
        return ReportContent.model_validate(self._content_fields())

    @property
    def attribution(self) -> ReportAttribution:
        """Return who the report comes from; every revision inherits it.

        Returns:
            Reporter, organisation and source.
        """
        return ReportAttribution(
            reporter_id=self.reporter_id,
            organization_id=self.organization_id,
            source_id=self.source_id,
        )

    @property
    def is_current(self) -> bool:
        """Tell whether this is the submitted, current revision of its chain.

        Returns:
            ``True`` while ``status`` is ``submitted``.
        """
        return self.status is ReportStatus.SUBMITTED

    # ----------------------------------------------------------------------- #
    # Changes                                                                 #
    # ----------------------------------------------------------------------- #

    def submit(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["Report"]:
        """Submit a draft; from now on its content never changes.

        Args:
            clock: Source of ``submitted_at``, ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The submitted report and ``ReportSubmitted``.

        Raises:
            ReportAlreadySubmittedError: If the report is submitted or superseded.
            ReportWithdrawnError: If the report was withdrawn.
        """
        if self.status is ReportStatus.WITHDRAWN:
            raise ReportWithdrawnError.for_report(self.id)
        if self.status is not ReportStatus.DRAFT:
            raise ReportAlreadySubmittedError.for_report(self.id, self.status)
        now = clock.now()
        state = self._evolve(now, status=ReportStatus.SUBMITTED, submitted_at=now)
        return AggregateChange[Report](
            state=state, events=(state._submitted_event(ids, now),)
        )

    def revise(
        self, content: ReportContent, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Report"]:
        """Return the next revision of this report; this report is left as it is.

        Step one of the two-step pattern in the module docstring: the caller must
        also call ``mark_superseded`` on this report with the returned state, in the
        same unit of work. The new revision is submitted immediately, starts at
        version 1 with no triage result and keeps the reporter, organisation and
        source of this one.

        Args:
            content: The complete corrected content.
            clock: Source of the new report's timestamps and ``occurred_at``.
            ids: Source of the new report's id and the event id.

        Returns:
            The new report and ``ReportRevised``.

        Raises:
            ReportNotSubmittedError: If this report is a draft.
            ReportImmutableError: If this report was already superseded.
            ReportWithdrawnError: If this report was withdrawn.
            ReportRevisionUnchangedError: If ``content`` equals this revision's.
        """
        self._require_submitted()
        if content == self.content:
            raise ReportRevisionUnchangedError.for_report(self.id)
        now = clock.now()
        state = Report.model_validate(
            {
                **content.model_dump(),
                **self.attribution.model_dump(),
                "id": ids.new_id(),
                "status": ReportStatus.SUBMITTED,
                "revision": self.revision + 1,
                "supersedes_id": self.id,
                "submitted_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        event = state._event(
            ReportRevised,
            ids,
            now,
            supersedes_id=self.id,
            reporter_id=state.reporter_id,
            organization_id=state.organization_id,
            source_id=state.source_id,
            media_count=len(state.media_ids),
        )
        return AggregateChange[Report](state=state, events=(event,))

    def mark_superseded(
        self, successor: "Report", *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Report"]:
        """Record that ``successor`` replaced this report (step two of ``revise``).

        Args:
            successor: The report ``revise`` returned for this one.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            This report as ``superseded`` and ``ReportSuperseded``, or the report
            unchanged and no events if ``successor`` already superseded it.

        Raises:
            ReportSupersessionMismatchError: If ``successor`` is not this report's
                next revision.
            ReportNotSubmittedError: If this report is a draft.
            ReportImmutableError: If another report already superseded this one.
            ReportWithdrawnError: If this report was withdrawn.
        """
        is_successor = (
            successor.supersedes_id == self.id
            and successor.revision == self.revision + 1
            and successor.reporter_id == self.reporter_id
        )
        if not is_successor:
            raise ReportSupersessionMismatchError.for_reports(self.id, successor.id)
        if self.superseded_by_id == successor.id:
            return AggregateChange[Report](state=self)
        self._require_submitted()
        now = clock.now()
        state = self._evolve(
            now, status=ReportStatus.SUPERSEDED, superseded_by_id=successor.id
        )
        event = state._event(ReportSuperseded, ids, now, superseded_by_id=successor.id)
        return AggregateChange[Report](state=state, events=(event,))

    def withdraw(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Report"]:
        """Take the report back; it stays on record with the reason.

        Drafts and current revisions can be withdrawn. A superseded revision
        cannot: the reporter withdraws the latest revision instead.

        Args:
            reason: Why, as safe single-line text of 1 to 500 characters.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The withdrawn report and ``ReportWithdrawn``, or the report unchanged
            and no events if it was already withdrawn.

        Raises:
            ReportImmutableError: If the report was superseded.
            pydantic.ValidationError: If ``reason`` is empty, too long or unsafe.
        """
        if self.status is ReportStatus.WITHDRAWN:
            return AggregateChange[Report](state=self)
        if self.status is ReportStatus.SUPERSEDED:
            raise ReportImmutableError.for_report(self.id, self.status)
        normalised = _WITHDRAWAL_REASON.validate_python(reason)
        now = clock.now()
        state = self._evolve(
            now, status=ReportStatus.WITHDRAWN, withdrawal_reason=normalised
        )
        event = state._event(ReportWithdrawn, ids, now, previous_status=self.status)
        return AggregateChange[Report](state=state, events=(event,))

    def attach_triage(
        self, result: TriageResult, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Report"]:
        """Store the triage chain's result; the status and content never change.

        A later run replaces an earlier result: triage only suggests, so the
        latest suggestion is the one a moderator should see.

        Args:
            result: The chain's result.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The report with ``triage`` set and ``ReportTriaged``, or the report
            unchanged and no events if ``result`` equals the stored one.

        Raises:
            ReportNotSubmittedError: If the report is a draft.
            ReportImmutableError: If the report was superseded.
            ReportWithdrawnError: If the report was withdrawn.
        """
        self._require_submitted()
        if result == self.triage:
            return AggregateChange[Report](state=self)
        now = clock.now()
        state = self._evolve(now, triage=result)
        event = state._event(ReportTriaged, ids, now, flag_kinds=result.kinds)
        return AggregateChange[Report](state=state, events=(event,))

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _require_submitted(self) -> None:
        if self.status is ReportStatus.DRAFT:
            raise ReportNotSubmittedError.for_report(self.id)
        if self.status is ReportStatus.SUPERSEDED:
            raise ReportImmutableError.for_report(self.id, self.status)
        if self.status is ReportStatus.WITHDRAWN:
            raise ReportWithdrawnError.for_report(self.id)

    def _evolve(self, now: datetime, **updates: object) -> "Report":
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant.
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return self.model_validate(
            {**fields, **updates, "version": self.version + 1, "updated_at": now}
        )

    def _submitted_event(self, ids: IdGenerator, now: datetime) -> ReportSubmitted:
        return self._event(
            ReportSubmitted,
            ids,
            now,
            reporter_id=self.reporter_id,
            organization_id=self.organization_id,
            source_id=self.source_id,
            media_count=len(self.media_ids),
        )

    def _event[EventT: ReportEvent](
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
                "revision": self.revision,
                **fields,
            }
        )
