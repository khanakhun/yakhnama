"""Orchestrates loading every reference file, idempotently, in dependency order.

The handler reads each file through the ``ReferenceFileReader`` port (the YAML adapter
lives with the composition root, because reading files is I/O) and hands it to the
module's load use case: hazard types, then impact metrics, then places, then (when
the composition root wires the ``DatasetSeedStep``) the ingestion dataset catalog,
with or without its synthetic fixture entries, and last (when it wires the
``DemoAccountsSeedStep``, in development and test only) the development demo accounts,
their roles and the demo organisation. Each module
loads in its own unit of work, so the seed is not one atomic transaction; every load
is idempotent, so re-running after a partial failure completes the work without
duplicating anything. It depends only on the modules' public facades.

Patterns: Command, Command Handler, Adapter (port side), DTO.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.public import LoadReferencePlaces, PlaceReferenceFile
from yakhnama.modules.geography.public import LoadReport as PlaceLoadReport
from yakhnama.modules.hazards.public import (
    HazardTypeReferenceFile,
    LoadReferenceHazardTypes,
)
from yakhnama.modules.hazards.public import LoadReport as HazardTypeLoadReport
from yakhnama.modules.identity.public import (
    Actor,
    AuthorisationPolicy,
    SeedAccount,
    SeedAccounts,
    SeedAccountsReport,
    SeedOrganization,
    require_allowed,
)
from yakhnama.modules.impacts.public import (
    ImpactMetricReferenceFile,
    LoadReferenceImpactMetrics,
)
from yakhnama.modules.impacts.public import LoadReport as ImpactMetricLoadReport
from yakhnama.modules.ingestion.public import (
    DatasetReferenceFile,
    LoadReferenceDatasets,
)
from yakhnama.modules.ingestion.public import LoadReport as DatasetLoadReport

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

type LoadDatasets = Callable[[LoadReferenceDatasets], Awaitable[DatasetLoadReport]]
"""The ingestion catalog load use case, usually ``LoadReferenceDatasetsHandler``."""

type LoadAccounts = Callable[[SeedAccounts], Awaitable[SeedAccountsReport]]
"""The identity accounts use case, usually ``SeedAccountsHandler``."""


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


class DatasetReferenceReader(Protocol):
    """Reads and validates the dataset catalog reference file.

    Implements: Adapter (port side).
    """

    def read_datasets(self) -> DatasetReferenceFile:
        """Return the parsed dataset catalog file.

        Returns:
            The validated file.

        Raises:
            ValidationError: If the file cannot be read or does not match its
                model; the kernel error, as for the other reference files.
        """
        ...


class DatasetSeedStep:
    """What the seed needs to load the ingestion dataset catalog.

    Implements: Dependency Injection.

    Attributes:
        reader: Reads ``datasets.yaml``.
        load: The ingestion catalog load use case.
        include_fixtures: Also register entries marked ``is_fixture`` (synthetic
            test data); the composition root sets it outside production only.
    """

    def __init__(
        self,
        *,
        reader: DatasetReferenceReader,
        load: LoadDatasets,
        include_fixtures: bool,
    ) -> None:
        """Group the step's dependencies.

        Args:
            reader: Reads ``datasets.yaml``.
            load: The ingestion catalog load use case.
            include_fixtures: Whether fixture entries are registered too.
        """
        self.reader = reader
        self.load = load
        self.include_fixtures = include_fixtures


class DemoAccountsSeedStep:
    """What the seed needs to ensure the development demo accounts.

    Implements: Dependency Injection.

    Attributes:
        load: The identity accounts use case.
        organization: The demo organisation.
        accounts: The demo accounts, with their roles and memberships.
    """

    def __init__(
        self,
        *,
        load: LoadAccounts,
        organization: SeedOrganization,
        accounts: tuple[SeedAccount, ...],
    ) -> None:
        """Group the step's dependencies.

        Args:
            load: The identity accounts use case.
            organization: The demo organisation.
            accounts: The demo accounts.
        """
        self.load = load
        self.organization = organization
        self.accounts = accounts


class SeedReferenceData(BaseModel):
    """Load every reference file into its module.

    Implements: Command.

    Attributes:
        actor: Who asks; the seed normally runs as a system actor holding
            ``admin``, which ``CanManageReferenceData`` allows.
        dry_run: Compute every report but roll every load back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
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
        datasets: Report of the dataset catalog load; ``None`` when the seed was
            built without the dataset step.
        accounts: Report of the demo accounts step; ``None`` when the seed was
            built without it (production, or no ``oidc_issuer``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dry_run: bool
    hazard_types: HazardTypeLoadReport
    impact_metrics: ImpactMetricLoadReport
    places: PlaceLoadReport
    datasets: DatasetLoadReport | None = None
    accounts: SeedAccountsReport | None = None

    @property
    def is_unchanged(self) -> bool:
        """Return ``True`` if no load created or updated anything."""
        return (
            self.hazard_types.is_unchanged
            and self.impact_metrics.is_unchanged
            and self.places.is_unchanged
            and (self.datasets is None or self.datasets.is_unchanged)
            and (self.accounts is None or self.accounts.is_unchanged)
        )


class SeedReferenceDataHandler:
    """Seed hazard types, impact metrics, places, datasets and demo accounts.

    Hazard types and impact metrics come first because later phases' records refer
    to them by code; places are independent but loaded after them so a failure in
    the small registries surfaces before the larger hierarchy is touched. The
    dataset catalog, when wired, comes next: nothing else refers to it. The demo
    accounts, when wired (development only), come last.

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected load step
        self,
        *,
        reader: ReferenceFileReader,
        policy: AuthorisationPolicy,
        load_hazard_types: LoadHazardTypes,
        load_impact_metrics: LoadImpactMetrics,
        load_places: LoadPlaces,
        datasets: DatasetSeedStep | None = None,
        accounts: DemoAccountsSeedStep | None = None,
    ) -> None:
        """Create the handler.

        Args:
            reader: Reads and validates the reference files.
            policy: Decides whether the actor may seed; checked before any file is
                read, and again by every module handler.
            load_hazard_types: The hazards load use case.
            load_impact_metrics: The impacts load use case.
            load_places: The geography load use case.
            datasets: The dataset catalog step; ``None`` leaves the catalog out
                (``build_seed_handler`` always wires it).
            accounts: The demo accounts step; ``None`` leaves them out
                (``build_seed_handler`` wires it in development and test only).
        """
        self._reader = reader
        self._policy = policy
        self._load_hazard_types = load_hazard_types
        self._load_impact_metrics = load_impact_metrics
        self._load_places = load_places
        self._datasets = datasets
        self._accounts = accounts

    async def __call__(self, command: SeedReferenceData) -> SeedReport:
        """Load every reference file.

        Args:
            command: The validated command.

        Returns:
            One report per file.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ValidationError: If the reader rejects a reference file.
        """
        require_allowed(self._policy, command.actor, action="seed reference data")
        hazard_types = await self._load_hazard_types(
            LoadReferenceHazardTypes(
                file=self._reader.read_hazard_types(),
                actor=command.actor,
                dry_run=command.dry_run,
            )
        )
        impact_metrics = await self._load_impact_metrics(
            LoadReferenceImpactMetrics(
                file=self._reader.read_impact_metrics(),
                actor=command.actor,
                dry_run=command.dry_run,
            )
        )
        places = await self._load_places(
            LoadReferencePlaces(
                file=self._reader.read_places(),
                actor=command.actor,
                dry_run=command.dry_run,
            )
        )
        return SeedReport(
            dry_run=command.dry_run,
            hazard_types=hazard_types,
            impact_metrics=impact_metrics,
            places=places,
            datasets=await self._load_datasets(command),
            accounts=await self._load_accounts(command),
        )

    async def _load_datasets(
        self, command: SeedReferenceData
    ) -> DatasetLoadReport | None:
        if self._datasets is None:
            return None
        return await self._datasets.load(
            LoadReferenceDatasets(
                actor=command.actor,
                file=self._datasets.reader.read_datasets(),
                include_fixtures=self._datasets.include_fixtures,
                dry_run=command.dry_run,
            )
        )

    async def _load_accounts(
        self, command: SeedReferenceData
    ) -> SeedAccountsReport | None:
        if self._accounts is None:
            return None
        return await self._accounts.load(
            SeedAccounts(
                actor=command.actor,
                organization=self._accounts.organization,
                accounts=self._accounts.accounts,
                dry_run=command.dry_run,
            )
        )
