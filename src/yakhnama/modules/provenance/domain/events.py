"""Domain events of the ``provenance`` bounded context.

Every event carries the source's id as ``aggregate_id``, ``aggregate_type`` ``source``
and the source's ``version`` after the change. **Payloads carry ids, types and field
names only**: never a title, citation, URL, publisher or licence text, because events
are relayed to subscribers and kept in the outbox, and a citizen citation can name a
person (``AGENTS.md`` §5).

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.provenance.domain.value_objects import (
    RecordVersion,
    SourceType,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

SOURCE_AGGREGATE_TYPE: Final = "source"
SOURCE_DETAIL_FIELD_COUNT: Final = 7
"""Number of fields in ``SourceDetails``; bounds ``changed_fields``."""


class SourceEvent(DomainEvent):
    """Fields shared by every event about a source; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"source"``.
        version: The source's version after the change.
    """

    aggregate_type: Literal["source"] = SOURCE_AGGREGATE_TYPE
    version: RecordVersion


class SourceRegistered(SourceEvent):
    """A new source was registered.

    Implements: Domain Events.

    Attributes:
        source_type: The kind of source.
        owner_actor_id: The user who registered it, or ``None`` for the system.
        organization_id: The organisation it was registered for, if any.
    """

    event_type: ClassVar[str] = "provenance.source_registered"

    source_type: SourceType
    owner_actor_id: EntityId | None
    organization_id: EntityId | None


class SourceDetailsUpdated(SourceEvent):
    """The descriptive details of an unreferenced source were changed.

    The new values are deliberately absent; only the names of the changed fields
    travel.

    Implements: Domain Events.

    Attributes:
        changed_fields: Names of the ``SourceDetails`` fields that changed.
    """

    event_type: ClassVar[str] = "provenance.source_details_updated"

    changed_fields: frozenset[str] = Field(
        min_length=1, max_length=SOURCE_DETAIL_FIELD_COUNT
    )


class SourceReferenced(SourceEvent):
    """A fact referenced the source for the first time; it is now immutable.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "provenance.source_referenced"
