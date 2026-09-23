"""Who may submit, correct, withdraw and read reports (**proposed** rules).

- **Submit.** Any authenticated user; submitting *for* an organisation also needs
  membership of it.
- **Revise and withdraw.** The reporter only (``IsSelf``): a report is one person's
  observation, and a moderator's view of it belongs in verification, not in the
  report.
- **Read one report.** The reporter and moderators see it exactly; members of the
  organisation it was reported for see it with the position rounded and the GPS
  accuracy hidden; anyone else is told it does not exist. Only moderators see the
  triage flags.
- **List reports.** Authenticated users; moderators see every report, everyone else
  only their own. Listings always show rounded positions.
- **Triage.** ``RunTriage`` has no actor: it is a system task enqueued by the
  handlers above and never routed from the API.

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    Actor,
    ActorPolicy,
    CanModerate,
    IsAuthenticated,
    IsMemberOf,
    IsSelf,
    require_allowed,
)
from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

__all__ = [
    "exact_view_policy",
    "is_moderator",
    "reporter_policy",
    "require_allowed",
    "require_user",
    "rounded_view_policy",
    "submit_policy",
    "triage_view_policy",
]


def submit_policy(organization_id: EntityId | None) -> ActorPolicy:
    """Return who may submit a report, optionally for an organisation.

    Args:
        organization_id: The organisation reported for, if any.

    Returns:
        ``IsAuthenticated()``, conjoined with ``IsMemberOf(organization_id)``.
    """
    if organization_id is None:
        return IsAuthenticated()
    return IsAuthenticated() & IsMemberOf(organization_id)


def reporter_policy(reporter_id: EntityId) -> ActorPolicy:
    """Return who may revise or withdraw a report: its reporter.

    Args:
        reporter_id: The report's reporter.

    Returns:
        ``IsSelf(reporter_id)``.
    """
    return IsSelf(reporter_id)


def exact_view_policy(record: ReportRecord) -> ActorPolicy:
    """Return who may see a report with its exact position.

    Args:
        record: The report.

    Returns:
        ``IsSelf(reporter) | CanModerate()``.
    """
    return IsSelf(record.reporter_id) | CanModerate()


def rounded_view_policy(record: ReportRecord) -> ActorPolicy | None:
    """Return who else may see a report with its position rounded.

    Args:
        record: The report.

    Returns:
        ``IsMemberOf(organization)`` for an organisation's report, else ``None``.
    """
    if record.organization_id is None:
        return None
    return IsMemberOf(record.organization_id)


def triage_view_policy() -> ActorPolicy:
    """Return who may see triage flags.

    Returns:
        ``CanModerate()``.
    """
    return CanModerate()


def require_user(actor: Actor, *, action: str) -> EntityId:
    """Refuse an anonymous actor and return the acting user's id.

    The same rule and error as ``require_allowed(IsAuthenticated(), ...)``
    (``Actor.is_authenticated`` means "has a user id"), with the id returned so the
    caller holds a narrowed ``EntityId`` instead of re-checking for ``None``.

    Args:
        actor: Who is acting.
        action: What is being attempted, for the error.

    Returns:
        ``actor.user_id``.

    Raises:
        PermissionDeniedError: If the actor is anonymous; the same error
            ``require_allowed`` raises for ``IsAuthenticated``.
    """
    user_id = actor.user_id
    if user_id is None:
        message = f"the actor may not {action}"
        raise PermissionDeniedError(
            message, details={"action": action, "policy": "IsAuthenticated"}
        )
    return user_id


def is_moderator(actor: Actor) -> bool:
    """Tell whether ``actor`` may moderate, so listings are not scoped to them.

    Args:
        actor: Who asks.

    Returns:
        ``CanModerate().is_allowed(actor)``.
    """
    return CanModerate().is_allowed(actor)
