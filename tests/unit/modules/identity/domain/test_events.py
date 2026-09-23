"""Unit tests for ``yakhnama.modules.identity.domain.events``."""

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.identity import UserTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain import events as events_module
from yakhnama.modules.identity.domain.entities import Memberships
from yakhnama.modules.identity.domain.events import (
    MembershipEvent,
    OrganizationEvent,
    UserEvent,
    UserMirrored,
)
from yakhnama.modules.identity.domain.factories import (
    MembershipFactory,
    OrganizationFactory,
    UserFactory,
)
from yakhnama.modules.identity.domain.value_objects import (
    ExternalIdentity,
    OrganizationRole,
    OrganizationType,
    Role,
)
from yakhnama.shared_kernel.events import DomainEvent, is_event_type

DISPLAY_NAME = "Distinctive Display Name"
ORGANIZATION_NAME = "Distinctive Organisation Name"
REASON = "Distinctive suspension reason"
SUBJECT = "distinctive-subject-0001"
ISSUER = "https://distinctive-issuer.example.test/realms/yakhnama"

_BASES = (UserEvent, OrganizationEvent, MembershipEvent)
CONCRETE_EVENTS = [
    member
    for _, member in inspect.getmembers(events_module, inspect.isclass)
    if issubclass(member, DomainEvent)
    and member.__module__ == events_module.__name__
    and member not in _BASES
]


def test_events_module_defines_briefed_events_and_organisation_reinstatement() -> None:
    names = {event.__name__ for event in CONCRETE_EVENTS}

    assert names == {
        "UserMirrored",
        "UserRenamed",
        "UserRoleGranted",
        "UserRoleRevoked",
        "UserSuspended",
        "UserReinstated",
        "OrganizationCreated",
        "OrganizationRenamed",
        "OrganizationSuspended",
        "OrganizationReinstated",
        "OrganizationRetired",
        "MembershipAdded",
        "MembershipRoleChanged",
        "MembershipRemoved",
    }


@pytest.mark.parametrize("event_class", CONCRETE_EVENTS, ids=lambda cls: cls.__name__)
def test_event_type_is_identity_prefixed_snake_case_of_class_name(
    event_class: type[DomainEvent],
) -> None:
    snake = "".join(
        f"_{character.lower()}" if character.isupper() else character
        for character in event_class.__name__
    ).lstrip("_")

    assert event_class.event_type == f"identity.{snake}"
    assert is_event_type(event_class.event_type)


@pytest.mark.parametrize("base", _BASES, ids=lambda cls: cls.__name__)
def test_event_base_without_event_type_cannot_be_instantiated(
    base: type[DomainEvent],
) -> None:
    ids = SequentialIdGenerator()
    fields: dict[str, object] = {
        "event_id": ids.new_id(),
        "occurred_at": datetime(2026, 9, 1, tzinfo=UTC),
        "aggregate_id": ids.new_id(),
        "version": 1,
        "slug": "test-org",
        "organization_id": ids.new_id(),
        "user_id": ids.new_id(),
        "role": "member",
    }
    allowed = {name: fields[name] for name in base.model_fields if name in fields}

    with pytest.raises(TypeError, match="does not declare an event_type"):
        base.model_validate(allowed)


def test_user_mirrored_without_roles_is_rejected() -> None:
    ids = SequentialIdGenerator()

    with pytest.raises(PydanticValidationError):
        UserMirrored(
            event_id=ids.new_id(),
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            aggregate_id=ids.new_id(),
            version=1,
            roles=frozenset(),
        )


def _every_event_of_a_full_lifecycle() -> list[DomainEvent]:
    clock = SteppingClock(datetime(2026, 9, 2, tzinfo=UTC), timedelta(seconds=1))
    ids = SequentialIdGenerator()
    recorded: list[DomainEvent] = []
    user_change = UserFactory().mirror(
        ExternalIdentity(issuer=ISSUER, subject=SUBJECT),
        DISPLAY_NAME,
        {Role.MODERATOR},
        ids=ids,
        clock=clock,
    )
    user = user_change.state
    recorded.extend(user_change.events)
    user_changes = [user.rename(DISPLAY_NAME + " 2", clock=clock, ids=ids)]
    user_changes.append(
        user_changes[-1].state.grant_role(Role.ADMIN, clock=clock, ids=ids)
    )
    user_changes.append(
        user_changes[-1].state.revoke_role(Role.ADMIN, clock=clock, ids=ids)
    )
    user_changes.append(user_changes[-1].state.suspend(REASON, clock=clock, ids=ids))
    user_changes.append(user_changes[-1].state.reinstate(clock=clock, ids=ids))
    user = user_changes[-1].state
    for user_step in user_changes:
        recorded.extend(user_step.events)
    organization_change = OrganizationFactory().create(
        "distinctive-org", ORGANIZATION_NAME, OrganizationType.NGO, ids=ids, clock=clock
    )
    organization = organization_change.state
    recorded.extend(organization_change.events)
    admin_change = Memberships(organization_id=organization.id).add(
        MembershipFactory().create(
            organization, user, OrganizationRole.ADMIN, ids=ids, clock=clock
        )
    )
    other = UserTestFactory.build()
    memberships = admin_change.state.add(
        MembershipFactory().create(
            organization, other, OrganizationRole.MEMBER, ids=ids, clock=clock
        )
    )
    recorded.extend((*admin_change.events, *memberships.events))
    promoted = memberships.state.change_role(
        other.id, OrganizationRole.ADMIN, clock=clock, ids=ids
    )
    removed = promoted.state.remove(user.id, clock=clock, ids=ids)
    recorded.extend((*promoted.events, *removed.events))
    organization_changes = [
        organization.rename(ORGANIZATION_NAME + " 2", clock=clock, ids=ids)
    ]
    organization_changes.append(
        organization_changes[-1].state.suspend(REASON, clock=clock, ids=ids)
    )
    organization_changes.append(
        organization_changes[-1].state.reinstate(clock=clock, ids=ids)
    )
    organization_changes.append(
        organization_changes[-1].state.retire(REASON, clock=clock, ids=ids)
    )
    for organization_step in organization_changes:
        recorded.extend(organization_step.events)
    return recorded


def test_events_of_a_full_lifecycle_cover_every_event_type() -> None:
    recorded = _every_event_of_a_full_lifecycle()

    assert {type(event) for event in recorded} == set(CONCRETE_EVENTS)


def test_events_never_carry_names_subjects_issuers_or_reasons() -> None:
    recorded = _every_event_of_a_full_lifecycle()

    for event in recorded:
        payload = event.model_dump_json()
        for secret in (DISPLAY_NAME, ORGANIZATION_NAME, REASON, SUBJECT, ISSUER):
            assert secret not in payload, (type(event).__name__, secret)


def test_events_payload_fields_are_ids_roles_slugs_and_flags_only() -> None:
    allowed = {
        "event_id",
        "occurred_at",
        "aggregate_id",
        "aggregate_type",
        "version",
        "roles",
        "role",
        "previous_role",
        "has_display_name",
        "slug",
        "organization_type",
        "organization_id",
        "user_id",
    }

    for event_class in CONCRETE_EVENTS:
        assert set(event_class.model_fields) <= allowed, event_class.__name__
