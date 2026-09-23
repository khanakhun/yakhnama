"""Adapter binding the ``ingestion.run`` task to the ingestion module.

- ``ExecuteIngestionRunTaskAdapter``: validates the broker payload into
  ``ExecuteIngestionRun`` and runs ``ExecuteIngestionRunHandler`` (the Template
  Method pipeline of the run's source adapter).

Patterns: Adapter.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.ingestion.public import (
    ExecuteIngestionRun,
    ExecuteIngestionRunHandler,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.tasks import ScheduledTask


class IngestionTaskPayload(BaseModel):
    """The payload of ``ingestion.run``, validated from the broker's JSON.

    Implements: DTO.

    Attributes:
        run_id: The pending run to execute.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: EntityId


class ExecuteIngestionRunTaskAdapter:
    """The ``ingestion.run`` task handler over ``ExecuteIngestionRunHandler``.

    Implements: Adapter.
    """

    def __init__(self, handler: ExecuteIngestionRunHandler) -> None:
        """Create the task handler.

        Args:
            handler: The run execution use case.
        """
        self._handler = handler

    async def __call__(self, task: ScheduledTask) -> None:
        """Execute the run named in the payload.

        Args:
            task: The task; payload ``{run_id}``.

        Raises:
            pydantic.ValidationError: If the payload is not an ingestion payload.
            IngestionRunNotFoundError: If the run does not exist.
        """
        payload = IngestionTaskPayload.model_validate(dict(task.payload))
        await self._handler(ExecuteIngestionRun(run_id=payload.run_id))
