"""Unit tests for ``yakhnama.modules.reports.domain.value_objects``."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportContentFactory
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.modules.reports.domain.value_objects import (
    DESCRIPTION_MAX_LENGTH,
    GPS_ACCURACY_MAX_METRES,
    MEDIA_PER_REPORT_MAX,
    REVISION_NUMBER_MAX,
    Description,
    GpsAccuracy,
    GuessedHazardCode,
    HazardGuess,
    ObservationPoint,
    PlaceHint,
    ReportAttribution,
    ReportContent,
    RevisionNumber,
    TriageFlag,
    TriageResult,
    WithdrawalReason,
)
from yakhnama.shared_kernel.value_objects import Confidence, Coordinates

_DESCRIPTION: TypeAdapter[str] = TypeAdapter(Description)
_REASON: TypeAdapter[str] = TypeAdapter(WithdrawalReason)
_REVISION: TypeAdapter[int] = TypeAdapter(RevisionNumber)
_GUESSED_CODE: TypeAdapter[str] = TypeAdapter(GuessedHazardCode)
_HAZARD_CODE: TypeAdapter[str] = TypeAdapter(HazardCode)
_PLACE_HINT: TypeAdapter[str] = TypeAdapter(PlaceHint)
_PLACE_CODE: TypeAdapter[str] = TypeAdapter(PlaceCode)

_CODE_CANDIDATES = st.from_regex(re.compile(r"[a-z0-9_.\-A-Z]{0,70}"), fullmatch=True)


def _accepts(adapter: TypeAdapter[str], value: str) -> bool:
    try:
        adapter.validate_python(value)
    except PydanticValidationError:
        return False
    return True


# --------------------------------------------------------------------------- #
# GpsAccuracy                                                                 #
# --------------------------------------------------------------------------- #


@given(value=st.floats(min_value=0.0, max_value=GPS_ACCURACY_MAX_METRES))
def test_gps_accuracy_metres_within_bounds_returns_metre_measurement(
    value: float,
) -> None:
    accuracy = GpsAccuracy.metres(value)

    assert accuracy.value == value
    assert accuracy.unit == "metre"


@pytest.mark.parametrize(
    "value", [-0.1, GPS_ACCURACY_MAX_METRES + 0.1, float("nan"), float("inf")]
)
def test_gps_accuracy_metres_out_of_bounds_raises_validation_error(
    value: float,
) -> None:
    with pytest.raises(PydanticValidationError):
        GpsAccuracy.metres(value)


def test_gps_accuracy_with_other_unit_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        GpsAccuracy.model_validate({"value": 5.0, "unit": "kilogram"})


def test_observation_point_without_accuracy_defaults_to_none() -> None:
    point = ObservationPoint(coordinates=Coordinates(longitude=74.6, latitude=36.3))

    assert point.accuracy is None


# --------------------------------------------------------------------------- #
# Numbers and codes                                                           #
# --------------------------------------------------------------------------- #


@given(value=st.integers(min_value=1, max_value=REVISION_NUMBER_MAX))
def test_revision_number_within_bounds_is_accepted(value: int) -> None:
    result = _REVISION.validate_python(value)

    assert result == value


@pytest.mark.parametrize("value", [0, -1, REVISION_NUMBER_MAX + 1])
def test_revision_number_out_of_bounds_raises_validation_error(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        _REVISION.validate_python(value)


@given(candidate=_CODE_CANDIDATES)
def test_guessed_hazard_code_accepts_exactly_what_hazard_code_accepts(
    candidate: str,
) -> None:
    mirrored = _accepts(_GUESSED_CODE, candidate)

    original = _accepts(_HAZARD_CODE, candidate)

    assert mirrored is original


@given(candidate=_CODE_CANDIDATES)
def test_place_hint_accepts_exactly_what_place_code_accepts(candidate: str) -> None:
    mirrored = _accepts(_PLACE_HINT, candidate)

    original = _accepts(_PLACE_CODE, candidate)

    assert mirrored is original


def test_hazard_guess_with_valid_code_keeps_confidence() -> None:
    guess = HazardGuess(hazard_code="glof", confidence=Confidence.LOW)

    assert guess.confidence is Confidence.LOW


# --------------------------------------------------------------------------- #
# Safe text                                                                   #
# --------------------------------------------------------------------------- #


def test_description_with_crlf_normalises_line_breaks_to_lf() -> None:
    raw = "  Water rose.\r\nThen the bridge went.\r  "

    result = _DESCRIPTION.validate_python(raw)

    assert result == "Water rose.\nThen the bridge went."


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * (DESCRIPTION_MAX_LENGTH + 1),
        "a\x00b",
        "a" + chr(0x202E) + "b",
        "a" + chr(0x2066) + "b",
    ],
)
def test_description_empty_long_or_unsafe_raises_validation_error(value: str) -> None:
    with pytest.raises(PydanticValidationError):
        _DESCRIPTION.validate_python(value)


@given(text=st.text(min_size=1, max_size=50).filter(str.strip))
def test_description_validation_is_idempotent(text: str) -> None:
    try:
        once = _DESCRIPTION.validate_python(text)
    except PydanticValidationError:
        return

    twice = _DESCRIPTION.validate_python(once)

    assert twice == once


def test_withdrawal_reason_with_line_break_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _REASON.validate_python("first line\nsecond line")


# --------------------------------------------------------------------------- #
# Triage values                                                               #
# --------------------------------------------------------------------------- #


def test_triage_flag_with_unknown_kind_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        TriageFlag.model_validate(
            {"kind": "blocked", "detail": "x", "confidence": "low"}
        )


def test_triage_result_with_offset_time_normalises_to_utc() -> None:
    evaluated_at = datetime(2026, 9, 1, 17, 0, tzinfo=timezone(timedelta(hours=5)))

    result = TriageResult(evaluated_at=evaluated_at)

    assert result.evaluated_at.tzinfo is UTC
    assert result.evaluated_at == evaluated_at


def test_triage_result_without_flags_has_no_flags_and_no_kinds() -> None:
    result = TriageResult(evaluated_at=datetime(2026, 9, 1, tzinfo=UTC))

    assert result.has_flags is False
    assert result.kinds == ()


def test_triage_result_with_flags_lists_kinds_in_order() -> None:
    flags = (
        TriageFlag(kind="pii_detected", detail="phone", confidence=Confidence.MEDIUM),
        TriageFlag(kind="spam_suspected", detail="short", confidence=Confidence.LOW),
    )

    result = TriageResult(flags=flags, evaluated_at=datetime(2026, 9, 1, tzinfo=UTC))

    assert result.has_flags is True
    assert result.kinds == ("pii_detected", "spam_suspected")


def test_triage_result_with_naive_time_raises_validation_error() -> None:
    naive = datetime(2026, 9, 1)  # noqa: DTZ001  # reason: asserting rejection

    with pytest.raises(PydanticValidationError):
        TriageResult(evaluated_at=naive)


# --------------------------------------------------------------------------- #
# Content and attribution                                                     #
# --------------------------------------------------------------------------- #


def test_report_content_with_duplicate_media_raises_validation_error() -> None:
    media_id = FACTORY_IDS.new_id()

    with pytest.raises(PydanticValidationError):
        ReportContentFactory.build(
            factory_use_construct=False, media_ids=(media_id, media_id)
        )


def test_report_content_with_too_many_media_raises_validation_error() -> None:
    media_ids = tuple(FACTORY_IDS.new_id() for _ in range(MEDIA_PER_REPORT_MAX + 1))

    with pytest.raises(PydanticValidationError):
        ReportContentFactory.build(factory_use_construct=False, media_ids=media_ids)


def test_report_content_round_trip_returns_equal_content() -> None:
    content = ReportContentFactory.build(factory_use_construct=False)

    restored = ReportContent.model_validate(content.model_dump())

    assert restored == content


def test_report_attribution_without_organization_defaults_to_none() -> None:
    attribution = ReportAttribution(
        reporter_id=FACTORY_IDS.new_id(), source_id=FACTORY_IDS.new_id()
    )

    assert attribution.organization_id is None


def test_report_content_assignment_raises_validation_error() -> None:
    content = ReportContentFactory.build(factory_use_construct=False)

    with pytest.raises(PydanticValidationError):
        content.description = "changed"  # type: ignore[misc]  # reason: frozen check
