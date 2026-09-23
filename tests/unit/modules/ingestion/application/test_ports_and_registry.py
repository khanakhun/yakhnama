"""Unit tests for ``RawPayload``, the registries and the public facade."""

import hashlib

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ingestion import FakeSourceAdapter, MinimalTestPipeline
from tests.unit.modules.ingestion.application.support import ADMIN, CITIZEN, NOW, World
from yakhnama.modules.ingestion import public
from yakhnama.modules.ingestion.application.authorisation import catalog_policy
from yakhnama.modules.ingestion.application.ports import RawPayload
from yakhnama.modules.ingestion.application.registry import (
    PipelineClassRegistry,
    SourceAdapterRegistry,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError, ValidationError

CONTENT = b"station,value\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def test_raw_payload_of_computes_the_checksum() -> None:
    payload = RawPayload.of(CONTENT, media_type="text/csv", retrieved_at=NOW)

    assert payload.checksum == DIGEST


def test_raw_payload_with_matching_checksum_in_upper_case_is_accepted() -> None:
    payload = RawPayload(
        content=CONTENT,
        media_type="text/csv",
        retrieved_at=NOW,
        checksum=DIGEST.upper(),
    )

    assert payload.checksum == DIGEST


def test_raw_payload_with_checksum_of_other_bytes_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="does not match"):
        RawPayload(
            content=CONTENT,
            media_type="text/csv",
            retrieved_at=NOW,
            checksum=hashlib.sha256(b"other").hexdigest(),
        )


def test_raw_payload_without_bytes_reports_the_field_error() -> None:
    with pytest.raises(PydanticValidationError, match="content"):
        RawPayload.model_validate({"media_type": "text/csv", "retrieved_at": NOW})


def test_source_adapter_registry_resolves_by_name() -> None:
    world = World(b"")

    assert world.adapters.get("test_adapter") is world.adapter
    assert world.adapters.is_registered("test_adapter")
    assert world.adapters.names == ("test_adapter",)


def test_source_adapter_registry_with_unknown_name_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        SourceAdapterRegistry().get("missing_adapter")


def test_source_adapter_registry_refuses_a_second_adapter_with_one_name() -> None:
    world = World(b"")

    with pytest.raises(ConflictError):
        world.adapters.register(FakeSourceAdapter({}, clock=world.clock))


def test_source_adapter_registry_refuses_a_malformed_name() -> None:
    world = World(b"")

    with pytest.raises(ValidationError):
        SourceAdapterRegistry([FakeSourceAdapter({}, clock=world.clock, name="Bad")])


def test_pipeline_class_registry_builds_a_fresh_pipeline_per_call() -> None:
    world = World(b"")
    registry = PipelineClassRegistry(
        clock=world.clock,
        ids=world.ids,
        pipelines={"test_adapter": MinimalTestPipeline},
    )

    first = registry.create(world.adapter)
    second = registry.create(world.adapter)

    assert isinstance(first, MinimalTestPipeline)
    assert first is not second
    assert registry.supports("test_adapter")


def test_pipeline_class_registry_without_entry_raises_not_found() -> None:
    world = World(b"")
    registry = PipelineClassRegistry(clock=world.clock, ids=world.ids)

    with pytest.raises(NotFoundError):
        registry.create(world.adapter)

    assert not registry.supports("test_adapter")


def test_pipeline_class_registry_refuses_a_second_entry_for_one_name() -> None:
    world = World(b"")
    registry = PipelineClassRegistry(clock=world.clock, ids=world.ids)
    registry.register("test_adapter", MinimalTestPipeline)

    with pytest.raises(ConflictError):
        registry.register("test_adapter", MinimalTestPipeline)


def test_catalog_policy_allows_admins_only() -> None:
    assert catalog_policy().is_allowed(ADMIN)
    assert not catalog_policy().is_allowed(CITIZEN)


def test_public_facade_exports_resolve() -> None:
    missing = [name for name in public.__all__ if not hasattr(public, name)]

    assert missing == []
    assert public.INGESTION_RUN_TASK == "ingestion.run"
