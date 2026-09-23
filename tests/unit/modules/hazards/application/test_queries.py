"""Unit tests for the hazards queries, specifications, DTOs and read-side fakes."""

import typing

import pydantic
import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.hazards import (
    InMemoryHazardsUnitOfWork,
    InMemoryHazardTypeQueryService,
    InMemoryHazardTypeRepository,
)
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.hazards.application.support import (
    NOW,
    stored_types,
)
from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
    LoadReport,
    SkippedChange,
)
from yakhnama.modules.hazards.application.ports import (
    HazardsUnitOfWork,
    HazardsUnitOfWorkFactory,
    HazardTypeQueryService,
    HazardTypeRepository,
)
from yakhnama.modules.hazards.application.queries import GetHazardType, ListHazardTypes
from yakhnama.modules.hazards.application.specifications import (
    ActiveHazardTypeSpecification,
)
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeStatus,
    RetirementReason,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError, ValidationError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import NotSpecification


def retire(hazard_type: HazardType) -> HazardType:
    """Return ``hazard_type`` retired."""
    return hazard_type.retire(
        RetirementReason(text="obsolete"),
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(seed=5),
    ).state


def three_types() -> tuple[HazardType, ...]:
    """Return ``example_a`` (root), ``example_b`` (retired) and ``example_c``."""
    first, second, third = stored_types(
        ("example_a", None), ("example_b", "example_a"), ("example_c", "example_a")
    )
    return first, retire(second), third


# --------------------------------------------------------------------------- #
# Queries and specifications                                                  #
# --------------------------------------------------------------------------- #


def test_list_hazard_types_with_limit_above_max_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        ListHazardTypes(page=PageRequest(limit=201))


def test_list_hazard_types_by_default_filters_to_active_only() -> None:
    specification = ListHazardTypes().to_specification()

    assert isinstance(specification, ActiveHazardTypeSpecification)


def test_list_hazard_types_including_retired_has_no_specification() -> None:
    assert ListHazardTypes(include_retired=True).to_specification() is None


def test_get_hazard_type_with_malformed_code_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        GetHazardType(code="Not A Code")


def test_active_hazard_type_specification_composes_with_not() -> None:
    active, retired, _ = (HazardTypeSummary.from_entity(t) for t in three_types())
    specification = ActiveHazardTypeSpecification()

    negated = ~specification

    assert isinstance(negated, NotSpecification)
    assert specification.is_satisfied_by(active) is True
    assert specification.is_satisfied_by(retired) is False
    assert negated.is_satisfied_by(retired) is True


# --------------------------------------------------------------------------- #
# DTOs                                                                        #
# --------------------------------------------------------------------------- #


def test_hazard_type_detail_from_entity_copies_every_field() -> None:
    _, retired, _ = three_types()

    detail = HazardTypeDetail.from_entity(retired)

    assert detail.code == "example_b"
    assert detail.parent_code == "example_a"
    assert detail.status is HazardTypeStatus.RETIRED
    assert detail.retirement == RetirementReason(text="obsolete")
    assert detail.version == retired.version
    assert detail.id == retired.id


def test_load_report_with_created_code_is_not_unchanged() -> None:
    report = LoadReport(
        data_version="1",
        dry_run=False,
        created=("example_a",),
        updated=(),
        unchanged=(),
        skipped_with_reason=(),
    )

    assert report.is_unchanged is False


def test_skipped_change_with_empty_reason_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        SkippedChange(code="example_a", reason="")


# --------------------------------------------------------------------------- #
# Fakes honour the ports                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("protocol", "fake"),
    [
        (HazardTypeRepository, InMemoryHazardTypeRepository),
        (HazardTypeQueryService, InMemoryHazardTypeQueryService),
        (HazardsUnitOfWork, InMemoryHazardsUnitOfWork),
    ],
)
def test_fake_defines_every_protocol_member(protocol: type, fake: type) -> None:
    members = typing.get_protocol_members(protocol)

    missing = sorted(member for member in members if not hasattr(fake, member))

    # ``hazard_types`` is an instance attribute of the unit of work fake.
    assert missing in ([], ["hazard_types"])


def test_fakes_satisfy_ports_statically() -> None:
    uow = InMemoryHazardsUnitOfWork()

    repository: HazardTypeRepository = uow.hazard_types
    unit_of_work: HazardsUnitOfWork = uow
    factory: HazardsUnitOfWorkFactory = InMemoryUnitOfWorkFactory(uow)
    service: HazardTypeQueryService = InMemoryHazardTypeQueryService(uow.hazard_types)

    assert unit_of_work.hazard_types is repository
    assert factory() is uow
    assert service is not None


async def test_fake_repository_add_with_taken_code_raises_conflict() -> None:
    (existing,) = stored_types(("example_a", None))
    repository = InMemoryHazardTypeRepository([existing])

    with pytest.raises(ConflictError):
        await repository.add(existing)


async def test_fake_repository_save_of_unknown_type_raises_not_found() -> None:
    (unknown,) = stored_types(("example_a", None))
    repository = InMemoryHazardTypeRepository()

    with pytest.raises(NotFoundError):
        await repository.save(unknown)


async def test_fake_repository_get_by_code_sees_staged_until_discarded() -> None:
    (new,) = stored_types(("example_a", None))
    repository = InMemoryHazardTypeRepository()
    await repository.add(new)

    staged = await repository.get_by_code("example_a")
    repository.discard_staged()

    assert staged == new
    assert await repository.get_by_code("example_a") is None


async def test_fake_query_service_pages_active_types_by_code() -> None:
    repository = InMemoryHazardTypeRepository(three_types())
    service = InMemoryHazardTypeQueryService(repository)

    first = await service.list_hazard_types(
        ListHazardTypes(include_retired=True, page=PageRequest(limit=2))
    )
    second = await service.list_hazard_types(
        ListHazardTypes(
            include_retired=True, page=PageRequest(limit=2, cursor=first.next_cursor)
        )
    )
    active = await service.list_hazard_types(ListHazardTypes())

    assert [item.code for item in first.items] == ["example_a", "example_b"]
    assert [item.code for item in second.items] == ["example_c"]
    assert second.next_cursor is None
    assert [item.code for item in active.items] == ["example_a", "example_c"]


async def test_fake_query_service_with_malformed_cursor_raises_validation_error() -> (
    None
):
    service = InMemoryHazardTypeQueryService(InMemoryHazardTypeRepository())

    with pytest.raises(ValidationError):
        await service.list_hazard_types(
            ListHazardTypes(page=PageRequest(cursor="not-a-cursor"))
        )


async def test_fake_query_service_get_returns_detail_or_none() -> None:
    service = InMemoryHazardTypeQueryService(
        InMemoryHazardTypeRepository(three_types())
    )

    found = await service.get("example_c")
    missing = await service.get("example_missing")

    assert found is not None
    assert found.code == "example_c"
    assert missing is None
