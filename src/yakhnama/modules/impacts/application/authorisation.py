"""Authorisation of the impacts write side, through the identity facade.

Every impacts command handler receives an ``AuthorisationPolicy`` from the
composition root and asks it about the command's ``actor`` before reading or
staging anything, through ``require_allowed``. The policy that guards changes to
the impact metric registry is ``CanManageReferenceData`` (platform administrators only);
``reference_data_policy`` returns it so the composition root does not repeat the
choice. Handlers deny by default: only what the policy explicitly allows passes.

Recording impacts (claims, assets, damage) is moderation work, guarded by
``CanModerate``, which ``moderation_policy`` returns. Reading them follows the
event: a non-moderator may read the impacts of an event only while that event is
publicly visible (published and verified, **proposed**).

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    Actor,
    AuthorisationPolicy,
    CanManageReferenceData,
    CanModerate,
    require_allowed,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

__all__ = [
    "AuthorisationPolicy",
    "acting_user_id",
    "moderation_policy",
    "reference_data_policy",
    "require_allowed",
]


def reference_data_policy() -> AuthorisationPolicy:
    """Return the policy guarding every change to the impact metrics.

    Returns:
        ``CanManageReferenceData()``.
    """
    return CanManageReferenceData()


def moderation_policy() -> AuthorisationPolicy:
    """Return the policy guarding every change to claims, assets and damage.

    Returns:
        ``CanModerate()``.
    """
    return CanModerate()


def acting_user_id(actor: Actor) -> EntityId:
    """Return the user id of an actor that passed a policy.

    Every policy used here refuses an anonymous actor already; this keeps a future
    permissive policy from recording a fact without an account behind it.

    Args:
        actor: Who is acting.

    Returns:
        ``actor.user_id``.

    Raises:
        PermissionDeniedError: If the actor is anonymous.
    """
    if actor.user_id is None:
        message = "an anonymous actor cannot record impacts"
        raise PermissionDeniedError(message, details={"reason": "anonymous"})
    return actor.user_id
