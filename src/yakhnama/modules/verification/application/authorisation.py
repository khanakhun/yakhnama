"""Authorisation of the verification module, through the identity facade.

Every verification command and read is guarded by ``CanModerate`` (moderators and
administrators), handed to the handlers by the composition root. Two rules below are
**proposed defaults**, listed in the task report's open questions:

- a reporter may move the case of **their own report** from ``needs_information``
  back to ``submitted`` once they have added what was asked for;
- raising ``disputed`` on a verified record is for moderators only for now, although
  the maintainer may later open it to every authenticated user.

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    Actor,
    AuthorisationPolicy,
    CanModerate,
    require_allowed,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

__all__ = [
    "AuthorisationPolicy",
    "acting_user_id",
    "moderation_policy",
    "require_allowed",
]


def moderation_policy() -> AuthorisationPolicy:
    """Return the policy guarding every verification command and read.

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
        message = "an anonymous actor cannot change verification cases"
        raise PermissionDeniedError(message, details={"reason": "anonymous"})
    return actor.user_id
