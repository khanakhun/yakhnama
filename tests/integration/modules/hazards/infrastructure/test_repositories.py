"""The SQLAlchemy hazard type repository and unit of work against real PostGIS."""

import pytest
from sqlalchemy.exc import IntegrityError

from tests.factories.hazards import HazardTypeTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.hazards.application.ports import HazardsUnitOfWorkFactory
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.domain.value_objects import RetirementReason
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError, InvariantViolationError
from yakhnama.shared_kernel.value_objects import LocalizedText

pytestmark = pytest.mark.integration

type HazardsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyHazardsUnitOfWork]


def _tree() -> tuple[HazardType, HazardType, HazardType]:
    root = HazardTypeTestFactory.build(
        description=LocalizedText(texts={"en": "Test description"})
    )
    child = HazardTypeTestFactory.build(
        parent_code=root.code, attributes_schema="test_schema"
    )
    replacement = HazardTypeTestFactory.build(parent_code=root.code)
    return root, child, replacement


async def _store(factory: HazardsUnitOfWorkFactory, *hazard_types: HazardType) -> None:
    async with factory() as uow:
        for hazard_type in hazard_types:
            await uow.hazard_types.add(hazard_type)
        await uow.commit()


async def _get(factory: HazardsUnitOfWorkFactory, code: str) -> HazardType | None:
    async with factory() as uow:
        return await uow.hazard_types.get_by_code(code)


async def test_hazard_type_repository_add_then_get_returns_equal_hazard_type(
    hazards_uow_factory: HazardsFactory,
) -> None:
    root, child, _ = _tree()

    await _store(hazards_uow_factory, root, child)

    assert await _get(hazards_uow_factory, root.code) == root
    assert await _get(hazards_uow_factory, child.code) == child


async def test_hazard_type_repository_get_when_missing_returns_none(
    hazards_uow_factory: HazardsFactory,
) -> None:
    root, _, _ = _tree()

    assert await _get(hazards_uow_factory, root.code) is None


async def test_hazard_type_repository_list_all_rebuilds_saved_taxonomy(
    hazards_uow_factory: HazardsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    root, child, replacement = _tree()
    retired = child.retire(
        RetirementReason(text="Test retirement", replaced_by=replacement.code),
        clock=clock,
        ids=ids,
    ).state
    await _store(hazards_uow_factory, root, replacement)
    async with hazards_uow_factory() as uow:
        await uow.hazard_types.add(child)
        await uow.hazard_types.save(retired)
        await uow.commit()

    async with hazards_uow_factory() as uow:
        taxonomy = await uow.hazard_types.list_all()

    assert taxonomy == HazardTaxonomy.of((retired, root, replacement))


async def test_hazard_type_repository_list_all_when_empty_returns_empty_taxonomy(
    hazards_uow_factory: HazardsFactory,
) -> None:
    async with hazards_uow_factory() as uow:
        taxonomy = await uow.hazard_types.list_all()

    assert taxonomy == HazardTaxonomy()


async def test_hazard_type_repository_add_duplicate_code_raises_conflict(
    hazards_uow_factory: HazardsFactory,
) -> None:
    root, _, _ = _tree()
    await _store(hazards_uow_factory, root)
    duplicate = HazardTypeTestFactory.build(code=root.code)
    other = HazardTypeTestFactory.build()

    async with hazards_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.hazard_types.add(duplicate)
        await uow.hazard_types.add(other)
        await uow.commit()

    assert await _get(hazards_uow_factory, root.code) == root
    assert await _get(hazards_uow_factory, other.code) == other


async def test_hazard_type_repository_add_with_unstored_parent_reraises_integrity_error(
    hazards_uow_factory: HazardsFactory,
) -> None:
    _, child, _ = _tree()

    async with hazards_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.hazard_types.add(child)


async def test_hazard_type_repository_save_after_list_all_tracks_loaded_version(
    hazards_uow_factory: HazardsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    root, _, _ = _tree()
    await _store(hazards_uow_factory, root)

    async with hazards_uow_factory() as uow:
        loaded = (await uow.hazard_types.list_all()).get(root.code)
        relabelled = loaded.relabel(
            LocalizedText(texts={"en": "Test label one"}), clock=clock, ids=ids
        ).state
        relabelled = relabelled.relabel(
            LocalizedText(texts={"en": "Test label two"}), clock=clock, ids=ids
        ).state
        await uow.hazard_types.save(relabelled)
        await uow.commit()

    assert await _get(hazards_uow_factory, root.code) == relabelled


async def test_hazard_type_repository_save_with_stale_version_raises_conflict(
    hazards_uow_factory: HazardsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    root, _, _ = _tree()
    await _store(hazards_uow_factory, root)

    async with hazards_uow_factory() as slow:
        stale = await slow.hazard_types.get_by_code(root.code)
        async with hazards_uow_factory() as fast:
            fresh = await fast.hazard_types.get_by_code(root.code)
            assert fresh is not None
            await fast.hazard_types.save(
                fresh.relabel(
                    LocalizedText(texts={"en": "Test fast"}), clock=clock, ids=ids
                ).state
            )
            await fast.commit()
        assert stale is not None
        with pytest.raises(ConflictError) as raised:
            await slow.hazard_types.save(
                stale.relabel(
                    LocalizedText(texts={"en": "Test slow"}), clock=clock, ids=ids
                ).state
            )

    assert raised.value.details["stored"] == root.version + 1


async def test_hazard_type_repository_save_when_missing_raises_not_found(
    hazards_uow_factory: HazardsFactory,
) -> None:
    root, _, _ = _tree()

    async with hazards_uow_factory() as uow:
        with pytest.raises(HazardTypeNotFoundError):
            await uow.hazard_types.save(root)


async def test_hazard_type_repository_save_with_other_id_raises_not_found(
    hazards_uow_factory: HazardsFactory,
) -> None:
    root, _, _ = _tree()
    await _store(hazards_uow_factory, root)
    impostor = HazardTypeTestFactory.build(code=root.code, version=2)

    async with hazards_uow_factory() as uow:
        with pytest.raises(HazardTypeNotFoundError):
            await uow.hazard_types.save(impostor)


async def test_hazards_unit_of_work_outside_block_raises_invariant_violation(
    hazards_uow_factory: HazardsFactory,
) -> None:
    uow = hazards_uow_factory()

    with pytest.raises(InvariantViolationError):
        _ = uow.hazard_types
