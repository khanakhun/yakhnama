"""Unit tests for the hazards command handlers, with in-memory fakes only."""

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.seed import AllowAllPolicy, DenyAllPolicy, FakeReferenceFileReader
from tests.unit.modules.hazards.application.support import (
    NOW,
    entry,
    reference_file,
    stored_types,
    unit_of_work,
)
from yakhnama.modules.hazards.application.authorisation import AdminOnlyPolicy
from yakhnama.modules.hazards.application.commands import (
    LoadReferenceHazardTypes,
    ReactivateHazardType,
    RetireHazardType,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
    ReactivateHazardTypeHandler,
    RetireHazardTypeHandler,
)
from yakhnama.modules.hazards.application.ports import HazardsUnitOfWorkFactory
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.errors import (
    HazardTypeNotFoundError,
    HazardTypeNotRetiredError,
    HazardTypeRetiredError,
    InvalidTaxonomyError,
)
from yakhnama.modules.hazards.domain.events import (
    HazardTypeCreated,
    HazardTypeReactivated,
    HazardTypeRelabelled,
    HazardTypeRetired,
)
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeRef,
    HazardTypeStatus,
    RetirementReason,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.value_objects import LocalizedText

ACTOR_ID = SequentialIdGenerator(seed=99).new_id()
REAL_HAZARD_CODES = 10


def retired(hazard_type: HazardType, text: str = "obsolete") -> HazardType:
    """Return ``hazard_type`` retired with ``text``."""
    change = hazard_type.retire(
        RetirementReason(text=text),
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(seed=3),
    )
    return change.state


def load_handler(
    factory: HazardsUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> LoadReferenceHazardTypesHandler:
    """Build the load handler with deterministic time and ids."""
    return LoadReferenceHazardTypesHandler(
        factory,
        policy or AllowAllPolicy(),
        FrozenClock(NOW),
        SequentialIdGenerator(),
    )


# --------------------------------------------------------------------------- #
# LoadReferenceHazardTypes                                                    #
# --------------------------------------------------------------------------- #


async def test_load_reference_hazard_types_with_real_file_creates_every_code() -> None:
    uow, factory = unit_of_work()
    file = FakeReferenceFileReader().read_hazard_types()
    handler = load_handler(factory)

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert len(report.created) == REAL_HAZARD_CODES
    assert report.updated == ()
    assert report.unchanged == ()
    assert report.skipped_with_reason == ()
    assert report.data_version == file.data_version
    assert set(uow.hazard_types.committed) == file.codes()
    assert uow.committed is True
    assert len(uow.committed_events) == REAL_HAZARD_CODES
    assert all(isinstance(event, HazardTypeCreated) for event in uow.committed_events)


async def test_load_reference_hazard_types_twice_second_run_is_all_unchanged() -> None:
    uow, factory = unit_of_work()
    file = FakeReferenceFileReader().read_hazard_types()
    handler = load_handler(factory)
    await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))
    state_after_first = dict(uow.hazard_types.committed)
    events_after_first = len(uow.committed_events)

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert report.created == ()
    assert report.updated == ()
    assert len(report.unchanged) == REAL_HAZARD_CODES
    assert report.is_unchanged is True
    assert uow.hazard_types.committed == state_after_first
    assert len(uow.committed_events) == events_after_first


async def test_load_reference_hazard_types_when_denied_raises_and_stages_nothing() -> (
    None
):
    uow, factory = unit_of_work()
    policy = DenyAllPolicy()
    handler = load_handler(factory, policy)
    command = LoadReferenceHazardTypes(
        file=reference_file(entry("example_a")), actor_id=ACTOR_ID
    )

    with pytest.raises(PermissionDeniedError):
        await handler(command)

    assert policy.checked == [ACTOR_ID]
    assert factory.calls == 0
    assert uow.hazard_types.committed == {}


async def test_load_reference_hazard_types_dry_run_reports_but_commits_nothing() -> (
    None
):
    uow, factory = unit_of_work()
    handler = load_handler(factory)
    command = LoadReferenceHazardTypes(
        file=reference_file(entry("example_a"), entry("example_b", parent="example_a")),
        actor_id=ACTOR_ID,
        dry_run=True,
    )

    report = await handler(command)

    assert report.created == ("example_a", "example_b")
    assert report.dry_run is True
    assert uow.committed is False
    assert uow.rolled_back is True
    assert uow.hazard_types.committed == {}
    assert uow.committed_events == ()


async def test_load_reference_hazard_types_lists_child_first_creates_parent_first() -> (
    None
):
    uow, factory = unit_of_work()
    handler = load_handler(factory)
    file = reference_file(
        entry("example_c", parent="example_b"),
        entry("example_b", parent="example_a"),
        entry("example_a"),
    )

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert report.created == ("example_a", "example_b", "example_c")
    assert uow.hazard_types.committed["example_c"].parent_code == "example_b"


async def test_load_reference_hazard_types_with_changed_label_relabels_existing() -> (
    None
):
    uow, factory = unit_of_work(stored_types(("example_a", None)))
    handler = load_handler(factory)
    file = reference_file(entry("example_a", label="Better label"))

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    stored = uow.hazard_types.committed["example_a"]
    assert report.updated == ("example_a",)
    assert stored.labels == LocalizedText(texts={"en": "Better label"})
    assert stored.version == 2  # one change after creation
    assert [event.event_type for event in uow.committed_events] == [
        HazardTypeRelabelled.event_type
    ]


async def test_load_reference_hazard_types_new_retired_entry_creates_then_retires() -> (
    None
):
    uow, factory = unit_of_work()
    handler = load_handler(factory)
    reason = RetirementReason(text="superseded", replaced_by="example_b")
    file = reference_file(entry("example_a", retirement=reason), entry("example_b"))

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    stored = uow.hazard_types.committed["example_a"]
    assert report.created == ("example_a", "example_b")
    assert report.updated == ()
    assert stored.status is HazardTypeStatus.RETIRED
    assert stored.retirement == reason
    assert [event.event_type for event in uow.committed_events] == [
        HazardTypeCreated.event_type,
        HazardTypeCreated.event_type,
        HazardTypeRetired.event_type,
    ]


async def test_load_reference_hazard_types_file_retirement_retires_stored_type() -> (
    None
):
    uow, factory = unit_of_work(stored_types(("example_a", None)))
    handler = load_handler(factory)
    reason = RetirementReason(text="no longer used")
    file = reference_file(entry("example_a", retirement=reason))

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert report.updated == ("example_a",)
    assert uow.hazard_types.committed["example_a"].retirement == reason
    assert [event.event_type for event in uow.committed_events] == [
        HazardTypeRetired.event_type
    ]


async def test_load_reference_hazard_types_never_reactivates_a_stored_retirement() -> (
    None
):
    (stored,) = stored_types(("example_a", None))
    uow, factory = unit_of_work((retired(stored),))
    handler = load_handler(factory)

    report = await handler(
        LoadReferenceHazardTypes(
            file=reference_file(entry("example_a")), actor_id=ACTOR_ID
        )
    )

    assert report.unchanged == ("example_a",)
    assert len(report.skipped_with_reason) == 1
    assert "never reactivates" in report.skipped_with_reason[0].reason
    assert uow.hazard_types.committed["example_a"].is_retired is True
    assert uow.committed_events == ()


async def test_load_reference_hazard_types_with_different_retirement_skips_it() -> None:
    (stored,) = stored_types(("example_a", None))
    _, factory = unit_of_work((retired(stored, "first reason"),))
    handler = load_handler(factory)
    file = reference_file(
        entry("example_a", retirement=RetirementReason(text="other reason"))
    )

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert report.unchanged == ("example_a",)
    assert [skip.code for skip in report.skipped_with_reason] == ["example_a"]
    assert "not rewritten" in report.skipped_with_reason[0].reason


async def test_load_reference_hazard_types_with_structural_changes_skips_each() -> None:
    uow, factory = unit_of_work(stored_types(("example_a", None), ("example_b", None)))
    handler = load_handler(factory)
    file = reference_file(
        entry("example_a"),
        entry(
            "example_b",
            parent="example_a",
            alignment={"family": "geophysical", "main_event": "Other"},
            attributes_schema="glof",
            description={"en": "A description"},
        ),
    )

    report = await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    reasons = [skip.reason for skip in report.skipped_with_reason]
    assert report.unchanged == ("example_a", "example_b")
    assert len(reasons) == 4  # parent, alignment, schema, description
    assert uow.hazard_types.committed["example_b"].parent_code is None
    assert uow.committed_events == ()


async def test_load_reference_hazard_types_under_stored_retired_parent_raises() -> None:
    (parent,) = stored_types(("example_a", None))
    uow, factory = unit_of_work((retired(parent),))
    handler = load_handler(factory)
    file = reference_file(
        entry("example_a", retirement=RetirementReason(text="obsolete")),
        entry("example_b", parent="example_a"),
    )

    with pytest.raises(HazardTypeRetiredError):
        await handler(LoadReferenceHazardTypes(file=file, actor_id=ACTOR_ID))

    assert uow.committed is False
    assert set(uow.hazard_types.committed) == {"example_a"}
    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# RetireHazardType                                                            #
# --------------------------------------------------------------------------- #


def retire_handler(
    factory: HazardsUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> RetireHazardTypeHandler:
    """Build the retire handler with deterministic time and ids."""
    return RetireHazardTypeHandler(
        factory,
        policy or AllowAllPolicy(),
        FrozenClock(NOW),
        SequentialIdGenerator(),
    )


def retire_command(
    code: str = "example_a", replaced_by: str | None = None
) -> RetireHazardType:
    """Return a retire command for ``code``."""
    return RetireHazardType(
        ref=HazardTypeRef(code=code),
        reason=RetirementReason(text="merged", replaced_by=replaced_by),
        actor_id=ACTOR_ID,
    )


async def test_retire_hazard_type_when_active_commits_retired_type_and_event() -> None:
    uow, factory = unit_of_work(stored_types(("example_a", None), ("example_b", None)))
    handler = retire_handler(factory)

    await handler(retire_command(replaced_by="example_b"))

    stored = uow.hazard_types.committed["example_a"]
    assert stored.is_retired is True
    assert stored.retirement == RetirementReason(text="merged", replaced_by="example_b")
    assert uow.committed is True
    assert uow.collected_events == ()
    (event,) = uow.committed_events
    assert isinstance(event, HazardTypeRetired)
    assert event.code == "example_a"
    assert event.replaced_by == "example_b"
    assert event.occurred_at == NOW


async def test_retire_hazard_type_when_policy_denies_raises_before_reading() -> None:
    uow, factory = unit_of_work(stored_types(("example_a", None)))
    policy = DenyAllPolicy()
    handler = retire_handler(factory, policy)

    with pytest.raises(PermissionDeniedError):
        await handler(retire_command())

    assert policy.checked == [ACTOR_ID]
    assert factory.calls == 0
    assert uow.hazard_types.committed["example_a"].is_retired is False


async def test_retire_hazard_type_when_missing_raises_not_found() -> None:
    uow, factory = unit_of_work()
    handler = retire_handler(factory)

    with pytest.raises(HazardTypeNotFoundError):
        await handler(retire_command())

    assert uow.committed is False


async def test_retire_hazard_type_when_already_retired_raises_invalid_transition() -> (
    None
):
    (stored,) = stored_types(("example_a", None))
    uow, factory = unit_of_work((retired(stored),))
    handler = retire_handler(factory)

    with pytest.raises(HazardTypeRetiredError):
        await handler(retire_command())

    assert uow.committed is False
    assert uow.committed_events == ()


async def test_retire_hazard_type_unknown_replacement_raises_invalid_taxonomy() -> None:
    uow, factory = unit_of_work(stored_types(("example_a", None)))
    handler = retire_handler(factory)

    with pytest.raises(InvalidTaxonomyError):
        await handler(retire_command(replaced_by="example_missing"))

    assert uow.committed is False
    assert uow.hazard_types.committed["example_a"].is_retired is False


# --------------------------------------------------------------------------- #
# ReactivateHazardType                                                        #
# --------------------------------------------------------------------------- #


def reactivate_handler(
    factory: HazardsUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> ReactivateHazardTypeHandler:
    """Build the reactivate handler with deterministic time and ids."""
    return ReactivateHazardTypeHandler(
        factory,
        policy or AllowAllPolicy(),
        FrozenClock(NOW),
        SequentialIdGenerator(),
    )


def reactivate_command(code: str = "example_a") -> ReactivateHazardType:
    """Return a reactivate command for ``code``."""
    return ReactivateHazardType(
        ref=HazardTypeRef(code=code), reason="retired by mistake", actor_id=ACTOR_ID
    )


async def test_reactivate_hazard_type_when_retired_commits_active_type() -> None:
    (stored,) = stored_types(("example_a", None))
    uow, factory = unit_of_work((retired(stored),))
    handler = reactivate_handler(factory)

    await handler(reactivate_command())

    current = uow.hazard_types.committed["example_a"]
    assert current.status is HazardTypeStatus.ACTIVE
    assert current.retirement is None
    assert [event.event_type for event in uow.committed_events] == [
        HazardTypeReactivated.event_type
    ]


async def test_reactivate_hazard_type_when_policy_denies_raises_permission_denied() -> (
    None
):
    (stored,) = stored_types(("example_a", None))
    uow, factory = unit_of_work((retired(stored),))
    handler = reactivate_handler(factory, DenyAllPolicy())

    with pytest.raises(PermissionDeniedError):
        await handler(reactivate_command())

    assert factory.calls == 0
    assert uow.hazard_types.committed["example_a"].is_retired is True


async def test_reactivate_hazard_type_when_missing_raises_not_found() -> None:
    uow, factory = unit_of_work()
    handler = reactivate_handler(factory)

    with pytest.raises(HazardTypeNotFoundError):
        await handler(reactivate_command())

    assert uow.committed is False


async def test_reactivate_hazard_type_when_active_raises_invalid_transition() -> None:
    uow, factory = unit_of_work(stored_types(("example_a", None)))
    handler = reactivate_handler(factory)

    with pytest.raises(HazardTypeNotRetiredError):
        await handler(reactivate_command())

    assert uow.committed is False
    assert uow.committed_events == ()
