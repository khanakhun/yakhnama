"""Adapters answering the verification module's ports from other facades.

- ``ReportOwnerAdapter`` (``ReportOwnerLookup``): who submitted a report, from the
  reports read model, for the reporter's resubmission rule.
- ``ReviewerEligibilityAdapter`` (``ReviewerEligibility``): whether a user may be
  made responsible for cases: the user exists, is active, and their actor
  satisfies identity's ``CanModerate``.

Patterns: Adapter.
"""

from yakhnama.modules.identity.public import CanModerate, IdentityUnitOfWorkFactory
from yakhnama.modules.reports.public import ReportQueryService
from yakhnama.shared_kernel.ids import EntityId


class ReportOwnerAdapter:
    """``ReportOwnerLookup`` over the reports read model.

    Implements: Adapter.
    """

    def __init__(self, reports: ReportQueryService) -> None:
        """Create the adapter.

        Args:
            reports: The reports module's read port.
        """
        self._reports = reports

    async def reporter_of(self, report_id: EntityId) -> EntityId | None:
        """Return the user who submitted a report.

        Args:
            report_id: The report.

        Returns:
            The reporter's user id, or ``None`` if the report does not exist.
        """
        record = await self._reports.get_report(report_id)
        return None if record is None else record.reporter_id


class ReviewerEligibilityAdapter:
    """``ReviewerEligibility`` over the identity users and ``CanModerate``.

    Implements: Adapter.
    """

    def __init__(self, identity: IdentityUnitOfWorkFactory) -> None:
        """Create the adapter.

        Args:
            identity: Opens an identity unit of work; this adapter only reads.
        """
        self._identity = identity
        self._policy = CanModerate()

    async def can_review(self, user_id: EntityId) -> bool:
        """Tell whether ``user_id`` may review cases.

        Args:
            user_id: The candidate reviewer.

        Returns:
            ``True`` if the user exists, is active and may moderate.
        """
        # Read-only: the unit of work is never committed, so leaving it rolls back.
        async with self._identity() as uow:
            user = await uow.users.get(user_id)
        # A suspended user never gets an actor (User.to_actor raises), so the
        # status is checked first rather than turned into an error here.
        if user is None or not user.is_active:
            return False
        return self._policy.is_allowed(user.to_actor())
