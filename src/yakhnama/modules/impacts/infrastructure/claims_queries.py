"""SQL implementation of the ``ImpactQueryService`` port (claims and assets).

Claims are listed per event in recording order by keyset on ``(created_at, id)``
ascending, served by ``ix_impact_claims_event_id_created_at_id``; the cursor's
``sort_key`` is ``created_at`` in ISO 8601 and ``last_id`` the last claim's id, as
the port requires. Rows are rebuilt through the aggregate before the public view is
taken, so every value is validated against its ``ClaimValue`` variant; the view
itself (``ImpactClaimSummary.from_entity``) drops who recorded or retracted a claim,
its note and its reason.

Patterns: Query Service (adapter side).
"""

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.impacts.application.claims_dto import (
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_queries import ListClaims
from yakhnama.modules.impacts.domain.value_objects import ClaimStatus
from yakhnama.modules.impacts.infrastructure.claims_mappers import (
    row_to_asset,
    row_to_claim,
)
from yakhnama.modules.impacts.infrastructure.claims_orm import (
    ImpactClaimRow,
    InfrastructureAssetRow,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


def decode_created_after(sort_key: str) -> datetime:
    """Parse the ``created_at`` instant a claim-listing cursor carries.

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


class SqlAlchemyImpactQueryService:
    """PostgreSQL-backed implementation of ``ImpactQueryService``.

    Implements: Query Service (port ``ImpactQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def list_claims(self, query: ListClaims) -> Page[ImpactClaimSummary]:
        """Return one page of an event's claims in recording order.

        Args:
            query: The event, filters and page request; ``actor`` is ignored here.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = (
            select(ImpactClaimRow)
            .where(ImpactClaimRow.event_id == query.event_id)
            .order_by(ImpactClaimRow.created_at, ImpactClaimRow.id)
            # One extra row tells whether another page follows.
            .limit(query.page.limit + 1)
        )
        if query.metric_code is not None:
            statement = statement.where(ImpactClaimRow.metric_code == query.metric_code)
        if not query.include_retracted:
            statement = statement.where(
                ImpactClaimRow.status == ClaimStatus.ACTIVE.value
            )
        if cursor is not None:
            after = decode_created_after(cursor.sort_key)
            statement = statement.where(
                or_(
                    ImpactClaimRow.created_at > after,
                    and_(
                        ImpactClaimRow.created_at == after,
                        ImpactClaimRow.id > cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        summaries = [ImpactClaimSummary.from_entity(row_to_claim(row)) for row in rows]
        window = summaries[: query.page.limit]
        next_cursor = None
        if len(summaries) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[ImpactClaimSummary](items=tuple(window), next_cursor=next_cursor)

    async def get_asset(self, asset_id: EntityId) -> InfrastructureAssetDetail | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The detail view, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(InfrastructureAssetRow, asset_id)
        return (
            None
            if row is None
            else InfrastructureAssetDetail.from_entity(row_to_asset(row))
        )
