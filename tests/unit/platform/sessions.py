"""An in-memory ``AsyncSession`` that records calls instead of talking to a database."""

from typing import override

from sqlalchemy.ext.asyncio import AsyncSession


class RecordingSession(AsyncSession):
    """``AsyncSession`` whose transaction methods only record that they were called.

    It subclasses SQLAlchemy's session (not one of our ports) so that the unit of work
    and the outbox writer can be exercised without a database; the real behaviour is
    covered by ``tests/integration/platform``.

    Implements: Fake.

    Attributes:
        added: Objects passed to ``add``, in order.
        calls: Names of the transaction methods called, in order.
        commit_error: Raised by ``commit`` when set, to simulate a rejected commit.
    """

    def __init__(self) -> None:
        """Create a session with no bind; nothing here can reach a database."""
        super().__init__()
        self.added: list[object] = []
        self.calls: list[str] = []
        self.commit_error: Exception | None = None

    @override
    def add(self, instance: object, _warn: bool = True) -> None:
        """Record ``instance`` instead of staging it."""
        self.added.append(instance)

    @override
    async def commit(self) -> None:
        """Record the commit, or raise ``commit_error`` if one is set."""
        self.calls.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    @override
    async def rollback(self) -> None:
        """Record the rollback."""
        self.calls.append("rollback")

    @override
    async def close(self) -> None:
        """Record the close."""
        self.calls.append("close")
