"""Unit tests for ``yakhnama.modules.reports.domain.factories``."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportContentFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.reports.domain.events import ReportSubmitted
from yakhnama.modules.reports.domain.factories import ReportFactory
from yakhnama.modules.reports.domain.value_objects import (
    ReportAttribution,
    ReportStatus,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _attribution() -> ReportAttribution:
    return ReportAttribution(
        reporter_id=FACTORY_IDS.new_id(),
        organization_id=FACTORY_IDS.new_id(),
        source_id=FACTORY_IDS.new_id(),
    )


def test_report_factory_draft_returns_draft_with_client_id_and_no_events() -> None:
    report_id = FACTORY_IDS.new_id()
    attribution = _attribution()
    content = ReportContentFactory.build(factory_use_construct=False)

    change = ReportFactory().draft(
        report_id, attribution, content, clock=FrozenClock(NOW)
    )

    report = change.state
    assert report.id == report_id
    assert report.status is ReportStatus.DRAFT
    assert report.submitted_at is None
    assert report.attribution == attribution
    assert report.content == content
    assert report.created_at == report.updated_at == NOW
    assert change.events == ()


def test_report_factory_submitted_returns_submitted_report_and_event() -> None:
    report_id = FACTORY_IDS.new_id()
    content = ReportContentFactory.build(factory_use_construct=False)

    change = ReportFactory().submitted(
        report_id,
        _attribution(),
        content,
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(seed=3),
    )

    assert change.state.status is ReportStatus.SUBMITTED
    assert change.state.submitted_at == NOW
    assert change.state.version == 1
    (event,) = change.events
    assert isinstance(event, ReportSubmitted)
    assert event.aggregate_id == report_id
    assert event.version == 1


def test_report_factory_submitted_and_draft_submit_publish_same_payload() -> None:
    report_id, attribution = FACTORY_IDS.new_id(), _attribution()
    content = ReportContentFactory.build(factory_use_construct=False)
    clock = FrozenClock(NOW)
    draft = ReportFactory().draft(report_id, attribution, content, clock=clock).state

    direct = ReportFactory().submitted(
        report_id, attribution, content, clock=clock, ids=SequentialIdGenerator(seed=3)
    )
    via_draft = draft.submit(clock=clock, ids=SequentialIdGenerator(seed=3))

    ignored = {"version", "event_id"}
    assert direct.events[0].model_dump(exclude=ignored) == via_draft.events[
        0
    ].model_dump(exclude=ignored)


def test_report_factory_with_non_uuid7_client_id_raises_validation_error() -> None:
    random_v4 = UUID("0b1c7d2e-3f40-4a51-8b62-7c83d94ea5f6")

    with pytest.raises(PydanticValidationError):
        ReportFactory().draft(
            random_v4,
            _attribution(),
            ReportContentFactory.build(factory_use_construct=False),
            clock=FrozenClock(NOW),
        )
