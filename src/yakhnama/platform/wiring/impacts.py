"""Adapters answering the impacts claims ports from other modules' facades.

- ``HazardEventDirectoryAdapter`` (``HazardEventDirectory``): whether an event
  exists and whether anyone may read it, from the events read model.
- ``ImpactSourceMarkerAdapter`` (``ImpactSourceMarker``): cites a source through
  the provenance ``MarkSourceReferencedHandler`` and returns its type, which the
  best-figure policy ranks.

Internal import: ``is_publicly_visible`` comes from
``events.application.authorisation``. It is the events module's own visibility rule
(published and verified); re-implementing it here would let the two drift, and the
events facade does not export it yet (open question for the events owner).

Patterns: Adapter.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from yakhnama.modules.events.application.authorisation import is_publicly_visible
from yakhnama.modules.events.public import EventQueryService
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.public import SourceTypeName
from yakhnama.modules.provenance.public import (
    MarkSourceReferenced,
    SourceReferenceMarker,
    SourceType,
)
from yakhnama.shared_kernel.ids import EntityId

SOURCE_TYPE_NAMES: Final[Mapping[SourceType, SourceTypeName]] = MappingProxyType(
    {
        SourceType.CITIZEN: "citizen",
        SourceType.ORGANISATION: "organisation",
        SourceType.GOVERNMENT: "government",
        SourceType.NEWS: "news",
        SourceType.SATELLITE: "satellite",
        SourceType.RESEARCH: "research",
        SourceType.DATASET: "dataset",
    }
)
"""Provenance source types as the impacts module names them (anti-corruption map).

Spelled out rather than derived from the enum values so a new provenance type fails
the exhaustiveness test instead of reaching the best-figure ranking unranked."""


class HazardEventDirectoryAdapter:
    """``HazardEventDirectory`` over the events read model.

    Implements: Adapter.
    """

    def __init__(self, events: EventQueryService) -> None:
        """Create the adapter.

        Args:
            events: The events module's read port (joins the verification state).
        """
        self._events = events

    async def exists(self, event_id: EntityId) -> bool:
        """Tell whether a hazard event exists, whatever its status.

        Args:
            event_id: The event.

        Returns:
            ``True`` if it exists.
        """
        return await self._events.get(event_id) is not None

    async def is_publicly_visible(self, event_id: EntityId) -> bool:
        """Tell whether anyone may read the event (published and verified).

        Args:
            event_id: The event.

        Returns:
            ``True`` if the event exists and a non-moderator may read it.
        """
        detail = await self._events.get(event_id)
        return detail is not None and is_publicly_visible(detail)


class ImpactSourceMarkerAdapter:
    """``ImpactSourceMarker`` over the provenance marker.

    Implements: Adapter.
    """

    def __init__(self, marker: SourceReferenceMarker) -> None:
        """Create the adapter.

        Args:
            marker: The provenance ``MarkSourceReferencedHandler``.
        """
        self._marker = marker

    async def mark_referenced(
        self, source_id: EntityId, *, actor: Actor
    ) -> SourceTypeName:
        """Mark the source referenced and return its type.

        Args:
            source_id: The source a claim, asset or damage record cites.
            actor: The moderator whose record cites it.

        Returns:
            The source's type, as the impacts module names it.

        Raises:
            SourceNotFoundError: If the source does not exist.
            PermissionDeniedError: If the actor may not cite sources.
        """
        detail = await self._marker(
            MarkSourceReferenced(actor=actor, source_id=source_id)
        )
        return SOURCE_TYPE_NAMES[detail.source_type]
