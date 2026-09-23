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
- **Read.** Sources of the ranked types (``government``, ``news``, ``research``,
  ``dataset``, ``satellite``) are read by everyone (``CanReadVerifiedData``): they
  are the provenance of the open dataset. A ``citizen`` or ``organisation`` source
  exists because someone submitted a raw, unverified report or upload, so its mere
  existence, timestamps and organisation reveal that private record (security
  review, Phase 3). It is read only by moderators, by members of the organisation
  it was registered for, or by anyone once a published and verified event cites
  it (checked through ``SourceCitationChecker`` by the query service). Everyone
  else is told it does not exist. Listings follow the same rule, except that
  non-moderators never see citizen or organisation sources there (see
  ``source_listing_specification``).

Patterns: Policy.
"""

from typing import Final

from yakhnama.modules.identity.public import (
    Actor,
    ActorPolicy,
    CanModerate,
    CanReadVerifiedData,
    IsAuthenticated,
    IsMemberOf,
    IsSelf,
    require_allowed,
)
from yakhnama.modules.provenance.application.dto import SourceDetail
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.specification import Specification, TrueSpecification

__all__ = [
    "SELF_REGISTERED_SOURCE_TYPES",
    "reference_policy",
    "registration_policy",
    "require_allowed",
    "source_editor_policy",
    "source_listing_specification",
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


def source_read_policy(source: SourceDetail) -> ActorPolicy:
    """Return who may read ``source`` without it being cited by a public event.

    Args:
        source: The source.

    Returns:
        ``CanReadVerifiedData()`` for the ranked types; for ``citizen`` and
        ``organisation`` sources ``CanModerate()``, or
        ``CanModerate() | IsMemberOf(organization_id)`` when it was registered
        for an organisation.
    """
    if source.source_type not in SELF_REGISTERED_SOURCE_TYPES:
        return CanReadVerifiedData()
    if source.organization_id is None:
        return CanModerate()
    return CanModerate() | IsMemberOf(source.organization_id)


def source_listing_specification(actor: Actor) -> Specification[Source]:
    """Return the visibility filter every source listing is conjoined with.

    A per-row citation or membership check cannot be compiled into the listing
    query without reading other modules' tables, and filtering a page after the
    query would leak hidden sources' ids through the cursor. So non-moderators
    see only the ranked types in listings; a cited citizen or organisation source
    is still readable by id (for example from the event that cites it).

    Args:
        actor: Who lists.

    Returns:
        A specification every source satisfies for moderators; otherwise one
        that excludes ``citizen`` and ``organisation`` sources.
    """
    if CanModerate().is_allowed(actor):
        return TrueSpecification[Source]()
    return ~(
        SourceTypeSpecification(SourceType.CITIZEN)
        | SourceTypeSpecification(SourceType.ORGANISATION)
    )
