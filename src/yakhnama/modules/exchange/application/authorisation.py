"""Authorisation of the exchange module, through the identity facade.

Rules (**proposed**, Phase 4 plan §5, listed in the task report's open questions):

- exporting ``events`` or ``claims`` needs an authenticated user: the data is the
  verified public record anyone may read, but an export is a stored job that costs
  storage and worker time, so it has an owner;
- exporting ``reports`` needs a moderator, and rows carry rounded positions only;
- an export job is visible to, and cancellable by, its owner and moderators;
- imports (requesting and reading them) need a moderator.

Deny by default: every function returns a policy that refuses anonymous actors.

Patterns: Policy.
"""

from yakhnama.modules.exchange.domain.value_objects import ExportDataset
from yakhnama.modules.identity.public import (
    ActorPolicy,
    CanModerate,
    IsAuthenticated,
    IsSelf,
    any_of,
)
from yakhnama.shared_kernel.ids import EntityId


def export_policy(dataset: ExportDataset) -> ActorPolicy:
    """Return who may export a dataset.

    Args:
        dataset: The dataset.

    Returns:
        ``CanModerate()`` for ``reports``, ``IsAuthenticated()`` otherwise.
    """
    if dataset is ExportDataset.REPORTS:
        return CanModerate()
    return IsAuthenticated()


def export_job_policy(requested_by: EntityId) -> ActorPolicy:
    """Return who may read or cancel an export job.

    Args:
        requested_by: The job's owner.

    Returns:
        The owner or any moderator.
    """
    return any_of(IsSelf(requested_by), CanModerate())


def import_policy() -> ActorPolicy:
    """Return who may request and read imports.

    Returns:
        ``CanModerate()``.
    """
    return CanModerate()
