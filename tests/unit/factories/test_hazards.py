"""Unit tests for ``tests.factories.hazards``."""

from datetime import UTC

import pytest

from tests.factories.hazards import (
    HAZARD_ATTRIBUTES_BUILDERS,
    GlacierSurgeAttributesFactory,
    HazardTypeTestFactory,
)
from yakhnama.modules.hazards.domain.attributes import (
    DEFAULT_REGISTRY,
    HAZARD_ATTRIBUTES_ADAPTER,
)
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.shared_kernel.ids import is_uuid7

BUILDS = 50


def test_hazard_type_test_factory_builds_valid_types_with_distinct_ids() -> None:
    hazard_types = [HazardTypeTestFactory.build() for _ in range(BUILDS)]

    assert all(
        HazardType.model_validate(hazard_type.model_dump()) == hazard_type
        for hazard_type in hazard_types
    )
    assert len({hazard_type.id for hazard_type in hazard_types}) == BUILDS
    assert all(is_uuid7(hazard_type.id) for hazard_type in hazard_types)


def test_hazard_type_test_factory_types_form_a_consistent_taxonomy() -> None:
    hazard_types = [HazardTypeTestFactory.build() for _ in range(BUILDS)]

    taxonomy = HazardTaxonomy.of(hazard_types)

    assert len(taxonomy.codes()) == BUILDS
    assert len(taxonomy.roots()) == BUILDS


def test_hazard_type_test_factory_builds_active_version_one_utc_types() -> None:
    hazard_types = [HazardTypeTestFactory.build() for _ in range(BUILDS)]

    assert all(not hazard_type.is_retired for hazard_type in hazard_types)
    assert all(hazard_type.version == 1 for hazard_type in hazard_types)
    assert all(hazard_type.created_at.tzinfo is UTC for hazard_type in hazard_types)
    assert all(
        hazard_type.updated_at == hazard_type.created_at for hazard_type in hazard_types
    )


def test_hazard_attributes_builders_cover_every_registered_schema() -> None:
    codes = set(HAZARD_ATTRIBUTES_BUILDERS)

    assert codes == DEFAULT_REGISTRY.codes()


@pytest.mark.parametrize("code", sorted(HAZARD_ATTRIBUTES_BUILDERS))
def test_hazard_attributes_factory_builds_payloads_the_registry_accepts(
    code: str,
) -> None:
    build = HAZARD_ATTRIBUTES_BUILDERS[code]

    attributes = [build() for _ in range(BUILDS)]

    assert all(attribute.hazard_type == code for attribute in attributes)
    assert all(
        DEFAULT_REGISTRY.validate(code, attribute.model_dump()) == attribute
        for attribute in attributes
    )
    assert all(
        HAZARD_ATTRIBUTES_ADAPTER.validate_json(attribute.model_dump_json())
        == attribute
        for attribute in attributes
    )


def test_glacier_surge_attributes_factory_surge_never_ends_before_it_starts() -> None:
    surges = [GlacierSurgeAttributesFactory.build() for _ in range(BUILDS)]

    assert all(
        surge.surge_start is not None
        and surge.surge_end is not None
        and surge.surge_end.value >= surge.surge_start.value
        for surge in surges
    )


def test_glacier_surge_attributes_factory_without_start_has_no_end() -> None:
    surge = GlacierSurgeAttributesFactory.build(surge_start=None)

    assert surge.surge_end is None
