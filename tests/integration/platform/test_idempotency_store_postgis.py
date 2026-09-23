"""``SqlAlchemyIdempotencyStore`` against real PostGIS."""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.platform.container import Container
from yakhnama.platform.idempotency.models import IdempotencyKeyRow
from yakhnama.platform.idempotency.sqlalchemy_store import SqlAlchemyIdempotencyStore
from yakhnama.platform.idempotency.store import (
    PENDING_LEASE,
    IdempotencyReservation,
    StoredResponse,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("idempotency_schema")]

KEY = UUID("0192f4c1-0000-7000-8000-000000000001")
SCOPE = "a" * 64
RESPONSE = StoredResponse(
    status_code=201,
    headers=(("content-type", "application/json"), ("location", "/x/1")),
    body=b'{"id": 1}',
)


def _store(container: Container) -> SqlAlchemyIdempotencyStore:
    return SqlAlchemyIdempotencyStore(
        container.session_factory, SequentialIdGenerator()
    )


def _reservation(
    clock: FrozenClock, request_hash: str = "b" * 64, key: UUID = KEY
) -> IdempotencyReservation:
    return IdempotencyReservation(
        scope=SCOPE,
        key=key,
        request_hash=request_hash,
        method="POST",
        path="/api/v1/organizations",
        created_at=clock.now(),
        expires_at=clock.now() + PENDING_LEASE,
    )


async def _insert_rows(container: Container, *rows: IdempotencyKeyRow) -> None:
    async with container.session_factory.begin() as session:
        session.add_all(rows)


async def _row_count(container: Container) -> int:
    async with container.session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(IdempotencyKeyRow)
        )
    return count or 0


async def test_reserve_complete_and_reserve_again_replays_stored_response(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)
    won = await store.reserve(_reservation(clock))
    await store.complete(SCOPE, KEY, RESPONSE, clock.now() + timedelta(hours=72))

    existing = await store.reserve(_reservation(clock))

    assert won is None
    assert existing is not None
    assert existing.response == RESPONSE
    assert existing.request_hash == "b" * 64
    assert existing.expires_at == clock.now() + timedelta(hours=72)


async def test_reserve_while_pending_returns_pending_record(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)
    await store.reserve(_reservation(clock))

    existing = await store.reserve(_reservation(clock, request_hash="c" * 64))

    assert existing is not None
    assert existing.response is None
    assert existing.request_hash == "b" * 64


async def test_concurrent_reservations_have_exactly_one_winner(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)

    outcomes = await asyncio.gather(
        *(store.reserve(_reservation(clock)) for _ in range(5))
    )

    assert sum(outcome is None for outcome in outcomes) == 1
    assert await _row_count(container) == 1


async def test_reserve_after_expiry_replaces_lapsed_record(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)
    await store.reserve(_reservation(clock))
    clock.advance(PENDING_LEASE)

    won = await store.reserve(_reservation(clock, request_hash="d" * 64))

    assert won is None
    assert await _row_count(container) == 1


async def test_release_deletes_pending_but_not_completed_records(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)
    other_key = UUID("0192f4c1-0000-7000-8000-000000000002")
    await store.reserve(_reservation(clock))
    await store.reserve(_reservation(clock, key=other_key))
    await store.complete(SCOPE, other_key, RESPONSE, clock.now() + timedelta(hours=1))

    await store.release(SCOPE, KEY)
    await store.release(SCOPE, other_key)

    assert await store.reserve(_reservation(clock)) is None
    assert await store.reserve(_reservation(clock, key=other_key)) is not None


async def test_purge_expired_deletes_only_lapsed_records(
    container: Container, clock: FrozenClock
) -> None:
    store = _store(container)
    other_key = UUID("0192f4c1-0000-7000-8000-000000000003")
    await store.reserve(_reservation(clock))
    await store.reserve(_reservation(clock, key=other_key))
    await store.complete(SCOPE, other_key, RESPONSE, clock.now() + timedelta(hours=72))

    deleted = await store.purge_expired(clock.now() + PENDING_LEASE)

    assert deleted == 1
    assert await _row_count(container) == 1


async def test_unique_constraint_rejects_second_row_for_scope_and_key(
    container: Container, clock: FrozenClock
) -> None:
    row_values = {
        "scope": SCOPE,
        "key": KEY,
        "request_hash": "e" * 64,
        "method": "POST",
        "path": "/p",
        "created_at": clock.now(),
        "expires_at": clock.now() + PENDING_LEASE,
    }
    generator = SequentialIdGenerator()

    first = IdempotencyKeyRow(id=generator.new_id(), **row_values)
    second = IdempotencyKeyRow(id=generator.new_id(), **row_values)

    with pytest.raises(IntegrityError):
        await _insert_rows(container, first, second)


async def test_check_constraint_rejects_half_completed_row(
    container: Container, clock: FrozenClock
) -> None:
    row = IdempotencyKeyRow(
        id=SequentialIdGenerator().new_id(),
        scope=SCOPE,
        key=KEY,
        request_hash="f" * 64,
        method="POST",
        path="/p",
        status_code=201,
        created_at=clock.now(),
        expires_at=clock.now() + PENDING_LEASE,
    )

    with pytest.raises(IntegrityError):
        await _insert_rows(container, row)
