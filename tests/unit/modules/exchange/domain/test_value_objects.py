"""Unit tests for ``yakhnama.modules.exchange.domain.value_objects``."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.exchange.domain.value_objects import (
    EXPORT_SCHEMA_VERSION,
    IMPORT_MAX_ROWS,
    NO_FILTERS_FRAGMENT,
    NO_WRITES,
    PROPOSED_DATASET_LICENCE,
    REPORT_MAX_ISSUES,
    ArtifactRef,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ExportRequest,
    ImportBatch,
    ImportWrites,
    JobStatus,
    LicenceStatement,
    MetadataSidecar,
    RowIssue,
    ValidationReport,
    ValidationSeverity,
    format_moment,
    plan_import_batches,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.text import is_forbidden_character
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
)

SHA = "a" * 64
MOMENTS = st.builds(
    DateWithPrecision,
    value=st.datetimes(
        # Hypothesis takes naive bounds; timezones= makes every drawn value aware.
        min_value=datetime(1900, 1, 1),  # noqa: DTZ001  # reason: naive bound
        max_value=datetime(2100, 1, 1),  # noqa: DTZ001  # reason: naive bound
        timezones=st.just(UTC),
    ),
    precision=st.sampled_from(DatePrecision),
)
BOXES = st.tuples(
    st.floats(-180, 180), st.floats(-180, 180), st.floats(-90, 90), st.floats(-90, 90)
).map(
    lambda edges: BoundingBox(
        min_longitude=min(edges[0], edges[1]),
        max_longitude=max(edges[0], edges[1]),
        min_latitude=min(edges[2], edges[3]),
        max_latitude=max(edges[2], edges[3]),
    )
)
CODES = st.from_regex(r"[a-z][a-z0-9_]{1,20}", fullmatch=True)
STATUSES = st.from_regex(r"[a-z][a-z_]{1,20}", fullmatch=True)


@st.composite
def filters(draw: st.DrawFn) -> ExportFilters:
    """Draw valid export filters, with a time window in order when both are set."""
    moments = sorted(
        draw(st.lists(MOMENTS, min_size=0, max_size=2)),
        key=lambda moment: moment.value,
    )
    window: dict[str, DateWithPrecision] = {}
    if len(moments) == 2:  # reason: a window has two bounds
        window = {"occurred_from": moments[0], "occurred_to": moments[1]}
    elif moments:
        window = {draw(st.sampled_from(["occurred_from", "occurred_to"])): moments[0]}
    return ExportFilters(
        bbox=draw(st.none() | BOXES),
        hazard_type=draw(st.none() | CODES),
        place_code=draw(st.none() | st.just("pk.gb.hunza")),
        status=draw(st.none() | STATUSES),
        **window,
    )


def _sidecar(**fields: object) -> MetadataSidecar:
    defaults: dict[str, object] = {
        "licence": PROPOSED_DATASET_LICENCE,
        "generated_at": datetime(2026, 9, 23, 12, tzinfo=UTC),
        "dataset": ExportDataset.EVENTS,
        "format": ExportFormat.GEOJSON,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "citation": "Synthetic citation",
        "row_count": 2,
        "checksum": SHA,
        "generator": "yakhnama/0.1.0",
    }
    return MetadataSidecar.model_validate(defaults | fields)


# --------------------------------------------------------------------------- #
# Codes and status                                                            #
# --------------------------------------------------------------------------- #


def test_job_status_is_final_only_for_ended_statuses() -> None:
    final = {status for status in JobStatus if status.is_final}

    assert final == {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


def test_export_request_defaults_to_no_filters() -> None:
    request = ExportRequest(dataset=ExportDataset.CLAIMS, format=ExportFormat.CSV)

    assert request.filters.is_empty


# --------------------------------------------------------------------------- #
# Filters                                                                     #
# --------------------------------------------------------------------------- #


@given(value=filters())
def test_export_filters_json_round_trip_returns_equal_filters(
    value: ExportFilters,
) -> None:
    dumped = value.model_dump_json()

    restored = ExportFilters.model_validate_json(dumped)

    assert restored == value


@given(value=filters())
def test_export_filters_citation_fragment_is_deterministic_and_names_each_filter(
    value: ExportFilters,
) -> None:
    fragment = value.to_citation_fragment()

    assert (
        fragment
        == ExportFilters.model_validate(value.model_dump()).to_citation_fragment()
    )
    assert (fragment == NO_FILTERS_FRAGMENT) == value.is_empty
    for name, is_set in (
        ("hazard_type=", value.hazard_type is not None),
        ("place_code=", value.place_code is not None),
        ("bbox=", value.bbox is not None),
        ("from=", value.occurred_from is not None),
        ("to=", value.occurred_to is not None),
        ("status=", value.status is not None),
    ):
        assert (name in fragment) == is_set
    assert all(character.isprintable() for character in fragment)


def test_export_filters_citation_fragment_uses_fixed_order_and_exact_bbox() -> None:
    value = ExportFilters(
        status="published",
        hazard_type="glof",
        place_code="pk.gb.hunza",
        bbox=BoundingBox(
            min_longitude=74.1, min_latitude=36.0, max_longitude=75.0, max_latitude=37.2
        ),
        occurred_from=DateWithPrecision(
            value=datetime(2010, 5, 5, tzinfo=UTC), precision=DatePrecision.YEAR
        ),
        occurred_to=DateWithPrecision(
            value=datetime(2022, 7, 15, tzinfo=UTC), precision=DatePrecision.MONTH
        ),
    )

    fragment = value.to_citation_fragment()

    assert fragment == (
        "hazard_type=glof; place_code=pk.gb.hunza; bbox=74.1,36.0,75.0,37.2; "
        "from=2010; to=2022-07; status=published"
    )


def test_export_filters_without_filters_cites_no_filters() -> None:
    assert ExportFilters().to_citation_fragment() == "no filters"


def test_export_filters_with_window_end_before_start_raises_validation_error() -> None:
    start = DateWithPrecision(
        value=datetime(2022, 7, 15, tzinfo=UTC), precision=DatePrecision.DAY
    )
    end = DateWithPrecision(
        value=datetime(2022, 6, 1, tzinfo=UTC), precision=DatePrecision.MONTH
    )

    with pytest.raises(PydanticValidationError, match="occurred_to"):
        ExportFilters(occurred_from=start, occurred_to=end)


def test_export_filters_with_coarser_end_in_same_month_is_accepted() -> None:
    start = DateWithPrecision(
        value=datetime(2022, 7, 15, tzinfo=UTC), precision=DatePrecision.DAY
    )
    end = DateWithPrecision(
        value=datetime(2022, 7, 1, tzinfo=UTC), precision=DatePrecision.MONTH
    )

    value = ExportFilters(occurred_from=start, occurred_to=end)

    assert value.occurred_to == end


def test_export_filters_with_malformed_status_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        ExportFilters(status="Published!")


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.YEAR, "2022"),
        (DatePrecision.SEASON, "2022-06 season"),
        (DatePrecision.MONTH, "2022-07"),
        (DatePrecision.DAY, "2022-07-15"),
        (DatePrecision.HOUR, "2022-07-15T06Z"),
        (DatePrecision.EXACT, "2022-07-15T06:07:08Z"),
    ],
)
def test_format_moment_writes_only_the_known_precision(
    precision: DatePrecision, expected: str
) -> None:
    moment = DateWithPrecision(
        value=datetime(2022, 7, 15, 6, 7, 8, tzinfo=UTC), precision=precision
    )

    assert format_moment(moment) == expected


# --------------------------------------------------------------------------- #
# Artifacts, licence, sidecar                                                 #
# --------------------------------------------------------------------------- #


def test_artifact_ref_with_valid_fields_keeps_them() -> None:
    artifact = ArtifactRef(
        object_key="exports/1/events.csv",
        byte_size=0,
        sha256=SHA,
        media_type="text/csv",
    )

    assert artifact.byte_size == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"object_key": "exports/../secret"},
        {"object_key": "Exports/x.csv"},
        {"byte_size": -1},
        {"sha256": "A" * 64},
        {"media_type": "text/csv; charset=utf-8"},
    ],
)
def test_artifact_ref_with_invalid_field_raises_validation_error(
    fields: dict[str, object],
) -> None:
    defaults: dict[str, object] = {
        "object_key": "exports/1/events.csv",
        "byte_size": 10,
        "sha256": SHA,
        "media_type": "text/csv",
    }

    with pytest.raises(PydanticValidationError):
        ArtifactRef.model_validate(defaults | fields)


def test_proposed_dataset_licence_is_cc_by_4_marked_proposed() -> None:
    assert PROPOSED_DATASET_LICENCE.label == "CC-BY-4.0 (proposed)"
    assert PROPOSED_DATASET_LICENCE.status == "proposed"


def test_licence_statement_with_http_url_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        LicenceStatement(spdx_id="CC-BY-4.0", status="accepted", url="http://x.org/")


def test_metadata_sidecar_to_json_dict_has_every_required_key() -> None:
    sidecar = _sidecar(filters=ExportFilters(hazard_type="glof"))

    payload = sidecar.to_json_dict()

    assert payload["licence"] == {
        "spdx_id": "CC-BY-4.0",
        "status": "proposed",
        "url": "https://creativecommons.org/licenses/by/4.0/",
    }
    assert payload["generated_at"] == "2026-09-23T12:00:00Z"
    assert payload["filters"] == {"hazard_type": "glof"}
    assert payload["schema_version"] == "1.0"
    assert payload["row_count"] == 2  # reason: the arranged count
    assert payload["checksum"] == SHA
    assert payload["generator"] == "yakhnama/0.1.0"


CITATIONS = st.text(
    alphabet=st.characters(codec="utf-8"), min_size=1, max_size=50
).filter(
    lambda text: (
        text.strip() != ""
        and not any(is_forbidden_character(character) for character in text)
    )
)


@given(value=filters(), row_count=st.integers(0, 10**9), citation=CITATIONS)
def test_metadata_sidecar_json_boundary_round_trip_returns_equal_sidecar(
    value: ExportFilters, row_count: int, citation: str
) -> None:
    sidecar = _sidecar(filters=value, row_count=row_count, citation=citation)

    restored_from_dict = MetadataSidecar.model_validate(sidecar.to_json_dict())
    restored_from_bytes = MetadataSidecar.model_validate(
        json.loads(sidecar.to_json_bytes())
    )

    assert restored_from_dict == sidecar
    assert restored_from_bytes == sidecar


def test_metadata_sidecar_to_json_bytes_is_indented_utf8() -> None:
    sidecar = _sidecar(citation="Yakhnama — یخ نامہ")

    content = sidecar.to_json_bytes()

    assert content.startswith(b'{\n  "licence"')
    assert "یخ نامہ".encode() in content


def test_metadata_sidecar_normalises_generated_at_to_utc() -> None:
    local = datetime(2026, 9, 23, 17, tzinfo=timezone(timedelta(hours=5)))

    sidecar = _sidecar(generated_at=local)

    assert sidecar.generated_at == datetime(2026, 9, 23, 12, tzinfo=UTC)
    assert sidecar.generated_at.tzinfo is UTC


@pytest.mark.parametrize(
    "fields",
    [
        {"generated_at": datetime(2026, 9, 23)},  # noqa: DTZ001  # reason: naive on purpose
        {"row_count": -1},
        {"generator": "other/1.0"},
        {"schema_version": "1"},
        {"schema_version": "\u0661.\u0660"},
        {"citation": "line\nbreak"},
        {"checksum": "0" * 63},
    ],
)
def test_metadata_sidecar_with_invalid_field_raises_validation_error(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        _sidecar(**fields)


# --------------------------------------------------------------------------- #
# Row issues and reports                                                      #
# --------------------------------------------------------------------------- #


def test_row_issue_defaults_to_blocking_error() -> None:
    issue = RowIssue(row_number=1, field="title", message="  bad  ")

    assert issue.is_blocking
    assert issue.message == "bad"


def test_row_issue_warning_is_not_blocking() -> None:
    issue = RowIssue(row_number=1, message="odd", severity=ValidationSeverity.WARNING)

    assert not issue.is_blocking


@pytest.mark.parametrize(
    "fields",
    [
        {"row_number": 0},
        {"field": "Title"},
        {"message": ""},
        {"message": "x" * 501},
        {"message": "a\u202eb"},
    ],
)
def test_row_issue_with_invalid_field_raises_validation_error(
    fields: dict[str, object],
) -> None:
    defaults: dict[str, object] = {"row_number": 1, "message": "bad"}

    with pytest.raises(PydanticValidationError):
        RowIssue.model_validate(defaults | fields)


ISSUES = st.builds(
    RowIssue,
    row_number=st.integers(1, 30),
    field=st.none() | st.sampled_from(["title", "claim_1_value"]),
    message=st.just("synthetic problem"),
    severity=st.sampled_from(ValidationSeverity),
)


@given(issues=st.lists(ISSUES, max_size=60), extra_rows=st.integers(0, 10))
def test_validation_report_from_issues_counts_rows_with_errors(
    issues: list[RowIssue], extra_rows: int
) -> None:
    rows_seen = 30 + extra_rows

    report = ValidationReport.from_issues(rows_seen, issues)

    error_rows = {issue.row_number for issue in issues if issue.is_blocking}
    assert report.rows_rejected == len(error_rows)
    assert report.rows_valid == rows_seen - len(error_rows)
    assert report.has_blocking_errors == bool(error_rows)
    assert report.error_count + report.warning_count == len(issues)
    assert [issue.row_number for issue in report.issues] == sorted(
        issue.row_number for issue in issues
    )
    assert not report.is_truncated


def test_validation_report_from_issues_beyond_cap_truncates_but_counts_all() -> None:
    rows = REPORT_MAX_ISSUES // 2 + 1
    issues = [
        RowIssue(row_number=row, field=field, message="synthetic problem")
        for row in range(1, rows + 1)
        for field in ("title", "hazard_type")
    ]

    report = ValidationReport.from_issues(rows + 1, issues)

    assert report.is_truncated
    assert len(report.issues) == REPORT_MAX_ISSUES
    assert report.rows_rejected == rows
    assert report.rows_valid == 1


def test_validation_report_without_issues_has_no_blocking_errors() -> None:
    report = ValidationReport.from_issues(5, [])

    assert not report.has_blocking_errors
    assert report.rows_valid == 5  # reason: the arranged count


def test_validation_report_from_issue_beyond_rows_seen_raises_validation_error() -> (
    None
):
    with pytest.raises(PydanticValidationError, match="not seen"):
        ValidationReport.from_issues(1, [RowIssue(row_number=2, message="bad")])


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"rows_seen": 3, "rows_valid": 1, "rows_rejected": 1}, "equal rows_seen"),
        (
            {
                "rows_seen": 2,
                "rows_valid": 2,
                "rows_rejected": 0,
                "issues": (RowIssue(row_number=1, message="bad"),),
            },
            "count the rows",
        ),
        (
            {"rows_seen": 1, "rows_valid": 1, "rows_rejected": 0, "is_truncated": True},
            "maximum",
        ),
    ],
)
def test_validation_report_with_inconsistent_counts_raises_validation_error(
    fields: dict[str, object], match: str
) -> None:
    with pytest.raises(PydanticValidationError, match=match):
        ValidationReport.model_validate(fields)


def test_validation_report_truncated_may_count_more_rejected_rows_than_kept() -> None:
    issues = tuple(
        RowIssue(row_number=1, message="bad") for _ in range(REPORT_MAX_ISSUES)
    )

    report = ValidationReport(
        issues=issues,
        rows_seen=3,
        rows_valid=0,
        rows_rejected=3,
        is_truncated=True,
    )

    assert report.rows_rejected == 3  # reason: the arranged count


# --------------------------------------------------------------------------- #
# Batches and writes                                                          #
# --------------------------------------------------------------------------- #


@given(row_count=st.integers(0, IMPORT_MAX_ROWS), batch_size=st.integers(1, 5000))
def test_plan_import_batches_covers_every_row_once_in_order(
    row_count: int, batch_size: int
) -> None:
    batches = plan_import_batches(row_count, batch_size)

    covered = [
        row for batch in batches for row in range(batch.first_row, batch.last_row + 1)
    ]
    assert covered == list(range(1, row_count + 1))
    assert [batch.number for batch in batches] == list(range(1, len(batches) + 1))
    assert all(batch.size <= batch_size for batch in batches)


def test_plan_import_batches_default_size_cuts_last_batch_short() -> None:
    batches = plan_import_batches(1201)

    assert [batch.size for batch in batches] == [500, 500, 201]
    assert batches[2].contains(1201)
    assert not batches[2].contains(1000)


@pytest.mark.parametrize(("row_count", "batch_size"), [(-1, 1), (1, 0), (10**6, 10)])
def test_plan_import_batches_out_of_range_raises_validation_error(
    row_count: int, batch_size: int
) -> None:
    with pytest.raises(ValidationError, match="row_count"):
        plan_import_batches(row_count, batch_size)


def test_import_batch_with_last_before_first_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="last_row"):
        ImportBatch(number=1, first_row=5, last_row=4)


def test_import_writes_empty_by_default() -> None:
    assert NO_WRITES.is_empty


def test_import_writes_with_ids_and_lineage_is_not_empty() -> None:
    writes = ImportWrites(
        created_ids=(FACTORY_IDS.new_id(),),
        lineage_source_id=FACTORY_IDS.new_id(),
        batches_applied=1,
    )

    assert not writes.is_empty


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"created_ids": "twice"}, "distinct"),
        ({"lineage_source_id": None}, "lineage"),
        ({"batches_applied": 0}, "batch"),
    ],
)
def test_import_writes_inconsistent_raises_validation_error(
    fields: dict[str, object], match: str
) -> None:
    created = FACTORY_IDS.new_id()
    defaults: dict[str, object] = {
        "created_ids": (created,),
        "lineage_source_id": FACTORY_IDS.new_id(),
        "batches_applied": 1,
    }
    if fields.get("created_ids") == "twice":
        fields = {"created_ids": (created, created)}

    with pytest.raises(PydanticValidationError, match=match):
        ImportWrites.model_validate(defaults | fields)
