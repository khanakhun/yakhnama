"""Fixtures for the identity persistence tests: the SQLAlchemy identity UoW factory."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory


@pytest.fixture
def identity_uow_factory(
    session_factory: async_sessionmaker[AsyncSession], outbox_writer: OutboxWriter
) -> SqlAlchemyUnitOfWorkFactory[SqlAlchemyIdentityUnitOfWork]:
    """Return the SQLAlchemy identity unit-of-work factory."""
    return SqlAlchemyUnitOfWorkFactory(
        SqlAlchemyIdentityUnitOfWork,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
    )
