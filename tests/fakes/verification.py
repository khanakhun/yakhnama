"""In-memory fakes of the verification ports.

The repository stages writes until the unit of work commits, as a rolled-back
database transaction would leave the table unchanged. The query service and the
state read model read the repository's committed rows only, like the SQL
implementations reading committed data.

Patterns: Fake.
"""

from collections.abc import Iterable, Mapping

from tests.fakes.uow import InMemoryUnitOfWork
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
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


class InMemoryVerificationCaseRepository:
    """``VerificationCaseRepository`` over a dictionary keyed by case id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored cases, as a committed transaction left them.
    """

    def __init__(self, cases: Iterable[VerificationCase] = ()) -> None:
        """Create the repository.

        Args:
            cases: Cases that exist before the test acts.
        """
        self.committed: dict[EntityId, VerificationCase] = {
            case.id: case for case in cases
        }
        self._staged: dict[EntityId, VerificationCase] = {}

    def _current(self) -> dict[EntityId, VerificationCase]:
        return {**self.committed, **self._staged}

    async def get(self, case_id: EntityId) -> VerificationCase | None:
        """Return one case, staged changes included.

        Args:
            case_id: The case.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(case_id)

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCase | None:
        """Return the case of a target, staged changes included.

        Args:
            target: The record under verification.

        Returns:
            The aggregate, or ``None``.
        """
        return next(
            (case for case in self._current().values() if case.target == target), None
        )

    async def add(self, case: VerificationCase) -> None:
        """Stage a new case.

        Args:
            case: The new aggregate.

        Raises:
            ConflictError: If the id or the target already has a case.
        """
        current = self._current()
        if case.id in current or any(
            stored.target == case.target for stored in current.values()
        ):
            message = "the verification case or its target already exists"
            raise ConflictError(message)
        self._staged[case.id] = case

    async def save(self, case: VerificationCase) -> None:
        """Stage a changed case.

        Args:
            case: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no case with that id is stored.
        """
        if case.id not in self._current():
            message = "the verification case is not stored"
            raise NotFoundError(message)
        self._staged[case.id] = case

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryVerificationUnitOfWork(InMemoryUnitOfWork):
    """``VerificationUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        verification_cases: The repository bound to this unit of work.
    """

    def __init__(self, cases: Iterable[VerificationCase] = ()) -> None:
        """Create the unit of work.

        Args:
            cases: Cases that exist before the test acts.
        """
        super().__init__()
        self.verification_cases = InMemoryVerificationCaseRepository(cases)

    def _on_commit(self) -> None:
        self.verification_cases.apply_staged()

    def _on_rollback(self) -> None:
        self.verification_cases.discard_staged()


class InMemoryVerificationQueryService:
    """``VerificationQueryService`` and ``VerificationStateReadModel`` in one fake.

    Reads the committed rows of a fake repository; pages by keyset on
    ``(created_at, id)``.

    Implements: Fake (of Query Service).
    """

    def __init__(self, repository: InMemoryVerificationCaseRepository) -> None:
        """Create the query service.

        Args:
            repository: The repository whose committed rows are served.
        """
        self._repository = repository

    def _find(self, target: VerificationTarget) -> VerificationCase | None:
        return next(
            (
                case
                for case in self._repository.committed.values()
                if case.target == target
            ),
            None,
        )

    async def get(self, case_id: EntityId) -> VerificationCaseDetail | None:
        """Return the detail view of one committed case.

        Args:
            case_id: The case.

        Returns:
            The detail view, or ``None``.
        """
        case = self._repository.committed.get(case_id)
        return None if case is None else VerificationCaseDetail.from_entity(case)

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCaseDetail | None:
        """Return the detail view of a target's committed case.

        Args:
            target: The record under verification.

        Returns:
            The detail view, or ``None``.
        """
        case = self._find(target)
        return None if case is None else VerificationCaseDetail.from_entity(case)

    async def list_cases(
        self, query: ListVerificationCases
    ) -> Page[VerificationCaseSummary]:
        """Filter, order and page like the SQL implementation.

        Args:
            query: Filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        ordered = sorted(
            self._repository.committed.values(),
            key=lambda case: (case.created_at, case.id),
        )
        matching = [
            case
            for case in ordered
            if (query.state is None or case.state is query.state)
            and (query.target_kind is None or case.target.kind is query.target_kind)
            and (query.assigned_to is None or case.assigned_to == query.assigned_to)
            and (
                cursor is None
                or (case.created_at.isoformat(), case.id)
                > (cursor.sort_key, cursor.last_id)
            )
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[VerificationCaseSummary](
            items=tuple(VerificationCaseSummary.from_entity(case) for case in window),
            next_cursor=next_cursor,
        )

    async def state_for(self, target: VerificationTarget) -> VerificationState | None:
        """Return the committed state of a target's case.

        Args:
            target: The record.

        Returns:
            The state, or ``None`` if the target has no case.
        """
        case = self._find(target)
        return None if case is None else case.state


class FakeReportOwnerLookup:
    """``ReportOwnerLookup`` answering from a fixed mapping.

    Implements: Fake.

    Attributes:
        asked: Report ids asked about, in call order.
    """

    def __init__(self, owners: Mapping[EntityId, EntityId] | None = None) -> None:
        """Create the lookup.

        Args:
            owners: Reporter user id per report id.
        """
        self._owners = dict(owners or {})
        self.asked: list[EntityId] = []

    async def reporter_of(self, report_id: EntityId) -> EntityId | None:
        """Return the mapped reporter.

        Args:
            report_id: The report.

        Returns:
            The reporter's user id, or ``None`` if unmapped.
        """
        self.asked.append(report_id)
        return self._owners.get(report_id)


class FakeReviewerEligibility:
    """``ReviewerEligibility`` allowing a fixed set of users.

    Implements: Fake.
    """

    def __init__(self, reviewers: Iterable[EntityId] = ()) -> None:
        """Create the fake.

        Args:
            reviewers: The users who may review.
        """
        self._reviewers = frozenset(reviewers)

    async def can_review(self, user_id: EntityId) -> bool:
        """Tell whether ``user_id`` is in the allowed set.

        Args:
            user_id: The candidate reviewer.

        Returns:
            ``True`` if allowed.
        """
        return user_id in self._reviewers
