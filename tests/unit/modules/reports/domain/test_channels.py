"""Unit tests for report channels and the assisted consent record (ADR 0019)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.events import ReportRevised, ReportSubmitted
from yakhnama.modules.reports.domain.factories import ReportFactory
from yakhnama.modules.reports.domain.value_objects import (
    AssistedSubmission,
    ConsentMethod,
    ReportAttribution,
    ReportChannel,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=931)
REPORTER_ID = IDS.new_id()
SOURCE_ID = IDS.new_id()
ORGANIZATION_ID = IDS.new_id()
ASSISTED = AssistedSubmission(
    consent_method=ConsentMethod.VERBAL,
    consent_statement_version="2026-10-05",
    note="Entered for a neighbour without a phone.",
)


def report(**overrides: object) -> Report:
    """Return a submitted report with ``overrides`` applied and validated."""
    built = ReportTestFactory.build(reporter_id=REPORTER_ID)
    fields = {name: getattr(built, name) for name in Report.model_fields}
    return Report.model_validate({**fields, **overrides})


def test_report_without_channel_is_an_account_report() -> None:
    built = report()

    assert built.channel is ReportChannel.ACCOUNT
    assert built.assisted is None


def test_assisted_report_keeps_consent_record() -> None:
    built = report(channel=ReportChannel.ASSISTED, assisted=ASSISTED)

    assert built.attribution.assisted == ASSISTED
    assert built.attribution.channel is ReportChannel.ASSISTED


@pytest.mark.parametrize(
    "overrides",
    [
        {"channel": ReportChannel.ASSISTED},
        {"channel": ReportChannel.ACCOUNT, "assisted": ASSISTED},
        {"channel": ReportChannel.GUEST, "assisted": ASSISTED},
        {"channel": ReportChannel.GUEST, "organization_id": ORGANIZATION_ID},
    ],
)
def test_report_channel_and_its_fields_must_agree(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        report(**overrides)


def test_attribution_assisted_without_consent_is_refused() -> None:
    with pytest.raises(ValidationError, match="assisted must be set"):
        ReportAttribution(
            reporter_id=REPORTER_ID,
            source_id=SOURCE_ID,
            channel=ReportChannel.ASSISTED,
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"consent_method": "nodded", "consent_statement_version": "v1"},
        {"consent_method": "verbal", "consent_statement_version": "V1"},
        {"consent_method": "verbal", "consent_statement_version": "-v1"},
        {"consent_method": "verbal", "consent_statement_version": "v" * 33},
        {"consent_method": "verbal", "consent_statement_version": "v1", "note": ""},
        {
            "consent_method": "verbal",
            "consent_statement_version": "v1",
            "note": "x" * 501,
        },
        {
            "consent_method": "verbal",
            "consent_statement_version": "v1",
            "note": "a" + chr(0x202E) + "b",
        },
        {"consent_method": "written"},
    ],
)
def test_assisted_submission_invalid_fields_are_refused(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AssistedSubmission.model_validate(fields)


def test_assisted_submission_note_keeps_line_breaks() -> None:
    assisted = AssistedSubmission(
        consent_method=ConsentMethod.WRITTEN,
        consent_statement_version="v1.2",
        note="Line one\r\nLine two",
    )

    assert assisted.note == "Line one\nLine two"


def test_revision_inherits_channel_and_consent_record() -> None:
    original = report(channel=ReportChannel.ASSISTED, assisted=ASSISTED)
    content = original.content.model_copy(update={"description": "Corrected words."})

    change = original.revise(content, clock=FrozenClock(NOW), ids=IDS)

    [event] = change.events
    assert change.state.channel is ReportChannel.ASSISTED
    assert change.state.assisted == ASSISTED
    assert isinstance(event, ReportRevised)
    assert event.channel is ReportChannel.ASSISTED


def test_factory_submitted_guest_report_event_names_the_channel() -> None:
    attribution = ReportAttribution(
        reporter_id=REPORTER_ID, source_id=SOURCE_ID, channel=ReportChannel.GUEST
    )
    content = report().content

    change = ReportFactory().submitted(
        IDS.new_id(), attribution, content, clock=FrozenClock(NOW), ids=IDS
    )

    [event] = change.events
    assert isinstance(event, ReportSubmitted)
    assert event.channel is ReportChannel.GUEST
    assert change.state.channel is ReportChannel.GUEST


def test_submitted_event_without_channel_reads_as_account() -> None:
    change = ReportFactory().submitted(
        IDS.new_id(),
        ReportAttribution(reporter_id=REPORTER_ID, source_id=SOURCE_ID),
        report().content,
        clock=FrozenClock(NOW),
        ids=IDS,
    )
    payload = change.events[0].model_dump(mode="json")
    del payload["channel"]

    restored = ReportSubmitted.model_validate(payload)

    assert restored.channel is ReportChannel.ACCOUNT
