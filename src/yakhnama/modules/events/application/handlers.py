"""Write-side use cases of the events module.

Every handler asks its ``AuthorisationPolicy`` (``CanModerate`` in the composition
root) and raises ``PermissionDeniedError`` before opening a unit of work, so nothing
is read or staged for a refused actor. The aggregate enforces its own invariants;
handlers load, check what only the application can see (other aggregates, other
modules), call one domain method, stage the result with its events and commit.

Reporter privacy: every report reaches the domain through ``ReportFactsProvider``
and is passed through ``PublicCoordinatePolicy`` here as well, so an event's
centroid is only ever derived from rounded report points (Phase 2 security review).

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from collections.abc import Callable, Sequence

from yakhnama.modules.events.application.authorisation import (
    AuthorisationPolicy,
    acting_user_id,
    require_allowed,
)
from yakhnama.modules.events.application.commands import (
    AddAffectedPlace,
    CreateEventFromReports,
    LinkReportToEvent,
    MergeEvents,
    PublishEvent,
    RelateEvents,
    RetractEvent,
    SetEventAttributes,
    SetEventGeometry,
    SetEventPeriod,
    UnlinkReportFromEvent,
)
from yakhnama.modules.events.application.ports import (
    EventsUnitOfWork,
    EventsUnitOfWorkFactory,
    PlaceDirectory,
    ReportFactsProvider,
    SourceReferenceMarker,
    VerificationCaseOpener,
)
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.errors import (
    EventImmutableError,
    EventNotFoundError,
    InvalidRelationError,
)
from yakhnama.modules.events.domain.events import ReportLinkedToEvent
from yakhnama.modules.events.domain.factories import EventFactory
from yakhnama.modules.events.domain.value_objects import (
    EventRelation,
    ReportForEvent,
)
from yakhnama.modules.hazards.public import (
    HazardTypeQueryService,
    HazardTypeRef,
    HazardTypeStatus,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import NotFoundError, ValidationError
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy

type EventChange = Callable[[Event, EntityId], AggregateChange[Event]]
"""Applies one domain change to a loaded event on behalf of the acting user."""


class EventHandlerDependencies:
    """What every events command handler is built from.

    Grouped so the composition root and the tests wire the module once, instead of
    repeating the same ports for every handler.

    Implements: Dependency Injection.

    Attributes:
        uow_factory: Opens an events unit of work per call.
        policy: Decides whether an actor may moderate (``CanModerate``).
        clock: Source of timestamps and event times.
        ids: Source of aggregate and event ids.
        reports: Maps reports to ``ReportForEvent``.
        sources: Marks cited sources as referenced.
        cases: Opens verification cases for new events.
        places: Tells whether a place code exists.
        hazard_types: The hazards read port, to check a hazard type is active.
        coordinates: Rounds report points before they reach an event.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: EventsUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
        reports: ReportFactsProvider,
        sources: SourceReferenceMarker,
        cases: VerificationCaseOpener,
        places: PlaceDirectory,
        hazard_types: HazardTypeQueryService,
        coordinates: PublicCoordinatePolicy,
    ) -> None:
        """Group the dependencies.

        Args:
            uow_factory: Opens an events unit of work per call.
            policy: Decides whether an actor may moderate.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
            reports: Maps reports to ``ReportForEvent``.
            sources: Marks cited sources as referenced.
            cases: Opens verification cases for new events.
            places: Tells whether a place code exists.
            hazard_types: The hazards read port.
            coordinates: Rounds report points before they reach an event.
        """
        self.uow_factory = uow_factory
        self.policy = policy
        self.clock = clock
        self.ids = ids
        self.reports = reports
        self.sources = sources
        self.cases = cases
        self.places = places
        self.hazard_types = hazard_types
        self.coordinates = coordinates


# --------------------------------------------------------------------------- #
# Shared steps                                                                #
# --------------------------------------------------------------------------- #


def _authorise(deps: EventHandlerDependencies, actor: Actor, action: str) -> EntityId:
    require_allowed(deps.policy, actor, action=action)
    return acting_user_id(actor)


async def _load_event(uow: EventsUnitOfWork, event_id: EntityId) -> Event:
    event = await uow.events.get(event_id)
    if event is None:
        raise EventNotFoundError(event_id)
    return event


async def _load_reports(
    deps: EventHandlerDependencies, report_ids: Sequence[EntityId]
) -> list[ReportForEvent]:
    found = {report.id: report for report in await deps.reports.get_many(report_ids)}
    missing = [str(report_id) for report_id in report_ids if report_id not in found]
    if missing:
        message = "some reports do not exist"
        raise NotFoundError(message, details={"report_ids": missing})
    # Rounding again is a no-op for a correct adapter and a safeguard otherwise.
    return [
        ReportForEvent.model_validate(
            {
                **dict(found[report_id]),
                "coordinates": deps.coordinates.apply(found[report_id].coordinates),
            }
        )
        for report_id in report_ids
    ]


async def _save_if_changed(uow: EventsUnitOfWork, before: Event, after: Event) -> None:
    # Idempotent domain methods return the same version and no event.
    if after.version != before.version:
        await uow.events.save(after)


async def _change_event(
    deps: EventHandlerDependencies,
    *,
    actor: Actor,
    event_id: EntityId,
    action: str,
    change: EventChange,
) -> None:
    actor_id = _authorise(deps, actor, action)
    async with deps.uow_factory() as uow:
        event = await _load_event(uow, event_id)
        changed = change(event, actor_id).record_into(uow)
        await _save_if_changed(uow, event, changed)
        await uow.commit()


# --------------------------------------------------------------------------- #
# Creating and linking                                                        #
# --------------------------------------------------------------------------- #


class CreateEventFromReportsHandler:
    """Create a draft event from reports, cite their sources and open its case.

    Period and centroid are derived by ``EventFactory`` from the rounded report
    points; every report is linked as ``primary`` and a ``ReportLinkedToEvent``
    event is recorded per report after ``EventCreated``, so subscribers of links
    see links made at creation too.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: CreateEventFromReports) -> EntityId:
        """Create the event.

        Args:
            command: The validated command.

        Returns:
            The id of the new event.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ValidationError: If the hazard type is unknown or retired, or the report
                ids repeat.
            NotFoundError: If a report or a report's source does not exist.
            AttributesMismatchError: If the attributes belong to another hazard
                type.
        """
        deps = self._deps
        created_by = _authorise(deps, command.actor, "create events")
        await _require_active_hazard_type(deps.hazard_types, command.hazard_type)
        async with deps.uow_factory() as uow:
            reports = await _load_reports(deps, command.report_ids)
            event = EventFactory.from_reports(
                reports,
                hazard_type=command.hazard_type,
                title=command.title,
                created_by=created_by,
                clock=deps.clock,
                ids=deps.ids,
                attributes=command.attributes,
            ).record_into(uow)
            if command.summary is not None:
                # The factory takes no summary; the event is still at version 1 and
                # unpublished, so the summary belongs to its initial state.
                event = Event.model_validate(
                    {**dict(event), "summary": command.summary}
                )
            for link in event.report_links:
                uow.record_event(
                    ReportLinkedToEvent(
                        event_id=deps.ids.new_id(),
                        occurred_at=link.linked_at,
                        aggregate_id=event.id,
                        actor_id=created_by,
                        report_id=link.report_id,
                        role=link.role,
                    )
                )
            await uow.events.add(event)
            await deps.sources.mark_referenced(event.source_ids, actor=command.actor)
            await deps.cases.open_for_event(event.id, actor=command.actor)
            await uow.commit()
        return event.id


async def _require_active_hazard_type(
    hazard_types: HazardTypeQueryService, hazard_type: HazardTypeRef
) -> None:
    detail = await hazard_types.get(hazard_type.code)
    if detail is None or detail.status is not HazardTypeStatus.ACTIVE:
        message = f"hazard type {hazard_type.code!r} is unknown or retired"
        raise ValidationError(
            message, details={"field": "hazard_type", "code": hazard_type.code}
        )


class LinkReportToEventHandler:
    """Link one more report to an event and cite its source.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: LinkReportToEvent) -> None:
        """Link the report.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            NotFoundError: If the report or its source does not exist.
            EventImmutableError: If the event is retracted or merged.
            ReportAlreadyLinkedError: If the report is already linked.
        """
        deps = self._deps
        actor_id = _authorise(deps, command.actor, "link reports to events")
        async with deps.uow_factory() as uow:
            event = await _load_event(uow, command.event_id)
            (report,) = await _load_reports(deps, (command.report_id,))
            linked = event.link_report(
                report,
                role=command.role,
                linked_by=actor_id,
                clock=deps.clock,
                ids=deps.ids,
            ).record_into(uow)
            await uow.events.save(linked)
            await deps.sources.mark_referenced((report.source_id,), actor=command.actor)
            await uow.commit()


class UnlinkReportFromEventHandler:
    """Remove a report link; the source stays cited and the unlink is recorded.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: UnlinkReportFromEvent) -> None:
        """Unlink the report.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
            ReportNotLinkedError: If the report is not linked.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="unlink reports from events",
            change=lambda event, actor_id: event.unlink_report(
                command.report_id,
                reason=command.reason,
                actor_id=actor_id,
                clock=deps.clock,
                ids=deps.ids,
            ),
        )


class RelateEventsHandler:
    """Record a relation between two existing events.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RelateEvents) -> None:
        """Relate the events.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            InvalidRelationError: If both ends are the same event, the relation is
                already recorded, or it would close a ``part_of`` cycle.
            EventNotFoundError: If either event does not exist.
        """
        deps = self._deps
        actor_id = _authorise(deps, command.actor, "relate events")
        if command.from_event_id == command.to_event_id:
            message = "an event cannot be related to itself"
            raise InvalidRelationError(
                message, details={"event_id": str(command.from_event_id)}
            )
        async with deps.uow_factory() as uow:
            await _load_event(uow, command.from_event_id)
            await _load_event(uow, command.to_event_id)
            graph = await uow.event_relations.graph_around(
                (command.from_event_id, command.to_event_id)
            )
            relation = EventRelation(
                from_event_id=command.from_event_id,
                to_event_id=command.to_event_id,
                kind=command.kind,
                note=command.note,
                related_by=actor_id,
                related_at=deps.clock.now(),
            )
            graph.relate(relation, ids=deps.ids).record_into(uow)
            await uow.event_relations.add(relation)
            await uow.commit()


# --------------------------------------------------------------------------- #
# Editing one event                                                           #
# --------------------------------------------------------------------------- #


class SetEventGeometryHandler:
    """Set, replace or remove an event's geometry.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: SetEventGeometry) -> None:
        """Change the geometry; the same geometry changes nothing.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="change event geometry",
            change=lambda event, actor_id: event.set_geometry(
                command.geometry, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class SetEventPeriodHandler:
    """Correct when an event happened.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: SetEventPeriod) -> None:
        """Change the period; the same period changes nothing.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="change event periods",
            change=lambda event, actor_id: event.set_period(
                command.period, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class SetEventAttributesHandler:
    """Set, replace or clear an event's hazard-specific attributes.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: SetEventAttributes) -> None:
        """Change the attributes; equal attributes change nothing.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
            AttributesMismatchError: If the attributes belong to another hazard
                type.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="change event attributes",
            change=lambda event, actor_id: event.set_attributes(
                command.attributes, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class AddAffectedPlaceHandler:
    """Add a gazetteer place to an event after checking the code exists.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: AddAffectedPlace) -> None:
        """Add the place; a place already listed changes nothing.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ValidationError: If the place code does not exist.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
        """
        deps = self._deps
        action = "change event places"
        _authorise(deps, command.actor, action)
        if not await deps.places.exists(command.place.place_code):
            message = f"place {command.place.place_code!r} does not exist"
            raise ValidationError(message, details={"field": "place_code"})
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action=action,
            change=lambda event, actor_id: event.add_affected_place(
                command.place, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class PublishEventHandler:
    """Publish a draft event.

    Publishing and verification are independent gates (**proposed**): a published
    event reaches non-moderators only once its verification case is ``verified``
    as well, so publishing does not wait for verification.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: PublishEvent) -> None:
        """Publish the event.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is retracted or merged.
            InvalidEventStatusError: If the event is already published.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="publish events",
            change=lambda event, actor_id: event.publish(
                actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class RetractEventHandler:
    """Retract an event; it stays stored and readable with its reason.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RetractEvent) -> None:
        """Retract the event.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            EventNotFoundError: If the event does not exist.
            EventImmutableError: If the event is already retracted or merged.
        """
        deps = self._deps
        await _change_event(
            deps,
            actor=command.actor,
            event_id=command.event_id,
            action="retract events",
            change=lambda event, actor_id: event.retract(
                command.reason, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ),
        )


class MergeEventsHandler:
    """Merge one event into another describing the same occurrence.

    The merged event becomes final and keeps its links as history. The surviving
    event receives, through its own methods, every report link (same role) and
    every affected place it does not have yet, so each aggregate records its own
    change. Impact claims and the verification case stay where they are (an open
    question for the maintainer).

    Implements: Command Handler.
    """

    def __init__(self, dependencies: EventHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: MergeEvents) -> None:
        """Merge ``command.event_id`` into ``command.into_event_id``.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            InvalidRelationError: If both ids name the same event.
            EventNotFoundError: If either event does not exist.
            EventImmutableError: If either event is retracted or merged.
            NotFoundError: If a report to move no longer exists.
        """
        deps = self._deps
        actor_id = _authorise(deps, command.actor, "merge events")
        if command.event_id == command.into_event_id:
            message = "an event cannot be merged into itself"
            raise InvalidRelationError(
                message, details={"event_id": str(command.event_id)}
            )
        async with deps.uow_factory() as uow:
            merged = await _load_event(uow, command.event_id)
            target = await _load_event(uow, command.into_event_id)
            if target.status.is_final:
                raise EventImmutableError(target.id, target.status.value, "merge_into")
            final = merged.merge_into(
                target.id,
                reason=command.reason,
                actor_id=actor_id,
                clock=deps.clock,
                ids=deps.ids,
            ).record_into(uow)
            survivor = await self._absorb(uow, merged, target, actor_id)
            await uow.events.save(final)
            await _save_if_changed(uow, target, survivor)
            await uow.commit()

    async def _absorb(
        self, uow: EventsUnitOfWork, merged: Event, target: Event, actor_id: EntityId
    ) -> Event:
        deps = self._deps
        moving = [
            link
            for link in merged.report_links
            if link.report_id not in target.report_ids
        ]
        reports = await _load_reports(deps, [link.report_id for link in moving])
        for link, report in zip(moving, reports, strict=True):
            target = target.link_report(
                report,
                role=link.role,
                linked_by=actor_id,
                clock=deps.clock,
                ids=deps.ids,
            ).record_into(uow)
        for place in merged.affected_places:
            target = target.add_affected_place(
                place, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ).record_into(uow)
        return target
