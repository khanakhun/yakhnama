"""Orchestrates loading every reference file, idempotently, in dependency order.

The handler reads each file through the ``ReferenceFileReader`` port (the YAML adapter
lives with the composition root, because reading files is I/O) and hands it to the
module's load use case: hazard types, then impact metrics, then places. Each module
loads in its own unit of work, so the seed is not one atomic transaction; every load
is idempotent, so re-running after a partial failure completes the work without
duplicating anything. It depends only on the modules' public facades.

Patterns: Command, Command Handler, Adapter (port side), Policy, DTO.
"""

from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.public import LoadReferencePlaces, PlaceReferenceFile
from yakhnama.modules.geography.public import LoadReport as PlaceLoadReport
from yakhnama.modules.hazards.public import (
    HazardTypeReferenceFile,
    LoadReferenceHazardTypes,
)
from yakhnama.modules.hazards.public import LoadReport as HazardTypeLoadReport
from yakhnama.modules.impacts.public import (
    ImpactMetricReferenceFile,
    LoadReferenceImpactMetrics,
)
from yakhnama.modules.impacts.public import LoadReport as ImpactMetricLoadReport
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

type LoadHazardTypes = Callable[
    [LoadReferenceHazardTypes], Awaitable[HazardTypeLoadReport]
]
"""The hazards load use case, usually ``LoadReferenceHazardTypesHandler``."""

type LoadImpactMetrics = Callable[
    [LoadReferenceImpactMetrics], Awaitable[ImpactMetricLoadReport]
]
"""The impacts load use case, usually ``LoadReferenceImpactMetricsHandler``."""

type LoadPlaces = Callable[[LoadReferencePlaces], Awaitable[PlaceLoadReport]]
"""The geography load use case, usually ``LoadReferencePlacesHandler``."""


class ReferenceFileReader(Protocol):
    """Reads and validates the versioned reference files.

    Implements: Adapter (port side).
    """

    def read_hazard_types(self) -> HazardTypeReferenceFile:
        """Return the parsed hazard taxonomy file.

        Returns:
            The validated file.

        Raises:
            ValidationError: If the file cannot be read or does not match its
                model; the kernel error, so callers never see parser or
                Pydantic exceptions.
        """
        ...

    def read_impact_metrics(self) -> ImpactMetricReferenceFile:
        """Return the parsed impact metric file.

        Returns:
            The validated file.

        Raises:
            ValidationError: If the file cannot be read or does not match its
                model; the kernel error, so callers never see parser or
                Pydantic exceptions.
        """
        ...

    def read_places(self) -> PlaceReferenceFile:
        """Return the parsed place hierarchy file.

        Returns:
            The validated file.

        Raises:
            ValidationError: If the file cannot be read or does not match its
                model; the kernel error, so callers never see parser or
                Pydantic exceptions.
        """
        ...


class SeedPolicy(Protocol):
    """Decides whether an actor may seed reference data.

    Placeholder until the identity policies of Phase 2 replace it; the same object
    satisfies every module's ``AdminOnlyPolicy``.

    Implements: Policy.
    """

    def is_allowed(self, actor_id: EntityId | None) -> bool:
        """Tell whether ``actor_id`` may seed reference data.

        Args:
            actor_id: The acting user or system, or ``None`` if unknown.

        Returns:
            ``True`` only if the actor is explicitly allowed.
        """
        ...


class ActorAllowListPolicy:
    """Phase 1 placeholder policy: allows exactly the listed actor ids.

    Deny by default: an unknown actor (``None``) and every id not listed are
    refused. The composition root lists the system actor that runs the seed. It
    satisfies ``SeedPolicy`` and each module's ``AdminOnlyPolicy`` and is replaced by
    the identity policies in Phase 2.

    Implements: Policy.
    """

    def __init__(self, allowed_actor_ids: Iterable[EntityId]) -> None:
        """Create the policy.

        Args:
            allowed_actor_ids: The only actors allowed; may be empty.
        """
        self._allowed = frozenset(allowed_actor_ids)

    def is_allowed(self, actor_id: EntityId | None) -> bool:
        """Tell whether ``actor_id`` is on the list.

        Args:
            actor_id: The acting user or system, or ``None`` if unknown.

        Returns:
            ``True`` if the id is listed.
        """
        return actor_id is not None and actor_id in self._allowed


class SeedReferenceData(BaseModel):
    """Load every reference file into its module.

    Implements: Command.

    Attributes:
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
        dry_run: Compute every report but roll every load back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: EntityId | None
    dry_run: bool = False


class SeedReport(BaseModel):
    """What one seed run did, per reference file.

    Each report carries the ``data_version`` of the file it loaded.

    Implements: DTO.

    Attributes:
        dry_run: Whether every load was rolled back.
        hazard_types: Report of the hazard taxonomy load.
        impact_metrics: Report of the impact metric load.
        places: Report of the place hierarchy load.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dry_run: bool
    hazard_types: HazardTypeLoadReport
    impact_metrics: ImpactMetricLoadReport
    places: PlaceLoadReport

    @property
    def is_unchanged(self) -> bool:
        """Return ``True`` if no load created or updated anything."""
        return (
            self.hazard_types.is_unchanged
            and self.impact_metrics.is_unchanged
            and self.places.is_unchanged
        )


class SeedReferenceDataHandler:
    """Seed hazard types, impact metrics and places, in that order.

    Hazard types and impact metrics come first because later phases' records refer
    to them by code; places are independent but loaded last so a failure in the
    small registries surfaces before the larger hierarchy is touched.

    Implements: Command Handler.
    """

    def __init__(
        self,
        *,
        reader: ReferenceFileReader,
        policy: SeedPolicy,
        load_hazard_types: LoadHazardTypes,
        load_impact_metrics: LoadImpactMetrics,
        load_places: LoadPlaces,
    ) -> None:
        """Create the handler.

        Args:
            reader: Reads and validates the reference files.
            policy: Decides whether the actor may seed; checked before any file is
                read, and again by every module handler.
            load_hazard_types: The hazards load use case.
            load_impact_metrics: The impacts load use case.
            load_places: The geography load use case.
        """
        self._reader = reader
        self._policy = policy
        self._load_hazard_types = load_hazard_types
        self._load_impact_metrics = load_impact_metrics
        self._load_places = load_places

    async def __call__(self, command: SeedReferenceData) -> SeedReport:
        """Load every reference file.

        Args:
            command: The validated command.

        Returns:
            One report per file.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
            ValidationError: If the reader rejects a reference file.
        """
        if not self._policy.is_allowed(command.actor_id):
            message = "the actor may not seed reference data"
            raise PermissionDeniedError(message, details={"action": "seed"})
        hazard_types = await self._load_hazard_types(
            LoadReferenceHazardTypes(
                file=self._reader.read_hazard_types(),
                actor_id=command.actor_id,
                dry_run=command.dry_run,
            )
        )
        impact_metrics = await self._load_impact_metrics(
            LoadReferenceImpactMetrics(
                file=self._reader.read_impact_metrics(),
                actor_id=command.actor_id,
                dry_run=command.dry_run,
            )
        )
        places = await self._load_places(
            LoadReferencePlaces(
                file=self._reader.read_places(),
                actor_id=command.actor_id,
                dry_run=command.dry_run,
            )
        )
        return SeedReport(
            dry_run=command.dry_run,
            hazard_types=hazard_types,
            impact_metrics=impact_metrics,
            places=places,
        )
