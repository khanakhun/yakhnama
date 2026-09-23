"""Adapters answering the events module's ports from other modules' facades.

- ``ReportFactsAdapter`` (``ReportFactsProvider``): reports as the events domain
  sees them, with each point already rounded by ``PublicCoordinatePolicy`` so a
  reporter's exact position cannot reach a published centroid.
- ``EventSourceMarkerAdapter`` (``SourceReferenceMarker``): cites sources through
  the provenance ``MarkSourceReferencedHandler``.
- ``VerificationCaseOpenerAdapter`` (``VerificationCaseOpener``): opens the event's
  case through ``OpenVerificationCaseHandler`` with ``if_absent=True``.
- ``PlaceDirectoryAdapter`` (``PlaceDirectory``): place codes, from geography.
- ``EventTimelineAdapter`` (``EventTimelineSources``): verification transitions and
  impact claims of an event, for its timeline.

Internal import: ``GeographyUnitOfWorkFactory`` comes from
``geography.application.ports``. The geography facade's ``PlaceQueryService`` finds
places by id or by name, not by code, so the code lookup reads the repository in a
read-only unit of work (never committed). A ``PlaceQueryService.exists_code`` read
is an open question for the geography owner.

Patterns: Adapter.
"""

from collections.abc import Sequence
from typing import Final

from yakhnama.modules.events.public import (
    ReportForEvent,
    TimelineEntry,
    TimelineEntryKind,
)
from yakhnama.modules.geography.application.ports import GeographyUnitOfWorkFactory
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.public import ImpactQueryService, ListClaims
from yakhnama.modules.provenance.public import (
    MarkSourceReferenced,
    SourceReferenceMarker,
)
from yakhnama.modules.reports.public import ReportQueryService
from yakhnama.modules.verification.public import (
    OpenVerificationCase,
    OpenVerificationCaseHandler,
    TargetKind,
    VerificationQueryService,
    VerificationTarget,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import MAX_PAGE_LIMIT, PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

# The impacts read port ignores the actor (visibility is the events read service's
# job, applied before a timeline is assembled), but ``ListClaims`` requires one.
_TIMELINE_READER: Final = Actor.anonymous()


class ReportFactsAdapter:
    """``ReportFactsProvider`` over the reports read model.

    Implements: Adapter.
    """

    def __init__(
        self, reports: ReportQueryService, coordinates: PublicCoordinatePolicy
    ) -> None:
        """Create the adapter.

        Args:
            reports: The reports module's read port (exact positions).
            coordinates: The rounding applied before a point leaves reports.
        """
        self._reports = reports
        self._coordinates = coordinates

    async def get_many(
        self, report_ids: Sequence[EntityId]
    ) -> Sequence[ReportForEvent]:
        """Return the reports that exist among ``report_ids``.

        Args:
            report_ids: The reports asked for.

        Returns:
            One ``ReportForEvent`` per existing report, in first-asked order, with
            its public (rounded) point; missing ids are left out.
        """
        facts: list[ReportForEvent] = []
        for report_id in dict.fromkeys(report_ids):
            record = await self._reports.get_report(report_id)
            if record is None:
                continue
            facts.append(
                ReportForEvent(
                    id=record.id,
                    observed_at=record.observed_at,
                    coordinates=self._coordinates.apply(record.observation.coordinates),
                    source_id=record.source_id,
                )
            )
        return facts


class EventSourceMarkerAdapter:
    """``SourceReferenceMarker`` (events) over the provenance marker.

    Implements: Adapter.
    """

    def __init__(self, marker: SourceReferenceMarker) -> None:
        """Create the adapter.

        Args:
            marker: The provenance ``MarkSourceReferencedHandler``.
        """
        self._marker = marker

    async def mark_referenced(
        self, source_ids: Sequence[EntityId], *, actor: Actor
    ) -> None:
        """Mark every source in ``source_ids`` as referenced, each once.

        Args:
            source_ids: The sources an event now cites.
            actor: The moderator whose change cites them.

        Raises:
            SourceNotFoundError: If a source does not exist.
            PermissionDeniedError: If the actor may not cite sources.
        """
        for source_id in dict.fromkeys(source_ids):
            await self._marker(MarkSourceReferenced(actor=actor, source_id=source_id))


class VerificationCaseOpenerAdapter:
    """``VerificationCaseOpener`` over the verification open handler.

    Implements: Adapter.
    """

    def __init__(self, handler: OpenVerificationCaseHandler) -> None:
        """Create the adapter.

        Args:
            handler: The verification module's open use case.
        """
        self._handler = handler

    async def open_for_event(self, event_id: EntityId, *, actor: Actor) -> None:
        """Open the event's case unless it already has one.

        Args:
            event_id: The new event.
            actor: The moderator who created it.
        """
        await self._handler(
            OpenVerificationCase(
                actor=actor,
                target=VerificationTarget(kind=TargetKind.EVENT, target_id=event_id),
                if_absent=True,
            )
        )


class PlaceDirectoryAdapter:
    """``PlaceDirectory`` over the geography place repository.

    Implements: Adapter.
    """

    def __init__(self, geography: GeographyUnitOfWorkFactory) -> None:
        """Create the adapter.

        Args:
            geography: Opens a geography unit of work; this adapter only reads.
        """
        self._geography = geography

    async def exists(self, place_code: str) -> bool:
        """Tell whether the gazetteer has a place with ``place_code``.

        Args:
            place_code: The code.

        Returns:
            ``True`` if the place exists, active or retired.
        """
        # Read-only: the unit of work is never committed, so leaving it rolls back.
        async with self._geography() as uow:
            return await uow.places.get_by_code(place_code) is not None


class EventTimelineAdapter:
    """``EventTimelineSources`` over the verification and impacts read models.

    Implements: Adapter.
    """

    def __init__(
        self, verification: VerificationQueryService, impacts: ImpactQueryService
    ) -> None:
        """Create the adapter.

        Args:
            verification: The verification module's read port.
            impacts: The impacts module's claims read port.
        """
        self._verification = verification
        self._impacts = impacts

    async def verification_transitions(
        self, event_id: EntityId
    ) -> Sequence[TimelineEntry]:
        """Return one entry per transition of the event's verification case.

        Args:
            event_id: The event.

        Returns:
            Entries at each transition time (``exact``), labelled with the state
            reached, subject the case id; empty if the event has no case.
        """
        case = await self._verification.get_for_target(
            VerificationTarget(kind=TargetKind.EVENT, target_id=event_id)
        )
        if case is None:
            return ()
        return tuple(
            TimelineEntry(
                kind=TimelineEntryKind.VERIFICATION_TRANSITION,
                at=DateWithPrecision(
                    value=transition.occurred_at, precision=DatePrecision.EXACT
                ),
                subject_id=case.id,
                label=transition.to_state.value,
            )
            for transition in case.history
        )

    async def impact_claims(self, event_id: EntityId) -> Sequence[TimelineEntry]:
        """Return one entry per impact claim of the event, retracted ones included.

        Args:
            event_id: The event.

        Returns:
            Entries at each claim's ``claimed_at``, labelled with the metric code,
            subject the claim id.
        """
        entries: list[TimelineEntry] = []
        page = PageRequest(limit=MAX_PAGE_LIMIT)
        while True:
            claims = await self._impacts.list_claims(
                ListClaims(
                    actor=_TIMELINE_READER,
                    event_id=event_id,
                    include_retracted=True,
                    page=page,
                )
            )
            entries.extend(
                TimelineEntry(
                    kind=TimelineEntryKind.IMPACT_CLAIM,
                    at=claim.claimed_at,
                    subject_id=claim.id,
                    label=claim.metric_code,
                )
                for claim in claims.items
            )
            if claims.next_cursor is None:
                return entries
            page = PageRequest(limit=MAX_PAGE_LIMIT, cursor=claims.next_cursor)
