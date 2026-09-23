"""SQLAlchemy adapters of the identity repository ports.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them, which is what the in-memory
fakes model with their staged writes. Inserts run inside a savepoint so that a unique
violation leaves the transaction usable. Row objects are expunged from the session as
soon as they are read or written: the database is the only state, so an identity map
holding a stale row can never shadow a later write.

Optimistic concurrency: ``save`` updates a row only ``WHERE version = new version -
1``, as the ports require. If no row matches, the repository looks the id up once more
to tell a missing record (``NotFoundError``) from a concurrent change
(``ConflictError``).

``touch`` updates ``last_seen_at`` alone, with ``GREATEST`` so a request that finishes
late never moves it backwards, and without a version check because ``User.touch``
does not change the version.

``list_for_organization`` locks the organisation row and its membership rows with
``SELECT ... FOR UPDATE`` until the transaction ends. Every change to an
organisation's memberships starts from that call, so two such changes run one after
the other and cannot both pass the last-admin rule.

Patterns: Repository (adapter side).
"""

from collections.abc import Callable, Mapping
from typing import Final

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.identity.domain.entities import (
    Membership,
    Memberships,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.errors import (
    DuplicateMembershipError,
    MembershipNotFoundError,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from yakhnama.modules.identity.domain.value_objects import ExternalIdentity
from yakhnama.modules.identity.infrastructure.mappers import (
    membership_to_row,
    organization_to_row,
    roles_to_column,
    row_to_membership,
    row_to_organization,
    row_to_user,
    user_to_row,
)
from yakhnama.modules.identity.infrastructure.orm import (
    MembershipRow,
    OrganizationRow,
    UserRow,
)
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId

type IdentityRow = UserRow | OrganizationRow | MembershipRow
type IdentityRowClass = type[UserRow] | type[OrganizationRow] | type[MembershipRow]

_STALE_MESSAGE: Final = (
    "{kind} {record_id} was changed concurrently (expected version {expected})"
)
# Record kinds named in concurrency errors.
_USER: Final = "user"
_ORGANIZATION: Final = "organisation"
_MEMBERSHIP: Final = "membership"


async def _insert(
    session: AsyncSession, row: IdentityRow, conflict: Callable[[], ConflictError]
) -> None:
    """Insert ``row`` in a savepoint, turning a unique violation into ``conflict()``.

    Args:
        session: The unit of work's session.
        row: The transient row.
        conflict: Builds the error raised on a unique violation.

    Raises:
        ConflictError: If a unique constraint rejects the row.
        sqlalchemy.exc.IntegrityError: For any other integrity violation (a foreign
            key to a missing record), which is a bug rather than a conflict.
    """
    try:
        # The savepoint keeps the transaction usable after a duplicate, so the
        # caller can still roll back or carry on.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if not is_unique_violation(error):
            raise
        raise conflict() from error
    session.expunge(row)


async def _update_versioned(
    session: AsyncSession,
    row_class: IdentityRowClass,
    record_id: EntityId,
    new_version: int,
    values: Mapping[str, object],
) -> bool:
    """Update one row if its stored version is ``new_version - 1``.

    Args:
        session: The unit of work's session.
        row_class: The row model of the table.
        record_id: The row's id.
        new_version: The version the row moves to.
        values: The new column values, ``version`` included.

    Returns:
        ``True`` if a row was updated, ``False`` if none matched.
    """
    statement = (
        update(row_class)
        .where(row_class.id == record_id, row_class.version == new_version - 1)
        .values(dict(values))
        .returning(row_class.id)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _stored_version(
    session: AsyncSession, row_class: IdentityRowClass, record_id: EntityId
) -> int | None:
    """Return the stored version of a row, or ``None`` if it does not exist."""
    stored = await session.scalar(
        select(row_class.version).where(row_class.id == record_id)
    )
    # The union of row classes makes the column type Any to mypy; it is an INTEGER.
    return None if stored is None else int(stored)


def _stale(kind: str, record_id: EntityId, expected: int, stored: int) -> ConflictError:
    """Build the error of an update that lost an optimistic-concurrency race."""
    return ConflictError(
        _STALE_MESSAGE.format(kind=kind, record_id=record_id, expected=expected),
        details={
            "id": str(record_id),
            "expected_version": expected,
            "stored_version": stored,
        },
    )


class SqlAlchemyUserRepository:
    """PostgreSQL-backed implementation of ``UserRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``UserRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, user_id: EntityId) -> User | None:
        """Return the user with ``user_id``, active or suspended.

        Args:
            user_id: The user's id.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load(UserRow.id == user_id)

    async def get_by_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the user mirrored from ``identity``, compared exactly.

        Args:
            identity: The ``(issuer, subject)`` pair.

        Returns:
            The aggregate, or ``None`` if the identity was never seen.
        """
        return await self._load(
            (UserRow.issuer == identity.issuer) & (UserRow.subject == identity.subject)
        )

    async def add(self, user: User) -> None:
        """Insert a newly mirrored user.

        Args:
            user: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the ``(issuer, subject)`` pair is taken,
                including by a concurrent first request of the same user.
        """
        # Neither the subject nor the issuer goes into the error: together they
        # identify a person (identity errors carry ids only).
        await _insert(
            self._session,
            user_to_row(user),
            lambda: ConflictError(
                f"user {user.id} or its external identity already exists",
                details={"user_id": str(user.id)},
            ),
        )

    async def save(self, user: User) -> None:
        """Update a stored user, checking optimistic concurrency.

        ``last_seen_at`` only moves forwards, as in ``touch``, because a concurrent
        request may have touched the user after this one loaded it.

        Args:
            user: The new state; its ``version`` is one more than the stored one.

        Raises:
            UserNotFoundError: If no user with that id exists.
            ConflictError: If the stored version is not ``user.version - 1``.
        """
        is_updated = await _update_versioned(
            self._session,
            UserRow,
            user.id,
            user.version,
            {
                "display_name": user.display_name,
                "roles": roles_to_column(user),
                "status": user.status.value,
                "status_reason": user.status_reason,
                "version": user.version,
                "updated_at": user.updated_at,
                "last_seen_at": func.greatest(UserRow.last_seen_at, user.last_seen_at),
            },
        )
        if is_updated:
            return
        stored = await _stored_version(self._session, UserRow, user.id)
        if stored is None:
            raise UserNotFoundError.for_id(user.id)
        raise _stale(_USER, user.id, user.version - 1, stored)

    async def touch(self, user: User) -> None:
        """Update ``last_seen_at`` only, never moving it backwards.

        Args:
            user: The touched user.

        Raises:
            UserNotFoundError: If no user with that id exists.
        """
        statement = (
            update(UserRow)
            .where(UserRow.id == user.id)
            .values(last_seen_at=func.greatest(UserRow.last_seen_at, user.last_seen_at))
            .returning(UserRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is None:
            raise UserNotFoundError.for_id(user.id)

    async def _load(self, condition: ColumnElement[bool]) -> User | None:
        row = (
            await self._session.execute(select(UserRow).where(condition))
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_user(row)


class SqlAlchemyOrganizationRepository:
    """PostgreSQL-backed implementation of ``OrganizationRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``OrganizationRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, organization_id: EntityId) -> Organization | None:
        """Return the organisation with ``organization_id``, whatever its status.

        Args:
            organization_id: The organisation's id.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load(OrganizationRow.id == organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Return the organisation with ``slug``, whatever its status.

        Args:
            slug: The slug, compared exactly.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load(OrganizationRow.slug == slug)

    async def add(self, organization: Organization) -> None:
        """Insert a new organisation.

        Args:
            organization: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the slug is taken, including by a
                concurrent creation.
        """
        await _insert(
            self._session,
            organization_to_row(organization),
            lambda: ConflictError(
                f"organisation {organization.slug!r} already exists",
                details={"slug": organization.slug},
            ),
        )

    async def save(self, organization: Organization) -> None:
        """Update a stored organisation, checking optimistic concurrency.

        Args:
            organization: The new state; its ``version`` is one more than the
                stored one.

        Raises:
            OrganizationNotFoundError: If no organisation with that id exists.
            ConflictError: If the stored version is not ``version - 1``, or the new
                slug is taken by another organisation.
        """
        values = {
            "slug": organization.slug,
            "name": organization.name,
            "organization_type": organization.organization_type.value,
            "status": organization.status.value,
            "status_reason": organization.status_reason,
            "version": organization.version,
            "updated_at": organization.updated_at,
        }
        try:
            async with self._session.begin_nested():
                is_updated = await _update_versioned(
                    self._session,
                    OrganizationRow,
                    organization.id,
                    organization.version,
                    values,
                )
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"organisation slug {organization.slug!r} is taken"
            raise ConflictError(message, details={"slug": organization.slug}) from error
        if is_updated:
            return
        stored = await _stored_version(self._session, OrganizationRow, organization.id)
        if stored is None:
            raise OrganizationNotFoundError.for_id(organization.id)
        raise _stale(_ORGANIZATION, organization.id, organization.version - 1, stored)

    async def _load(self, condition: ColumnElement[bool]) -> Organization | None:
        row = (
            await self._session.execute(select(OrganizationRow).where(condition))
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_organization(row)


class SqlAlchemyMembershipRepository:
    """PostgreSQL-backed implementation of ``MembershipRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``MembershipRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def list_for_organization(self, organization_id: EntityId) -> Memberships:
        """Return and lock every membership of an organisation, in joining order.

        The organisation row is locked ``FOR UPDATE`` first, so a concurrent change
        to the same organisation's memberships (including an addition, which has no
        row to lock yet) waits until this transaction ends.

        Args:
            organization_id: The organisation.

        Returns:
            The collection, empty if it has no members or does not exist.
        """
        await self._session.execute(
            select(OrganizationRow.id)
            .where(OrganizationRow.id == organization_id)
            .with_for_update()
        )
        members = await self._load(
            MembershipRow.organization_id == organization_id, is_locked=True
        )
        return Memberships(organization_id=organization_id, members=members)

    async def list_for_user(self, user_id: EntityId) -> tuple[Membership, ...]:
        """Return every membership of a user, in any organisation.

        Args:
            user_id: The user.

        Returns:
            The memberships, oldest first.
        """
        return await self._load(MembershipRow.user_id == user_id, is_locked=False)

    async def add(self, membership: Membership) -> None:
        """Insert a new membership.

        Args:
            membership: The new membership at version 1.

        Raises:
            DuplicateMembershipError: If the id or the ``(organization_id,
                user_id)`` pair is taken, including by a concurrent addition.
            sqlalchemy.exc.IntegrityError: If the organisation or the user is not
                stored, which the application layer rules out.
        """
        await _insert(
            self._session,
            membership_to_row(membership),
            lambda: DuplicateMembershipError.for_member(
                membership.organization_id, membership.user_id
            ),
        )

    async def save(self, membership: Membership) -> None:
        """Update a stored membership, checking optimistic concurrency.

        Only the role and the bookkeeping columns change: a membership never moves
        to another organisation or user.

        Args:
            membership: The new state; its ``version`` is one more than the
                stored one.

        Raises:
            MembershipNotFoundError: If no membership with that id exists.
            ConflictError: If the stored version is not ``version - 1``.
        """
        is_updated = await _update_versioned(
            self._session,
            MembershipRow,
            membership.id,
            membership.version,
            {
                "role": membership.role.value,
                "version": membership.version,
                "updated_at": membership.updated_at,
            },
        )
        if is_updated:
            return
        stored = await _stored_version(self._session, MembershipRow, membership.id)
        if stored is None:
            raise MembershipNotFoundError.for_member(
                membership.organization_id, membership.user_id
            )
        raise _stale(_MEMBERSHIP, membership.id, membership.version - 1, stored)

    async def remove(self, membership_id: EntityId) -> None:
        """Delete a membership.

        Args:
            membership_id: The membership.

        Raises:
            NotFoundError: If no membership with that id exists.
        """
        statement = (
            delete(MembershipRow)
            .where(MembershipRow.id == membership_id)
            .returning(MembershipRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is None:
            message = f"no membership with id {membership_id}"
            raise NotFoundError(message, details={"membership_id": str(membership_id)})

    async def _load(
        self, condition: ColumnElement[bool], *, is_locked: bool
    ) -> tuple[Membership, ...]:
        statement = (
            select(MembershipRow)
            .where(condition)
            .order_by(MembershipRow.created_at, MembershipRow.id)
        )
        if is_locked:
            statement = statement.with_for_update()
        rows = list((await self._session.execute(statement)).scalars())
        for row in rows:
            self._session.expunge(row)
        return tuple(row_to_membership(row) for row in rows)
