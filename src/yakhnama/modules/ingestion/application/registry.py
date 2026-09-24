"""Registries of source adapters and of the pipelines that read through them.

Adding a source means registering one adapter and one pipeline class under the same
adapter name in the composition root; no existing entry is touched, and a name can
be registered only once, so one source can never silently replace another.

Patterns: Registry, Factory.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Final, Protocol

from yakhnama.modules.ingestion.application.ports import (
    RunnablePipeline,
    SourceAdapter,
)
from yakhnama.modules.ingestion.domain.value_objects import ADAPTER_NAME_PATTERN
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError, ValidationError
from yakhnama.shared_kernel.ids import IdGenerator

_ADAPTER_NAME: Final = re.compile(ADAPTER_NAME_PATTERN)


def _require_adapter_name(name: str) -> str:
    if _ADAPTER_NAME.fullmatch(name) is None:
        message = "an adapter name is 2 to 64 of a-z, 0-9, '_' and '.'"
        raise ValidationError(message, details={"field": "adapter_name"})
    return name


def _unknown(name: str) -> NotFoundError:
    message = "no source adapter is registered under this name"
    return NotFoundError(message, details={"adapter_name": name})


class SourceAdapterRegistry:
    """Maps adapter names to source adapters.

    Implements: Registry.
    """

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        """Create the registry.

        Args:
            adapters: Adapters to register, each under its own ``name``.

        Raises:
            ConflictError: If two adapters share a name.
            ValidationError: If a name is not an ``AdapterName``.
        """
        self._adapters: dict[str, SourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        """Add ``adapter`` under its ``name``.

        Args:
            adapter: The adapter.

        Raises:
            ConflictError: If the name is taken.
            ValidationError: If the name is not an ``AdapterName``.
        """
        name = _require_adapter_name(adapter.name)
        if name in self._adapters:
            message = "a source adapter is already registered under this name"
            raise ConflictError(message, details={"adapter_name": name})
        self._adapters[name] = adapter

    def is_registered(self, name: str) -> bool:
        """Tell whether an adapter is registered under ``name``.

        Args:
            name: An adapter name.

        Returns:
            ``True`` if ``get`` would find one.
        """
        return name in self._adapters

    def get(self, name: str) -> SourceAdapter:
        """Return the adapter registered under ``name``.

        Args:
            name: An adapter name.

        Returns:
            The adapter.

        Raises:
            NotFoundError: If none is registered.
        """
        adapter = self._adapters.get(name)
        if adapter is None:
            raise _unknown(name)
        return adapter

    @property
    def names(self) -> tuple[str, ...]:
        """Return every registered name, sorted."""
        return tuple(sorted(self._adapters))


class PipelineConstructor(Protocol):
    """Builds one pipeline; an ``IngestionPipeline`` subclass itself fits.

    Implements: Factory.
    """

    def __call__(
        self, adapter: SourceAdapter, *, clock: Clock, ids: IdGenerator
    ) -> RunnablePipeline:
        """Build a pipeline.

        Args:
            adapter: The source adapter to fetch through.
            clock: Source of the run's timestamps.
            ids: Source of event ids.

        Returns:
            A pipeline that has not run yet.
        """
        ...


class PipelineClassRegistry:
    """Maps adapter names to pipeline classes and builds one pipeline per run.

    The production ``PipelineFactory``: the composition root registers, for each
    source, ``registry.register("local_csv_temperature", TemperatureCsvPipeline)``.

    Implements: Registry, Factory.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        pipelines: Mapping[str, PipelineConstructor] | None = None,
    ) -> None:
        """Create the registry.

        Args:
            clock: Handed to every pipeline built.
            ids: Handed to every pipeline built.
            pipelines: Constructors to register, by adapter name.

        Raises:
            ValidationError: If a name is not an ``AdapterName``.
        """
        self._clock = clock
        self._ids = ids
        self._pipelines: dict[str, PipelineConstructor] = {}
        for name, constructor in (pipelines or {}).items():
            self.register(name, constructor)

    def register(self, adapter_name: str, constructor: PipelineConstructor) -> None:
        """Add the pipeline for ``adapter_name``.

        Args:
            adapter_name: The source adapter the pipeline reads through.
            constructor: The pipeline class, or any callable building one.

        Raises:
            ConflictError: If the name is taken.
            ValidationError: If the name is not an ``AdapterName``.
        """
        name = _require_adapter_name(adapter_name)
        if name in self._pipelines:
            message = "a pipeline is already registered for this adapter"
            raise ConflictError(message, details={"adapter_name": name})
        self._pipelines[name] = constructor

    def supports(self, adapter_name: str) -> bool:
        """Tell whether a pipeline is registered for ``adapter_name``.

        Args:
            adapter_name: A source adapter's name.

        Returns:
            ``True`` if ``create`` can build one.
        """
        return adapter_name in self._pipelines

    def create(self, adapter: SourceAdapter) -> RunnablePipeline:
        """Build a fresh pipeline reading through ``adapter``.

        Args:
            adapter: The resolved source adapter.

        Returns:
            A pipeline that has not run yet.

        Raises:
            NotFoundError: If no pipeline is registered for ``adapter.name``.
        """
        constructor = self._pipelines.get(adapter.name)
        if constructor is None:
            message = "no pipeline is registered for this source adapter"
            raise NotFoundError(message, details={"adapter_name": adapter.name})
        return constructor(adapter, clock=self._clock, ids=self._ids)
