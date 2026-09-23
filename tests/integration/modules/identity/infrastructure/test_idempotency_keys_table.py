"""The ``idempotency_keys`` table built by migration 0006, through raw SQL.

The store itself is tested under ``tests/integration/platform``; these tests prove
that the migrated table enforces its own constraints.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

CREATED: Final = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
SCOPE: Final = "0" * 64
KEY: Final = UUID("0192a3b4-0000-7000-8000-00000000f001")
REQUEST_HASH: Final = "a" * 64

_INSERT: Final = text(
    """
    INSERT INTO idempotency_keys (
        id, scope, key, request_hash, method, path, status_code, response_body,
        response_headers, created_at, expires_at
    ) VALUES (
        :id, :scope, :key, :request_hash, 'POST', '/v1/test', :status_code,
        :response_body, CAST(:response_headers AS jsonb), :created_at, :expires_at
    )
    """
)


def _row(row_number: int, **overrides: object) -> dict[str, object]:
    return {
        "id": UUID(int=row_number),
        "scope": SCOPE,
        "key": KEY,
        "request_hash": REQUEST_HASH,
        "status_code": None,
        "response_body": None,
        "response_headers": None,
        "created_at": CREATED,
        "expires_at": CREATED + timedelta(hours=24),
        **overrides,
    }


async def _insert(
    session_factory: async_sessionmaker[AsyncSession], *rows: dict[str, object]
) -> None:
    async with session_factory() as session:
        for row in rows:
            await session.execute(_INSERT, row)
        await session.commit()


async def test_idempotency_keys_pending_then_stored_rows_are_accepted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    pending = _row(1)
    stored = _row(
        2,
        key=UUID(int=2),
        status_code=201,
        response_body=b"{}",
        response_headers='[["content-type", "application/json"]]',
    )

    await _insert(session_factory, pending, stored)
    async with session_factory() as session:
        count = await session.scalar(text("SELECT count(*) FROM idempotency_keys"))

    assert count == 2


async def test_idempotency_keys_same_scope_and_key_violates_unique_constraint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert(session_factory, _row(1))

    with pytest.raises(IntegrityError, match="uq_idempotency_keys_scope_key"):
        await _insert(session_factory, _row(2))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        (
            {
                "status_code": 500,
                "response_body": b"{}",
                "response_headers": "[]",
            },
            "ck_idempotency_keys_status_code_success",
        ),
        ({"status_code": 200}, "ck_idempotency_keys_response_complete"),
        (
            {"status_code": 200, "response_body": b"{}"},
            "ck_idempotency_keys_response_complete",
        ),
        ({"response_body": b"{}"}, "ck_idempotency_keys_response_complete"),
    ],
    ids=["error-status", "status-only", "no-headers", "body-while-pending"],
)
async def test_idempotency_keys_incoherent_row_violates_check_constraint(
    session_factory: async_sessionmaker[AsyncSession],
    overrides: dict[str, object],
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await _insert(session_factory, _row(1, **overrides))
