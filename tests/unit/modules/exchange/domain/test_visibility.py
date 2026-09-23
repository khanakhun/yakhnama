"""Unit tests for the visibility of export requests, jobs and sidecars."""

from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.exchange import ExportJobTestFactory, artifact_for, sidecar_for
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.exchange.domain.entities import ExportJob
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFormat,
    ExportRequest,
)


def test_export_request_defaults_to_public_visibility() -> None:
    request = ExportRequest(dataset=ExportDataset.EVENTS, format=ExportFormat.CSV)

    assert request.visibility == "public"


def test_export_request_of_reports_with_public_visibility_raises() -> None:
    with pytest.raises(PydanticValidationError):
        ExportRequest(
            dataset=ExportDataset.REPORTS, format=ExportFormat.CSV, visibility="public"
        )


def test_export_job_of_reports_with_public_visibility_raises() -> None:
    job = ExportJobTestFactory.build()

    with pytest.raises(PydanticValidationError):
        ExportJob.model_validate(
            {**dict(job), "dataset": ExportDataset.REPORTS, "visibility": "public"}
        )


def test_export_job_complete_with_sidecar_of_other_visibility_raises() -> None:
    job = ExportJobTestFactory.build(visibility="moderation")
    clock = SteppingClock(job.requested_at, timedelta(seconds=1))
    ids = SequentialIdGenerator(seed=41)
    running = job.start(clock=clock, ids=ids).state
    artifact = artifact_for(running)
    public_sidecar = sidecar_for(running, artifact)

    with pytest.raises(PydanticValidationError):
        running.complete(artifact, public_sidecar, clock=clock, ids=ids)

    matching = public_sidecar.model_copy(update={"visibility": "moderation"})
    completed = running.complete(artifact, matching, clock=clock, ids=ids).state
    assert completed.sidecar is not None
    assert completed.sidecar.visibility == "moderation"
