"""Unit tests for ``yakhnama.modules.ingestion.domain.value_objects``."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from geojson_pydantic import MultiPolygon, Polygon
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.factories.ingestion import TEST_FOOTPRINT, DatasetDetailsTestFactory
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.errors import (
    ObservationUnitMismatchError,
    UnknownVariableError,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    ADAPTER_NAME_PATTERN,
    COG_MEDIA_TYPE,
    DATASET_CODE_PATTERN,
    DATASET_TRANSITIONS,
    FINISHED_RUN_STATUSES,
    MAX_FOOTPRINT_POSITIONS,
    MAX_REPORT_ISSUES,
    RUN_TRANSITIONS,
    VARIABLE_CODE_PATTERN,
    VARIABLES,
    VERSION_LABEL_PATTERN,
    AdapterName,
    AssetHref,
    DatasetCode,
    DatasetLicence,
    DatasetStatus,
    DatasetUrl,
    DatasetVersionDetails,
    GridCellRef,
    IngestionIssue,
    IngestionReport,
    InputChecksum,
    IssueSeverity,
    MediaType,
    ObservationKey,
    RasterAssetDescription,
    RasterFootprint,
    RunCounts,
    RunRequest,
    RunStatus,
    SpatialCoverage,
    StacAsset,
    StacBand,
    StationRef,
    TemporalCoverage,
    VariableCode,
    VersionLabel,
    can_move_dataset,
    can_move_run,
    is_known_variable,
    is_stac_id,
    latest_instant,
    require_variable_unit,
    variable_definition,
)
from yakhnama.shared_kernel.value_objects import (
    KNOWN_UNITS,
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

JULY_15 = datetime(2024, 7, 15, 10, 30, tzinfo=UTC)
SHA256 = "a" * 64

_dataset_code = TypeAdapter(DatasetCode)
_version_label = TypeAdapter(VersionLabel)
_adapter_name = TypeAdapter(AdapterName)
_variable_code = TypeAdapter(VariableCode)
_checksum = TypeAdapter(InputChecksum)
_dataset_url = TypeAdapter(DatasetUrl)
_asset_href = TypeAdapter(AssetHref)
_media_type = TypeAdapter(MediaType)

counts_tuples = st.tuples(*[st.integers(min_value=0, max_value=50)] * 6)
instants = st.datetimes(
    min_value=datetime(1900, 1, 1),  # noqa: DTZ001  # reason: hypothesis bounds must be naive; timezones= makes values aware
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001  # reason: as above
    timezones=st.just(UTC),
)
moments = st.builds(
    DateWithPrecision, value=instants, precision=st.sampled_from(DatePrecision)
)


def _moment(value: datetime, precision: DatePrecision) -> DateWithPrecision:
    return DateWithPrecision(value=value, precision=precision)


def _issue(severity: IssueSeverity = IssueSeverity.ERROR) -> IngestionIssue:
    return IngestionIssue(stage="validate", message="bad value", severity=severity)


def _square(size: float = 1.0) -> Polygon:
    return Polygon.model_validate(
        {
            "type": "Polygon",
            "coordinates": [
                [[74.0, 36.0], [74.0 + size, 36.0], [74.0 + size, 37.0], [74.0, 36.0]]
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Codes and labels                                                            #
# --------------------------------------------------------------------------- #


@given(st.from_regex(DATASET_CODE_PATTERN, fullmatch=True))
def test_dataset_code_matching_pattern_is_accepted(code: str) -> None:
    result = _dataset_code.validate_python(code)

    assert result == code


@pytest.mark.parametrize("code", ["a", "Pmd", "1pmd", "pmd daily", "p" * 65, ""])
def test_dataset_code_malformed_is_rejected(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        _dataset_code.validate_python(code)


@given(st.from_regex(VERSION_LABEL_PATTERN, fullmatch=True))
def test_version_label_matching_pattern_is_accepted(label: str) -> None:
    result = _version_label.validate_python(label)

    assert result == label


@pytest.mark.parametrize("label", ["", "-v1", "v 1", "v" * 65])
def test_version_label_malformed_is_rejected(label: str) -> None:
    with pytest.raises(PydanticValidationError):
        _version_label.validate_python(label)


@given(st.from_regex(ADAPTER_NAME_PATTERN, fullmatch=True))
def test_adapter_name_matching_pattern_is_accepted(name: str) -> None:
    result = _adapter_name.validate_python(name)

    assert result == name


@pytest.mark.parametrize("name", ["a", "Local", "local-csv", "1csv"])
def test_adapter_name_malformed_is_rejected(name: str) -> None:
    with pytest.raises(PydanticValidationError):
        _adapter_name.validate_python(name)


@given(st.from_regex(VARIABLE_CODE_PATTERN, fullmatch=True))
def test_variable_code_matching_pattern_is_accepted(code: str) -> None:
    result = _variable_code.validate_python(code)

    assert result == code


@given(st.text(alphabet="0123456789abcdefABCDEF", min_size=64, max_size=64))
def test_input_checksum_hex_digest_is_normalised_to_lower_case(digest: str) -> None:
    result = _checksum.validate_python(digest)

    assert result == digest.lower()


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "g" * 64, ""])
def test_input_checksum_malformed_is_rejected(digest: str) -> None:
    with pytest.raises(PydanticValidationError):
        _checksum.validate_python(digest)


def test_input_checksum_non_string_is_rejected_by_type_check() -> None:
    with pytest.raises(PydanticValidationError):
        _checksum.validate_python(42)


# --------------------------------------------------------------------------- #
# URL and licence                                                             #
# --------------------------------------------------------------------------- #


def test_dataset_url_https_is_accepted_verbatim() -> None:
    url = "https://data.example.test:8443/path?q=1"

    result = _dataset_url.validate_python(url)

    assert result == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://data.example.test/a b", "printable ASCII"),
        ("javascript:alert(1)", "http or https"),
        ("ftp://data.example.test/", "http or https"),
        ("https://user:secret@data.example.test/", "user information"),
        ("https:///path", "host"),
        ("https://data.example.test:99999/", "invalid port"),
    ],
)
def test_dataset_url_breaking_a_rule_is_rejected(url: str, reason: str) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        _dataset_url.validate_python(url)


def test_dataset_licence_spdx_is_not_custom() -> None:
    licence = DatasetLicence(spdx_id="CC-BY-4.0", attribution="Test publisher")

    assert licence.is_custom is False


def test_dataset_licence_custom_text_is_custom() -> None:
    licence = DatasetLicence(
        custom_text="Research use only", attribution="Test publisher"
    )

    assert licence.is_custom is True


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"spdx_id": "CC-BY-4.0", "custom_text": "Research use only"},
    ],
)
def test_dataset_licence_without_exactly_one_term_is_rejected(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError, match="exactly one"):
        DatasetLicence.model_validate({**fields, "attribution": "Test publisher"})


def test_dataset_licence_without_attribution_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="attribution"):
        DatasetLicence.model_validate({"spdx_id": "CC-BY-4.0"})


def test_dataset_licence_unsafe_attribution_is_rejected() -> None:
    right_to_left_override = chr(0x202E)

    with pytest.raises(PydanticValidationError):
        DatasetLicence(
            spdx_id="CC-BY-4.0", attribution=f"Test{right_to_left_override}publisher"
        )


# --------------------------------------------------------------------------- #
# Transition tables                                                           #
# --------------------------------------------------------------------------- #


def test_dataset_transitions_cover_every_status_and_retired_is_terminal() -> None:
    keys = set(DATASET_TRANSITIONS)

    assert keys == set(DatasetStatus)
    assert DATASET_TRANSITIONS[DatasetStatus.RETIRED] == frozenset()


@given(st.sampled_from(DatasetStatus), st.sampled_from(DatasetStatus))
def test_can_move_dataset_matches_transition_table(
    from_status: DatasetStatus, to_status: DatasetStatus
) -> None:
    result = can_move_dataset(from_status, to_status)

    assert result is (to_status in DATASET_TRANSITIONS[from_status])


def test_dataset_deprecated_cannot_be_reactivated() -> None:
    result = can_move_dataset(DatasetStatus.DEPRECATED, DatasetStatus.ACTIVE)

    assert result is False


def test_run_transitions_cover_every_status_and_finished_are_terminal() -> None:
    keys = set(RUN_TRANSITIONS)

    assert keys == set(RunStatus)
    assert {
        RunStatus.SUCCEEDED,
        RunStatus.PARTIALLY_SUCCEEDED,
        RunStatus.FAILED,
    } == FINISHED_RUN_STATUSES


@given(st.sampled_from(RunStatus), st.sampled_from(RunStatus))
def test_can_move_run_matches_transition_table(
    from_status: RunStatus, to_status: RunStatus
) -> None:
    result = can_move_run(from_status, to_status)

    assert result is (to_status in RUN_TRANSITIONS[from_status])


# --------------------------------------------------------------------------- #
# Coverage                                                                    #
# --------------------------------------------------------------------------- #


def test_spatial_coverage_wraps_bounding_box() -> None:
    box = BoundingBox(
        min_longitude=72.0, min_latitude=34.0, max_longitude=78.0, max_latitude=37.5
    )

    coverage = SpatialCoverage(bbox=box)

    assert coverage.bbox == box


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.EXACT, JULY_15),
        (DatePrecision.HOUR, datetime(2024, 7, 15, 10, 59, 59, 999999, tzinfo=UTC)),
        (DatePrecision.DAY, datetime(2024, 7, 15, 23, 59, 59, 999999, tzinfo=UTC)),
        (DatePrecision.MONTH, datetime(2024, 7, 31, 23, 59, 59, 999999, tzinfo=UTC)),
        (DatePrecision.SEASON, datetime(2024, 8, 31, 23, 59, 59, 999999, tzinfo=UTC)),
        (DatePrecision.YEAR, datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)),
    ],
)
def test_latest_instant_returns_end_of_precision_period(
    precision: DatePrecision, expected: datetime
) -> None:
    result = latest_instant(_moment(JULY_15, precision))

    assert result == expected


@pytest.mark.parametrize(
    "precision",
    [DatePrecision.HOUR, DatePrecision.DAY, DatePrecision.MONTH, DatePrecision.YEAR],
)
def test_latest_instant_in_last_representable_period_is_capped(
    precision: DatePrecision,
) -> None:
    last_day = datetime(9999, 12, 31, 23, 30, tzinfo=UTC)

    result = latest_instant(_moment(last_day, precision))

    assert result == datetime.max.replace(tzinfo=UTC)


@given(moments)
def test_latest_instant_is_never_before_the_truncated_moment(
    moment: DateWithPrecision,
) -> None:
    result = latest_instant(moment)

    assert result >= moment.truncate().value


def test_temporal_coverage_end_in_same_month_as_day_start_is_accepted() -> None:
    coverage = TemporalCoverage(
        start=_moment(JULY_15, DatePrecision.DAY),
        end=_moment(datetime(2024, 7, 1, tzinfo=UTC), DatePrecision.MONTH),
    )

    assert coverage.end is not None


def test_temporal_coverage_end_before_start_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="end before it starts"):
        TemporalCoverage(
            start=_moment(JULY_15, DatePrecision.DAY),
            end=_moment(datetime(2024, 6, 1, tzinfo=UTC), DatePrecision.MONTH),
        )


def test_temporal_coverage_open_ended_is_accepted() -> None:
    coverage = TemporalCoverage(start=_moment(JULY_15, DatePrecision.DAY))

    assert coverage.end is None


@given(moments, moments)
def test_temporal_coverage_accepts_exactly_when_end_period_reaches_start(
    start: DateWithPrecision, end: DateWithPrecision
) -> None:
    is_ordered = latest_instant(end) >= start.truncate().value

    if is_ordered:
        coverage = TemporalCoverage(start=start, end=end)
        assert coverage.end == end
    else:
        with pytest.raises(PydanticValidationError):
            TemporalCoverage(start=start, end=end)


# --------------------------------------------------------------------------- #
# Counts and reports                                                          #
# --------------------------------------------------------------------------- #


def _counts_are_consistent(values: tuple[int, ...]) -> bool:
    fetched, parsed, valid, invalid, deduplicated, persisted = values
    return (
        persisted <= valid <= parsed <= fetched
        and valid + invalid <= parsed
        and deduplicated + persisted <= valid
    )


@given(counts_tuples)
def test_run_counts_accepts_exactly_the_consistent_combinations(
    values: tuple[int, ...],
) -> None:
    fields = dict(
        zip(
            ("fetched", "parsed", "valid", "invalid", "deduplicated", "persisted"),
            values,
            strict=True,
        )
    )

    if _counts_are_consistent(values):
        counts = RunCounts.model_validate(fields)
        assert counts.model_dump() == fields
    else:
        with pytest.raises(PydanticValidationError):
            RunCounts.model_validate(fields)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"fetched": 1, "parsed": 2}, "persisted <= valid <= parsed <= fetched"),
        ({"fetched": 2, "parsed": 2, "valid": 2, "invalid": 1}, "valid + invalid"),
        (
            {"fetched": 2, "parsed": 2, "valid": 2, "deduplicated": 1, "persisted": 2},
            "deduplicated + persisted",
        ),
    ],
)
def test_run_counts_each_broken_invariant_is_named(
    fields: dict[str, int], reason: str
) -> None:
    with pytest.raises(PydanticValidationError, match=re.escape(reason)):
        RunCounts.model_validate(fields)


def test_run_counts_negative_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        RunCounts(fetched=-1)


def test_run_counts_default_is_all_zero() -> None:
    counts = RunCounts()

    assert set(counts.model_dump().values()) == {0}


def test_ingestion_issue_unknown_stage_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        IngestionIssue.model_validate({"stage": "transform", "message": "x"})


def test_ingestion_issue_default_severity_is_error() -> None:
    issue = IngestionIssue(stage="parse", reference="row 3", message="bad header")

    assert issue.severity is IssueSeverity.ERROR


def test_ingestion_issue_multiline_message_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        IngestionIssue(stage="parse", message="line one\nline two")


@given(
    st.integers(min_value=0, max_value=5),
    st.integers(min_value=0, max_value=5),
)
def test_ingestion_report_counts_errors_and_warnings(
    errors: int, warnings: int
) -> None:
    issues = [_issue()] * errors + [_issue(IssueSeverity.WARNING)] * warnings

    report = IngestionReport.collect(issues, RunCounts())

    assert report.error_count == errors
    assert report.warning_count == warnings
    assert report.has_errors is (errors > 0)


def test_ingestion_report_collect_beyond_cap_counts_omitted_by_severity() -> None:
    warnings = [_issue(IssueSeverity.WARNING)] * MAX_REPORT_ISSUES
    extra = [_issue(), _issue(), _issue(IssueSeverity.WARNING)]

    report = IngestionReport.collect(iter(warnings + extra), RunCounts())

    assert len(report.issues) == MAX_REPORT_ISSUES
    assert report.omitted_error_count == 2
    assert report.omitted_warning_count == 1
    assert report.error_count == 2
    assert report.has_errors is True


def test_ingestion_report_omissions_before_cap_are_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="only once the report is full"):
        IngestionReport(issues=(_issue(),), omitted_error_count=1)


def test_ingestion_report_more_issues_than_cap_are_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        IngestionReport(issues=(_issue(),) * (MAX_REPORT_ISSUES + 1))


# --------------------------------------------------------------------------- #
# Variables                                                                   #
# --------------------------------------------------------------------------- #


def test_variables_registry_holds_proposed_si_entries() -> None:
    units = {code: definition.unit for code, definition in VARIABLES.items()}

    assert units == {
        "air_temperature": "kelvin",
        "precipitation_depth": "metre",
        "discharge": "cubic_metre_per_second",
        "snow_depth": "metre",
    }
    assert all(definition.is_proposed for definition in VARIABLES.values())
    assert all(definition.code == code for code, definition in VARIABLES.items())


def test_variables_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        VARIABLES["wind_speed"] = VARIABLES["discharge"]  # type: ignore[index]  # reason: asserting the mapping is read-only


def test_is_known_variable_registered_code_is_known() -> None:
    result = is_known_variable("air_temperature")

    assert result is True


def test_is_known_variable_unregistered_code_is_unknown() -> None:
    result = is_known_variable("wind_speed")

    assert result is False


def test_variable_definition_unknown_code_raises_unknown_variable_error() -> None:
    with pytest.raises(UnknownVariableError) as caught:
        variable_definition("wind_speed")

    assert caught.value.details == {"variable": "wind_speed"}


@given(st.sampled_from(sorted(VARIABLES)), st.sampled_from(sorted(KNOWN_UNITS)))
def test_require_variable_unit_accepts_only_the_registry_unit(
    variable: str, unit: str
) -> None:
    expected = VARIABLES[variable].unit

    if unit == expected:
        require_variable_unit(variable, unit)
    else:
        with pytest.raises(ObservationUnitMismatchError) as caught:
            require_variable_unit(variable, unit)
        assert caught.value.details["expected_unit"] == expected


def test_require_variable_unit_unknown_variable_raises_unknown_variable_error() -> None:
    with pytest.raises(UnknownVariableError):
        require_variable_unit("wind_speed", "metre_per_second")


# --------------------------------------------------------------------------- #
# Sites and keys                                                              #
# --------------------------------------------------------------------------- #


def test_station_ref_site_ref_prefixes_code() -> None:
    station = StationRef(
        code="41530",
        name="Test station",
        location=Coordinates(longitude=74.3, latitude=35.9),
        elevation=Measurement(value=1460.0, unit=SiUnit.METRE),
    )

    assert station.site_ref == "station:41530"


def test_station_ref_elevation_not_in_metre_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="elevation must be in metre"):
        StationRef(code="1", elevation=Measurement(value=1.0, unit=SiUnit.SECOND))


def test_grid_cell_ref_site_ref_prefixes_cell_id() -> None:
    cell = GridCellRef(
        cell_id="r12c40",
        centroid=Coordinates(longitude=74.3, latitude=35.9),
        resolution=Measurement(value=1000.0, unit=SiUnit.METRE),
    )

    assert cell.site_ref == "grid_cell:r12c40"


@pytest.mark.parametrize(
    ("resolution", "reason"),
    [
        (Measurement(value=0.0, unit=SiUnit.METRE), "positive"),
        (Measurement(value=10.0, unit=SiUnit.KELVIN), "resolution must be in metre"),
    ],
)
def test_grid_cell_ref_bad_resolution_is_rejected(
    resolution: Measurement, reason: str
) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        GridCellRef(
            cell_id="r1c1",
            centroid=Coordinates(longitude=74.0, latitude=36.0),
            resolution=resolution,
        )


def test_observation_key_observed_at_is_normalised_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))

    key = ObservationKey(
        observed_at=datetime(2024, 7, 15, 15, 30, tzinfo=plus_five),
        dataset_version_id=SequentialIdGenerator().new_id(),
        variable="air_temperature",
        site_ref="station:1",
    )

    assert key.observed_at == JULY_15
    assert key.observed_at.tzinfo is UTC


# --------------------------------------------------------------------------- #
# Rasters                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "href",
    [
        "rasters/2024/scene.tif",
        "https://data.example.test/scene.tif",
        "s3://bucket/rasters/scene.tif",
    ],
)
def test_asset_href_object_key_or_allowed_url_is_accepted(href: str) -> None:
    result = _asset_href.validate_python(href)

    assert result == href


@pytest.mark.parametrize(
    ("href", "reason"),
    [
        ("rasters/a scene.tif", "whitespace"),
        ("javascript:alert(1)", "http, https or s3"),
        ("https://user@data.example.test/x.tif", "no user information"),
        ("s3:///x.tif", "must have a host"),
        ("/etc/passwd", "relative"),
        ("rasters/../secrets.tif", "relative"),
        ("rasters/./x.tif", "relative"),
        ("rasters\\x.tif", "relative"),
    ],
)
def test_asset_href_breaking_a_rule_is_rejected(href: str, reason: str) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        _asset_href.validate_python(href)


@pytest.mark.parametrize(
    "media_type", [COG_MEDIA_TYPE, "application/json", "image/png"]
)
def test_media_type_well_formed_is_accepted(media_type: str) -> None:
    result = _media_type.validate_python(media_type)

    assert result == media_type


@pytest.mark.parametrize("media_type", ["tiff", "Image/TIFF", "image/tiff;", ""])
def test_media_type_malformed_is_rejected(media_type: str) -> None:
    with pytest.raises(PydanticValidationError):
        _media_type.validate_python(media_type)


def test_stac_asset_duplicate_roles_are_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="roles must be unique"):
        StacAsset(
            key="data",
            href="rasters/x.tif",
            media_type=COG_MEDIA_TYPE,
            roles=("data", "data"),
        )


def test_stac_band_optional_fields_default_to_none() -> None:
    band = StacBand(name="B04")

    assert band.common_name is None
    assert band.description is None


@pytest.mark.parametrize("stac_id", ["S2A_MSIL2A_20240715", "scene:1.0"])
def test_is_stac_id_well_formed_id_is_accepted(stac_id: str) -> None:
    result = is_stac_id(stac_id)

    assert result is True


@pytest.mark.parametrize("stac_id", ["-bad", "", "s" * 129])
def test_is_stac_id_malformed_id_is_rejected(stac_id: str) -> None:
    result = is_stac_id(stac_id)

    assert result is False


def test_raster_footprint_bounding_box_spans_every_position() -> None:
    box = TEST_FOOTPRINT.bounding_box()

    assert box == BoundingBox(
        min_longitude=74.0, min_latitude=36.0, max_longitude=75.0, max_latitude=37.0
    )


def test_raster_footprint_multipolygon_is_accepted() -> None:
    multi = MultiPolygon.model_validate(
        {"type": "MultiPolygon", "coordinates": [_square().coordinates]}
    )

    footprint = RasterFootprint(geojson=multi)

    assert footprint.geojson.type == "MultiPolygon"
    assert footprint.bounding_box().max_longitude == 75.0


def test_raster_footprint_copies_the_mutable_geometry() -> None:
    polygon = _square()

    footprint = RasterFootprint(geojson=polygon)

    assert footprint.geojson is not polygon
    assert footprint.geojson == polygon


@pytest.mark.parametrize(
    ("ring", "reason"),
    [
        (
            [
                [74.0, 36.0, 1.0],
                [75.0, 36.0, 1.0],
                [75.0, 37.0, 1.0],
                [74.0, 36.0, 1.0],
            ],
            "two-dimensional",
        ),
        ([[181.0, 36.0], [75.0, 36.0], [75.0, 37.0], [181.0, 36.0]], "longitude"),
        ([[74.0, 91.0], [75.0, 36.0], [75.0, 37.0], [74.0, 91.0]], "latitude"),
    ],
)
def test_raster_footprint_bad_position_is_rejected(
    ring: list[list[float]], reason: str
) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        RasterFootprint.model_validate(
            {"geojson": {"type": "Polygon", "coordinates": [ring]}}
        )


def test_raster_footprint_with_too_many_positions_is_rejected() -> None:
    count = MAX_FOOTPRINT_POSITIONS
    ring = [[74.0 + index / count, 36.0] for index in range(count)]
    ring += [[74.5, 37.0], ring[0]]

    with pytest.raises(PydanticValidationError, match="more than"):
        RasterFootprint.model_validate(
            {"geojson": {"type": "Polygon", "coordinates": [ring]}}
        )


def _description(**fields: object) -> RasterAssetDescription:
    return RasterAssetDescription.model_validate(
        {
            "stac_id": "scene-1",
            "footprint": TEST_FOOTPRINT,
            "acquired_at": _moment(JULY_15, DatePrecision.EXACT),
            "platform": "test-platform",
            "assets": (
                StacAsset(key="data", href="rasters/x.tif", media_type=COG_MEDIA_TYPE),
            ),
            **fields,
        }
    )


def test_raster_asset_description_as_fields_keeps_nested_objects() -> None:
    description = _description()

    fields = description.as_fields()

    assert fields["footprint"] is description.footprint
    assert set(fields) == set(RasterAssetDescription.model_fields)


def test_raster_asset_description_without_assets_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _description(assets=())


def test_raster_asset_description_duplicate_band_names_are_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="band names must be unique"):
        _description(bands=(StacBand(name="B04"), StacBand(name="B04")))


def test_raster_asset_description_duplicate_asset_keys_are_rejected() -> None:
    asset = StacAsset(key="data", href="rasters/x.tif", media_type=COG_MEDIA_TYPE)

    with pytest.raises(PydanticValidationError, match="asset keys must be unique"):
        _description(assets=(asset, asset))


# --------------------------------------------------------------------------- #
# Registration inputs                                                         #
# --------------------------------------------------------------------------- #


def test_dataset_details_as_fields_keeps_the_licence_object() -> None:
    details = DatasetDetailsTestFactory.build(factory_use_construct=False)

    fields = details.as_fields()

    assert fields["licence"] is details.licence
    assert fields["code"] == details.code


def test_dataset_version_details_notes_allow_line_breaks() -> None:
    details = DatasetVersionDetails(
        label="v1",
        retrieved_at=_moment(JULY_15, DatePrecision.DAY),
        input_checksum=SHA256,
        notes="First line\nSecond line",
    )

    assert details.notes == "First line\nSecond line"


def test_run_request_triggered_by_defaults_to_system() -> None:
    request = RunRequest(adapter_name="local_csv_temperature")

    assert request.triggered_by is None
