"""Public facade of the ``events`` module.

Other modules, the API and the composition root import only this file: the commands,
queries, DTOs, handlers and read service the API wires, the ports the composition
root binds (including those towards other modules), and the values other contexts
may hold about events.

Patterns: Facade.
"""

from yakhnama.modules.events.application.authorisation import moderation_policy
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
from yakhnama.modules.events.application.dto import (
    EventDetail,
    EventRelationView,
    EventSummary,
    ReportLinkView,
    TimelineEntry,
    TimelineEntryKind,
)
from yakhnama.modules.events.application.handlers import (
    AddAffectedPlaceHandler,
    CreateEventFromReportsHandler,
    EventHandlerDependencies,
    LinkReportToEventHandler,
    MergeEventsHandler,
    PublishEventHandler,
    RelateEventsHandler,
    RetractEventHandler,
    SetEventAttributesHandler,
    SetEventGeometryHandler,
    SetEventPeriodHandler,
    UnlinkReportFromEventHandler,
)
from yakhnama.modules.events.application.ports import (
    EventCitationQueryService,
    EventQueryService,
    EventRelationRepository,
    EventRepository,
    EventsUnitOfWork,
    EventsUnitOfWorkFactory,
    EventTimelineSources,
    PlaceDirectory,
    ReportFactsProvider,
    SourceReferenceMarker,
    VerificationCaseOpener,
)
from yakhnama.modules.events.application.queries import (
    GetEvent,
    GetEventTimeline,
    ListEvents,
)
from yakhnama.modules.events.application.query_services import (
    EventRecordQueryService,
)
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventStatus,
    RelationKind,
    ReportForEvent,
)

__all__ = [
    "AddAffectedPlace",
    "AddAffectedPlaceHandler",
    "AffectedPlace",
    "CreateEventFromReports",
    "CreateEventFromReportsHandler",
    "EventCitationQueryService",
    "EventDetail",
    "EventGeometry",
    "EventHandlerDependencies",
    "EventPeriod",
    "EventQueryService",
    "EventRecordQueryService",
    "EventRelationRepository",
    "EventRelationView",
    "EventRepository",
    "EventStatus",
    "EventSummary",
    "EventTimelineSources",
    "EventsUnitOfWork",
    "EventsUnitOfWorkFactory",
    "GetEvent",
    "GetEventTimeline",
    "LinkReportToEvent",
    "LinkReportToEventHandler",
    "ListEvents",
    "MergeEvents",
    "MergeEventsHandler",
    "PlaceDirectory",
    "PublishEvent",
    "PublishEventHandler",
    "RelateEvents",
    "RelateEventsHandler",
    "RelationKind",
    "ReportFactsProvider",
    "ReportForEvent",
    "ReportLinkView",
    "RetractEvent",
    "RetractEventHandler",
    "SetEventAttributes",
    "SetEventAttributesHandler",
    "SetEventGeometry",
    "SetEventGeometryHandler",
    "SetEventPeriod",
    "SetEventPeriodHandler",
    "SourceReferenceMarker",
    "TimelineEntry",
    "TimelineEntryKind",
    "UnlinkReportFromEvent",
    "UnlinkReportFromEventHandler",
    "VerificationCaseOpener",
    "moderation_policy",
]
