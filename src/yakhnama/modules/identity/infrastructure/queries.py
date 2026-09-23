"""SQL implementation of the identity query service port.

Each query opens its own short read session. Rows are mapped to the domain aggregates
and the DTOs are built with the same ``from_entity`` / ``from_entities`` constructors
the in-memory fake uses, so both implementations share one definition of every field.

``list_members`` pages by keyset on ``(memberships.created_at, memberships.user_id)``
(``created_at > since OR (created_at = since AND user_id > last_id)``), one bounded
query per page. The cursor carries ``since`` in ISO 8601 (microsecond precision,
like ``timestamptz``) and the last user id.

Patterns: Query Service (adapter side).
"""

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.identity.application.dto import (
    MeDetail,
    MemberSummary,
    OrganizationDetail,
)
from yakhnama.modules.identity.application.queries import ListOrganizationMembers
from yakhnama.modules.identity.domain.value_objects import OrganizationStatus
from yakhnama.modules.identity.infrastructure.mappers import (
    row_to_membership,
    row_to_organization,
    row_to_user,
)
from yakhnama.modules.identity.infrastructure.orm import (
    MembershipRow,
    OrganizationRow,
    UserRow,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


def decode_since(sort_key: str) -> datetime:
    """Parse the ``since`` instant a member-listing cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        since = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # A naive instant never comes from encode_cursor here, and comparing it with
    # timestamptz would silently assume the session time zone.
    if since.utcoffset() is None:
        raise _invalid_cursor()
    return since


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class SqlAlchemyIdentityQueryService:
    """PostgreSQL-backed implementation of ``IdentityQueryService``.

    Implements: Query Service (port ``IdentityQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def get_me(self, user_id: EntityId) -> MeDetail | None:
        """Return a user's own record with their memberships of active organisations.

        Args:
            user_id: The user.

        Returns:
            The view, or ``None`` if no user has that id.
        """
        async with self._session_factory() as session:
            user_row = await session.get(UserRow, user_id)
            if user_row is None:
                return None
            # Filtered here as well as in MeDetail.from_entities: the rows of
            # suspended or retired organisations are never needed.
            pairs = (
                await session.execute(
                    select(MembershipRow, OrganizationRow)
                    .join(
                        OrganizationRow,
                        OrganizationRow.id == MembershipRow.organization_id,
                    )
                    .where(
                        MembershipRow.user_id == user_id,
                        OrganizationRow.status == OrganizationStatus.ACTIVE.value,
                    )
                    .order_by(OrganizationRow.slug)
                )
            ).tuples()
            memberships = [
                (row_to_membership(membership), row_to_organization(organization))
                for membership, organization in pairs
            ]
            return MeDetail.from_entities(row_to_user(user_row), memberships)

    async def get_organization(
        self, organization_id: EntityId
    ) -> OrganizationDetail | None:
        """Return one organisation with its member count, whatever its status.

        Args:
            organization_id: The organisation.

        Returns:
            The detail view, or ``None``.
        """
        member_count = (
            select(func.count())
            .select_from(MembershipRow)
            .where(MembershipRow.organization_id == OrganizationRow.id)
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            found = (
                (
                    await session.execute(
                        select(OrganizationRow, member_count).where(
                            OrganizationRow.id == organization_id
                        )
                    )
                )
                .tuples()
                .one_or_none()
            )
        if found is None:
            return None
        organization_row, count = found
        return OrganizationDetail.from_entity(
            row_to_organization(organization_row), count
        )

    async def list_members(self, query: ListOrganizationMembers) -> Page[MemberSummary]:
        """Return one page of an organisation's members.

        Display names are returned as stored; whether a caller may see them is the
        API's decision.

        Args:
            query: The organisation and page request.

        Returns:
            Up to ``query.page.limit`` members and the next cursor, if any; an empty
            page if the organisation has no members or does not exist.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = (
            select(MembershipRow, UserRow)
            .join(UserRow, UserRow.id == MembershipRow.user_id)
            .where(MembershipRow.organization_id == query.organization_id)
            .order_by(MembershipRow.created_at, MembershipRow.user_id)
            # One extra row tells whether another page follows.
            .limit(query.page.limit + 1)
        )
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    MembershipRow.created_at > since,
                    and_(
                        MembershipRow.created_at == since,
                        MembershipRow.user_id > cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).tuples().all()
        summaries = [
            MemberSummary.from_entities(
                row_to_membership(membership), row_to_user(user)
            )
            for membership, user in rows
        ]
        window = summaries[: query.page.limit]
        next_cursor = None
        if len(summaries) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.since.isoformat(), last_id=last.user_id)
            )
        return Page[MemberSummary](items=tuple(window), next_cursor=next_cursor)
