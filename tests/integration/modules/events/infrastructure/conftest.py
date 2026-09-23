"""Fixtures of the events infrastructure integration tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.events.infrastructure.queries import SqlAlchemyEventQueryService


@pytest.fixture
def event_queries(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyEventQueryService:
    """Return the SQL event query service on the test database."""
    return SqlAlchemyEventQueryService(session_factory)
