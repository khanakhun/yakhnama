"""Ports the verification application layer depends on, and the one it offers.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1). ``ReportOwnerLookup`` and ``ReviewerEligibility`` are
answered by other modules (``reports`` and ``identity``) through adapters over their
facades, so this module never imports them. ``VerificationStateReadModel`` is the
port other modules read verification states through; the facade exports it.

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

from yakhnama.modules.verification.application.dto import (
    VerificationCaseDetail,
    VerificationCaseSummary,
)
from yakhnama.modules.verification.application.queries import ListVerificationCases
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class VerificationCaseRepository(Protocol):
    """Loads and stages ``VerificationCase`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, case_id: EntityId) -> VerificationCase | None:
        """Return one case.

        Args:
            case_id: The case.

        Returns:
            The aggregate, or ``None`` if no case has that id.
        """
        ...

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCase | None:
        """Return the case of a target; a target has at most one case for life.

        Args:
            target: The record under verification.

        Returns:
            The aggregate, or ``None`` if the target has no case yet.
        """
        ...

    async def add(self, case: VerificationCase) -> None:
        """Stage a new case.

        Args:
            case: The new aggregate.

        Raises:
            ConflictError: If the id or the target already has a case.
        """
        ...

    async def save(self, case: VerificationCase) -> None:
        """Stage a changed case.

        Args:
            case: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no case with that id is stored.
        """
        ...


class VerificationUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the verification repository.

    Implements: Unit of Work.
    """

    @property
    def verification_cases(self) -> VerificationCaseRepository:
        """Return the case repository bound to this transaction."""
        ...


type VerificationUnitOfWorkFactory = UnitOfWorkFactory[VerificationUnitOfWork]
"""Opens a fresh verification unit of work per use case."""


class ReportOwnerLookup(Protocol):
    """Tells who submitted a report, for the reporter's resubmission rule.

    Bound in the composition root to an adapter over the ``reports`` facade.

    Implements: Adapter (port side).
    """

    async def reporter_of(self, report_id: EntityId) -> EntityId | None:
        """Return the user who submitted a report.

        Args:
            report_id: The report.

        Returns:
            The reporter's user id, or ``None`` if the report does not exist or has
            no user account behind it.
        """
        ...


class ReviewerEligibility(Protocol):
    """Tells whether a user may be made responsible for verification cases.

    Bound in the composition root to an adapter over the ``identity`` facade; the
    rule it applies there is ``CanModerate`` on the user's current actor.

    Implements: Adapter (port side).
    """

    async def can_review(self, user_id: EntityId) -> bool:
        """Tell whether ``user_id`` may review cases.

        Args:
            user_id: The candidate reviewer.

        Returns:
            ``True`` if the user exists, is active and may moderate.
        """
        ...


class VerificationQueryService(Protocol):
    """Read port for verification cases.

    Authorisation is applied by ``VerificationCaseQueryService`` in this layer
    before this port is called.

    Implements: Query Service.
    """

    async def get(self, case_id: EntityId) -> VerificationCaseDetail | None:
        """Return one case with its history.

        Args:
            case_id: The case.

        Returns:
            The detail view, or ``None`` if no case has that id.
        """
        ...

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCaseDetail | None:
        """Return the case of a target with its history.

        Args:
            target: The record under verification.

        Returns:
            The detail view, or ``None`` if the target has no case.
        """
        ...

    async def list_cases(
        self, query: ListVerificationCases
    ) -> Page[VerificationCaseSummary]:
        """Return one page of cases matching every set filter of ``query``.

        Ordered by ``created_at``, then by id; the cursor's ``sort_key`` is
        ``created_at`` in ISO 8601 and its ``last_id`` the last case's id.

        Args:
            query: Filters and page request; ``actor`` is ignored here.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...


class VerificationStateReadModel(Protocol):
    """Where other modules read the current verification state of their records.

    The events read model, for example, mirrors ``state_for`` of each event as
    ``verification_state``. Reads are not authorised here: a state is not personal
    data, and callers apply their own visibility rules.

    Implements: Query Service.
    """

    async def state_for(self, target: VerificationTarget) -> VerificationState | None:
        """Return the current state of a target's case.

        Args:
            target: The record.

        Returns:
            The state, or ``None`` if the target has no case.
        """
        ...
