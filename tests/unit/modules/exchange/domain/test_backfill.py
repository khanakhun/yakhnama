"""Unit tests for ``yakhnama.modules.exchange.domain.backfill``."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import assume, given, reject
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.factories.exchange import imported_event_draft
from yakhnama.modules.events.domain.value_objects import (
    SUMMARY_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    TITLE_MIN_LENGTH,
)
from yakhnama.modules.events.public import EventGeometry, EventPeriod
from yakhnama.modules.exchange.domain import backfill
from yakhnama.modules.exchange.domain.backfill import (
    BACKFILL_COLUMNS,
    CLAIM_COLUMNS,
    EVENT_COLUMNS,
    IMPORT_CITATION_MAX_LENGTH,
    IMPORT_NOTE_MAX_LENGTH,
    IMPORT_SUMMARY_MAX_LENGTH,
    IMPORT_TITLE_MAX_LENGTH,
    IMPORT_TITLE_MIN_LENGTH,
    IMPORT_URL_MAX_LENGTH,
    MAX_CLAIMS_PER_ROW,
    REQUIRED_COLUMNS,
    ImportedClaimDraft,
    ImportedEventDraft,
    ImportedSourceDraft,
    ImportSourceUrl,
    claim_column,
    read_flat_rows,
    require_backfill_header,
)
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import (
    ISSUE_MESSAGE_MAX_LENGTH,
    RowIssue,
)
from yakhnama.modules.impacts.domain.value_objects import NOTE_MAX_LENGTH
from yakhnama.modules.impacts.public import (
    CountValue,
    MeasurementValue,
    MonetaryValue,
)
from yakhnama.modules.provenance.domain.value_objects import (
    CITATION_MAX_LENGTH,
    SOURCE_URL_MAX_LENGTH,
)
from yakhnama.modules.provenance.public import Licence
from yakhnama.shared_kernel.text import is_forbidden_character
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

# Synthetic values only: no real event, place or source.
VALID_ROW: dict[str, str] = {
    "title": "Synthetic GLOF, upper valley",
    "hazard_type": "glof",
    "started_at": "2022-07-15T06:00:00Z",
    "started_at_precision": "day",
    "longitude": "74.5",
    "latitude": "36.5",
    "place_codes": "pk.gb.test ; pk.gb.test.village",
    "source_citation": "Synthetic source, 2022",
    "source_url": "https://example.org/report/1",
    "source_licence": "CC-BY-4.0",
    claim_column(1, "metric_code"): "houses_destroyed",
    claim_column(1, "value_kind"): "count",
    claim_column(1, "value"): "12",
    claim_column(1, "confidence"): "medium",
    claim_column(1, "claimed_at"): "2022-07-16T00:00:00+05:00",
    claim_column(1, "claimed_at_precision"): "day",
}


def _row(**changes: str) -> dict[str, str]:
    return VALID_ROW | changes


def _issues(cells: dict[str, str], row_number: int = 4) -> tuple[RowIssue, ...]:
    outcome = ImportedEventDraft.from_flat_row(row_number, cells)
    assert isinstance(outcome, tuple), "expected issues, got a draft"
    return outcome


def _fields(cells: dict[str, str]) -> list[str | None]:
    return [issue.field for issue in _issues(cells)]


def _draft(cells: dict[str, str]) -> ImportedEventDraft:
    outcome = ImportedEventDraft.from_flat_row(1, cells)
    assert isinstance(outcome, ImportedEventDraft), outcome
    return outcome


def _claim(slot: int, **fields: str) -> dict[str, str]:
    base = {
        "metric_code": "test_metric",
        "confidence": "low",
        "claimed_at": "2022-07-16T00:00:00Z",
        "claimed_at_precision": "day",
    }
    return {claim_column(slot, name): text for name, text in (base | fields).items()}


# --------------------------------------------------------------------------- #
# Contract                                                                    #
# --------------------------------------------------------------------------- #


def test_backfill_columns_are_event_columns_then_five_claim_slots() -> None:
    assert BACKFILL_COLUMNS[: len(EVENT_COLUMNS)] == EVENT_COLUMNS
    assert len(CLAIM_COLUMNS) == MAX_CLAIMS_PER_ROW * 10
    assert len(set(BACKFILL_COLUMNS)) == len(BACKFILL_COLUMNS)
    assert set(REQUIRED_COLUMNS) <= set(EVENT_COLUMNS)


def test_claim_column_names_slot_and_field() -> None:
    assert claim_column(3, "value_kind") == "claim_3_value_kind"


def test_mirrored_text_bounds_equal_the_owning_modules() -> None:
    assert (IMPORT_TITLE_MIN_LENGTH, IMPORT_TITLE_MAX_LENGTH) == (
        TITLE_MIN_LENGTH,
        TITLE_MAX_LENGTH,
    )
    assert IMPORT_SUMMARY_MAX_LENGTH == SUMMARY_MAX_LENGTH
    assert IMPORT_CITATION_MAX_LENGTH == CITATION_MAX_LENGTH
    assert IMPORT_URL_MAX_LENGTH == SOURCE_URL_MAX_LENGTH
    assert IMPORT_NOTE_MAX_LENGTH == NOTE_MAX_LENGTH


def test_require_backfill_header_with_contract_columns_passes() -> None:
    require_backfill_header(list(BACKFILL_COLUMNS))
    require_backfill_header(list(REQUIRED_COLUMNS))


def test_require_backfill_header_with_problems_raises_contract_error() -> None:
    columns = ["title", "title", "hazard_type", "Unknown Column", "another"]

    with pytest.raises(ImportContractError) as caught:
        require_backfill_header(columns)

    assert caught.value.details == {
        "missing_columns": ("started_at", "started_at_precision", "source_citation"),
        "unknown_column_count": 2,
        "duplicated_columns": ("title",),
    }


def test_require_backfill_header_repeated_unknown_column_is_counted_once() -> None:
    with pytest.raises(ImportContractError) as caught:
        require_backfill_header([*REQUIRED_COLUMNS, "x", "x", "x"])

    assert caught.value.details["unknown_column_count"] == 1
    assert caught.value.details["duplicated_columns"] == ()


def test_import_source_url_rules_match_provenance() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(ImportSourceUrl)

    assert adapter.validate_python("http://example.org:8080/a") == (
        "http://example.org:8080/a"
    )
    for url, match in [
        ("ftp://example.org/a", "http or https"),
        ("https://user:pw@example.org/", "credentials"),
        ("https:///path", "host"),
        ("https://example.org:99999/", "port"),
        ("https://example.org/é", "ASCII"),
    ]:
        with pytest.raises(PydanticValidationError, match=match):
            adapter.validate_python(url)


# --------------------------------------------------------------------------- #
# Valid rows                                                                  #
# --------------------------------------------------------------------------- #


def test_from_flat_row_valid_row_returns_draft_with_every_value() -> None:
    draft = _draft(VALID_ROW)

    assert draft.title == "Synthetic GLOF, upper valley"
    assert draft.hazard_type == "glof"
    assert draft.period.started_at == DateWithPrecision(
        value=datetime(2022, 7, 15, 6, tzinfo=UTC), precision=DatePrecision.DAY
    )
    assert draft.period.ended_at is None
    assert draft.geometry == EventGeometry.from_coordinates(
        Coordinates(longitude=74.5, latitude=36.5)
    )
    assert draft.place_codes == ("pk.gb.test", "pk.gb.test.village")
    assert draft.source == ImportedSourceDraft(
        citation="Synthetic source, 2022",
        url="https://example.org/report/1",
        licence=Licence.spdx("CC-BY-4.0"),
    )
    (claim,) = draft.claims
    assert claim.value == CountValue(count=12)
    assert claim.claimed_at.value == datetime(2022, 7, 15, 19, tzinfo=UTC)


def test_from_flat_row_ignores_surrounding_whitespace_and_absent_columns() -> None:
    cells = {
        "title": "  Synthetic event  ",
        "hazard_type": " glof ",
        "started_at": " 2022-07-15T06:00Z ",
        "started_at_precision": "year",
        "place_codes": "pk.gb.test",
        "source_citation": "Synthetic source",
    }

    draft = _draft(cells)

    assert draft.title == "Synthetic event"
    assert draft.geometry is None
    assert draft.claims == ()


def test_from_flat_row_reads_every_claim_kind() -> None:
    cells = (
        _row()
        | _claim(2, value_kind="measurement", value="1.5e3", unit="square_metre")
        | _claim(
            5,
            value_kind="monetary",
            value="1000.50",
            currency="PKR",
            price_year="2022",
            note="Synthetic\nnote",
        )
        | {claim_column(1, "unit"): "count"}
    )

    draft = _draft(cells)

    assert [claim.value for claim in draft.claims] == [
        CountValue(count=12),
        MeasurementValue(measurement=Measurement(value=1500.0, unit="square_metre")),
        MonetaryValue(amount=Decimal("1000.50"), currency="PKR", price_year=2022),
    ]
    assert draft.claims[2].note == "Synthetic\nnote"


def test_from_flat_row_reads_polygon_geometry_and_period_end() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [[[74.0, 36.0], [74.1, 36.0], [74.1, 36.1], [74.0, 36.0]]],
    }
    cells = _row(
        longitude="",
        latitude="",
        geometry=json.dumps(polygon),
        ended_at="2022-08-01T00:00:00Z",
        ended_at_precision="month",
    )

    draft = _draft(cells)

    assert draft.geometry is not None
    assert draft.geometry.geometry_type == "Polygon"
    assert draft.period.ended_at is not None


def test_from_flat_row_reads_custom_licence_text() -> None:
    cells = _row(source_licence="", source_licence_text="Shared for research use")

    assert _draft(cells).source.licence == Licence.custom("Shared for research use")


def test_from_flat_row_without_licence_has_none() -> None:
    assert _draft(_row(source_licence="")).source.licence is None


def test_read_flat_rows_numbers_rows_from_one_and_splits_outcomes() -> None:
    rows = [VALID_ROW, _row(title=""), VALID_ROW]

    drafts, issues, count = read_flat_rows(rows)

    assert count == 3  # reason: three arranged rows
    assert len(drafts) == 2  # reason: two valid rows
    assert [(issue.row_number, issue.field) for issue in issues] == [(2, "title")]


def test_read_flat_rows_without_rows_reads_nothing() -> None:
    assert read_flat_rows([]) == ((), (), 0)


# --------------------------------------------------------------------------- #
# Malformed columns                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("changes", "expected_fields"),
    [
        ({"title": ""}, ["title"]),
        ({"title": "ab"}, ["title"]),
        ({"title": "x" * 10_001}, ["title"]),
        ({"hazard_type": "GLOF"}, ["hazard_type"]),
        ({"started_at": ""}, ["started_at"]),
        ({"started_at": "", "started_at_precision": ""}, ["started_at"]),
        ({"started_at_precision": ""}, ["started_at_precision"]),
        ({"started_at_precision": "decade"}, ["started_at_precision"]),
        ({"started_at": "2" * 10_001}, ["started_at"]),
        ({"started_at_precision": "d" * 10_001}, ["started_at_precision"]),
        ({"started_at": "2022"}, ["started_at"]),
        ({"started_at": "1600000000"}, ["started_at"]),
        ({"started_at": "2022-07-15T06:00:00"}, ["started_at"]),
        ({"started_at": "2022-13-45T06:00:00Z"}, ["started_at"]),
        ({"ended_at": "2022-07-01T00:00:00Z"}, ["ended_at_precision"]),
        ({"ended_at_precision": "day"}, ["ended_at"]),
        (
            {"ended_at": "2022-06-01T00:00:00Z", "ended_at_precision": "month"},
            ["ended_at"],
        ),
        ({"longitude": ""}, ["longitude"]),
        ({"latitude": "91"}, ["latitude"]),
        ({"longitude": "nan"}, ["longitude"]),
        ({"longitude": "1_0"}, ["longitude"]),
        ({"geometry": '{"type": "Point", "coordinates": [1, 2]}'}, ["geometry"]),
        ({"place_codes": "pk.gb.test;;x"}, ["place_codes"]),
        ({"place_codes": ";".join(f"pk.p{i}" for i in range(21))}, ["place_codes"]),
        ({"place_codes": "pk.gb.test;pk.gb.test"}, ["place_codes"]),
        ({"summary": "x" * 2001}, ["summary"]),
        ({"source_citation": ""}, ["source_citation"]),
        ({"source_citation": "a\u202eb"}, ["source_citation"]),
        ({"source_url": "javascript:alert(1)"}, ["source_url"]),
        ({"source_licence": "not an id"}, ["source_licence"]),
        (
            {"source_licence": "", "source_licence_text": "x" * 501},
            ["source_licence_text"],
        ),
        ({"source_licence_text": "Custom terms"}, ["source_licence_text"]),
        ({"unexpected": "1"}, ["unexpected"]),
        ({"Bad Column!": "1"}, [None]),
    ],
)
def test_from_flat_row_malformed_column_reports_that_column(
    changes: dict[str, str], expected_fields: list[str | None]
) -> None:
    assert _fields(_row(**changes)) == expected_fields


def test_from_flat_row_issue_carries_row_number_and_never_quotes_value() -> None:
    private_value = "Person Name 0300-1234567"

    issues = _issues(_row(hazard_type=private_value), row_number=9)

    assert [issue.row_number for issue in issues] == [9]
    assert private_value not in issues[0].message


def test_from_flat_row_without_location_reports_row_level_issue() -> None:
    cells = _row(longitude="", latitude="", place_codes="")

    issues = _issues(cells)

    assert [issue.field for issue in issues] == [None]
    assert "place code" in issues[0].message


def test_from_flat_row_point_and_geometry_together_reports_geometry() -> None:
    cells = _row(geometry='{"type": "Point", "coordinates": [74.5, 36.5]}')

    assert _fields(cells) == ["geometry"]


@pytest.mark.parametrize(
    "geometry",
    ["not json", "[1, 2]", "[" * 100_000 + "]" * 100_000, "x" * 1_000_001],
)
def test_from_flat_row_unreadable_geometry_reports_geometry(geometry: str) -> None:
    cells = _row(longitude="", latitude="", place_codes="", geometry=geometry)

    assert _fields(cells) == ["geometry"]


@pytest.mark.parametrize(
    ("claim", "expected_fields"),
    [
        ({"value_kind": "count", "value": "1.5"}, ["claim_2_value"]),
        ({"value_kind": "count", "value": "-1"}, ["claim_2_value"]),
        ({"value_kind": "count", "value": "1", "unit": "metre"}, ["claim_2_unit"]),
        (
            {"value_kind": "count", "value": "1", "currency": "PKR"},
            ["claim_2_currency"],
        ),
        (
            {"value_kind": "count", "value": "1", "price_year": "2022"},
            ["claim_2_price_year"],
        ),
        ({"value_kind": "count"}, ["claim_2_value"]),
        ({"value_kind": "ratio", "value": "1"}, ["claim_2_value_kind"]),
        ({"value": "1"}, ["claim_2_value_kind"]),
        ({"value_kind": "measurement", "value": "3"}, ["claim_2_unit"]),
        (
            {"value_kind": "measurement", "value": "3", "unit": "parsec"},
            ["claim_2_unit"],
        ),
        (
            {"value_kind": "measurement", "value": "3", "unit": "count"},
            ["claim_2_value"],
        ),
        (
            {"value_kind": "measurement", "value": "-3", "unit": "metre"},
            ["claim_2_value"],
        ),
        (
            {"value_kind": "measurement", "value": "1e999", "unit": "metre"},
            ["claim_2_value"],
        ),
        (
            {
                "value_kind": "measurement",
                "value": "3",
                "unit": "metre",
                "currency": "PKR",
                "price_year": "2022",
            },
            ["claim_2_currency", "claim_2_price_year"],
        ),
        (
            {"value_kind": "monetary", "value": "10", "price_year": "2022"},
            ["claim_2_currency"],
        ),
        (
            {
                "value_kind": "monetary",
                "value": "10",
                "currency": "pkr",
                "price_year": "2022",
            },
            ["claim_2_currency"],
        ),
        (
            {
                "value_kind": "monetary",
                "value": "10",
                "currency": "PKR",
                "price_year": "1800",
            },
            ["claim_2_price_year"],
        ),
        (
            {
                "value_kind": "monetary",
                "value": "10.001",
                "currency": "PKR",
                "price_year": "2022",
            },
            ["claim_2_value"],
        ),
        (
            {
                "value_kind": "monetary",
                "value": "10",
                "currency": "PKR",
                "price_year": "2022",
                "unit": "count",
            },
            ["claim_2_unit"],
        ),
        (
            {
                "value_kind": "monetary",
                "value": "ten",
                "currency": "PKR",
                "price_year": "y",
            },
            ["claim_2_value", "claim_2_price_year"],
        ),
        (
            {"value_kind": "count", "value": "1", "metric_code": "Deaths"},
            ["claim_2_metric_code"],
        ),
        (
            {"value_kind": "count", "value": "1", "confidence": "certain"},
            ["claim_2_confidence"],
        ),
        (
            {"value_kind": "count", "value": "1", "claimed_at": ""},
            ["claim_2_claimed_at"],
        ),
        (
            {"value_kind": "count", "value": "1", "note": "a\x00b"},
            ["claim_2_note"],
        ),
        (
            {"value_kind": "count", "value": "1" * 65},
            ["claim_2_value"],
        ),
    ],
)
def test_from_flat_row_malformed_claim_reports_that_claim_column(
    claim: dict[str, str], expected_fields: list[str]
) -> None:
    cells = _row() | _claim(2, **claim)

    assert _fields(cells) == expected_fields


def test_from_flat_row_blank_claim_slot_is_skipped() -> None:
    cells = _row() | {claim_column(3, "metric_code"): "   "}

    assert len(_draft(cells).claims) == 1


def test_from_flat_row_reports_every_faulty_column_at_once() -> None:
    cells = _row(title="", hazard_type="X", source_citation="") | _claim(
        2, value_kind="count", value="x"
    )

    assert _fields(cells) == [
        "title",
        "hazard_type",
        "source_citation",
        "claim_2_value",
    ]


def test_issue_message_cleans_control_characters_and_never_empties() -> None:
    # The helper is private; it is the guard that keeps from_flat_row from raising.
    assert backfill._issue_message("bad\x00value\n") == "bad value"
    assert backfill._issue_message("\x00") == "the value is invalid"
    assert len(backfill._issue_message("x" * 900)) == ISSUE_MESSAGE_MAX_LENGTH


# --------------------------------------------------------------------------- #
# Draft invariants                                                            #
# --------------------------------------------------------------------------- #


def test_imported_event_draft_without_location_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="geometry or a place code"):
        imported_event_draft(geometry=None, place_codes=())


def test_imported_event_draft_with_repeated_place_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="distinct"):
        imported_event_draft(place_codes=("pk.gb.test", "pk.gb.test"))


def test_imported_event_draft_with_six_claims_raises_validation_error() -> None:
    claims = imported_event_draft().claims * 6

    with pytest.raises(PydanticValidationError):
        imported_event_draft(claims=claims)


def test_imported_event_draft_to_flat_row_writes_every_column_in_order() -> None:
    cells = imported_event_draft().to_flat_row()

    assert tuple(cells) == BACKFILL_COLUMNS
    assert cells["longitude"] == "74.5"
    assert cells[claim_column(1, "unit")] == "count"
    assert cells[claim_column(2, "metric_code")] == ""


# --------------------------------------------------------------------------- #
# Properties                                                                  #
# --------------------------------------------------------------------------- #

SAFE_CHARACTERS = st.characters(codec="utf-8").filter(
    lambda character: not is_forbidden_character(character)
)
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
PLACE_CODES = st.lists(
    st.sampled_from(["pk.gb.test", "pk.gb.test.a", "pk.gb.test.b", "pk.test-c"]),
    unique=True,
    max_size=4,
)
MEASUREMENT_UNITS = st.sampled_from(
    [unit.value for unit in SiUnit if unit is not SiUnit.COUNT]
)
CLAIM_VALUES = st.one_of(
    st.builds(CountValue, count=st.integers(0, 10**12)),
    st.builds(
        MeasurementValue,
        measurement=st.builds(
            Measurement,
            value=st.floats(0, 1e12, allow_nan=False),
            unit=MEASUREMENT_UNITS,
        ),
    ),
    st.builds(
        MonetaryValue,
        amount=st.decimals(min_value=0, max_value=10**12, places=2),
        currency=st.sampled_from(["PKR", "USD"]),
        price_year=st.integers(1900, 2100),
    ),
)


def _text(max_size: int, *, multiline: bool = False) -> st.SearchStrategy[str]:
    alphabet = st.one_of(SAFE_CHARACTERS, st.sampled_from([",", '"', "ی", "خ", " "]))
    if multiline:
        alphabet = st.one_of(alphabet, st.just("\n"))
    return st.text(alphabet=alphabet, min_size=1, max_size=max_size).filter(
        lambda text: text.strip() != ""
    )


@st.composite
def drafts(draw: st.DrawFn) -> ImportedEventDraft:
    """Draw a valid backfill draft, including titles with commas and non-Latin text."""
    moments = sorted(
        draw(st.lists(MOMENTS, min_size=1, max_size=2)),
        key=lambda moment: moment.value,
    )
    geometry = draw(
        st.none()
        | st.builds(
            lambda lon, lat: EventGeometry.from_coordinates(
                Coordinates(longitude=lon, latitude=lat)
            ),
            st.floats(-180, 180),
            st.floats(-90, 90),
        )
        | st.builds(
            lambda lon, lat: EventGeometry.model_validate(
                {
                    "geojson": {
                        "type": "Polygon",
                        "coordinates": [
                            [[lon, lat], [lon + 0.5, lat], [lon, lat + 0.5], [lon, lat]]
                        ],
                    }
                }
            ),
            st.floats(-179, 179),
            st.floats(-89, 89),
        )
    )
    place_codes = draw(PLACE_CODES)
    assume(geometry is not None or place_codes)
    claims = draw(
        st.lists(
            st.builds(
                ImportedClaimDraft,
                metric_code=st.from_regex(r"[a-z][a-z0-9_]{1,20}", fullmatch=True),
                value=CLAIM_VALUES,
                confidence=st.sampled_from(Confidence),
                claimed_at=MOMENTS,
                note=st.none() | _text(40, multiline=True),
            ),
            max_size=MAX_CLAIMS_PER_ROW,
        )
    )
    licence = draw(
        st.none()
        | st.just(Licence.spdx("CC-BY-4.0"))
        | st.just(Licence.custom("Synthetic terms"))
    )
    try:
        return ImportedEventDraft(
            title=draw(_text(60)),
            hazard_type=draw(st.from_regex(r"[a-z][a-z0-9_]{1,20}", fullmatch=True)),
            period=EventPeriod(
                started_at=moments[0],
                ended_at=moments[1] if len(moments) > 1 else None,
            ),
            geometry=geometry,
            place_codes=tuple(place_codes),
            summary=draw(st.none() | _text(80, multiline=True)),
            source=ImportedSourceDraft(
                citation=draw(_text(60)),
                url=draw(st.none() | st.just("https://example.org/source")),
                licence=licence,
            ),
            claims=tuple(claims),
        )
    except PydanticValidationError:
        # Text that is blank or too short after normalisation is not a draft.
        reject()


@given(draft=drafts(), row_number=st.integers(1, 10_000))
def test_from_flat_row_of_to_flat_row_returns_equal_draft(
    draft: ImportedEventDraft, row_number: int
) -> None:
    cells = draft.to_flat_row()

    restored = ImportedEventDraft.from_flat_row(row_number, cells)

    assert restored == draft


CELL_VALUES = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "",
            "glof",
            "2022-07-15T06:00:00Z",
            "day",
            "count",
            "measurement",
            "monetary",
            "12",
            "-0.5",
            "1e400",
            "nan",
            "PKR",
            "{}",
            '{"type": "Point", "coordinates": [1, 2, 3]}',
            "[[[[",
        ]
    ),
)


@given(
    cells=st.dictionaries(
        keys=st.one_of(st.sampled_from(BACKFILL_COLUMNS), st.text(max_size=12)),
        values=CELL_VALUES,
        max_size=40,
    ),
    row_number=st.integers(1, 10_000),
)
def test_from_flat_row_arbitrary_cells_never_raise(
    cells: dict[str, str], row_number: int
) -> None:
    outcome = ImportedEventDraft.from_flat_row(row_number, cells)

    if isinstance(outcome, tuple):
        assert outcome
        assert all(issue.row_number == row_number for issue in outcome)
        assert all(
            issue.field is None
            or issue.field in BACKFILL_COLUMNS
            or issue.field in cells
            for issue in outcome
        )
    else:
        assert isinstance(outcome, ImportedEventDraft)
