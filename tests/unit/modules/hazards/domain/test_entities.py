"""Unit tests for ``yakhnama.modules.hazards.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.unit.modules.hazards.domain.sample_hazard_types import (
    CREATED_AT,
    hazard_type,
    ids,
    labels,
    stepping_clock,
)
from yakhnama.modules.hazards.domain.entities import (
    HazardTaxonomy,
    HazardType,
    find_parent_cycle,
)
from yakhnama.modules.hazards.domain.errors import (
    HazardCodeAlreadyUsedError,
    HazardTypeNotFoundError,
    HazardTypeNotRetiredError,
    HazardTypeRetiredError,
    InvalidTaxonomyError,
)
from yakhnama.modules.hazards.domain.events import (
    HazardTypeReactivated,
    HazardTypeRelabelled,
    HazardTypeReparented,
    HazardTypeRetired,
)
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeRef,
    HazardTypeStatus,
    RetirementReason,
)

LATER = CREATED_AT + timedelta(minutes=10)
CODES = st.sampled_from(["aa", "bb", "cc", "dd", "ee", "ff"])


def _fields(**overrides: object) -> dict[str, object]:
    fields = hazard_type("flood").model_dump()
    fields.update(overrides)
    return fields


# --------------------------------------------------------------------------- #
# HazardType invariants                                                       #
# --------------------------------------------------------------------------- #


def test_hazard_type_valid_fields_build_active_version_one() -> None:
    flood = hazard_type("flood")

    assert flood.status is HazardTypeStatus.ACTIVE
    assert flood.version == 1
    assert flood.ref == HazardTypeRef(code="flood")
    assert not flood.is_retired


def test_hazard_type_offset_timestamps_are_normalised_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))
    local = datetime(2026, 9, 23, 17, 0, tzinfo=plus_five)

    flood = HazardType.model_validate(_fields(created_at=local, updated_at=local))

    assert flood.created_at == CREATED_AT
    assert flood.created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "overrides",
    [
        {"parent_code": "flood"},
        {"status": HazardTypeStatus.RETIRED},
        {"retirement": {"text": "merged"}},
        {
            "status": HazardTypeStatus.RETIRED,
            "retirement": {"text": "merged", "replaced_by": "flood"},
        },
        {"updated_at": CREATED_AT - timedelta(seconds=1)},
        {"created_at": datetime(2026, 9, 23)},  # noqa: DTZ001  # reason: proves naive datetimes are rejected
        {"version": 0},
        {"code": "Flood"},
    ],
)
def test_hazard_type_broken_invariant_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        HazardType.model_validate(_fields(**overrides))


def test_hazard_type_is_frozen() -> None:
    flood = hazard_type("flood")

    with pytest.raises(PydanticValidationError):
        flood.code = "flash_flood"  # type: ignore[misc]  # reason: proves the code is immutable


# --------------------------------------------------------------------------- #
# Transitions                                                                 #
# --------------------------------------------------------------------------- #


def test_retire_active_type_returns_retired_state_and_event() -> None:
    flood = hazard_type("flood")
    reason = RetirementReason(text="split into two types", replaced_by="riverine")

    change = flood.retire(reason, clock=stepping_clock(LATER), ids=ids)

    assert change.state.is_retired
    assert change.state.retirement == reason
    assert change.state.code == flood.code
    assert change.state.id == flood.id
    assert change.state.version == 2
    assert change.state.updated_at == LATER
    assert flood.status is HazardTypeStatus.ACTIVE
    (event,) = change.events
    assert isinstance(event, HazardTypeRetired)
    assert (event.code, event.reason, event.replaced_by) == (
        "flood",
        "split into two types",
        "riverine",
    )
    assert event.aggregate_id == flood.id
    assert event.occurred_at == LATER


def test_retire_retired_type_raises_hazard_type_retired() -> None:
    retired = hazard_type("flood", is_retired=True)

    with pytest.raises(HazardTypeRetiredError):
        retired.retire(RetirementReason(text="again"), clock=stepping_clock(), ids=ids)


def test_retire_replaced_by_own_code_raises_invalid_taxonomy() -> None:
    flood = hazard_type("flood")

    with pytest.raises(InvalidTaxonomyError):
        flood.retire(
            RetirementReason(text="loop", replaced_by="flood"),
            clock=stepping_clock(),
            ids=ids,
        )


def test_reactivate_retired_type_clears_retirement_and_emits_event() -> None:
    retired = hazard_type("flood", replaced_by="riverine")

    change = retired.reactivate(
        "retired by mistake", clock=stepping_clock(LATER), ids=ids
    )

    assert change.state.status is HazardTypeStatus.ACTIVE
    assert change.state.retirement is None
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, HazardTypeReactivated)
    assert event.reason == "retired by mistake"


def test_reactivate_active_type_raises_hazard_type_not_retired() -> None:
    flood = hazard_type("flood")

    with pytest.raises(HazardTypeNotRetiredError):
        flood.reactivate("why", clock=stepping_clock(), ids=ids)


def test_reactivate_empty_reason_raises_validation_error() -> None:
    retired = hazard_type("flood", is_retired=True)

    with pytest.raises(PydanticValidationError):
        retired.reactivate("  ", clock=stepping_clock(), ids=ids)


@pytest.mark.parametrize("is_retired", [False, True])
def test_relabel_new_labels_returns_relabelled_state_and_event(
    *, is_retired: bool
) -> None:
    flood = hazard_type("flood", is_retired=is_retired)
    new_labels = labels("River flood")

    change = flood.relabel(new_labels, clock=stepping_clock(LATER), ids=ids)

    assert change.state.labels == new_labels
    assert change.state.version == 2
    assert change.state.status is flood.status
    (event,) = change.events
    assert isinstance(event, HazardTypeRelabelled)
    assert event.labels == new_labels


def test_relabel_same_labels_returns_unchanged_state_without_events() -> None:
    flood = hazard_type("flood")

    change = flood.relabel(labels("flood"), clock=stepping_clock(), ids=ids)

    assert change.state is flood
    assert change.events == ()


def test_reparent_new_parent_returns_moved_state_and_event() -> None:
    flash = hazard_type("flash_flood")

    change = flash.reparent("flood", clock=stepping_clock(LATER), ids=ids)

    assert change.state.parent_code == "flood"
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, HazardTypeReparented)
    assert (event.previous_parent_code, event.parent_code) == (None, "flood")


def test_reparent_to_root_records_previous_parent() -> None:
    flash = hazard_type("flash_flood", parent_code="flood")

    change = flash.reparent(None, clock=stepping_clock(), ids=ids)

    assert change.state.parent_code is None
    (event,) = change.events
    assert isinstance(event, HazardTypeReparented)
    assert event.previous_parent_code == "flood"


def test_reparent_same_parent_returns_unchanged_state_without_events() -> None:
    flash = hazard_type("flash_flood", parent_code="flood")

    change = flash.reparent("flood", clock=stepping_clock(), ids=ids)

    assert change.state is flash
    assert change.events == ()


def test_reparent_to_itself_raises_invalid_taxonomy() -> None:
    flood = hazard_type("flood")

    with pytest.raises(InvalidTaxonomyError):
        flood.reparent("flood", clock=stepping_clock(), ids=ids)


def test_reparent_retired_type_raises_hazard_type_retired() -> None:
    retired = hazard_type("flood", is_retired=True)

    with pytest.raises(HazardTypeRetiredError):
        retired.reparent("other", clock=stepping_clock(), ids=ids)


@given(steps=st.lists(st.sampled_from(["retire", "reactivate", "relabel"]), max_size=8))
def test_transition_sequence_keeps_code_and_counts_versions(steps: list[str]) -> None:
    current = hazard_type("flood")
    clock = stepping_clock()
    changes = 0

    for step in steps:
        if step == "retire" and not current.is_retired:
            current = current.retire(
                RetirementReason(text="r"), clock=clock, ids=ids
            ).state
            changes += 1
        elif step == "reactivate" and current.is_retired:
            current = current.reactivate("back", clock=clock, ids=ids).state
            changes += 1
        elif step == "relabel":
            current = current.relabel(labels(f"v{changes}"), clock=clock, ids=ids).state
            changes += 1

    assert current.code == "flood"
    assert current.version == 1 + changes
    assert current.is_retired == (current.retirement is not None)


# --------------------------------------------------------------------------- #
# find_parent_cycle                                                           #
# --------------------------------------------------------------------------- #


def _has_cycle_brute_force(parents: dict[str, str | None]) -> bool:
    for start, parent in parents.items():
        node: str | None = parent
        for _ in range(len(parents)):
            if node == start:
                return True
            node = parents.get(node) if node is not None else None
    return False


@given(parents=st.dictionaries(CODES, st.none() | CODES, max_size=6))
def test_find_parent_cycle_agrees_with_brute_force(
    parents: dict[str, str | None],
) -> None:
    cycle = find_parent_cycle(parents)

    assert (cycle is not None) == _has_cycle_brute_force(parents)
    if cycle is not None:
        links = [parents[code] for code in cycle]
        assert links == [*cycle[1:], cycle[0]]


def test_find_parent_cycle_forest_returns_none() -> None:
    result = find_parent_cycle({"aa": None, "bb": "aa", "cc": "bb", "dd": "zz"})

    assert result is None


def test_find_parent_cycle_three_cycle_returns_its_members() -> None:
    result = find_parent_cycle({"aa": "bb", "bb": "cc", "cc": "aa", "dd": "aa"})

    assert result == ("aa", "bb", "cc")


# --------------------------------------------------------------------------- #
# HazardTaxonomy                                                              #
# --------------------------------------------------------------------------- #


def _taxonomy() -> HazardTaxonomy:
    return HazardTaxonomy.of(
        [
            hazard_type("flood"),
            hazard_type("flash_flood", "flood"),
            hazard_type("glof", "flash_flood"),
            hazard_type("riverine", "flood"),
            hazard_type("old_flood", "flood", replaced_by="riverine"),
            hazard_type("landslide"),
        ]
    )


def test_taxonomy_roots_returns_parentless_types_by_code() -> None:
    taxonomy = _taxonomy()

    roots = [root.code for root in taxonomy.roots()]

    assert roots == ["flood", "landslide"]


def test_taxonomy_children_returns_direct_children_including_retired() -> None:
    taxonomy = _taxonomy()

    children = [child.code for child in taxonomy.children("flood")]

    assert children == ["flash_flood", "old_flood", "riverine"]
    assert taxonomy.children("glof") == ()


def test_taxonomy_ancestors_returns_parents_nearest_first() -> None:
    taxonomy = _taxonomy()

    ancestors = [ancestor.code for ancestor in taxonomy.ancestors("glof")]

    assert ancestors == ["flash_flood", "flood"]
    assert taxonomy.ancestors("flood") == ()


def test_taxonomy_resolve_retired_reference_returns_retired_type() -> None:
    taxonomy = _taxonomy()

    resolved = taxonomy.resolve(HazardTypeRef(code="old_flood"))

    assert resolved.is_retired
    assert resolved.retirement is not None
    assert resolved.retirement.replaced_by == "riverine"


@pytest.mark.parametrize("operation", ["get", "children", "ancestors"])
def test_taxonomy_unknown_code_raises_hazard_type_not_found(operation: str) -> None:
    taxonomy = _taxonomy()

    with pytest.raises(HazardTypeNotFoundError):
        getattr(taxonomy, operation)("avalanche")


def test_taxonomy_resolve_unknown_reference_raises_hazard_type_not_found() -> None:
    with pytest.raises(HazardTypeNotFoundError):
        _taxonomy().resolve(HazardTypeRef(code="avalanche"))


def test_taxonomy_codes_include_retired_codes() -> None:
    taxonomy = _taxonomy()

    codes = taxonomy.codes()

    assert "old_flood" in codes
    assert taxonomy.has_code("old_flood")
    assert not taxonomy.has_code("avalanche")


def test_taxonomy_equality_ignores_input_order() -> None:
    members = list(_taxonomy().hazard_types)

    reversed_taxonomy = HazardTaxonomy.of(reversed(members))

    assert reversed_taxonomy == HazardTaxonomy.of(members)
    assert hash(reversed_taxonomy) == hash(HazardTaxonomy.of(members))


def test_taxonomy_empty_has_no_roots() -> None:
    taxonomy = HazardTaxonomy()

    assert taxonomy.roots() == ()
    assert taxonomy.codes() == frozenset()


def test_taxonomy_duplicate_code_raises_invalid_taxonomy() -> None:
    with pytest.raises(InvalidTaxonomyError):
        HazardTaxonomy.of([hazard_type("flood"), hazard_type("flood")])


def test_taxonomy_duplicate_id_raises_invalid_taxonomy() -> None:
    flood = hazard_type("flood")
    same_id = hazard_type("landslide").model_copy(update={"id": flood.id})

    with pytest.raises(InvalidTaxonomyError):
        HazardTaxonomy.of([flood, same_id])


def test_taxonomy_missing_parent_raises_invalid_taxonomy() -> None:
    with pytest.raises(InvalidTaxonomyError) as caught:
        HazardTaxonomy.of([hazard_type("glof", "flash_flood")])

    assert caught.value.details["references"] == ["glof -> flash_flood"]


def test_taxonomy_missing_replacement_raises_invalid_taxonomy() -> None:
    with pytest.raises(InvalidTaxonomyError):
        HazardTaxonomy.of([hazard_type("old_flood", replaced_by="riverine")])


def test_taxonomy_cycle_raises_invalid_taxonomy() -> None:
    with pytest.raises(InvalidTaxonomyError) as caught:
        HazardTaxonomy.of(
            [hazard_type("aa", "cc"), hazard_type("bb", "aa"), hazard_type("cc", "bb")]
        )

    assert caught.value.details["cycle"] == ["aa", "cc", "bb"]


def test_taxonomy_with_hazard_type_adds_new_type() -> None:
    taxonomy = _taxonomy()

    extended = taxonomy.with_hazard_type(hazard_type("avalanche"))

    assert extended.has_code("avalanche")
    assert not taxonomy.has_code("avalanche")


def test_taxonomy_with_hazard_type_replaces_changed_state() -> None:
    taxonomy = _taxonomy()
    change = taxonomy.get("riverine").reparent(None, clock=stepping_clock(), ids=ids)

    updated = taxonomy.with_hazard_type(change.state)

    assert updated.get("riverine").parent_code is None
    assert len(updated.hazard_types) == len(taxonomy.hazard_types)


def test_taxonomy_with_hazard_type_reparent_into_cycle_raises_invalid_taxonomy() -> (
    None
):
    taxonomy = _taxonomy()
    change = taxonomy.get("flood").reparent("glof", clock=stepping_clock(), ids=ids)

    with pytest.raises(InvalidTaxonomyError):
        taxonomy.with_hazard_type(change.state)


def test_taxonomy_with_hazard_type_reparent_to_unknown_raises_invalid_taxonomy() -> (
    None
):
    taxonomy = _taxonomy()
    change = taxonomy.get("glof").reparent("nowhere", clock=stepping_clock(), ids=ids)

    with pytest.raises(InvalidTaxonomyError):
        taxonomy.with_hazard_type(change.state)


def test_taxonomy_with_hazard_type_same_code_other_id_raises_code_already_used() -> (
    None
):
    taxonomy = _taxonomy()

    with pytest.raises(HazardCodeAlreadyUsedError):
        taxonomy.with_hazard_type(hazard_type("old_flood"))
