"""Unit tests for the moderator directory (``ModeratorDirectoryQueryService``)."""

import pytest

from tests.fakes.identity import InMemoryIdentityQueryService, actor_with
from tests.unit.modules.identity.application.support import make_user, wire
from yakhnama.modules.identity.application.authorisation import (
    moderator_directory_policy,
)
from yakhnama.modules.identity.application.queries import ListModerators
from yakhnama.modules.identity.application.query_services import (
    MODERATOR_DIRECTORY_MAX,
    ModeratorDirectoryQueryService,
)
from yakhnama.modules.identity.domain.policies import CanModerate
from yakhnama.modules.identity.domain.value_objects import Actor, Role
from yakhnama.shared_kernel.errors import PermissionDeniedError

MODERATOR = actor_with({Role.MODERATOR})


def test_moderator_directory_policy_is_can_moderate() -> None:
    assert isinstance(moderator_directory_policy(), CanModerate)


async def test_list_moderators_returns_active_moderators_and_admins_by_name() -> None:
    zara = make_user(Role.MODERATOR, display_name="Zara")
    amin = make_user(Role.ADMIN, display_name="Amin")
    unnamed = make_user(Role.MODERATOR, display_name=None)
    citizen = make_user(display_name="Citizen")
    trusted = make_user(Role.TRUSTED_REPORTER, display_name="Trusted")
    suspended = make_user(Role.MODERATOR, display_name="Gone", is_suspended=True)
    uow, _ = wire(users=(zara, amin, unnamed, citizen, trusted, suspended))
    service = ModeratorDirectoryQueryService(InMemoryIdentityQueryService(uow))

    entries = await service.list_moderators(ListModerators(actor=MODERATOR))

    assert [entry.id for entry in entries] == [amin.id, zara.id, unnamed.id]
    assert entries[0].display_name == "Amin"
    assert entries[2].display_name is None


async def test_list_moderators_caps_the_directory() -> None:
    users = tuple(
        make_user(Role.MODERATOR, display_name=f"M{index:04d}")
        for index in range(MODERATOR_DIRECTORY_MAX + 1)
    )
    uow, _ = wire(users=users)
    service = ModeratorDirectoryQueryService(InMemoryIdentityQueryService(uow))

    entries = await service.list_moderators(ListModerators(actor=MODERATOR))

    assert len(entries) == MODERATOR_DIRECTORY_MAX


@pytest.mark.parametrize(
    "actor",
    [Actor.anonymous(), actor_with(), actor_with({Role.TRUSTED_REPORTER})],
)
async def test_list_moderators_non_moderator_is_refused(actor: Actor) -> None:
    uow, _ = wire(users=(make_user(Role.MODERATOR),))
    service = ModeratorDirectoryQueryService(InMemoryIdentityQueryService(uow))

    with pytest.raises(PermissionDeniedError):
        await service.list_moderators(ListModerators(actor=actor))
