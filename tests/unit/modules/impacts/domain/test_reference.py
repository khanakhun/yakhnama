"""Unit tests for ``yakhnama.modules.impacts.domain.reference``."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.reference import (
    ImpactMetricReferenceEntry,
    ImpactMetricReferenceFile,
)
from yakhnama.modules.impacts.domain.value_objects import MetricStatus, ValueKind
from yakhnama.shared_kernel.value_objects import LocalizedText


def _entry_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "code": "deaths",
        "labels": {"en": "Deaths"},
        "category": "human",
        "value_kind": "count",
        "unit": "count",
        "aggregation": "sum",
        "source": "proposed",
    }
    fields.update(overrides)
    return fields


def _file_fields(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "data_version": "2026.1",
        "source": "proposed",
        "licence": "CC-BY-4.0",
        "entries": list(entries),
    }


def test_reference_entry_bare_label_mapping_becomes_localized_text() -> None:
    entry = ImpactMetricReferenceEntry.model_validate(_entry_fields())

    assert entry.labels == LocalizedText(texts={"en": "Deaths"})


def test_reference_entry_wrapped_label_mapping_is_accepted() -> None:
    fields = _entry_fields(labels={"texts": {"en": "Deaths"}})

    entry = ImpactMetricReferenceEntry.model_validate(fields)

    assert (entry.labels.get("en"), entry.status) == ("Deaths", MetricStatus.ACTIVE)


def test_reference_entry_monetary_with_currency_is_accepted() -> None:
    fields = _entry_fields(
        code="direct_economic_loss",
        category="economic",
        value_kind="monetary",
        unit=None,
        currency="PKR",
        notes="Nominal PKR; the claim records the price year.",
    )

    entry = ImpactMetricReferenceEntry.model_validate(fields)

    assert (entry.value_kind, entry.currency) == (ValueKind.MONETARY, "PKR")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"unit": "metre"}, "must use unit 'count'"),
        ({"value_kind": "monetary"}, "needs a currency"),
        ({"labels": {"ur": "اموات"}}, "English"),
        ({"status": "retired"}, "required exactly when retired"),
    ],
)
def test_reference_entry_inconsistent_definition_raises_validation_error(
    overrides: dict[str, object], fragment: str
) -> None:
    fields = _entry_fields(**overrides)

    with pytest.raises(PydanticValidationError, match=f"'deaths': .*{fragment}"):
        ImpactMetricReferenceEntry.model_validate(fields)


@pytest.mark.parametrize(
    "overrides",
    [{"source": ""}, {"notes": ""}, {"unknown": 1}, {"sendai": {"code": "A1"}}],
)
def test_reference_entry_malformed_field_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    fields = _entry_fields(**overrides)

    with pytest.raises(PydanticValidationError):
        ImpactMetricReferenceEntry.model_validate(fields)


def test_reference_file_json_round_trip_is_lossless() -> None:
    reference = ImpactMetricReferenceFile.model_validate(
        _file_fields(
            _entry_fields(
                sendai={"code": "A-1"},
                desinventar={"name": "DEATHS"},
                description={"en": "People confirmed dead."},
            ),
            _entry_fields(
                code="missing_people",
                status="retired",
                retirement={"explanation": "merged", "replaced_by": "deaths"},
            ),
        )
    )

    restored = ImpactMetricReferenceFile.model_validate(
        reference.model_dump(mode="json")
    )

    assert restored == reference


def test_reference_file_duplicate_codes_raise_validation_error() -> None:
    fields = _file_fields(
        _entry_fields(),
        _entry_fields(status="retired", retirement={"explanation": "x"}),
    )

    with pytest.raises(
        PydanticValidationError, match=r"duplicate metric codes: \['deaths'\]"
    ):
        ImpactMetricReferenceFile.model_validate(fields)


@given(codes=st.lists(st.sampled_from(["deaths", "missing_people", "injured"])))
def test_reference_file_accepts_exactly_unique_codes(codes: list[str]) -> None:
    fields = _file_fields(*(_entry_fields(code=code) for code in codes))
    is_unique = len(set(codes)) == len(codes)

    try:
        ImpactMetricReferenceFile.model_validate(fields)
    except PydanticValidationError:
        was_accepted = False
    else:
        was_accepted = True

    assert was_accepted is is_unique


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"schema_version": 0},
        {"data_version": ""},
        {"data_version": "2026 1"},
        {"licence": ""},
    ],
)
def test_reference_file_malformed_header_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    fields = {**_file_fields(), **overrides}

    with pytest.raises(PydanticValidationError):
        ImpactMetricReferenceFile.model_validate(fields)
