"""Authorised read use cases of the events module.

``EventRecordQueryService`` applies the visibility rule of ``authorisation`` before
and after the ``EventQueryService`` port: searches are narrowed by specification,
single reads of an event a non-moderator may not see raise ``EventNotFoundError``
(not ``PermissionDeniedError``), so unpublished event ids cannot be probed.

The timeline is assembled here from the event itself, its linked reports and the
dated facts other modules hold, then ordered by the start of each entry's precision
period, then by ``TimelineEntryKind`` order, then by subject id.

Patterns: Query Service, Policy, Specification.
"""

from datetime import datetime

from yakhnama.modules.events.application.authorisation import (
    AuthorisationPolicy,
    is_publicly_visible,
)
from yakhnama.modules.events.application.dto import (
    EventDetail,
    EventSummary,
    TimelineEntry,
    TimelineEntryKind,
)
from yakhnama.modules.events.application.ports import (
    EventQueryService,
    EventTimelineSources,
    ReportFactsProvider,
)
from yakhnama.modules.events.application.queries import (
    GetEvent,
    GetEventTimeline,
    ListEvents,
)
from yakhnama.modules.events.domain.errors import EventNotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page

_KIND_ORDER = {kind: index for index, kind in enumerate(TimelineEntryKind)}


def _timeline_key(entry: TimelineEntry) -> tuple[datetime, int, EntityId]:
    return (entry.at.truncate().value, _KIND_ORDER[entry.kind], entry.subject_id)


class EventRecordQueryService:
    """Answers events queries with the visibility rule applied.

    Implements: Query Service.
    """

    def __init__(
        self,
        reads: EventQueryService,
        *,
        policy: AuthorisationPolicy,
        reports: ReportFactsProvider,
        timeline_sources: EventTimelineSources,
    ) -> None:
        """Create the service.

        Args:
            reads: The events read port.
            policy: Decides whether an actor sees every event (``CanModerate``).
            reports: Supplies the observation time of linked reports.
            timeline_sources: Supplies verification and impact facts.
        """
        self._reads = reads
        self._policy = policy
        self._reports = reports
        self._timeline_sources = timeline_sources

    async def list_events(self, query: ListEvents) -> Page[EventSummary]:
        """Return one page of the events the actor may see.

        Args:
            query: Filters, page request and the actor.

        Returns:
            The page.

        Raises:
            ValidationError: If the period bounds are reversed or the cursor is
                invalid.
        """
        specification = query.to_specification(
            may_moderate=self._policy.is_allowed(query.actor)
        )
        return await self._reads.search(specification, query.page)

    async def get_event(self, query: GetEvent) -> EventDetail:
        """Return one event the actor may see.

        Args:
            query: The event and the actor.

        Returns:
            The detail view.

        Raises:
            EventNotFoundError: If the event does not exist, or the actor may not
                moderate and the event is not both published and verified.
        """
        detail = await self._reads.get(query.event_id)
        if detail is None or not (
            self._policy.is_allowed(query.actor) or is_publicly_visible(detail)
        ):
            raise EventNotFoundError(query.event_id)
        return detail

    async def get_timeline(self, query: GetEventTimeline) -> tuple[TimelineEntry, ...]:
        """Return the dated history of one event the actor may see.

        Args:
            query: The event and the actor.

        Returns:
            Report observations, the event's start and end, verification
            transitions and impact claims, in timeline order.

        Raises:
            EventNotFoundError: As for ``get_event``.
        """
        detail = await self.get_event(
            GetEvent(actor=query.actor, event_id=query.event_id)
        )
        entries: list[TimelineEntry] = [
            TimelineEntry(
                kind=TimelineEntryKind.REPORT_OBSERVED,
                at=report.observed_at,
                subject_id=report.id,
            )
            for report in await self._reports.get_many(
                [link.report_id for link in detail.report_links]
            )
        ]
        entries.append(
            TimelineEntry(
                kind=TimelineEntryKind.EVENT_STARTED,
                at=detail.period.started_at,
                subject_id=detail.id,
            )
        )
        if detail.period.ended_at is not None:
            entries.append(
                TimelineEntry(
                    kind=TimelineEntryKind.EVENT_ENDED,
                    at=detail.period.ended_at,
                    subject_id=detail.id,
                )
            )
        entries.extend(await self._timeline_sources.verification_transitions(detail.id))
        entries.extend(await self._timeline_sources.impact_claims(detail.id))
        return tuple(sorted(entries, key=_timeline_key))
