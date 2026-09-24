"""Unit tests for ``yakhnama.modules.reports.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    ReportAlreadySubmittedError,
    ReportImmutableError,
    ReportNotSubmittedError,
    ReportRevisionUnchangedError,
    ReportSupersessionMismatchError,
    ReportWithdrawnError,
)
from yakhnama.modules.reports.domain.events import (
    ReportRevised,
    ReportSubmitted,
    ReportSuperseded,
    ReportTriaged,
    ReportWithdrawn,
)
from yakhnama.modules.reports.domain.value_objects import (
    HazardGuess,
    ReportContent,
    ReportStatus,
    TriageFlag,
    TriageResult,
)
from yakhnama.shared_kernel.value_objects import Confidence

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _ids() -> SequentialIdGenerator:
    return SequentialIdGenerator(seed=7)


def _report(**fields: object) -> Report:
    return ReportTestFactory.build(
        factory_use_construct=False, **{"created_at": CREATED_AT, **fields}
    )


def _draft(**fields: object) -> Report:
    return _report(**{"status": ReportStatus.DRAFT, "submitted_at": None, **fields})


def _corrected(report: Report) -> ReportContent:
    return report.content.model_copy(update={"description": "Corrected text here."})


def _triage(*flags: TriageFlag) -> TriageResult:
    return TriageResult(flags=flags, evaluated_at=CHANGED_AT)


def _withdrawn(report: Report) -> Report:
    return report.withdraw("Posted by mistake.", clock=_clock(), ids=_ids()).state


def _superseded(report: Report) -> tuple[Report, Report]:
    new = report.revise(_corrected(report), clock=_clock(), ids=_ids()).state
    old = report.mark_superseded(new, clock=_clock(), ids=_ids()).state
    return old, new


# --------------------------------------------------------------------------- #
# Invariants                                                                  #
# --------------------------------------------------------------------------- #


def test_report_from_factory_is_current_first_revision() -> None:
    report = _report()

    assert report.is_current is True
    assert report.revision == 1
    assert report.submitted_at == CREATED_AT


def test_report_with_offset_timestamps_normalises_to_utc() -> None:
    offset = CREATED_AT.astimezone(timezone(timedelta(hours=5)))

    report = _report(created_at=offset, updated_at=offset, submitted_at=offset)

    assert report.created_at.tzinfo is UTC
    assert report.submitted_at is not None
    assert report.submitted_at.tzinfo is UTC


@pytest.mark.parametrize(
    "fields",
    [
        {"status": ReportStatus.DRAFT},
        {"submitted_at": None},
        {"status": ReportStatus.SUPERSEDED},
        {"superseded_by_id": FACTORY_IDS.new_id()},
        {"status": ReportStatus.WITHDRAWN},
        {"withdrawal_reason": "no longer true"},
        {"revision": 2},
        {"supersedes_id": FACTORY_IDS.new_id()},
        {"updated_at": CREATED_AT - timedelta(seconds=1)},
        {"submitted_at": CREATED_AT - timedelta(seconds=1)},
        {"submitted_at": CREATED_AT + timedelta(seconds=1)},
    ],
    ids=",".join,
)
def test_report_breaking_an_invariant_raises_validation_error(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        _report(**fields)


def test_report_draft_with_triage_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _draft(triage=_triage())


def test_report_superseding_itself_raises_validation_error() -> None:
    report_id = FACTORY_IDS.new_id()

    with pytest.raises(PydanticValidationError):
        _report(id=report_id, revision=2, supersedes_id=report_id)


def test_report_with_duplicate_media_raises_validation_error() -> None:
    media_id = FACTORY_IDS.new_id()

    with pytest.raises(PydanticValidationError):
        _report(media_ids=(media_id, media_id))


def test_report_withdrawn_draft_without_submitted_at_is_valid() -> None:
    report = _draft(status=ReportStatus.WITHDRAWN, withdrawal_reason="mistake")

    assert report.submitted_at is None


def test_report_assignment_raises_validation_error() -> None:
    report = _report()

    with pytest.raises(PydanticValidationError):
        report.description = "edited"  # type: ignore[misc]  # reason: frozen check


def test_report_content_and_attribution_mirror_the_fields() -> None:
    report = _report(
        hazard_guess=HazardGuess(hazard_code="glof", confidence=Confidence.LOW)
    )

    content, attribution = report.content, report.attribution

    assert content.description == report.description
    assert content.hazard_guess == report.hazard_guess
    assert attribution.reporter_id == report.reporter_id
    assert attribution.source_id == report.source_id


def test_report_submitted_has_no_content_changing_method() -> None:
    changers = {"set_description", "edit", "update", "change_observation"}

    methods = {name for name in dir(Report) if not name.startswith("_")}

    assert methods.isdisjoint(changers)


# --------------------------------------------------------------------------- #
# submit                                                                      #
# --------------------------------------------------------------------------- #


def test_report_submit_draft_returns_submitted_report_and_event() -> None:
    draft = _draft()

    change = draft.submit(clock=_clock(), ids=_ids())

    assert change.state.status is ReportStatus.SUBMITTED
    assert change.state.submitted_at == CHANGED_AT
    assert change.state.version == draft.version + 1
    assert draft.status is ReportStatus.DRAFT
    (event,) = change.events
    assert isinstance(event, ReportSubmitted)
    assert event.aggregate_id == draft.id
    assert event.reporter_id == draft.reporter_id
    assert event.media_count == 0
    assert event.event_type == "reports.report_submitted"


@pytest.mark.parametrize("status", [ReportStatus.SUBMITTED, ReportStatus.SUPERSEDED])
def test_report_submit_after_submission_raises_already_submitted(
    status: ReportStatus,
) -> None:
    report = _report()
    if status is ReportStatus.SUPERSEDED:
        report, _ = _superseded(report)

    with pytest.raises(ReportAlreadySubmittedError):
        report.submit(clock=_clock(), ids=_ids())


def test_report_submit_withdrawn_raises_withdrawn_error() -> None:
    report = _withdrawn(_draft())

    with pytest.raises(ReportWithdrawnError):
        report.submit(clock=_clock(), ids=_ids())


# --------------------------------------------------------------------------- #
# revise and mark_superseded                                                  #
# --------------------------------------------------------------------------- #


def test_report_revise_returns_new_report_and_leaves_original_unchanged() -> None:
    report = _report()
    content = _corrected(report)

    change = report.revise(content, clock=_clock(), ids=_ids())

    revision = change.state
    assert revision.id != report.id
    assert revision.revision == report.revision + 1
    assert revision.supersedes_id == report.id
    assert revision.content == content
    assert revision.attribution == report.attribution
    assert revision.status is ReportStatus.SUBMITTED
    assert revision.version == 1
    assert revision.triage is None
    assert report.status is ReportStatus.SUBMITTED
    assert report.description != content.description
    (event,) = change.events
    assert isinstance(event, ReportRevised)
    assert event.aggregate_id == revision.id
    assert event.supersedes_id == report.id
    assert event.revision == 2


def test_report_revise_with_same_content_raises_unchanged_error() -> None:
    report = _report()

    with pytest.raises(ReportRevisionUnchangedError):
        report.revise(report.content, clock=_clock(), ids=_ids())


def test_report_revise_draft_raises_not_submitted_error() -> None:
    draft = _draft()

    with pytest.raises(ReportNotSubmittedError):
        draft.revise(_corrected(draft), clock=_clock(), ids=_ids())


def test_report_revise_superseded_raises_immutable_error() -> None:
    old, _ = _superseded(_report())

    with pytest.raises(ReportImmutableError):
        old.revise(_corrected(old), clock=_clock(), ids=_ids())


def test_report_revise_withdrawn_raises_withdrawn_error() -> None:
    report = _withdrawn(_report())

    with pytest.raises(ReportWithdrawnError):
        report.revise(_corrected(report), clock=_clock(), ids=_ids())


def test_report_mark_superseded_with_successor_returns_superseded_and_event() -> None:
    report = _report()
    successor = report.revise(_corrected(report), clock=_clock(), ids=_ids()).state

    change = report.mark_superseded(successor, clock=_clock(), ids=_ids())

    assert change.state.status is ReportStatus.SUPERSEDED
    assert change.state.superseded_by_id == successor.id
    assert change.state.content == report.content
    (event,) = change.events
    assert isinstance(event, ReportSuperseded)
    assert event.superseded_by_id == successor.id


def test_report_mark_superseded_twice_with_same_successor_returns_unchanged() -> None:
    report = _report()
    successor = report.revise(_corrected(report), clock=_clock(), ids=_ids()).state
    old = report.mark_superseded(successor, clock=_clock(), ids=_ids()).state

    change = old.mark_superseded(successor, clock=_clock(), ids=_ids())

    assert change.state is old
    assert change.events == ()


def test_report_mark_superseded_by_second_successor_raises_immutable_error() -> None:
    report = _report()
    first = report.revise(_corrected(report), clock=_clock(), ids=_ids()).state
    second = report.revise(
        report.content.model_copy(update={"description": "Another fix."}),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=8),
    ).state
    old = report.mark_superseded(first, clock=_clock(), ids=_ids()).state

    with pytest.raises(ReportImmutableError):
        old.mark_superseded(second, clock=_clock(), ids=_ids())


def test_report_mark_superseded_by_unrelated_report_raises_mismatch() -> None:
    report = _report()
    unrelated = _report()

    with pytest.raises(ReportSupersessionMismatchError):
        report.mark_superseded(unrelated, clock=_clock(), ids=_ids())


def test_report_mark_superseded_by_other_reporter_raises_mismatch() -> None:
    report = _report()
    successor = report.revise(_corrected(report), clock=_clock(), ids=_ids()).state
    forged = Report.model_validate(
        {**successor.model_dump(), "reporter_id": FACTORY_IDS.new_id()}
    )

    with pytest.raises(ReportSupersessionMismatchError):
        report.mark_superseded(forged, clock=_clock(), ids=_ids())


@settings(max_examples=25, deadline=None)
@given(length=st.integers(min_value=1, max_value=8))
def test_report_revise_chain_numbers_revisions_and_links_each_step(
    length: int,
) -> None:
    clock, ids = _clock(), _ids()
    current = _report()
    chain = [current]

    for step in range(length):
        content = current.content.model_copy(
            update={"description": f"Correction number {step + 1}."}
        )
        successor = current.revise(content, clock=clock, ids=ids).state
        chain[-1] = current.mark_superseded(successor, clock=clock, ids=ids).state
        chain.append(successor)
        current = successor

    assert [report.revision for report in chain] == list(range(1, length + 2))
    assert [report.is_current for report in chain] == [False] * length + [True]
    for older, newer in pairwise(chain):
        assert newer.supersedes_id == older.id
        assert older.superseded_by_id == newer.id


# --------------------------------------------------------------------------- #
# withdraw                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [ReportStatus.DRAFT, ReportStatus.SUBMITTED])
def test_report_withdraw_returns_withdrawn_report_and_event(
    status: ReportStatus,
) -> None:
    report = _draft() if status is ReportStatus.DRAFT else _report()

    change = report.withdraw("  Posted by mistake.  ", clock=_clock(), ids=_ids())

    assert change.state.status is ReportStatus.WITHDRAWN
    assert change.state.withdrawal_reason == "Posted by mistake."
    assert change.state.content == report.content
    (event,) = change.events
    assert isinstance(event, ReportWithdrawn)
    assert event.previous_status is status
    assert "Posted" not in event.model_dump_json()


def test_report_withdraw_already_withdrawn_returns_unchanged() -> None:
    report = _withdrawn(_report())

    change = report.withdraw("Again.", clock=_clock(), ids=_ids())

    assert change.state is report
    assert change.events == ()


def test_report_withdraw_superseded_raises_immutable_error() -> None:
    old, _ = _superseded(_report())

    with pytest.raises(ReportImmutableError):
        old.withdraw("Wrong.", clock=_clock(), ids=_ids())


def test_report_withdraw_with_unsafe_reason_raises_validation_error() -> None:
    report = _report()

    with pytest.raises(PydanticValidationError):
        report.withdraw("bad" + chr(0x202E) + "reason", clock=_clock(), ids=_ids())


# --------------------------------------------------------------------------- #
# attach_triage                                                               #
# --------------------------------------------------------------------------- #


def test_report_attach_triage_keeps_status_and_content_and_emits_kinds() -> None:
    report = _report()
    result = _triage(
        TriageFlag(kind="spam_suspected", detail="short", confidence=Confidence.LOW)
    )

    change = report.attach_triage(result, clock=_clock(), ids=_ids())

    assert change.state.triage == result
    assert change.state.status is ReportStatus.SUBMITTED
    assert change.state.content == report.content
    (event,) = change.events
    assert isinstance(event, ReportTriaged)
    assert event.flag_kinds == ("spam_suspected",)


def test_report_attach_same_triage_twice_returns_unchanged() -> None:
    report = _report().attach_triage(_triage(), clock=_clock(), ids=_ids()).state

    change = report.attach_triage(_triage(), clock=_clock(), ids=_ids())

    assert change.state is report
    assert change.events == ()


def test_report_attach_new_triage_replaces_previous_result() -> None:
    report = _report().attach_triage(_triage(), clock=_clock(), ids=_ids()).state
    newer = TriageResult(evaluated_at=CHANGED_AT + timedelta(hours=1))

    change = report.attach_triage(newer, clock=_clock(), ids=_ids())

    assert change.state.triage == newer


def test_report_attach_triage_to_draft_raises_not_submitted_error() -> None:
    with pytest.raises(ReportNotSubmittedError):
        _draft().attach_triage(_triage(), clock=_clock(), ids=_ids())


def test_report_attach_triage_to_withdrawn_raises_withdrawn_error() -> None:
    report = _withdrawn(_report())

    with pytest.raises(ReportWithdrawnError):
        report.attach_triage(_triage(), clock=_clock(), ids=_ids())


def test_report_event_payloads_carry_no_description_or_coordinates() -> None:
    report = _report()
    changes = [
        report.revise(_corrected(report), clock=_clock(), ids=_ids()),
        report.withdraw("A reason.", clock=_clock(), ids=_ids()),
        report.attach_triage(_triage(), clock=_clock(), ids=_ids()),
        _draft().submit(clock=_clock(), ids=_ids()),
    ]

    payloads = [event.model_dump() for change in changes for event in change.events]

    forbidden = {"description", "observation", "coordinates", "withdrawal_reason"}
    assert all(forbidden.isdisjoint(payload) for payload in payloads)
