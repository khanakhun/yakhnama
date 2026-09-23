"""Unit tests for the ingestion Template Method pipeline, with fakes only."""

import asyncio
from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from tests.fakes.ingestion import MinimalTestPipeline, MinimalTestRow
from tests.unit.modules.ingestion.application.support import (
    World,
    csv,
    hour,
    make_dataset,
    make_version,
    row,
    sha256,
)
from yakhnama.modules.ingestion.application.dto import IngestionOutcome
from yakhnama.modules.ingestion.application.pipeline import (
    PERSIST_BATCH_SIZE,
    IngestionPipeline,
    ObservationDraft,
    ValidationCollector,
    error_messages,
    issue_message,
    issue_reference,
    select_outcome,
)
from yakhnama.modules.ingestion.application.ports import IngestionUnitOfWork
from yakhnama.modules.ingestion.domain.entities import Observation
from yakhnama.modules.ingestion.domain.errors import VersionDatasetMismatchError
from yakhnama.modules.ingestion.domain.value_objects import (
    ISSUE_MESSAGE_MAX_LENGTH,
    MAX_REPORT_ISSUES,
    GridCellRef,
    IngestionIssue,
    IngestionReport,
    IssueSeverity,
    QualityFlag,
    RunCounts,
    RunStatus,
    StationRef,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvariantViolationError,
    ValidationError,
)
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
)

TEMPLATE_ORDER = [
    "fetch",
    "parse",
    "validate",
    "normalise",
    "deduplicate",
    "persist",
    "record_lineage",
]


async def run_pipeline(
    world: World, pipeline: MinimalTestPipeline | None = None
) -> tuple[IngestionOutcome, MinimalTestPipeline]:
    """Run a pipeline over the world and commit, as the handler does."""
    pipeline = world.pipeline() if pipeline is None else pipeline
    # The handler commits the running run before the pipeline starts.
    running = world.running()
    world.uow.ingestion_runs.store.committed[running.id] = running
    async with world.uow:
        outcome = await pipeline.run(world.dataset, world.version, running, world.uow)
        await world.uow.commit()
    return outcome, pipeline


def issues_at(outcome: IngestionOutcome, stage: str) -> list[IngestionIssue]:
    return [issue for issue in outcome.report.issues if issue.stage == stage]


# --------------------------------------------------------------------- order


async def test_pipeline_run_with_one_row_calls_hooks_in_template_order() -> None:
    world = World(csv(row()))

    _, pipeline = await run_pipeline(world)

    assert pipeline.calls == TEMPLATE_ORDER


async def test_pipeline_run_with_many_rows_reaches_each_stage_in_order() -> None:
    world = World(csv(row(observed_at=hour(0)), row(observed_at=hour(1))))

    _, pipeline = await run_pipeline(world)

    assert list(dict.fromkeys(pipeline.calls)) == TEMPLATE_ORDER


# ---------------------------------------------------------------- outcomes


async def test_pipeline_run_with_valid_rows_succeeds_and_records_lineage() -> None:
    world = World(csv(row(observed_at=hour(0)), row(observed_at=hour(1))))

    outcome, pipeline = await run_pipeline(world)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.report.counts == RunCounts(fetched=2, parsed=2, valid=2, persisted=2)
    assert world.stored_run() == outcome.run
    assert outcome.run.input_checksum == sha256(world.content)
    assert pipeline.input_checksum == sha256(world.content)
    stored = list(world.uow.observations.committed.values())
    assert len(stored) == 2
    assert {item.ingested_run_id for item in stored} == {world.run.id}
    assert {item.dataset_version_id for item in stored} == {world.version.id}


async def test_pipeline_run_with_some_invalid_rows_partially_succeeds() -> None:
    world = World(csv(row(), row(unit="metre", observed_at=hour(1))))

    outcome, _ = await run_pipeline(world)

    assert outcome.status is RunStatus.PARTIALLY_SUCCEEDED
    assert outcome.report.counts == RunCounts(
        fetched=2, parsed=2, valid=1, invalid=1, persisted=1
    )
    [issue] = issues_at(outcome, "validate")
    assert issue.reference == "line 2"
    assert issue.severity is IssueSeverity.ERROR
    assert "expected_unit=kelvin" in issue.message


async def test_pipeline_run_with_only_invalid_rows_fails_and_stores_nothing() -> None:
    world = World(csv(row(variable="not_a_variable")))

    outcome, _ = await run_pipeline(world)

    assert outcome.status is RunStatus.FAILED
    assert outcome.report.counts.persisted == 0
    assert world.uow.observations.committed == {}
    assert outcome.run.input_checksum == sha256(world.content)


async def test_pipeline_run_with_unparsable_line_counts_it_fetched_not_parsed() -> None:
    world = World(csv(row(), "too,few,fields"))

    outcome, _ = await run_pipeline(world)

    assert outcome.report.counts == RunCounts(fetched=2, parsed=1, valid=1, persisted=1)
    [issue] = issues_at(outcome, "parse")
    assert issue.reference == "line 2"
    assert outcome.status is RunStatus.PARTIALLY_SUCCEEDED


async def test_pipeline_run_with_header_only_succeeds_with_a_warning() -> None:
    world = World(csv())

    outcome, _ = await run_pipeline(world)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.report.counts == RunCounts()
    [warning] = outcome.report.issues
    assert warning.severity is IssueSeverity.WARNING
    assert warning.stage == "parse"


async def test_pipeline_run_with_checksum_mismatch_fails_before_parsing() -> None:
    world = World(csv(row()))
    world.adapter.payloads[world.version.id] = csv(row(value="280.0"))

    outcome, pipeline = await run_pipeline(world)

    assert outcome.status is RunStatus.FAILED
    assert pipeline.calls == ["fetch", "record_lineage"]
    [issue] = outcome.report.issues
    assert issue.stage == "fetch"
    assert "checksum" in issue.message
    assert outcome.run.input_checksum == sha256(csv(row(value="280.0")))
    assert world.uow.observations.committed == {}


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        (row(unit="metre"), "actual_unit=metre"),
        (row(variable="not_a_variable"), "not_a_variable"),
        (row(value="", quality="good"), "missing"),
        (row(value="warm"), "could not convert"),
        (row(station=""), "code"),
    ],
)
async def test_pipeline_run_with_bad_row_reports_why_at_validate(
    line: str, fragment: str
) -> None:
    world = World(csv(line))

    outcome, _ = await run_pipeline(world)

    [issue] = issues_at(outcome, "validate")
    assert fragment in issue.message
    assert outcome.report.counts.invalid == 1


async def test_pipeline_run_with_missing_value_flagged_missing_stores_it() -> None:
    world = World(csv(row(value="", quality="missing")))

    outcome, _ = await run_pipeline(world)

    assert outcome.status is RunStatus.SUCCEEDED
    [stored] = world.uow.observations.committed.values()
    assert stored.value is None


# ------------------------------------------------------------ deduplication


async def test_pipeline_run_with_identical_duplicate_keeps_one_without_warning() -> (
    None
):
    world = World(csv(row(), row()))

    outcome, _ = await run_pipeline(world)

    assert outcome.report.counts == RunCounts(
        fetched=2, parsed=2, valid=2, deduplicated=1, persisted=1
    )
    assert outcome.report.issues == ()
    assert outcome.status is RunStatus.SUCCEEDED


async def test_pipeline_run_with_conflicting_duplicate_keeps_first_and_warns() -> None:
    world = World(csv(row(value="270.0"), row(value="275.0")))

    outcome, _ = await run_pipeline(world)

    [stored] = world.uow.observations.committed.values()
    assert stored.value == Measurement(value=270.0, unit="kelvin")
    [warning] = issues_at(outcome, "deduplicate")
    assert warning.severity is IssueSeverity.WARNING
    assert warning.reference is not None
    assert warning.reference.startswith("station:TEST-01 at ")
    assert outcome.status is RunStatus.SUCCEEDED


async def test_pipeline_run_with_keys_already_stored_skips_them() -> None:
    world = World(csv(row(observed_at=hour(0)), row(observed_at=hour(1))))
    first, _ = await run_pipeline(world)
    earlier = next(iter(world.uow.observations.committed.values()))
    world.uow.observations.store.committed = {earlier.key: earlier}

    outcome, _ = await run_pipeline(world)

    assert first.report.counts.persisted == 2
    assert outcome.report.counts == RunCounts(
        fetched=2, parsed=2, valid=2, deduplicated=1, persisted=1
    )
    assert outcome.status is RunStatus.SUCCEEDED


async def test_pipeline_persist_splits_large_batches() -> None:
    lines = [row(observed_at=hour(index)) for index in range(PERSIST_BATCH_SIZE + 1)]
    world = World(csv(*lines))

    outcome, _ = await run_pipeline(world)

    assert world.uow.observations.batch_sizes == [PERSIST_BATCH_SIZE, 1]
    assert outcome.report.counts.persisted == PERSIST_BATCH_SIZE + 1


# ------------------------------------------------------------- failures


async def test_pipeline_run_with_unexpected_header_raises_and_reports_parse() -> None:
    world = World(b"id,name\n1,x\n")
    pipeline = world.pipeline()

    with pytest.raises(ValueError, match="unexpected header"):
        await run_pipeline(world, pipeline)

    report = pipeline.failure_report(ValueError("unexpected header"))
    [issue] = report.issues
    assert issue.stage == "parse"
    assert issue.message == "the parse stage failed with ValueError"
    assert world.uow.rolled_back is True


async def test_pipeline_failure_in_persist_reports_type_only_and_zero_stored() -> None:
    world = World(csv(row()))
    world.uow.observations.failure = RuntimeError("postgres://user:secret@db")
    pipeline = world.pipeline()

    with pytest.raises(RuntimeError):
        await run_pipeline(world, pipeline)

    report = pipeline.failure_report(RuntimeError("postgres://user:secret@db"))
    assert report.issues[-1].message == "the persist stage failed with RuntimeError"
    assert "secret" not in report.issues[-1].message
    assert report.counts == RunCounts(fetched=1, parsed=1, valid=1)


async def test_pipeline_failure_report_after_persist_zeroes_stored_counts() -> None:
    world = World(csv(row(), row()))
    pipeline = world.pipeline()
    await run_pipeline(world, pipeline)

    report = pipeline.failure_report(ConflictError("the run was changed"))

    assert report.counts == RunCounts(fetched=2, parsed=2, valid=2)
    assert report.issues[-1].stage == "record_lineage"
    assert report.issues[-1].message == (
        "the record_lineage stage failed: the run was changed"
    )


async def test_pipeline_failure_in_fetch_has_no_input_checksum() -> None:
    world = World(csv(row()))
    world.adapter.failure = OSError("unreachable")
    pipeline = world.pipeline()

    with pytest.raises(OSError, match="unreachable"):
        await run_pipeline(world, pipeline)

    assert pipeline.input_checksum is None
    assert pipeline.stage == "fetch"


async def test_pipeline_run_twice_is_refused() -> None:
    world = World(csv(row()))
    pipeline = world.pipeline()
    await run_pipeline(world, pipeline)

    with pytest.raises(InvariantViolationError, match="runs once"):
        await pipeline.run(world.dataset, world.version, world.running(), world.uow)


async def test_pipeline_run_with_version_of_other_dataset_is_refused() -> None:
    world = World(csv(row()))
    other_version = make_version(make_dataset(), world.content)

    with pytest.raises(VersionDatasetMismatchError):
        await world.pipeline().run(
            world.dataset, other_version, world.running(), world.uow
        )


async def test_pipeline_run_of_another_versions_run_is_refused() -> None:
    world = World(csv(row()))
    other_version = make_version(world.dataset, world.content)

    with pytest.raises(InvariantViolationError, match="does not ingest"):
        await world.pipeline().run(
            world.dataset, other_version, world.running(), world.uow
        )


async def test_pipeline_run_of_pending_run_is_refused() -> None:
    world = World(csv(row()))

    with pytest.raises(InvariantViolationError, match="running run"):
        await world.pipeline().run(world.dataset, world.version, world.run, world.uow)


# ------------------------------------------------------------ broken hooks


class _GrowingPipeline(MinimalTestPipeline):
    def deduplicate(
        self, observations: Sequence[Observation], collector: ValidationCollector
    ) -> Sequence[Observation]:
        return [*observations, *observations]


class _BoastingPipeline(MinimalTestPipeline):
    async def persist(
        self, observations: Sequence[Observation], uow: IngestionUnitOfWork
    ) -> int:
        return len(observations) + 1


class _TrustingPipeline(MinimalTestPipeline):
    """Skips validation, so the template's own normalise guard is reached."""

    def validate(self, row: MinimalTestRow) -> Sequence[str]:
        return ()


@pytest.mark.parametrize(
    ("pipeline_class", "match"),
    [(_GrowingPipeline, "deduplicate"), (_BoastingPipeline, "persist")],
)
async def test_pipeline_run_with_hook_breaking_counts_raises(
    pipeline_class: type[MinimalTestPipeline], match: str
) -> None:
    world = World(csv(row()))
    pipeline = pipeline_class(world.adapter, clock=world.clock, ids=world.ids)

    with pytest.raises(InvariantViolationError, match=match):
        await run_pipeline(world, pipeline)


@pytest.mark.parametrize(
    "line", [row(value="warm"), row(variable="not_a_variable"), row(station="")]
)
async def test_pipeline_run_without_validation_rejects_at_normalise(
    line: str,
) -> None:
    world = World(csv(line))
    pipeline = _TrustingPipeline(world.adapter, clock=world.clock, ids=world.ids)

    outcome, _ = await run_pipeline(world, pipeline)

    [issue] = outcome.report.issues
    assert issue.stage == "normalise"
    assert outcome.report.counts.invalid == 1
    assert outcome.status is RunStatus.FAILED


# ------------------------------------------------------------- invariants

_ROW_KINDS = st.sampled_from(["valid", "duplicate", "invalid", "malformed"])


def _lines_for(kinds: list[str]) -> list[str]:
    lines: list[str] = []
    for index, kind in enumerate(kinds):
        if kind == "valid":
            lines.append(row(observed_at=hour(index)))
        elif kind == "duplicate":
            lines.append(row(observed_at=hour(0)))
        elif kind == "invalid":
            lines.append(row(unit="metre", observed_at=hour(index)))
        else:
            lines.append("malformed")
    return lines


@settings(max_examples=40, deadline=None)
@given(kinds=st.lists(_ROW_KINDS, max_size=12), is_rerun=st.booleans())
def test_pipeline_counts_hold_invariants_for_random_inputs(
    kinds: list[str], *, is_rerun: bool
) -> None:
    world = World(csv(*_lines_for(kinds)))
    if is_rerun:
        asyncio.run(run_pipeline(world))

    outcome, _ = asyncio.run(run_pipeline(world))

    counts = outcome.report.counts
    assert counts.fetched == len(kinds)
    assert counts.fetched - counts.parsed == kinds.count("malformed")
    assert counts.valid + counts.invalid == counts.parsed
    assert counts.deduplicated + counts.persisted == counts.valid
    assert counts.invalid == kinds.count("invalid")
    assert select_outcome(outcome.report) is outcome.status
    if is_rerun:
        assert counts.persisted == 0


# ----------------------------------------------------------- building blocks


@pytest.mark.parametrize(
    ("counts", "errors", "expected"),
    [
        (RunCounts(), 0, RunStatus.SUCCEEDED),
        (RunCounts(fetched=1, parsed=1, valid=1, persisted=1), 0, RunStatus.SUCCEEDED),
        (
            RunCounts(fetched=2, parsed=2, valid=1, invalid=1, persisted=1),
            1,
            RunStatus.PARTIALLY_SUCCEEDED,
        ),
        (RunCounts(fetched=1, parsed=1, invalid=1), 1, RunStatus.FAILED),
        (RunCounts(), 1, RunStatus.FAILED),
    ],
)
def test_select_outcome_follows_errors_and_persisted_counts(
    counts: RunCounts, errors: int, expected: RunStatus
) -> None:
    report = IngestionReport(
        issues=tuple(
            IngestionIssue(stage="validate", message="bad") for _ in range(errors)
        ),
        counts=counts,
    )

    assert select_outcome(report) is expected


def test_collector_reject_with_no_message_records_a_generic_error() -> None:
    collector = ValidationCollector()
    collector.record_parsed(1)

    collector.reject("validate", "record 1", [])

    [issue] = collector.issues
    assert issue.message == "the problem could not be described"
    assert collector.counts.invalid == 1


def test_collector_beyond_cap_counts_omitted_issues_by_severity() -> None:
    collector = ValidationCollector()
    for _ in range(MAX_REPORT_ISSUES):
        collector.error("parse", "broken")

    collector.error("parse", "one more")
    collector.warn("parse", "and a warning")
    report = collector.report()

    assert len(report.issues) == MAX_REPORT_ISSUES
    assert report.omitted_error_count == 1
    assert report.omitted_warning_count == 1


def test_collector_counts_before_persist_leave_duplicates_at_zero() -> None:
    collector = ValidationCollector()
    collector.record_parsed(3)

    collector.record_valid(2)

    assert collector.counts == RunCounts(fetched=3, parsed=3, valid=2)


def test_collector_with_unprintable_text_cleans_message_and_reference() -> None:
    collector = ValidationCollector()

    collector.warn("parse", "bad\x00value" + chr(0x202E), reference="\x07")

    [issue] = collector.issues
    assert issue.message == "bad value"
    assert issue.reference is None


def test_issue_message_is_cut_to_the_field_bound() -> None:
    assert len(issue_message("x" * 2000)) == ISSUE_MESSAGE_MAX_LENGTH


def test_issue_message_with_nothing_printable_uses_fallback() -> None:
    assert issue_message("\x00\x01") == "the problem could not be described"


def test_issue_reference_of_none_is_none() -> None:
    assert issue_reference(None) is None


def test_error_messages_lists_each_pydantic_field_error() -> None:
    with pytest.raises(ValueError) as caught:  # noqa: PT011  # reason: pydantic's error is a ValueError; its content is asserted below
        StationRef.model_validate({"code": "", "extra": 1})

    messages = error_messages(caught.value)

    assert len(messages) == 2
    assert messages[0].startswith("code: ")


def test_error_messages_names_a_rootless_pydantic_error_value() -> None:
    with pytest.raises(ValueError) as caught:  # noqa: PT011  # reason: pydantic's error is a ValueError; its content is asserted below
        TypeAdapter(int).validate_python("warm")

    assert error_messages(caught.value)[0].startswith("value: ")


def test_error_messages_of_kernel_and_plain_errors_use_their_text() -> None:
    assert error_messages(ValidationError("kernel says no")) == ("kernel says no",)
    assert error_messages(ValueError("plain says no")) == ("plain says no",)


def _draft(**updates: object) -> ObservationDraft:
    base = ObservationDraft(
        station=StationRef(code="TEST-01"),
        variable="air_temperature",
        value=Measurement(value=273.15, unit="kelvin"),
        observed_at=DateWithPrecision(
            value="2026-01-01T00:00:00+00:00",  # type: ignore[arg-type]  # reason: pydantic parses the ISO text; the test keeps it literal
            precision=DatePrecision.HOUR,
        ),
        quality=QualityFlag.GOOD,
    )
    return base.model_validate({**base.model_dump(), **updates})


def test_observation_draft_problems_of_valid_draft_is_empty() -> None:
    assert _draft().problems() == ()


def test_observation_draft_problems_with_two_sites_names_the_site_rule() -> None:
    cell = GridCellRef(
        cell_id="C1",
        centroid=Coordinates(longitude=74.0, latitude=36.0),
        resolution=Measurement(value=1000.0, unit="metre"),
    )

    problems = _draft(grid_cell=cell.model_dump()).problems()

    assert problems == ("a record needs exactly one of a station and a grid cell",)


def test_observation_draft_problems_with_missing_value_checks_the_variable() -> None:
    problems = _draft(
        value=None, quality=QualityFlag.MISSING, variable="not_a_variable"
    ).problems()

    assert len(problems) == 1
    assert "not_a_variable" in problems[0]


def test_pipeline_default_reference_names_the_record_one_based() -> None:
    world = World(csv(row()))
    parsed = MinimalTestRow(
        line=9,
        station="TEST-01",
        variable="air_temperature",
        value="1",
        unit="kelvin",
        observed_at=hour(0),
        quality="good",
    )

    reference = IngestionPipeline.reference_for(world.pipeline(), parsed, 4)

    assert reference == "record 5"
