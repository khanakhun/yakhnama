"""Write requests accepted by the provenance command handlers.

Every command carries the ``actor`` the request runs as, except
``RegisterPlatformSource``, which the platform sends on its own behalf (ADR 0020).
``UpdateSourceDetails``
accepts an optional ``expected_version``: the API fills it from ``If-Match`` and the
handler raises ``PreconditionFailedError`` (HTTP 412) when the stored version differs.

Patterns: Command.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.provenance.application.authorisation import (
    SELF_REGISTERED_SOURCE_TYPES,
)
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


class RegisterPlatformSource(BaseModel):
    """Register a source the platform owns, for a fact it records itself.

    Internal: no endpoint accepts it. The reports module sends it through the
    ``PlatformSourceRegistrar`` port for a guest report or a guest upload, which
    has no user to own a source; the guest use case has checked the guest's
    capability first (ADR 0020). Only the types any user may register
    (``citizen``, ``organisation``) are accepted, so this path can never mint a
    higher-ranked source.

    Implements: Command.

    Attributes:
        source_type: ``citizen`` or ``organisation``.
        details: Title, citation and the optional descriptive fields; written by
            the platform, never copied from what a guest typed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: SourceType
    details: SourceDetails

    @model_validator(mode="after")
    def _only_self_registered_types(self) -> Self:
        if self.source_type not in SELF_REGISTERED_SOURCE_TYPES:
            message = "the platform registers only citizen or organisation sources"
            raise ValueError(message)
        return self
