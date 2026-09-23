"""The SQLAlchemy unit of work and outbox writer against real PostGIS."""

from uuid import UUID

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from tests.integration.platform.conftest import PROBE_ITEMS
from tests.unit.platform.events import make_event
from yakhnama.platform.container import Container
from yakhnama.platform.outbox.models import OutboxMessage

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("schema")]


async def _counts(container: Container) -> tuple[int, int]:
    async with container.session_factory() as session:
        items = await session.scalar(select(func.count()).select_from(PROBE_ITEMS))
        messages = await session.scalar(select(func.count()).select_from(OutboxMessage))
    return items or 0, messages or 0


async def _write_item_and_event(container: Container, item_id: UUID) -> None:
    async with container.uow_factory() as unit_of_work:
        await unit_of_work.session.execute(
            insert(PROBE_ITEMS).values(id=item_id, note="probe")
        )
        unit_of_work.record_event(make_event())
        await unit_of_work.commit()


async def test_unit_of_work_commit_stores_aggregate_row_and_outbox_row_together(
    container: Container,
) -> None:
    event = make_event("glof warning")

    async with container.uow_factory() as unit_of_work:
        await unit_of_work.session.execute(
            insert(PROBE_ITEMS).values(id=event.aggregate_id, note="probe")
        )
        unit_of_work.record_event(event)
        await unit_of_work.commit()

    async with container.session_factory() as session:
        stored = await session.get(OutboxMessage, event.event_id)
    assert await _counts(container) == (1, 1)
    assert stored is not None
    assert stored.payload == event.model_dump(mode="json")
    assert stored.occurred_at == event.occurred_at
    assert stored.occurred_at.utcoffset() is not None
    assert stored.published_at is None
    assert stored.attempts == 0


async def test_unit_of_work_exception_rolls_back_aggregate_row_and_outbox_row(
    container: Container,
) -> None:
    async def fail_after_writing() -> None:
        async with container.uow_factory() as unit_of_work:
            await unit_of_work.session.execute(
                insert(PROBE_ITEMS).values(id=make_event().aggregate_id, note="probe")
            )
            unit_of_work.record_event(make_event())
            message = "use case failed before commit"
            raise LookupError(message)

    with pytest.raises(LookupError, match="before commit"):
        await fail_after_writing()

    assert await _counts(container) == (0, 0)


async def test_unit_of_work_rejected_outbox_row_rolls_back_the_aggregate_row(
    container: Container,
) -> None:
    event = make_event()
    await _write_item_and_event(container, make_event().aggregate_id)

    async def record_duplicate_event() -> None:
        async with container.uow_factory() as unit_of_work:
            await unit_of_work.session.execute(
                insert(PROBE_ITEMS).values(id=event.aggregate_id, note="second")
            )
            unit_of_work.record_event(event)
            unit_of_work.record_event(event)
            await unit_of_work.commit()

    with pytest.raises(IntegrityError, match="pk_outbox_messages"):
        await record_duplicate_event()

    assert await _counts(container) == (1, 1)


async def test_unit_of_work_without_commit_stores_nothing(
    container: Container,
) -> None:
    async with container.uow_factory() as unit_of_work:
        await unit_of_work.session.execute(
            insert(PROBE_ITEMS).values(id=make_event().aggregate_id, note="probe")
        )
        unit_of_work.record_event(make_event())

    assert await _counts(container) == (0, 0)
