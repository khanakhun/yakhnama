"""Unit tests for the provenance command handlers, with in-memory fakes only."""

from datetime import UTC, datetime

import pytest

from tests.factories.provenance import SourceDetailsTestFactory, SourceTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import InMemoryProvenanceUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.public import Actor, OrganizationRole, Role
from yakhnama.modules.provenance.application.commands import (
    MarkSourceReferenced,
    RegisterSource,
    UpdateSourceDetails,
)
from yakhnama.modules.provenance.application.handlers import (
    MarkSourceReferencedHandler,
    RegisterSourceHandler,
    UpdateSourceDetailsHandler,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import (
    SourceImmutableError,
    SourceNotFoundError,
)
from yakhnama.modules.provenance.domain.events import (
    SourceDetailsUpdated,
    SourceReferenced,
    SourceRegistered,
)
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    PreconditionFailedError,
)

NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
CREATED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=301)
OWNER_ID = IDS.new_id()
OTHER_ID = IDS.new_id()
ORGANIZATION_ID = IDS.new_id()
MISSING_ID = IDS.new_id()
CITIZEN = actor_with(user_id=OWNER_ID)
OTHER_CITIZEN = actor_with(user_id=OTHER_ID)
MODERATOR = actor_with({Role.MODERATOR}, user_id=OTHER_ID)
MEMBER = actor_with(
    user_id=OWNER_ID, memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}
)


def unit_of_work(
    *sources: Source,
) -> tuple[
    InMemoryProvenanceUnitOfWork,
    InMemoryUnitOfWorkFactory[InMemoryProvenanceUnitOfWork],
]:
    """Return a fake unit of work holding ``sources`` and its factory."""
    uow = InMemoryProvenanceUnitOfWork(sources=sources)
    return uow, InMemoryUnitOfWorkFactory(uow)


def owned_source(*, is_referenced: bool = False) -> Source:
    """Return a stored citizen source owned by ``OWNER_ID``."""
    return SourceTestFactory.build(
        source_type=SourceType.CITIZEN,
        owner_actor_id=OWNER_ID,
        is_referenced=is_referenced,
        created_at=CREATED,
        updated_at=CREATED,
    )


def register(
    factory: InMemoryUnitOfWorkFactory[InMemoryProvenanceUnitOfWork],
) -> RegisterSourceHandler:
    """Build the register handler with deterministic time and ids."""
    return RegisterSourceHandler(factory, FrozenClock(NOW), SequentialIdGenerator())


def update(
    factory: InMemoryUnitOfWorkFactory[InMemoryProvenanceUnitOfWork],
) -> UpdateSourceDetailsHandler:
    """Build the update handler with deterministic time and ids."""
    return UpdateSourceDetailsHandler(
        factory, FrozenClock(NOW), SequentialIdGenerator()
    )


def mark(
    factory: InMemoryUnitOfWorkFactory[InMemoryProvenanceUnitOfWork],
) -> MarkSourceReferencedHandler:
    """Build the mark-referenced handler with deterministic time and ids."""
    return MarkSourceReferencedHandler(
        factory, FrozenClock(NOW), SequentialIdGenerator()
    )


# --------------------------------------------------------------------------- #
# RegisterSource                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source_type", [SourceType.CITIZEN, SourceType.ORGANISATION])
async def test_register_source_citizen_self_type_commits_owned_source(
    source_type: SourceType,
) -> None:
    uow, factory = unit_of_work()
    details = SourceDetailsTestFactory.build()

    result = await register(factory)(
        RegisterSource(actor=CITIZEN, source_type=source_type, details=details)
    )

    stored = uow.sources.committed[result.id]
    assert stored.owner_actor_id == OWNER_ID
    assert stored.source_type is source_type
    assert stored.details == details
    assert result.version == 1
    assert [type(event) for event in uow.committed_events] == [SourceRegistered]


@pytest.mark.parametrize(
    "source_type",
    [
        SourceType.GOVERNMENT,
        SourceType.NEWS,
        SourceType.RESEARCH,
        SourceType.DATASET,
        SourceType.SATELLITE,
    ],
)
async def test_register_source_ranked_type_by_citizen_raises_permission_denied(
    source_type: SourceType,
) -> None:
    uow, factory = unit_of_work()

    with pytest.raises(PermissionDeniedError):
        await register(factory)(
            RegisterSource(
                actor=CITIZEN,
                source_type=source_type,
                details=SourceDetailsTestFactory.build(),
            )
        )

    assert uow.sources.committed == {}
    assert factory.calls == 0


async def test_register_source_ranked_type_by_moderator_commits_source() -> None:
    uow, factory = unit_of_work()

    result = await register(factory)(
        RegisterSource(
            actor=MODERATOR,
            source_type=SourceType.GOVERNMENT,
            details=SourceDetailsTestFactory.build(),
        )
    )

    assert uow.sources.committed[result.id].source_type is SourceType.GOVERNMENT


async def test_register_source_anonymous_raises_permission_denied() -> None:
    _, factory = unit_of_work()

    with pytest.raises(PermissionDeniedError):
        await register(factory)(
            RegisterSource(
                actor=Actor.anonymous(),
                source_type=SourceType.CITIZEN,
                details=SourceDetailsTestFactory.build(),
            )
        )


async def test_register_source_for_organisation_by_member_records_organisation() -> (
    None
):
    uow, factory = unit_of_work()

    result = await register(factory)(
        RegisterSource(
            actor=MEMBER,
            source_type=SourceType.ORGANISATION,
            details=SourceDetailsTestFactory.build(),
            organization_id=ORGANIZATION_ID,
        )
    )

    assert uow.sources.committed[result.id].organization_id == ORGANIZATION_ID
    assert result.organization_id == ORGANIZATION_ID


async def test_register_source_for_organisation_by_non_member_raises_denied() -> None:
    uow, factory = unit_of_work()

    with pytest.raises(PermissionDeniedError):
        await register(factory)(
            RegisterSource(
                actor=CITIZEN,
                source_type=SourceType.ORGANISATION,
                details=SourceDetailsTestFactory.build(),
                organization_id=ORGANIZATION_ID,
            )
        )

    assert uow.sources.committed == {}


# --------------------------------------------------------------------------- #
# UpdateSourceDetails                                                         #
# --------------------------------------------------------------------------- #


async def test_update_source_details_by_owner_commits_new_version() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)
    details = SourceDetailsTestFactory.build()

    result = await update(factory)(
        UpdateSourceDetails(
            actor=CITIZEN, source_id=source.id, details=details, expected_version=1
        )
    )

    assert uow.sources.committed[source.id].details == details
    assert result.version == source.version + 1
    assert [type(event) for event in uow.committed_events] == [SourceDetailsUpdated]


async def test_update_source_details_by_moderator_commits_change() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)
    details = SourceDetailsTestFactory.build()

    await update(factory)(
        UpdateSourceDetails(actor=MODERATOR, source_id=source.id, details=details)
    )

    assert uow.sources.committed[source.id].details == details


async def test_update_source_details_same_details_commits_no_event() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)

    result = await update(factory)(
        UpdateSourceDetails(actor=CITIZEN, source_id=source.id, details=source.details)
    )

    assert result.version == 1
    assert uow.committed_events == ()
    assert uow.committed


async def test_update_source_details_by_other_citizen_raises_permission_denied() -> (
    None
):
    source = owned_source()
    uow, factory = unit_of_work(source)

    with pytest.raises(PermissionDeniedError):
        await update(factory)(
            UpdateSourceDetails(
                actor=OTHER_CITIZEN,
                source_id=source.id,
                details=SourceDetailsTestFactory.build(),
            )
        )

    assert uow.sources.committed[source.id] == source


async def test_update_source_details_system_source_by_citizen_raises_denied() -> None:
    source = owned_source().model_copy(update={"owner_actor_id": None})
    _, factory = unit_of_work(source)

    with pytest.raises(PermissionDeniedError):
        await update(factory)(
            UpdateSourceDetails(
                actor=CITIZEN,
                source_id=source.id,
                details=SourceDetailsTestFactory.build(),
            )
        )


async def test_update_source_details_missing_source_raises_not_found() -> None:
    _, factory = unit_of_work()

    with pytest.raises(SourceNotFoundError):
        await update(factory)(
            UpdateSourceDetails(
                actor=CITIZEN,
                source_id=MISSING_ID,
                details=SourceDetailsTestFactory.build(),
            )
        )


async def test_update_source_details_stale_version_raises_precondition_failed() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)

    with pytest.raises(PreconditionFailedError):
        await update(factory)(
            UpdateSourceDetails(
                actor=CITIZEN,
                source_id=source.id,
                details=SourceDetailsTestFactory.build(),
                expected_version=7,
            )
        )

    assert uow.sources.committed[source.id] == source


async def test_update_source_details_referenced_source_raises_immutable() -> None:
    source = owned_source(is_referenced=True)
    uow, factory = unit_of_work(source)

    with pytest.raises(SourceImmutableError):
        await update(factory)(
            UpdateSourceDetails(
                actor=CITIZEN,
                source_id=source.id,
                details=SourceDetailsTestFactory.build(),
            )
        )

    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# MarkSourceReferenced                                                        #
# --------------------------------------------------------------------------- #


async def test_mark_source_referenced_unreferenced_source_commits_referenced() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)

    result = await mark(factory)(
        MarkSourceReferenced(actor=OTHER_CITIZEN, source_id=source.id)
    )

    assert uow.sources.committed[source.id].is_referenced
    assert result.is_referenced
    assert [type(event) for event in uow.committed_events] == [SourceReferenced]


async def test_mark_source_referenced_twice_records_one_event() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)
    handler = mark(factory)
    command = MarkSourceReferenced(actor=CITIZEN, source_id=source.id)

    await handler(command)
    await handler(command)

    assert [type(event) for event in uow.committed_events] == [SourceReferenced]


async def test_mark_source_referenced_anonymous_raises_permission_denied() -> None:
    source = owned_source()
    uow, factory = unit_of_work(source)

    with pytest.raises(PermissionDeniedError):
        await mark(factory)(
            MarkSourceReferenced(actor=Actor.anonymous(), source_id=source.id)
        )

    assert not uow.sources.committed[source.id].is_referenced


async def test_mark_source_referenced_missing_source_raises_not_found() -> None:
    _, factory = unit_of_work()

    with pytest.raises(SourceNotFoundError):
        await mark(factory)(MarkSourceReferenced(actor=CITIZEN, source_id=MISSING_ID))
