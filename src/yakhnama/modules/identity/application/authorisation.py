"""Authorisation checks shared by every module's handlers, and identity read rules.

``require_allowed`` is the one way a handler asks a policy: it raises
``PermissionDeniedError`` naming the action and the policy class, and deliberately
nothing about the actor, because the error may be logged or returned and who was
refused is the audit log's business, not the client's (``AGENTS.md`` §5).

The read rules below are **proposed defaults** (see the task report's open
questions): anyone may read an organisation's public detail; only its members and
platform administrators may list its members, because the list shows display names.

Patterns: Policy.
"""

from yakhnama.modules.identity.domain.policies import (
    ActorPolicy,
    AuthorisationPolicy,
    CanReadVerifiedData,
    IsAdmin,
    IsAuthenticated,
    IsMemberOf,
    IsSelf,
)
from yakhnama.modules.identity.domain.value_objects import Actor
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId


def require_allowed(policy: AuthorisationPolicy, actor: Actor, *, action: str) -> None:
    """Refuse the action unless ``policy`` explicitly allows ``actor``.

    Args:
        policy: The rule guarding the action.
        actor: Who is acting.
        action: What is being attempted, in words, for example
            ``"rename organisations"``; it appears in the error.

    Raises:
        PermissionDeniedError: If the policy refuses; ``details`` hold the action
            and the policy's class name only.
    """
    if not policy.is_allowed(actor):
        message = f"the actor may not {action}"
        raise PermissionDeniedError(
            message, details={"action": action, "policy": type(policy).__name__}
        )


def self_policy(actor: Actor) -> ActorPolicy:
    """Return the policy allowing an actor to change their own record.

    Args:
        actor: Who is acting.

    Returns:
        ``IsSelf(actor.user_id)``, or ``IsAuthenticated()`` (which refuses) for an
        anonymous actor, who has no record.
    """
    return IsAuthenticated() if actor.user_id is None else IsSelf(actor.user_id)


def organization_read_policy() -> ActorPolicy:
    """Return who may read an organisation's detail: everyone (**proposed**).

    Returns:
        ``CanReadVerifiedData()``: slug, name, type, status and member count are
        not personal data.
    """
    return CanReadVerifiedData()


def member_list_policy(organization_id: EntityId) -> ActorPolicy:
    """Return who may list an organisation's members (**proposed**).

    Args:
        organization_id: The organisation.

    Returns:
        ``IsAdmin() | IsMemberOf(organization_id)``.
    """
    return IsAdmin() | IsMemberOf(organization_id)
