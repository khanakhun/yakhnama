"""Unit tests for ``yakhnama.modules.impacts.domain.value_objects``."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.value_objects import (
    COUNT_UNIT,
    DEFAULT_CURRENCY,
    DesInventarField,
    ImpactMetricRef,
    MetricCategory,
    MetricStatus,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
    default_aggregation,
    definition_problems,
    is_valid_metric_value,
)
from yakhnama.shared_kernel.value_objects import KNOWN_UNITS

VALID_CODES = st.from_regex(r"\A[a-z][a-z0-9_]{1,63}\Z")
NON_COUNT_UNITS = sorted(KNOWN_UNITS - {COUNT_UNIT})
CURRENCIES = st.from_regex(r"\A[A-Z]{3}\Z")
ANY_UNIT = st.one_of(st.none(), st.sampled_from(sorted(KNOWN_UNITS)), st.text())
ANY_CURRENCY = st.one_of(st.none(), CURRENCIES)


@given(code=VALID_CODES)
def test_impact_metric_ref_valid_code_round_trips(code: str) -> None:
    ref = ImpactMetricRef(code=code)

    restored = ImpactMetricRef.model_validate_json(ref.model_dump_json())

    assert restored == ref


@pytest.mark.parametrize(
    "code", ["", "a", "Deaths", "1deaths", "people-dead", "x" * 65, "deaths "]
)
def test_impact_metric_ref_malformed_code_raises_validation_error(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        ImpactMetricRef(code=code)


@given(
    target=st.sampled_from("ABCDEFG"),
    number=st.integers(min_value=0, max_value=99),
    suffix=st.sampled_from(["", "a", "z"]),
)
def test_sendai_indicator_well_formed_code_is_accepted(
    target: str, number: int, suffix: str
) -> None:
    code = f"{target}-{number}{suffix}"

    indicator = SendaiIndicator(code=code)

    assert indicator.code == code


@pytest.mark.parametrize("code", ["H-1", "A1", "A-", "A-100", "a-1", "A-1A", "A-1ab"])
def test_sendai_indicator_malformed_code_raises_validation_error(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        SendaiIndicator(code=code)


@pytest.mark.parametrize("name", ["DEATHS", "x", "Houses destroyed", "y" * 64])
def test_desinventar_field_valid_name_is_accepted(name: str) -> None:
    field = DesInventarField(name=name)

    assert field.name == name


@pytest.mark.parametrize("name", ["", " DEATHS", "DEATHS ", "y" * 65])
def test_desinventar_field_invalid_name_raises_validation_error(name: str) -> None:
    with pytest.raises(PydanticValidationError):
        DesInventarField(name=name)


def test_retirement_reason_defaults_to_no_successor() -> None:
    reason = RetirementReason(explanation="unit changed")

    assert reason.replaced_by is None


@pytest.mark.parametrize(
    "fields",
    [
        {"explanation": ""},
        {"explanation": "x" * 1001},
        {"explanation": "ok", "replaced_by": "Bad"},
    ],
)
def test_retirement_reason_invalid_fields_raise_validation_error(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError):
        RetirementReason.model_validate(fields)


def test_enums_expose_documented_values() -> None:
    values = (
        {category.value for category in MetricCategory},
        {kind.value for kind in ValueKind},
        {status.value for status in MetricStatus},
    )

    assert values == (
        {
            "human",
            "economic",
            "infrastructure",
            "housing",
            "agriculture",
            "environment",
            "services",
        },
        {"count", "measurement", "monetary"},
        {"active", "retired"},
    )


def test_default_currency_is_pkr() -> None:
    assert DEFAULT_CURRENCY == "PKR"


def test_definition_problems_count_with_count_unit_returns_none() -> None:
    problems = definition_problems(ValueKind.COUNT, COUNT_UNIT, None)

    assert problems == ()


@pytest.mark.parametrize("unit", [None, "metre", "furlong"])
def test_definition_problems_count_without_count_unit_returns_problem(
    unit: str | None,
) -> None:
    problems = definition_problems(ValueKind.COUNT, unit, None)

    assert problems == (f"a count metric must use unit 'count', not {unit!r}",)


@pytest.mark.parametrize("unit", NON_COUNT_UNITS)
def test_definition_problems_measurement_with_si_unit_returns_none(unit: str) -> None:
    problems = definition_problems(ValueKind.MEASUREMENT, unit, None)

    assert problems == ()


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (None, "a measurement metric needs a unit"),
        (COUNT_UNIT, "a measurement metric must not use unit 'count'"),
        ("furlong", "unit 'furlong' is not a registered SI unit"),
    ],
)
def test_definition_problems_measurement_bad_unit_returns_problem(
    unit: str | None, expected: str
) -> None:
    problems = definition_problems(ValueKind.MEASUREMENT, unit, None)

    assert problems == (expected,)


def test_definition_problems_monetary_with_currency_returns_none() -> None:
    problems = definition_problems(ValueKind.MONETARY, None, DEFAULT_CURRENCY)

    assert problems == ()


def test_definition_problems_monetary_with_unit_and_no_currency_returns_both() -> None:
    problems = definition_problems(ValueKind.MONETARY, COUNT_UNIT, None)

    assert problems == (
        "a monetary metric carries a currency, not a unit",
        "a monetary metric needs a currency",
    )


@pytest.mark.parametrize(
    ("value_kind", "unit"),
    [(ValueKind.COUNT, COUNT_UNIT), (ValueKind.MEASUREMENT, "metre")],
)
def test_definition_problems_currency_on_non_monetary_returns_problem(
    value_kind: ValueKind, unit: str
) -> None:
    problems = definition_problems(value_kind, unit, "USD")

    assert problems == (f"a {value_kind.value} metric must not carry a currency",)


@given(value_kind=st.sampled_from(ValueKind), unit=ANY_UNIT, currency=ANY_CURRENCY)
def test_definition_problems_consistency_rule_matches_specification(
    value_kind: ValueKind, unit: str | None, currency: str | None
) -> None:
    expected = {
        ValueKind.COUNT: unit == COUNT_UNIT and currency is None,
        ValueKind.MEASUREMENT: unit in KNOWN_UNITS
        and unit != COUNT_UNIT
        and currency is None,
        ValueKind.MONETARY: unit is None and currency is not None,
    }[value_kind]

    is_consistent = definition_problems(value_kind, unit, currency) == ()

    assert is_consistent is expected


@given(value=st.integers(min_value=0, max_value=10**12))
def test_is_valid_metric_value_whole_count_returns_true(value: int) -> None:
    assert is_valid_metric_value(ValueKind.COUNT, float(value))


@given(
    value=st.floats(min_value=0, max_value=1e12).filter(
        lambda number: not number.is_integer()
    )
)
def test_is_valid_metric_value_fractional_count_returns_false(value: float) -> None:
    assert not is_valid_metric_value(ValueKind.COUNT, value)


@given(
    value_kind=st.sampled_from([ValueKind.MEASUREMENT, ValueKind.MONETARY]),
    value=st.floats(min_value=0, allow_infinity=False, allow_nan=False),
)
def test_is_valid_metric_value_non_negative_finite_non_count_returns_true(
    value_kind: ValueKind, value: float
) -> None:
    assert is_valid_metric_value(value_kind, value)


@given(
    value_kind=st.sampled_from(ValueKind),
    value=st.one_of(
        st.floats(max_value=-1e-300, allow_infinity=False, allow_nan=False),
        st.sampled_from([math.inf, -math.inf, math.nan]),
    ),
)
def test_is_valid_metric_value_negative_or_non_finite_returns_false(
    value_kind: ValueKind, value: float
) -> None:
    assert not is_valid_metric_value(value_kind, value)


@pytest.mark.parametrize(
    ("value_kind", "expected"),
    [
        (ValueKind.COUNT, "sum"),
        (ValueKind.MEASUREMENT, "max"),
        (ValueKind.MONETARY, "sum"),
    ],
)
def test_default_aggregation_per_kind_returns_proposed_default(
    value_kind: ValueKind, expected: str
) -> None:
    assert default_aggregation(value_kind) == expected
