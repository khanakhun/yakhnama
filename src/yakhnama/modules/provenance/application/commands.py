"""Write requests accepted by the provenance command handlers.

Every command carries the ``actor`` the request runs as. ``UpdateSourceDetails``
accepts an optional ``expected_version``: the API fills it from ``If-Match`` and the
handler raises ``PreconditionFailedError`` (HTTP 412) when the stored version differs.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.provenance.domain.value_objects import (
    RecordVersion,
    SourceDetails,
    SourceType,
)
from yakhnama.shared_kernel.ids import EntityId


class RegisterSource(BaseModel):
    """Register a new provenance source.

    Implements: Command.

    Attributes:
        actor: Who asks; they become the source's owner.
        source_type: The kind of source; fixed from now on.
        details: Title, citation and the optional descriptive fields.
        organization_id: The organisation the source is registered for, if any;
            the actor must belong to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    source_type: SourceType
    details: SourceDetails
    organization_id: EntityId | None = None


class UpdateSourceDetails(BaseModel):
    """Replace the descriptive details of a source nothing references yet.

    Implements: Command.

    Attributes:
        actor: Who asks; the owner or a moderator.
        source_id: The source.
        details: The complete new details; a field left ``None`` is cleared.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    source_id: EntityId
    details: SourceDetails
    expected_version: RecordVersion | None = None


class MarkSourceReferenced(BaseModel):
    """Record that a fact now cites a source, which freezes the source for good.

    Internal: no endpoint accepts it. Other modules' handlers send it through the
    ``SourceReferenceMarker`` port (bound to ``MarkSourceReferencedHandler`` in the
    composition root) right after they commit the fact that cites the source.

    Implements: Command.

    Attributes:
        actor: The actor of the use case that cited the source.
        source_id: The cited source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    source_id: EntityId
