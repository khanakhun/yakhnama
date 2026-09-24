"""Adapter answering the provenance ``SourceCitationChecker`` port from events.

``SourceCitationCheckerAdapter`` lets ``AuthorisedSourceQueryService`` show a citizen
or organisation source to any reader once a published, verified event cites it
(Q178). The answer comes from the events module's ``EventCitationQueryService``, so
the rule "publicly visible event" stays in the events module.

Only the event's own ``source_ids`` count. A source reached solely through a linked
report or an impact claim stays private to non-members; that errs towards hiding,
and ``Event.link_report`` already adds a linked report's source to the event's
``source_ids``.

Patterns: Adapter.
"""

from yakhnama.modules.events.public import EventCitationQueryService
from yakhnama.shared_kernel.ids import EntityId


class SourceCitationCheckerAdapter:
    """``SourceCitationChecker`` over the events citation read.

    Implements: Adapter.
    """

    def __init__(self, citations: EventCitationQueryService) -> None:
        """Create the adapter.

        Args:
            citations: The events module's citation read port.
        """
        self._citations = citations

    async def is_cited_by_published_event(self, source_id: EntityId) -> bool:
        """Tell whether a published and verified event cites ``source_id``.

        Args:
            source_id: The source.

        Returns:
            ``True`` if at least one such event lists it among its sources.
        """
        return await self._citations.is_source_cited_by_public_event(source_id)
