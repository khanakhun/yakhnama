"""Who may register, change, cite and read sources (**proposed** rules).

- **Register.** Any authenticated user may register a ``citizen`` or
  ``organisation`` source; ``government``, ``news``, ``research``, ``dataset`` and
  ``satellite`` sources need ``CanModerate``, because the best-figure policy ranks
  them above citizen accounts (``docs/architecture/best-figure.md``) and a citizen
  must not be able to mint a higher-ranked source. Registering *for* an
  organisation additionally needs membership of it, or ``CanModerate``.
- **Change details.** The owner (``IsSelf``) or a moderator; a source registered by
  the system has no owner, so only moderators. The domain refuses the change once
  the source is referenced, whoever asks.
- **Mark referenced.** Any authenticated actor: the command is internal and reached
  only through other modules' handlers, which run their own policies first.
- **Read.** Everyone (``CanReadVerifiedData``): sources are the provenance of the
  open dataset, and the DTOs omit the owning user.

Patterns: Policy.
"""

from typing import Final

from yakhnama.modules.identity.public import (
    ActorPolicy,
    CanModerate,
    CanReadVerifiedData,
    IsAuthenticated,
    IsMemberOf,
    IsSelf,
    require_allowed,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.ids import EntityId

__all__ = [
    "SELF_REGISTERED_SOURCE_TYPES",
    "reference_policy",
    "registration_policy",
    "require_allowed",
    "source_editor_policy",
    "source_read_policy",
]

SELF_REGISTERED_SOURCE_TYPES: Final = frozenset(
    {SourceType.CITIZEN, SourceType.ORGANISATION}
)
"""Source types any authenticated user may register (**proposed**)."""


def registration_policy(
    source_type: SourceType, organization_id: EntityId | None
) -> ActorPolicy:
    """Return who may register a source of ``source_type``.

    Args:
        source_type: The kind of source being registered.
        organization_id: The organisation it is registered for, if any.

    Returns:
        ``IsAuthenticated()`` or ``CanModerate()`` by type, conjoined with
        ``IsMemberOf(organization_id) | CanModerate()`` when an organisation is
        named.
    """
    base: ActorPolicy = (
        IsAuthenticated()
        if source_type in SELF_REGISTERED_SOURCE_TYPES
        else CanModerate()
    )
    if organization_id is None:
        return base
    return base & (IsMemberOf(organization_id) | CanModerate())


def source_editor_policy(source: Source) -> ActorPolicy:
    """Return who may change the details of ``source``.

    Args:
        source: The source to change.

    Returns:
        ``IsSelf(owner) | CanModerate()``, or ``CanModerate()`` for a source the
        system registered.
    """
    if source.owner_actor_id is None:
        return CanModerate()
    return IsSelf(source.owner_actor_id) | CanModerate()


def reference_policy() -> ActorPolicy:
    """Return who may mark a source as referenced.

    Returns:
        ``IsAuthenticated()``.
    """
    return IsAuthenticated()


def source_read_policy() -> ActorPolicy:
    """Return who may read sources.

    Returns:
        ``CanReadVerifiedData()``: everyone.
    """
    return CanReadVerifiedData()
