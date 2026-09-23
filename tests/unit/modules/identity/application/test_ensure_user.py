"""Unit tests for ``EnsureUserFromPrincipalHandler``, with in-memory fakes only."""

from datetime import timedelta

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.identity import InMemoryIdentityUnitOfWork, InMemoryUserRepository
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.identity.application.support import (
    EARLIER,
    NOW,
    identity,
    ids,
    make_membership,
    make_organization,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.commands import EnsureUserFromPrincipal
from yakhnama.modules.identity.application.handlers import (
    EnsureUserFromPrincipalHandler,
)
from yakhnama.modules.identity.domain.entities import User
from yakhnama.modules.identity.domain.errors import AccountSuspendedError
from yakhnama.modules.identity.domain.events import UserMirrored
from yakhnama.modules.identity.domain.value_objects import (
    ExternalIdentity,
    OrganizationRole,
    OrganizationStatus,
    Role,
)
from yakhnama.shared_kernel.errors import ConflictError


def handler(
    factory: InMemoryUnitOfWorkFactory[InMemoryIdentityUnitOfWork],
    clock: FrozenClock | None = None,
) -> EnsureUserFromPrincipalHandler:
    """Build the handler at ``NOW`` unless another clock is given."""
    return EnsureUserFromPrincipalHandler(factory, clock or FrozenClock(NOW), ids())


def ensure(
    subject: str = "subject-1", *, realm_roles: frozenset[Role] = frozenset()
) -> EnsureUserFromPrincipal:
    """Return the command for one verified token."""
    return EnsureUserFromPrincipal(identity=identity(subject), realm_roles=realm_roles)


async def test_ensure_user_first_sight_mirrors_user_and_returns_actor() -> None:
    uow, factory = wire()

    actor = await handler(factory)(ensure(realm_roles=frozenset({Role.MODERATOR})))

    (user,) = uow.users.committed.values()
    assert user.external_identity == identity()
    assert user.roles == {Role.CITIZEN, Role.MODERATOR}
    assert user.display_name is None
    assert user.created_at == NOW
    assert actor.user_id == user.id
    assert actor.roles == user.roles
    assert actor.memberships == frozenset()
    (event,) = uow.committed_events
    assert isinstance(event, UserMirrored)
    assert event.aggregate_id == user.id


async def test_ensure_user_second_call_touches_without_new_user_or_role_sync() -> None:
    uow, factory = wire()
    await handler(factory)(ensure())
    (first,) = uow.users.committed.values()
    later = FrozenClock(NOW + timedelta(minutes=5))

    actor = await handler(factory, later)(ensure(realm_roles=frozenset({Role.ADMIN})))

    (stored,) = uow.users.committed.values()
    assert stored.id == first.id
    assert stored.roles == frozenset({Role.CITIZEN})
    assert stored.display_name is None
    assert stored.version == first.version
    assert stored.updated_at == first.updated_at
    assert stored.last_seen_at == NOW + timedelta(minutes=5)
    assert uow.users.touches == 1
    assert len(uow.committed_events) == 1
    assert actor.has_role(Role.ADMIN) is False


async def test_ensure_user_same_instant_writes_no_touch() -> None:
    user = make_user(subject="subject-1")
    uow, factory = wire(users=(user,))

    await handler(factory, FrozenClock(EARLIER))(ensure())

    assert uow.users.touches == 0
    assert uow.users.committed[user.id] == user
    assert uow.committed is True


async def test_ensure_user_actor_holds_only_memberships_of_active_organisations() -> (
    None
):
    user = make_user(subject="subject-1")
    active = make_organization("active-org")
    suspended = make_organization("paused-org", status=OrganizationStatus.SUSPENDED)
    memberships = (
        make_membership(active, user, OrganizationRole.ADMIN),
        make_membership(suspended, user),
    )
    _, factory = wire(
        users=(user,), organizations=(active, suspended), memberships=memberships
    )

    actor = await handler(factory)(ensure())

    assert actor.memberships == frozenset({(active.id, OrganizationRole.ADMIN)})


async def test_ensure_user_membership_of_missing_organisation_is_ignored() -> None:
    user = make_user(subject="subject-1")
    ghost = make_organization("ghost-org")
    _, factory = wire(users=(user,), memberships=(make_membership(ghost, user),))

    actor = await handler(factory)(ensure())

    assert actor.memberships == frozenset()


async def test_ensure_user_suspended_user_raises_after_recording_last_seen() -> None:
    user = make_user(subject="subject-1", is_suspended=True)
    uow, factory = wire(users=(user,))

    with pytest.raises(AccountSuspendedError):
        await handler(factory)(ensure())

    assert uow.committed is True
    assert uow.users.committed[user.id].last_seen_at == NOW
    assert uow.users.touches == 1


def test_ensure_user_command_has_no_display_name_field() -> None:
    # The token's display name is never copied: users choose one via PATCH /me.
    assert "display_name" not in EnsureUserFromPrincipal.model_fields


async def test_ensure_user_existing_display_name_is_kept_untouched() -> None:
    user = make_user(subject="subject-1", display_name="Chosen by user")
    uow, factory = wire(users=(user,))

    await handler(factory)(ensure())

    assert uow.users.committed[user.id].display_name == "Chosen by user"


class RacingUserRepository(InMemoryUserRepository):
    """User repository that misses the identity lookup a set number of times.

    It simulates a concurrent first request that inserted the same identity between
    this request's lookup and its insert.

    Implements: Fake (of Repository).
    """

    def __init__(self, users: tuple[User, ...], misses: int) -> None:
        """Create the repository.

        Args:
            users: Users that exist before the test acts.
            misses: How many lookups by identity return ``None`` regardless.
        """
        super().__init__(users)
        self.misses = misses

    async def get_by_identity(self, identity: ExternalIdentity) -> User | None:
        """Miss while ``misses`` is positive, then look up normally.

        Args:
            identity: The ``(issuer, subject)`` pair.

        Returns:
            ``None`` while missing, else the stored user.
        """
        if self.misses > 0:
            self.misses -= 1
            return None
        return await super().get_by_identity(identity)


async def test_ensure_user_concurrent_first_insert_is_retried_as_lookup() -> None:
    winner = make_user(subject="subject-1")
    uow, factory = wire()
    uow.users = RacingUserRepository((winner,), misses=1)

    actor = await handler(factory)(ensure())

    assert actor.user_id == winner.id
    assert list(uow.users.committed) == [winner.id]
    assert uow.rollback_count == 1
    assert uow.committed_events == ()


async def test_ensure_user_conflict_twice_raises_conflict() -> None:
    winner = make_user(subject="subject-1")
    uow, factory = wire()
    uow.users = RacingUserRepository((winner,), misses=2)

    with pytest.raises(ConflictError):
        await handler(factory)(ensure())

    assert list(uow.users.committed) == [winner.id]
