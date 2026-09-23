"""Unit tests for ``TemperatureCsvPipeline`` over the committed fixtures.

The full runs go through the real Template Method with the in-memory unit of work;
the parse and normalise tests call the hooks directly.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.ingestion.application.support import NOW
from tests.unit.modules.ingestion.infrastructure.adapters.support import (
    MALFORMED_FILE,
    TEST_FIXTURES_DIR,
    FixtureWorld,
    sha256_of,
)
from yakhnama.modules.ingestion.application.pipeline import ValidationCollector
from yakhnama.modules.ingestion.application.ports import RawPayload
from yakhnama.modules.ingestion.domain.entities import Observation
from yakhnama.modules.ingestion.domain.value_objects import (
    IngestionIssue,
    IssueSeverity,
    QualityFlag,
    RunCounts,
    RunStatus,
)
from yakhnama.modules.ingestion.infrastructure.adapters.temperature import (
    EXPECTED_COLUMNS,
    UNKNOWN_QUALITY_FLAG,
    TemperatureCsvPipeline,
    TemperatureRow,
    celsius_to_kelvin,
    parse_observed_at,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, SiUnit

SAMPLE_ROWS = 144
HEADER = ",".join(EXPECTED_COLUMNS)
GOOD_LINE = (
    "FIX-TEST-01,Fixture station 01,74.6600,36.3200,2450,"
    "2026-01-01T05:00:00+05:00,-4.8,good"
)


def payload_of(*lines: str) -> RawPayload:
    return RawPayload.of(
        "\n".join(lines).encode(), media_type="text/csv", retrieved_at=NOW
    )


def make_pipeline() -> TemperatureCsvPipeline:
    world = FixtureWorld()
    return world.pipeline()


def parse_lines(*lines: str) -> tuple[list[TemperatureRow], ValidationCollector]:
    pipeline = make_pipeline()
    rows = list(pipeline.parse(payload_of(HEADER, *lines), pipeline.collector))
    return rows, pipeline.collector


def make_row(**overrides: object) -> TemperatureRow:
    [row], _ = parse_lines(GOOD_LINE)
    return row.model_copy(update=overrides)


def stored(world: FixtureWorld) -> list[Observation]:
    return list(world.uow.observations.committed.values())


def warnings_of(issues: tuple[IngestionIssue, ...]) -> list[IngestionIssue]:
    return [issue for issue in issues if issue.severity is IssueSeverity.WARNING]


# ------------------------------------------------------------------- full runs


async def test_temperature_pipeline_run_over_sample_stores_every_row() -> None:
    world = FixtureWorld()

    outcome = await world.run_once()

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.report.counts == RunCounts(
        fetched=SAMPLE_ROWS,
        parsed=SAMPLE_ROWS,
        valid=SAMPLE_ROWS,
        persisted=SAMPLE_ROWS,
    )
    assert outcome.report.issues == ()
    assert len(stored(world)) == SAMPLE_ROWS


async def test_temperature_pipeline_run_over_sample_records_file_checksum() -> None:
    world = FixtureWorld()

    outcome = await world.run_once()

    assert outcome.run.input_checksum == sha256_of(world.path)
    assert world.uow.ingestion_runs.committed[outcome.run.id] == outcome.run
    assert {item.ingested_run_id for item in stored(world)} == {outcome.run.id}
    assert {item.dataset_version_id for item in stored(world)} == {world.version.id}


async def test_temperature_pipeline_run_over_sample_stores_kelvin_utc_hours() -> None:
    world = FixtureWorld()

    await world.run_once()

    observations = stored(world)
    assert {item.variable for item in observations} == {"air_temperature"}
    assert {item.value.unit for item in observations if item.value} == {SiUnit.KELVIN}
    assert {item.observed_at.precision for item in observations} == {DatePrecision.HOUR}
    assert min(item.observed_at.value for item in observations) == datetime(
        2025, 12, 31, 19, tzinfo=UTC
    )
    assert {item.station.code for item in observations if item.station} == {
        "FIX-HUNZA-01",
        "FIX-HUNZA-02",
        "FIX-HUNZA-03",
    }


async def test_temperature_pipeline_run_over_sample_keeps_one_missing_value() -> None:
    world = FixtureWorld()

    await world.run_once()

    [missing] = [item for item in stored(world) if item.value is None]
    assert missing.quality is QualityFlag.MISSING
    assert missing.station is not None
    assert missing.station.code == "FIX-HUNZA-02"


async def test_temperature_pipeline_run_over_sample_keeps_one_suspect_value() -> None:
    world = FixtureWorld()

    await world.run_once()

    [suspect] = [item for item in stored(world) if item.quality is QualityFlag.SUSPECT]
    assert suspect.value is not None
    assert suspect.value.value == pytest.approx(285.55)


async def test_temperature_pipeline_second_run_of_same_version_persists_none() -> None:
    world = FixtureWorld()
    await world.run_once()

    second = await world.run_once()

    assert second.status is RunStatus.SUCCEEDED
    assert second.report.counts == RunCounts(
        fetched=SAMPLE_ROWS,
        parsed=SAMPLE_ROWS,
        valid=SAMPLE_ROWS,
        deduplicated=SAMPLE_ROWS,
        persisted=0,
    )
    assert len(stored(world)) == SAMPLE_ROWS


async def test_temperature_pipeline_run_over_malformed_sample_partially_succeeds() -> (
    None
):
    world = FixtureWorld(directory=TEST_FIXTURES_DIR, file_name=MALFORMED_FILE)

    outcome = await world.run_once()

    assert outcome.status is RunStatus.PARTIALLY_SUCCEEDED
    assert outcome.report.counts == RunCounts(
        fetched=7, parsed=6, valid=3, invalid=3, persisted=3
    )
    errors = {
        (issue.stage, issue.reference)
        for issue in outcome.report.issues
        if issue.severity is IssueSeverity.ERROR
    }
    assert errors == {
        ("parse", "row 2"),
        ("validate", "row 3"),
        ("validate", "row 4"),
        ("validate", "row 6"),
    }
    assert [issue.reference for issue in warnings_of(outcome.report.issues)] == [
        "row 5",
        "row 7",
    ]
    assert outcome.run.input_checksum == sha256_of(world.path)


async def test_temperature_pipeline_run_with_changed_file_fails_on_checksum() -> None:
    world = FixtureWorld()
    world.version = world.version.model_copy(update={"input_checksum": "0" * 64})
    world.uow = type(world.uow)(datasets=[world.dataset], versions=[world.version])

    outcome = await world.run_once()

    assert outcome.status is RunStatus.FAILED
    assert stored(world) == []
    assert [issue.stage for issue in outcome.report.issues] == ["fetch"]


# ----------------------------------------------------------------------- parse


def test_temperature_parse_with_unexpected_header_raises_value_error() -> None:
    pipeline = make_pipeline()

    with pytest.raises(ValueError, match="header"):
        pipeline.parse(payload_of("a,b,c", "1,2,3"), pipeline.collector)


def test_temperature_parse_with_non_utf8_bytes_raises_unicode_error() -> None:
    pipeline = make_pipeline()
    payload = RawPayload.of(b"\xff\xfe", media_type="text/csv", retrieved_at=NOW)

    with pytest.raises(UnicodeDecodeError):
        pipeline.parse(payload, pipeline.collector)


def test_temperature_parse_with_surplus_field_rejects_the_row() -> None:
    rows, collector = parse_lines(f"{GOOD_LINE},extra", GOOD_LINE)

    assert [row.row_number for row in rows] == [2]
    # The template records the parsed count; parse only counts the rejection.
    assert collector.counts.fetched == 1
    [issue] = collector.issues
    assert (issue.stage, issue.reference) == ("parse", "row 1")


@pytest.mark.parametrize("code", ["good", "suspect", "estimated"])
def test_temperature_parse_with_known_quality_maps_it_without_warning(
    code: str,
) -> None:
    rows, collector = parse_lines(GOOD_LINE.replace(",good", f",{code}"))

    assert rows[0].mapped_quality is QualityFlag(code)
    assert collector.issues == ()


def test_temperature_parse_with_unknown_quality_maps_to_suspect_and_warns() -> None:
    rows, collector = parse_lines(GOOD_LINE.replace(",good", ",Q?"))

    assert rows[0].mapped_quality is UNKNOWN_QUALITY_FLAG
    [issue] = collector.issues
    assert issue.severity is IssueSeverity.WARNING
    assert "Q?" in issue.message


def test_temperature_parse_with_empty_value_flagged_missing_does_not_warn() -> None:
    rows, collector = parse_lines(GOOD_LINE.replace(",-4.8,good", ",,missing"))

    assert rows[0].mapped_quality is QualityFlag.MISSING
    assert collector.issues == ()


def test_temperature_parse_with_empty_value_flagged_good_warns_missing() -> None:
    rows, collector = parse_lines(GOOD_LINE.replace(",-4.8,good", ", ,good"))

    assert rows[0].mapped_quality is QualityFlag.MISSING
    assert [issue.severity for issue in collector.issues] == [IssueSeverity.WARNING]


# ------------------------------------------------------------------- normalise


def test_temperature_normalise_builds_station_with_location_and_elevation() -> None:
    pipeline = make_pipeline()

    draft = pipeline.normalise(make_row())

    assert draft.station is not None
    assert draft.station.code == "FIX-TEST-01"
    assert draft.station.name == "Fixture station 01"
    assert draft.station.location is not None
    assert draft.station.location.longitude == pytest.approx(74.66)
    assert draft.station.elevation is not None
    assert draft.station.elevation.unit == SiUnit.METRE
    assert draft.value is not None
    assert draft.value.value == pytest.approx(268.35)
    assert draft.problems() == ()


def test_temperature_normalise_without_optional_station_fields_leaves_them_unset() -> (
    None
):
    pipeline = make_pipeline()
    row = make_row(station_name=" ", longitude="", latitude="", elevation_m="")

    draft = pipeline.normalise(row)

    assert draft.station is not None
    assert (draft.station.name, draft.station.location, draft.station.elevation) == (
        None,
        None,
        None,
    )


def test_temperature_normalise_with_one_coordinate_raises_value_error() -> None:
    pipeline = make_pipeline()

    with pytest.raises(ValueError, match="together"):
        pipeline.normalise(make_row(latitude=""))


def test_temperature_normalise_with_out_of_range_latitude_raises_value_error() -> None:
    pipeline = make_pipeline()

    with pytest.raises(ValueError, match="latitude"):
        pipeline.normalise(make_row(latitude="95"))


def test_temperature_validate_with_bad_elevation_reports_the_field() -> None:
    pipeline = make_pipeline()

    result = pipeline.validate(make_row(elevation_m="high"))

    assert result == ("elevation_m: not a number",)


def test_temperature_validate_with_value_flagged_missing_reports_contradiction() -> (
    None
):
    pipeline = make_pipeline()

    result = pipeline.validate(make_row(mapped_quality=QualityFlag.MISSING))

    assert len(result) == 1
    assert "missing" in result[0]


def test_temperature_reference_for_uses_the_row_number() -> None:
    pipeline = make_pipeline()

    result = pipeline.reference_for(make_row(row_number=12), 0)

    assert result == "row 12"


# ----------------------------------------------------------------- conversions


@pytest.mark.parametrize(
    ("celsius", "kelvin"),
    [
        ("-4.8", 268.35),
        ("0", 273.15),
        ("12.4", 285.55),
        ("-273.15", 0.0),
        ("20.0004", 293.15),
        ("20.0005", 293.15),
        ("20.0015", 293.152),
    ],
)
def test_celsius_to_kelvin_adds_offset_and_rounds_to_millikelvin(
    celsius: str, kelvin: float
) -> None:
    result = celsius_to_kelvin(celsius)

    assert result == kelvin


@pytest.mark.parametrize("celsius", ["warm", "NaN", "Infinity", "-273.16"])
def test_celsius_to_kelvin_with_invalid_value_raises_value_error(celsius: str) -> None:
    with pytest.raises(ValueError, match="air_temperature_c"):
        celsius_to_kelvin(celsius)


@given(
    st.decimals(
        min_value=Decimal("-90"),
        max_value=Decimal("60"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_celsius_to_kelvin_of_centi_degrees_is_exact_decimal_sum(
    celsius: Decimal,
) -> None:
    result = celsius_to_kelvin(str(celsius))

    assert Decimal(str(result)) == celsius + Decimal("273.15")


def test_parse_observed_at_with_offset_returns_utc_at_hour_precision() -> None:
    result = parse_observed_at("2026-01-01T05:00:00+05:00")

    assert result.value == datetime(2026, 1, 1, tzinfo=UTC)
    assert result.precision is DatePrecision.HOUR


@pytest.mark.parametrize(
    ("text", "reason"),
    [("2026-01-01T05:00:00", "offset"), ("yesterday", "ISO 8601")],
)
def test_parse_observed_at_with_bad_text_raises_value_error(
    text: str, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_observed_at(text)


def test_temperature_pipeline_is_single_use_like_every_pipeline() -> None:
    world = FixtureWorld(clock=FrozenClock(NOW), ids=SequentialIdGenerator(seed=9))

    first, second = world.pipeline(), world.pipeline()

    assert first is not second
    assert first.collector is not second.collector
