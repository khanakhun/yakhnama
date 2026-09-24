"""Authorisation of the events module, through the identity facade.

Every events command is guarded by ``CanModerate`` (moderators and administrators),
handed to the handlers by the composition root. Reads apply a visibility rule
instead (**proposed**, listed in the task report's open questions): an actor the
moderation policy allows sees every event; everyone else, anonymous callers
included, sees only events that are both ``published`` and ``verified``.

Patterns: Policy.
"""

from yakhnama.modules.events.application.dto import EventDetail
from yakhnama.modules.events.domain.specifications import (
    VERIFIED_STATE,
    EventSearchCandidate,
    EventStatusSpecification,
    VerifiedEventSpecification,
)
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.identity.public import (
    Actor,
    AuthorisationPolicy,
    CanModerate,
    require_allowed,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.specification import Specification

__all__ = [
    "AuthorisationPolicy",
    "acting_user_id",
    "is_publicly_visible",
    "moderation_policy",
    "public_visibility",
    "require_allowed",
]


def moderation_policy() -> AuthorisationPolicy:
    """Return the policy guarding every events command and unrestricted reads.

    Returns:
        ``CanModerate()``.
    """
    return CanModerate()


def acting_user_id(actor: Actor) -> EntityId:
    """Return the user id of an actor that passed a policy.

    Every policy used here refuses an anonymous actor already; this keeps a future
    permissive policy from recording a change without a user behind it.

    Args:
        actor: Who is acting.

    Returns:
        ``actor.user_id``.

    Raises:
        PermissionDeniedError: If the actor is anonymous.
    """
    if actor.user_id is None:
        message = "an anonymous actor cannot change events"
        raise PermissionDeniedError(message, details={"reason": "anonymous"})
    return actor.user_id


def public_visibility() -> Specification[EventSearchCandidate]:
    """Return the filter every non-moderator's event search is narrowed by.

    Returns:
        ``published`` AND ``verified``.
    """
    return EventStatusSpecification(EventStatus.PUBLISHED).and_(
        VerifiedEventSpecification()
    )


def is_publicly_visible(detail: EventDetail) -> bool:
    """Tell whether a non-moderator may read an event.

    Args:
        detail: The event's detail view.

    Returns:
        ``True`` if it is published and its verification case is verified; the
        same rule as ``public_visibility``.
    """
    return (
        detail.status is EventStatus.PUBLISHED
        and detail.verification_state == VERIFIED_STATE
    )
