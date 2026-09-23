"""Unit tests for the user command handlers, with in-memory fakes only.

The scenarios every admin handler shares (denied, anonymous, unknown actor,
suspended actor, missing target, stale version) are parametrised over one table;
each handler's own rules follow.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest

from tests.fakes.identity import InMemoryIdentityUnitOfWork, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.identity.application.support import (
    NOW,
    actor_of,
    clock,
    ids,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.commands import (
    GrantRole,
    ReinstateUser,
    RenameSelf,
    RevokeRole,
    SuspendUser,
)
from yakhnama.modules.identity.application.dto import UserDetail
from yakhnama.modules.identity.application.handlers import (
    GrantRoleHandler,
    ReinstateUserHandler,
    RenameSelfHandler,
    RevokeRoleHandler,
    SuspendUserHandler,
)
from yakhnama.modules.identity.domain.entities import User
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    CitizenRoleRequiredError,
    RoleNotHeldError,
    UserNotFoundError,
    UserNotSuspendedError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    UserReinstated,
    UserRenamed,
    UserRoleGranted,
    UserRoleRevoked,
    UserSuspended,
)
from yakhnama.modules.identity.domain.value_objects import Actor, Role, UserStatus
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.ids import EntityId

type Factory = InMemoryUnitOfWorkFactory[InMemoryIdentityUnitOfWork]
type Run = Callable[[Factory, Actor, EntityId, int | None], Awaitable[UserDetail]]


async def grant(
    factory: Factory, actor: Actor, user_id: EntityId, version: int | None
) -> UserDetail:
    """Grant ``moderator`` through the handler."""
    command = GrantRole(
        actor=actor, user_id=user_id, role=Role.MODERATOR, expected_version=version
    )
    return await GrantRoleHandler(factory, clock(), ids())(command)


async def revoke(
    factory: Factory, actor: Actor, user_id: EntityId, version: int | None
) -> UserDetail:
    """Revoke ``trusted_reporter`` through the handler."""
    command = RevokeRole(
        actor=actor,
        user_id=user_id,
        role=Role.TRUSTED_REPORTER,
        expected_version=version,
    )
    return await RevokeRoleHandler(factory, clock(), ids())(command)


async def suspend(
    factory: Factory, actor: Actor, user_id: EntityId, version: int | None
) -> UserDetail:
    """Suspend through the handler."""
    command = SuspendUser(
        actor=actor, user_id=user_id, reason="spam reports", expected_version=version
    )
    return await SuspendUserHandler(factory, clock(), ids())(command)


async def reinstate(
    factory: Factory, actor: Actor, user_id: EntityId, version: int | None
) -> UserDetail:
    """Reinstate through the handler."""
    command = ReinstateUser(actor=actor, user_id=user_id, expected_version=version)
    return await ReinstateUserHandler(factory, clock(), ids())(command)


@dataclass(frozen=True)
class Case:
    """One admin handler and a target it can change.

    Implements: Fake (test table entry).
    """

    run: Run
    target: Callable[[], User]


CASES = {
    "grant": Case(grant, make_user),
    "revoke": Case(revoke, lambda: make_user(Role.TRUSTED_REPORTER)),
    "suspend": Case(suspend, make_user),
    "reinstate": Case(reinstate, lambda: make_user(is_suspended=True)),
}
CASE_IDS = list(CASES)


@pytest.fixture(params=CASE_IDS)
def case(request: pytest.FixtureRequest) -> Case:
    """Yield every admin handler case in turn."""
    return CASES[request.param]


async def test_admin_user_handler_citizen_actor_is_denied_before_reading(
    case: Case,
) -> None:
    target = case.target()
    uow, factory = wire(users=(target,))

    with pytest.raises(PermissionDeniedError) as raised:
        await case.run(factory, actor_of(target), target.id, None)

    assert raised.value.details["policy"] == "IsAdmin"
    assert factory.calls == 0
    assert uow.users.committed[target.id] == target


async def test_admin_user_handler_anonymous_actor_is_denied(case: Case) -> None:
    target = case.target()
    _, factory = wire(users=(target,))

    with pytest.raises(PermissionDeniedError):
        await case.run(factory, Actor.anonymous(), target.id, None)

    assert factory.calls == 0


async def test_admin_user_handler_actor_without_record_is_denied(case: Case) -> None:
    target = case.target()
    uow, factory = wire(users=(target,))
    ghost = actor_with({Role.ADMIN}, user_id=SequentialIdGenerator(seed=9).new_id())

    with pytest.raises(PermissionDeniedError) as raised:
        await case.run(factory, ghost, target.id, None)

    assert raised.value.details == {"reason": "unknown_actor"}
    assert uow.committed is False
    assert uow.committed_events == ()


async def test_admin_user_handler_suspended_admin_actor_is_refused(case: Case) -> None:
    admin = make_user(Role.ADMIN, is_suspended=True)
    target = case.target()
    uow, factory = wire(users=(admin, target))
    stale_actor = Actor(user_id=admin.id, roles=admin.roles)

    with pytest.raises(AccountSuspendedError):
        await case.run(factory, stale_actor, target.id, None)

    assert uow.committed is False
    assert uow.users.committed[target.id] == target


async def test_admin_user_handler_missing_target_raises_not_found(case: Case) -> None:
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,))

    with pytest.raises(UserNotFoundError):
        await case.run(factory, actor_of(admin), case.target().id, None)


async def test_admin_user_handler_stale_version_raises_precondition_failed(
    case: Case,
) -> None:
    admin = make_user(Role.ADMIN)
    target = case.target()
    uow, factory = wire(users=(admin, target))

    with pytest.raises(PreconditionFailedError) as raised:
        await case.run(factory, actor_of(admin), target.id, target.version + 1)

    assert raised.value.details == {"expected_version": 2, "current_version": 1}
    assert uow.users.committed[target.id] == target


async def test_admin_user_handler_matching_version_commits_one_event(
    case: Case,
) -> None:
    admin = make_user(Role.ADMIN)
    target = case.target()
    uow, factory = wire(users=(admin, target))

    detail = await case.run(factory, actor_of(admin), target.id, target.version)

    assert detail.version == target.version + 1
    assert uow.users.committed[target.id].version == target.version + 1
    assert len(uow.committed_events) == 1


# --------------------------------------------------------------------------- #
# Per-handler rules                                                           #
# --------------------------------------------------------------------------- #


async def test_grant_role_commits_role_and_event() -> None:
    admin, target = make_user(Role.ADMIN), make_user()
    uow, factory = wire(users=(admin, target))

    detail = await grant(factory, actor_of(admin), target.id, None)

    assert detail.roles == {Role.CITIZEN, Role.MODERATOR}
    assert uow.users.committed[target.id].roles == detail.roles
    (event,) = uow.committed_events
    assert isinstance(event, UserRoleGranted)
    assert event.role is Role.MODERATOR
    assert event.occurred_at == NOW


async def test_grant_role_already_held_changes_nothing() -> None:
    admin, target = make_user(Role.ADMIN), make_user(Role.MODERATOR)
    uow, factory = wire(users=(admin, target))

    detail = await grant(factory, actor_of(admin), target.id, None)

    assert detail.version == 1
    assert uow.users.committed[target.id] == target
    assert uow.committed_events == ()


async def test_grant_role_to_suspended_user_raises_user_suspended() -> None:
    admin, target = make_user(Role.ADMIN), make_user(is_suspended=True)
    _, factory = wire(users=(admin, target))

    with pytest.raises(UserSuspendedError):
        await grant(factory, actor_of(admin), target.id, None)


async def test_revoke_role_commits_removal_and_event() -> None:
    admin, target = make_user(Role.ADMIN), make_user(Role.TRUSTED_REPORTER)
    uow, factory = wire(users=(admin, target))

    detail = await revoke(factory, actor_of(admin), target.id, None)

    assert detail.roles == {Role.CITIZEN}
    (event,) = uow.committed_events
    assert isinstance(event, UserRoleRevoked)


async def test_revoke_role_not_held_raises_role_not_held() -> None:
    admin, target = make_user(Role.ADMIN), make_user()
    _, factory = wire(users=(admin, target))

    with pytest.raises(RoleNotHeldError):
        await revoke(factory, actor_of(admin), target.id, None)


async def test_revoke_role_citizen_raises_citizen_role_required() -> None:
    admin, target = make_user(Role.ADMIN), make_user()
    _, factory = wire(users=(admin, target))
    command = RevokeRole(actor=actor_of(admin), user_id=target.id, role=Role.CITIZEN)

    with pytest.raises(CitizenRoleRequiredError):
        await RevokeRoleHandler(factory, clock(), ids())(command)


async def test_suspend_user_commits_status_and_event_without_reason() -> None:
    admin, target = make_user(Role.ADMIN), make_user()
    uow, factory = wire(users=(admin, target))

    detail = await suspend(factory, actor_of(admin), target.id, None)

    assert detail.status is UserStatus.SUSPENDED
    assert uow.users.committed[target.id].status_reason == "spam reports"
    (event,) = uow.committed_events
    assert isinstance(event, UserSuspended)
    assert "spam" not in event.model_dump_json()


async def test_suspend_user_already_suspended_raises_user_suspended() -> None:
    admin, target = make_user(Role.ADMIN), make_user(is_suspended=True)
    _, factory = wire(users=(admin, target))

    with pytest.raises(UserSuspendedError):
        await suspend(factory, actor_of(admin), target.id, None)


async def test_reinstate_user_commits_active_status_and_event() -> None:
    admin, target = make_user(Role.ADMIN), make_user(is_suspended=True)
    uow, factory = wire(users=(admin, target))

    detail = await reinstate(factory, actor_of(admin), target.id, None)

    assert detail.status is UserStatus.ACTIVE
    (event,) = uow.committed_events
    assert isinstance(event, UserReinstated)


async def test_reinstate_user_active_user_raises_not_suspended() -> None:
    admin, target = make_user(Role.ADMIN), make_user()
    _, factory = wire(users=(admin, target))

    with pytest.raises(UserNotSuspendedError):
        await reinstate(factory, actor_of(admin), target.id, None)


# --------------------------------------------------------------------------- #
# RenameSelf                                                                  #
# --------------------------------------------------------------------------- #


def rename_self(
    actor: Actor, display_name: str | None, version: int | None = None
) -> RenameSelf:
    """Return a rename command."""
    return RenameSelf(actor=actor, display_name=display_name, expected_version=version)


async def test_rename_self_commits_new_name_and_event_without_name() -> None:
    user = make_user()
    uow, factory = wire(users=(user,))

    detail = await RenameSelfHandler(factory, clock(), ids())(
        rename_self(actor_of(user), "New name", version=1)
    )

    assert detail.display_name == "New name"
    assert uow.users.committed[user.id].display_name == "New name"
    (event,) = uow.committed_events
    assert isinstance(event, UserRenamed)
    assert event.has_display_name is True
    assert "New name" not in event.model_dump_json()


async def test_rename_self_to_none_clears_name() -> None:
    user = make_user()
    uow, factory = wire(users=(user,))

    await RenameSelfHandler(factory, clock(), ids())(rename_self(actor_of(user), None))

    assert uow.users.committed[user.id].display_name is None


async def test_rename_self_same_name_changes_nothing() -> None:
    user = make_user(display_name="Same")
    uow, factory = wire(users=(user,))

    detail = await RenameSelfHandler(factory, clock(), ids())(
        rename_self(actor_of(user), "Same")
    )

    assert detail.version == 1
    assert uow.committed_events == ()


async def test_rename_self_anonymous_actor_is_denied() -> None:
    _, factory = wire()

    with pytest.raises(PermissionDeniedError) as raised:
        await RenameSelfHandler(factory, clock(), ids())(
            rename_self(Actor.anonymous(), "Name")
        )

    assert raised.value.details["policy"] == "IsAuthenticated"
    assert factory.calls == 0


async def test_rename_self_suspended_user_is_refused() -> None:
    user = make_user(is_suspended=True)
    uow, factory = wire(users=(user,))

    with pytest.raises(AccountSuspendedError):
        await RenameSelfHandler(factory, clock(), ids())(
            rename_self(Actor(user_id=user.id, roles=user.roles), "Name")
        )

    assert uow.users.committed[user.id] == user


async def test_rename_self_stale_version_raises_precondition_failed() -> None:
    user = make_user()
    _, factory = wire(users=(user,))

    with pytest.raises(PreconditionFailedError):
        await RenameSelfHandler(factory, clock(), ids())(
            rename_self(actor_of(user), "Name", version=7)
        )
