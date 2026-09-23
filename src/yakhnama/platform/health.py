"""Health endpoints used by orchestrators and load balancers.

Patterns: API Schema (proposed in ADR 0011).
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    """Body returned by the liveness probe.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        status: Always ``"ok"`` when the process can serve requests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]


@router.get(
    "/health/live",
    summary="Liveness probe",
    response_model=LivenessResponse,
)
async def read_liveness() -> LivenessResponse:
    """Report that the process is running and able to answer HTTP requests.

    The probe deliberately checks no dependency: a database outage must not make an
    orchestrator restart healthy application processes.

    Returns:
        A response whose ``status`` is ``"ok"``.
    """
    return LivenessResponse(status="ok")


# ``/health/ready`` (database and object storage checks) is scheduled for Phase 1.
