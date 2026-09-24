"""SQL implementations of the verification query service and state read model.

Cases are read whole and rebuilt through the aggregate (``row_to_case``), so a
detail view carries a history that has been replayed against the state table.
Listings select only the summary columns and never load ``history``. They page by
keyset on ``(created_at, id)`` ascending, oldest first; the cursor's ``sort_key`` is
``created_at`` in ISO 8601 and ``last_id`` the last case's id, as the port requires.

Patterns: Query Service (adapter side).
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.verification.application.dto import (
    VerificationCaseDetail,
    VerificationCaseSummary,
)
from yakhnama.modules.verification.application.queries import ListVerificationCases
from yakhnama.modules.verification.domain.value_objects import (
    VerificationState,
    VerificationTarget,
)
from yakhnama.modules.verification.infrastructure.mappers import row_to_case
from yakhnama.modules.verification.infrastructure.orm import VerificationCaseRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


def decode_created_after(sort_key: str) -> datetime:
    """Parse the ``created_at`` instant a case-listing cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        after = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # Comparing a naive instant with timestamptz would assume the session zone.
    if after.utcoffset() is None:
        raise _invalid_cursor()
    return after


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


def _is_target(target: VerificationTarget) -> ColumnElement[bool]:
    return and_(
        VerificationCaseRow.target_kind == target.kind.value,
        VerificationCaseRow.target_id == target.target_id,
    )


def _filters(query: ListVerificationCases) -> ColumnElement[bool]:
    conditions: list[ColumnElement[bool]] = []
    if query.state is not None:
        conditions.append(VerificationCaseRow.state == query.state.value)
    if query.target_kind is not None:
        conditions.append(VerificationCaseRow.target_kind == query.target_kind.value)
    if query.assigned_to is not None:
        conditions.append(VerificationCaseRow.assigned_to == query.assigned_to)
    return and_(true(), *conditions)


class SqlAlchemyVerificationQueryService:
    """PostgreSQL-backed ``VerificationQueryService`` and state read model.

    Serves the ports ``VerificationQueryService`` and
    ``VerificationStateReadModel``.

    Implements: Query Service.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def get(self, case_id: EntityId) -> VerificationCaseDetail | None:
        """Return one case with its history.

        Args:
            case_id: The case.

        Returns:
            The detail view, or ``None``.
        """
        return await self._detail(VerificationCaseRow.id == case_id)

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCaseDetail | None:
        """Return the case of a target with its history.

        Args:
            target: The record under verification.

        Returns:
            The detail view, or ``None``.
        """
        return await self._detail(_is_target(target))

    async def _detail(
        self, condition: ColumnElement[bool]
    ) -> VerificationCaseDetail | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(VerificationCaseRow).where(condition))
            ).scalar_one_or_none()
        return (
            None
            if row is None
            else VerificationCaseDetail.from_entity(row_to_case(row))
        )

    async def list_cases(
        self, query: ListVerificationCases
    ) -> Page[VerificationCaseSummary]:
        """Return one page of cases matching every set filter, oldest first.

        Args:
            query: Filters and page request; ``actor`` is ignored here.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = (
            select(
                VerificationCaseRow.id,
                VerificationCaseRow.target_kind,
                VerificationCaseRow.target_id,
                VerificationCaseRow.state,
                VerificationCaseRow.assigned_to,
                VerificationCaseRow.version,
                VerificationCaseRow.created_at,
                VerificationCaseRow.updated_at,
            )
            .where(_filters(query))
            .order_by(VerificationCaseRow.created_at, VerificationCaseRow.id)
            # One extra row tells whether another page follows.
            .limit(query.page.limit + 1)
        )
        if cursor is not None:
            after = decode_created_after(cursor.sort_key)
            statement = statement.where(
                or_(
                    VerificationCaseRow.created_at > after,
                    and_(
                        VerificationCaseRow.created_at == after,
                        VerificationCaseRow.id > cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        summaries = [
            VerificationCaseSummary.model_validate(
                {
                    "id": row.id,
                    "target": {"kind": row.target_kind, "target_id": row.target_id},
                    "state": row.state,
                    "assigned_to": row.assigned_to,
                    "version": row.version,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
            for row in rows
        ]
        window = summaries[: query.page.limit]
        next_cursor = None
        if len(summaries) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[VerificationCaseSummary](
            items=tuple(window), next_cursor=next_cursor
        )

    async def state_for(self, target: VerificationTarget) -> VerificationState | None:
        """Return the current state of a target's case.

        Args:
            target: The record.

        Returns:
            The state, or ``None`` if the target has no case.
        """
        async with self._session_factory() as session:
            state = await session.scalar(
                select(VerificationCaseRow.state).where(_is_target(target))
            )
        return None if state is None else VerificationState(state)


class SqlAlchemyVerificationStateReadModel:
    """PostgreSQL-backed ``VerificationStateReadModel`` for other modules.

    A thin, separately bindable read model so the facade can hand other modules
    only ``state_for``, never case details.

    Implements: Query Service (port ``VerificationStateReadModel``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the read model.

        Args:
            session_factory: Opens one read session per query.
        """
        self._queries = SqlAlchemyVerificationQueryService(session_factory)

    async def state_for(self, target: VerificationTarget) -> VerificationState | None:
        """Return the current state of a target's case.

        Args:
            target: The record.

        Returns:
            The state, or ``None`` if the target has no case.
        """
        return await self._queries.state_for(target)
