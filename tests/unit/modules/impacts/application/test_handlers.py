"""Unit tests for the impacts command handlers, with in-memory fakes only."""

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.identity import DenyAllPolicy, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.seed import FakeReferenceFileReader
from tests.unit.modules.impacts.application.support import (
    NOW,
    entry,
    reference_file,
    stored_metric,
    unit_of_work,
)
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.application.authorisation import (
    AuthorisationPolicy,
    reference_data_policy,
)
from yakhnama.modules.impacts.application.commands import (
    LoadReferenceImpactMetrics,
    RetireImpactMetric,
)
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
    RetireImpactMetricHandler,
)
from yakhnama.modules.impacts.application.ports import ImpactsUnitOfWorkFactory
from yakhnama.modules.impacts.domain.errors import (
    ImpactMetricNotFoundError,
    ImpactMetricRetiredError,
    InconsistentMetricDefinitionError,
)
from yakhnama.modules.impacts.domain.events import (
    ImpactMetricCreated,
    ImpactMetricRelabelled,
    ImpactMetricRetired,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    MetricStatus,
    RetirementReason,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.value_objects import LocalizedText

ACTOR_ID = SequentialIdGenerator(seed=99).new_id()
ADMIN = actor_with({Role.ADMIN}, user_id=ACTOR_ID)
CITIZEN = actor_with(user_id=ACTOR_ID)
REAL_METRIC_CODES = 10
OBSOLETE = RetirementReason(explanation="obsolete")


def load_handler(
    factory: ImpactsUnitOfWorkFactory, policy: AuthorisationPolicy | None = None
) -> LoadReferenceImpactMetricsHandler:
    """Build the load handler with deterministic time and ids."""
    return LoadReferenceImpactMetricsHandler(
        factory,
        policy or reference_data_policy(),
        FrozenClock(NOW),
        SequentialIdGenerator(),
    )


def retire_handler(
    factory: ImpactsUnitOfWorkFactory, policy: AuthorisationPolicy | None = None
) -> RetireImpactMetricHandler:
    """Build the retire handler with deterministic time and ids."""
    return RetireImpactMetricHandler(
        factory,
        policy or reference_data_policy(),
        FrozenClock(NOW),
        SequentialIdGenerator(),
    )


def load(
    *entries: dict[str, object], dry_run: bool = False
) -> LoadReferenceImpactMetrics:
    """Return a load command for a synthetic file."""
    return LoadReferenceImpactMetrics(
        file=reference_file(*entries), actor=ADMIN, dry_run=dry_run
    )


# --------------------------------------------------------------------------- #
# LoadReferenceImpactMetrics                                                  #
# --------------------------------------------------------------------------- #


async def test_load_reference_impact_metrics_with_real_file_creates_every_code() -> (
    None
):
    uow, factory = unit_of_work()
    file = FakeReferenceFileReader().read_impact_metrics()

    report = await load_handler(factory)(
        LoadReferenceImpactMetrics(file=file, actor=ADMIN)
    )

    assert len(report.created) == REAL_METRIC_CODES
    assert (report.updated, report.unchanged, report.skipped_with_reason) == (
        (),
        (),
        (),
    )
    assert report.data_version == file.data_version
    assert set(uow.impact_metrics.committed) == {e.code for e in file.entries}
    assert len(uow.committed_events) == REAL_METRIC_CODES
    assert all(isinstance(e, ImpactMetricCreated) for e in uow.committed_events)


async def test_load_reference_impact_metrics_twice_second_run_is_all_unchanged() -> (
    None
):
    uow, factory = unit_of_work()
    file = FakeReferenceFileReader().read_impact_metrics()
    handler = load_handler(factory)
    command = LoadReferenceImpactMetrics(file=file, actor=ADMIN)
    await handler(command)
    state_after_first = dict(uow.impact_metrics.committed)
    events_after_first = len(uow.committed_events)

    report = await handler(command)

    assert report.is_unchanged is True
    assert len(report.unchanged) == REAL_METRIC_CODES
    assert uow.impact_metrics.committed == state_after_first
    assert len(uow.committed_events) == events_after_first


async def test_load_reference_impact_metrics_when_denied_raises_permission_denied() -> (
    None
):
    uow, factory = unit_of_work()
    policy = DenyAllPolicy()

    with pytest.raises(PermissionDeniedError):
        await load_handler(factory, policy)(load(entry("example_a")))

    assert policy.checked == [ADMIN]
    assert factory.calls == 0
    assert uow.impact_metrics.committed == {}


async def test_load_reference_impact_metrics_dry_run_commits_nothing() -> None:
    uow, factory = unit_of_work()

    report = await load_handler(factory)(load(entry("example_a"), dry_run=True))

    assert report.created == ("example_a",)
    assert report.dry_run is True
    assert uow.committed is False
    assert uow.impact_metrics.committed == {}
    assert uow.committed_events == ()


async def test_load_reference_impact_metrics_new_label_relabels_active_metric() -> None:
    uow, factory = unit_of_work((stored_metric("example_a"),))

    report = await load_handler(factory)(load(entry("example_a", label="New")))

    assert report.updated == ("example_a",)
    assert uow.impact_metrics.committed["example_a"].labels == LocalizedText(
        texts={"en": "New"}
    )
    assert [e.event_type for e in uow.committed_events] == [
        ImpactMetricRelabelled.event_type
    ]


async def test_load_reference_impact_metrics_with_retired_entry_retires_stored() -> (
    None
):
    uow, factory = unit_of_work((stored_metric("example_a"),))

    report = await load_handler(factory)(load(entry("example_a", retirement=OBSOLETE)))

    stored = uow.impact_metrics.committed["example_a"]
    assert report.updated == ("example_a",)
    assert stored.status is MetricStatus.RETIRED
    assert [e.event_type for e in uow.committed_events] == [
        ImpactMetricRetired.event_type
    ]


async def test_load_reference_impact_metrics_new_retired_entry_is_created_retired() -> (
    None
):
    uow, factory = unit_of_work()

    report = await load_handler(factory)(load(entry("example_a", retirement=OBSOLETE)))

    assert report.created == ("example_a",)
    assert uow.impact_metrics.committed["example_a"].is_active is False
    assert [e.event_type for e in uow.committed_events] == [
        ImpactMetricCreated.event_type,
        ImpactMetricRetired.event_type,
    ]


async def test_load_reference_impact_metrics_on_retired_metric_skips_every_change() -> (
    None
):
    uow, factory = unit_of_work((stored_metric("example_a", retirement=OBSOLETE),))

    report = await load_handler(factory)(load(entry("example_a", label="New")))

    reasons = [skip.reason for skip in report.skipped_with_reason]
    assert report.unchanged == ("example_a",)
    assert any("retired" in reason and "labels" in reason for reason in reasons)
    assert any("retirement is final" in reason for reason in reasons)
    assert uow.committed_events == ()


async def test_load_reference_impact_metrics_with_other_retirement_skips_it() -> None:
    _, factory = unit_of_work((stored_metric("example_a", retirement=OBSOLETE),))
    other = RetirementReason(explanation="another reason")

    report = await load_handler(factory)(load(entry("example_a", retirement=other)))

    assert report.unchanged == ("example_a",)
    assert ["not rewritten" in s.reason for s in report.skipped_with_reason] == [True]


async def test_load_reference_impact_metrics_with_new_definition_skips_fields() -> None:
    uow, factory = unit_of_work((stored_metric("example_a"),))
    changed = entry(
        "example_a",
        category="housing",
        aggregation="max",
        sendai={"code": "B-1"},
        description={"en": "Exact meaning"},
    )

    report = await load_handler(factory)(load(changed))

    fields = sorted(s.reason.split(" ", 1)[0] for s in report.skipped_with_reason)
    assert fields == ["aggregation", "category", "description", "sendai"]
    assert report.unchanged == ("example_a",)
    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# RetireImpactMetric                                                          #
# --------------------------------------------------------------------------- #


def retire_command(
    code: str = "example_a", replaced_by: str | None = None
) -> RetireImpactMetric:
    """Return a retire command for ``code``."""
    return RetireImpactMetric(
        ref=ImpactMetricRef(code=code),
        reason=RetirementReason(explanation="superseded", replaced_by=replaced_by),
        actor=ADMIN,
    )


async def test_retire_impact_metric_when_active_commits_retired_metric_and_event() -> (
    None
):
    uow, factory = unit_of_work(
        (stored_metric("example_a"), stored_metric("example_b"))
    )

    await retire_handler(factory)(retire_command(replaced_by="example_b"))

    stored = uow.impact_metrics.committed["example_a"]
    assert stored.status is MetricStatus.RETIRED
    assert uow.collected_events == ()
    (event,) = uow.committed_events
    assert isinstance(event, ImpactMetricRetired)
    assert event.reason.replaced_by == "example_b"
    assert event.occurred_at == NOW


async def test_retire_impact_metric_when_denied_raises_before_reading() -> None:
    uow, factory = unit_of_work((stored_metric("example_a"),))

    with pytest.raises(PermissionDeniedError):
        await retire_handler(factory, DenyAllPolicy())(retire_command())

    assert factory.calls == 0
    assert uow.impact_metrics.committed["example_a"].is_active is True


async def test_retire_impact_metric_when_missing_raises_not_found() -> None:
    uow, factory = unit_of_work()

    with pytest.raises(ImpactMetricNotFoundError):
        await retire_handler(factory)(retire_command())

    assert uow.committed is False


async def test_retire_impact_metric_with_unknown_replacement_raises_not_found() -> None:
    uow, factory = unit_of_work((stored_metric("example_a"),))

    with pytest.raises(ImpactMetricNotFoundError) as raised:
        await retire_handler(factory)(retire_command(replaced_by="example_missing"))

    assert raised.value.details["code"] == "example_missing"
    assert uow.impact_metrics.committed["example_a"].is_active is True


async def test_retire_impact_metric_when_retired_raises_invalid_transition() -> None:
    uow, factory = unit_of_work((stored_metric("example_a", retirement=OBSOLETE),))

    with pytest.raises(ImpactMetricRetiredError):
        await retire_handler(factory)(retire_command())

    assert uow.committed is False
    assert uow.committed_events == ()


async def test_retire_self_replacing_raises_inconsistent_metric_definition_error() -> (
    None
):
    uow, factory = unit_of_work((stored_metric("example_a"),))

    with pytest.raises(InconsistentMetricDefinitionError):
        await retire_handler(factory)(retire_command(replaced_by="example_a"))

    assert factory.calls == 0
    assert uow.impact_metrics.committed["example_a"].is_active is True


# --------------------------------------------------------------------------- #
# Reference-data policy                                                       #
# --------------------------------------------------------------------------- #


async def test_load_reference_impact_metrics_citizen_actor_is_denied_by_policy() -> (
    None
):
    uow, factory = unit_of_work()
    command = load(entry("example_a")).model_copy(update={"actor": CITIZEN})

    with pytest.raises(PermissionDeniedError) as raised:
        await load_handler(factory)(command)

    assert raised.value.details["policy"] == "CanManageReferenceData"
    assert factory.calls == 0
    assert uow.impact_metrics.committed == {}


async def test_retire_impact_metric_citizen_actor_is_denied_by_policy() -> None:
    uow, factory = unit_of_work((stored_metric("example_a"),))
    command = retire_command().model_copy(update={"actor": CITIZEN})

    with pytest.raises(PermissionDeniedError):
        await retire_handler(factory)(command)

    assert factory.calls == 0
    assert uow.impact_metrics.committed["example_a"].is_active is True
