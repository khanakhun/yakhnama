"""Ports for impact claims, infrastructure assets and damage records.

``ImpactClaimsUnitOfWork`` is a separate protocol from ``ImpactsUnitOfWork`` (the
metric registry's), so the registry's existing adapter stays valid while the claims
adapter is built; it exposes the metric repository as well, because claims are
validated against their metric in the same transaction.

The ports towards other modules (``HazardEventDirectory`` over the ``events``
facade, ``ImpactSourceMarker`` over the ``provenance`` facade) are declared here in
this module's terms and bound in the composition root, so impacts never imports
events or provenance and no import cycle can form.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import Sequence
from typing import Protocol

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.claims_dto import (
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_queries import ListClaims
from yakhnama.modules.impacts.application.ports import ImpactMetricRepository
from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.value_objects import SourceTypeName
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class ImpactClaimRepository(Protocol):
    """Loads and stages ``ImpactClaim`` aggregates; there is no delete.

    Implements: Repository (port side).
    """

    async def get(self, claim_id: EntityId) -> ImpactClaim | None:
        """Return one claim, active or retracted.

        Args:
            claim_id: The claim.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def list_for_event(self, event_id: EntityId) -> Sequence[ImpactClaim]:
        """Return every claim of an event, active and retracted.

        Args:
            event_id: The hazard event.

        Returns:
            The claims ordered by ``created_at``, then id.
        """
        ...

    async def add(self, claim: ImpactClaim) -> None:
        """Stage a new claim.

        Args:
            claim: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, claim: ImpactClaim) -> None:
        """Stage a changed claim; only retraction changes a stored claim.

        Args:
            claim: The new state.

        Raises:
            NotFoundError: If the claim is not stored.
        """
        ...


class InfrastructureAssetRepository(Protocol):
    """Loads and stages ``InfrastructureAsset`` aggregates.

    Implements: Repository (port side).
    """

    async def get(self, asset_id: EntityId) -> InfrastructureAsset | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def get_by_osm_id(self, osm_id: str) -> InfrastructureAsset | None:
        """Return the asset registered for an OpenStreetMap element.

        Args:
            osm_id: The element, for example ``way/123456``.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, asset: InfrastructureAsset) -> None:
        """Stage a new asset.

        Args:
            asset: The new aggregate.

        Raises:
            ConflictError: If the id or the OpenStreetMap element is taken.
        """
        ...


class DamageRecordRepository(Protocol):
    """Loads and stages ``DamageRecord`` aggregates; there is no delete.

    Implements: Repository (port side).
    """

    async def get(self, damage_id: EntityId) -> DamageRecord | None:
        """Return one damage record, active or retracted.

        Args:
            damage_id: The record.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, record: DamageRecord) -> None:
        """Stage a new damage record.

        Args:
            record: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, record: DamageRecord) -> None:
        """Stage a changed record; only retraction changes a stored record.

        Args:
            record: The new state.

        Raises:
            NotFoundError: If the record is not stored.
        """
        ...


class ImpactClaimsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary for claims, assets and damage, with the metrics.

    Implements: Unit of Work.
    """

    @property
    def impact_metrics(self) -> ImpactMetricRepository:
        """Return the metric repository bound to this transaction."""
        ...

    @property
    def impact_claims(self) -> ImpactClaimRepository:
        """Return the claim repository bound to this transaction."""
        ...

    @property
    def infrastructure_assets(self) -> InfrastructureAssetRepository:
        """Return the asset repository bound to this transaction."""
        ...

    @property
    def damage_records(self) -> DamageRecordRepository:
        """Return the damage record repository bound to this transaction."""
        ...


type ImpactClaimsUnitOfWorkFactory = UnitOfWorkFactory[ImpactClaimsUnitOfWork]
"""Opens a fresh claims unit of work per use case."""


class HazardEventDirectory(Protocol):
    """What impacts needs to know about hazard events, through the ``events`` facade.

    Implements: Adapter (port side).
    """

    async def exists(self, event_id: EntityId) -> bool:
        """Tell whether a hazard event exists, whatever its status.

        Args:
            event_id: The event.

        Returns:
            ``True`` if it exists.
        """
        ...

    async def is_publicly_visible(self, event_id: EntityId) -> bool:
        """Tell whether anyone may read the event (published and verified).

        Args:
            event_id: The event.

        Returns:
            ``True`` if the event exists and a non-moderator may read it.
        """
        ...


class ImpactSourceMarker(Protocol):
    """Marks a provenance source referenced and tells its type (``provenance``).

    Marking an already referenced source is a no-op, so the call is safe to repeat.

    Implements: Adapter (port side).
    """

    async def mark_referenced(
        self, source_id: EntityId, *, actor: Actor
    ) -> SourceTypeName:
        """Mark the source referenced.

        Args:
            source_id: The source a claim, asset or damage record cites.
            actor: The moderator whose record cites it.

        Returns:
            The source's type, which the best-figure policy ranks.

        Raises:
            NotFoundError: If the source does not exist.
        """
        ...


class ImpactQueryService(Protocol):
    """Read port for claims and assets, implemented with SQL in infrastructure.

    Visibility is applied by ``EventImpactsQueryService`` before this port is
    called.

    Implements: Query Service.
    """

    async def list_claims(self, query: ListClaims) -> Page[ImpactClaimSummary]:
        """Return one page of an event's claims.

        Ordered by ``created_at``, then id; the cursor's ``sort_key`` is
        ``created_at`` in ISO 8601 and its ``last_id`` the last claim's id.

        Args:
            query: The event, filters and page request; ``actor`` is ignored here.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get_asset(self, asset_id: EntityId) -> InfrastructureAssetDetail | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The detail view, or ``None``.
        """
        ...
