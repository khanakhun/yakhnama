"""Authorised read use cases of the verification module.

A case's history names moderators and holds their reasons, so every read is
guarded by ``CanModerate`` before the ``VerificationQueryService`` port is asked.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.verification.application.authorisation import (
    AuthorisationPolicy,
    require_allowed,
)
from yakhnama.modules.verification.application.dto import (
    VerificationCaseDetail,
    VerificationCaseSummary,
)
from yakhnama.modules.verification.application.ports import VerificationQueryService
from yakhnama.modules.verification.application.queries import (
    GetVerificationCase,
    ListVerificationCases,
)
from yakhnama.modules.verification.domain.errors import VerificationCaseNotFoundError
from yakhnama.shared_kernel.pagination import Page


class VerificationCaseQueryService:
    """Answers verification queries for actors the policy allows.

    Implements: Query Service.
    """

    def __init__(
        self, reads: VerificationQueryService, policy: AuthorisationPolicy
    ) -> None:
        """Create the service.

        Args:
            reads: The read port.
            policy: Decides whether an actor may read cases (``CanModerate``).
        """
        self._reads = reads
        self._policy = policy

    async def get_case(self, query: GetVerificationCase) -> VerificationCaseDetail:
        """Return one case with its history.

        Args:
            query: The case and the actor.

        Returns:
            The detail view.

        Raises:
            PermissionDeniedError: If the policy refuses ``query.actor``.
            VerificationCaseNotFoundError: If no case has that id.
        """
        require_allowed(self._policy, query.actor, action="read verification cases")
        case = await self._reads.get(query.case_id)
        if case is None:
            raise VerificationCaseNotFoundError(query.case_id)
        return case

    async def list_cases(
        self, query: ListVerificationCases
    ) -> Page[VerificationCaseSummary]:
        """Return one page of cases.

        Args:
            query: Filters, page request and the actor.

        Returns:
            The page.

        Raises:
            PermissionDeniedError: If the policy refuses ``query.actor``.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(self._policy, query.actor, action="list verification cases")
        return await self._reads.list_cases(query)
