"""Arrangements that run the real handlers to reach a state (for read tests)."""

from tests.unit.modules.ingestion.application.support import World
from yakhnama.modules.ingestion.application.commands import ExecuteIngestionRun
from yakhnama.modules.ingestion.application.handlers import (
    ExecuteIngestionRunHandler,
)


async def executed_world(content: bytes) -> World:
    """Return a world whose run was executed over ``content``."""
    world = World(content)
    handler = ExecuteIngestionRunHandler(
        uow_factory=world.uow_factory,
        adapters=world.adapters,
        pipelines=world.pipelines,
        clock=world.clock,
        ids=world.ids,
    )
    await handler(ExecuteIngestionRun(run_id=world.run.id))
    return world
