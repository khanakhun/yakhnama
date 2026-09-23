"""In-memory fakes of the impacts ports.

The repositories stage writes until the unit of work commits, exactly as a
rolled-back database transaction would leave the tables unchanged. The metric query
service reads the repository's committed rows through the same specifications the
SQL implementation compiles, and pages by keyset on ``code``. The claims side adds
claim, asset and damage repositories to the same unit of work (so it satisfies both
``ImpactsUnitOfWork`` and ``ImpactClaimsUnitOfWork``), a claims query service, and
fakes of the ports towards the events and provenance modules.

Patterns: Fake.
"""

from collections.abc import Iterable, Mapping, Sequence

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.claims_dto import (
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_queries import ListClaims
from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.modules.impacts.domain.value_objects import SourceTypeName
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


class InMemoryImpactMetricRepository:
    """``ImpactMetricRepository`` over a dictionary keyed by code.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored metrics, as a committed transaction left them.
    """

    def __init__(self, metrics: Iterable[ImpactMetric] = ()) -> None:
        """Create the repository.

        Args:
            metrics: Metrics that exist before the test acts.
        """
        self.committed: dict[str, ImpactMetric] = {
            metric.code: metric for metric in metrics
        }
        self._staged: dict[str, ImpactMetric] = {}

    def _current(self) -> dict[str, ImpactMetric]:
        return {**self.committed, **self._staged}

    async def get_by_code(self, code: str) -> ImpactMetric | None:
        """Return the metric with ``code``, staged changes included.

        Args:
            code: The metric code.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(code)

    async def list_all(self) -> ImpactMetricRegistry:
        """Return every stored and staged metric, ordered by code.

        Returns:
            The registry.
        """
        current = self._current()
        return ImpactMetricRegistry.from_metrics(
            current[code] for code in sorted(current)
        )

    async def add(self, metric: ImpactMetric) -> None:
        """Stage a new metric.

        Args:
            metric: The new aggregate.

        Raises:
            ConflictError: If the code or the id is already taken.
        """
        current = self._current()
        if metric.code in current or any(
            stored.id == metric.id for stored in current.values()
        ):
            message = f"impact metric {metric.code!r} already exists"
            raise ConflictError(message)
        self._staged[metric.code] = metric

    async def save(self, metric: ImpactMetric) -> None:
        """Stage a changed metric.

        Args:
            metric: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no stored metric has this code and id.
        """
        stored = self._current().get(metric.code)
        if stored is None or stored.id != metric.id:
            message = f"impact metric {metric.code!r} is not stored"
            raise NotFoundError(message)
        self._staged[metric.code] = metric

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class _StagedById[ItemT: ImpactClaim | InfrastructureAsset | DamageRecord]:
    """Stores aggregates by id and stages writes until commit.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored aggregates, as a committed transaction left them.
    """

    def __init__(self, items: Iterable[ItemT], label: str) -> None:
        """Create the store.

        Args:
            items: Aggregates that exist before the test acts.
            label: What the aggregates are, for error messages.
        """
        self.committed: dict[EntityId, ItemT] = {item.id: item for item in items}
        self._staged: dict[EntityId, ItemT] = {}
        self._label = label

    def current(self) -> dict[EntityId, ItemT]:
        """Return committed and staged aggregates, staged ones winning."""
        return {**self.committed, **self._staged}

    async def get(self, item_id: EntityId) -> ItemT | None:
        """Return one aggregate, staged changes included.

        Args:
            item_id: The id.

        Returns:
            The aggregate, or ``None``.
        """
        return self.current().get(item_id)

    async def add(self, item: ItemT) -> None:
        """Stage a new aggregate.

        Args:
            item: The aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if item.id in self.current():
            message = f"the {self._label} already exists"
            raise ConflictError(message)
        self._staged[item.id] = item

    async def save(self, item: ItemT) -> None:
        """Stage a changed aggregate.

        Args:
            item: The new state.

        Raises:
            NotFoundError: If it is not stored.
        """
        if item.id not in self.current():
            message = f"the {self._label} is not stored"
            raise NotFoundError(message)
        self._staged[item.id] = item

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryImpactClaimRepository(_StagedById[ImpactClaim]):
    """``ImpactClaimRepository`` over a dictionary keyed by claim id.

    Implements: Fake (of Repository).
    """

    def __init__(self, claims: Iterable[ImpactClaim] = ()) -> None:
        """Create the repository.

        Args:
            claims: Claims that exist before the test acts.
        """
        super().__init__(claims, "impact claim")

    async def list_for_event(self, event_id: EntityId) -> Sequence[ImpactClaim]:
        """Return every claim of an event in recording order, staged included.

        Args:
            event_id: The hazard event.

        Returns:
            The claims.
        """
        return sorted(
            (claim for claim in self.current().values() if claim.event_id == event_id),
            key=lambda claim: (claim.created_at, claim.id),
        )


class InMemoryInfrastructureAssetRepository(_StagedById[InfrastructureAsset]):
    """``InfrastructureAssetRepository`` over a dictionary keyed by asset id.

    Implements: Fake (of Repository).
    """

    def __init__(self, assets: Iterable[InfrastructureAsset] = ()) -> None:
        """Create the repository.

        Args:
            assets: Assets that exist before the test acts.
        """
        super().__init__(assets, "infrastructure asset")

    async def get_by_osm_id(self, osm_id: str) -> InfrastructureAsset | None:
        """Return the asset with an OpenStreetMap element, staged included.

        Args:
            osm_id: The element.

        Returns:
            The aggregate, or ``None``.
        """
        return next(
            (asset for asset in self.current().values() if asset.osm_id == osm_id),
            None,
        )


class InMemoryDamageRecordRepository(_StagedById[DamageRecord]):
    """``DamageRecordRepository`` over a dictionary keyed by record id.

    Implements: Fake (of Repository).
    """

    def __init__(self, records: Iterable[DamageRecord] = ()) -> None:
        """Create the repository.

        Args:
            records: Records that exist before the test acts.
        """
        super().__init__(records, "damage record")


class InMemoryImpactsUnitOfWork(InMemoryUnitOfWork):
    """``ImpactsUnitOfWork`` and ``ImpactClaimsUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        impact_metrics: The metric repository bound to this unit of work.
        impact_claims: The claim repository bound to this unit of work.
        infrastructure_assets: The asset repository bound to this unit of work.
        damage_records: The damage record repository bound to this unit of work.
    """

    def __init__(
        self,
        metrics: Iterable[ImpactMetric] = (),
        *,
        claims: Iterable[ImpactClaim] = (),
        assets: Iterable[InfrastructureAsset] = (),
        damage: Iterable[DamageRecord] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            metrics: Metrics that exist before the test acts.
            claims: Claims that exist before the test acts.
            assets: Assets that exist before the test acts.
            damage: Damage records that exist before the test acts.
        """
        super().__init__()
        self.impact_metrics = InMemoryImpactMetricRepository(metrics)
        self.impact_claims = InMemoryImpactClaimRepository(claims)
        self.infrastructure_assets = InMemoryInfrastructureAssetRepository(assets)
        self.damage_records = InMemoryDamageRecordRepository(damage)

    def _on_commit(self) -> None:
        self.impact_metrics.apply_staged()
        self.impact_claims.apply_staged()
        self.infrastructure_assets.apply_staged()
        self.damage_records.apply_staged()

    def _on_rollback(self) -> None:
        self.impact_metrics.discard_staged()
        self.impact_claims.discard_staged()
        self.infrastructure_assets.discard_staged()
        self.damage_records.discard_staged()


class InMemoryImpactMetricQueryService:
    """``ImpactMetricQueryService`` reading a fake repository's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, repository: InMemoryImpactMetricRepository) -> None:
        """Create the query service.

        Args:
            repository: The repository whose committed rows are served.
        """
        self._repository = repository

    async def list_impact_metrics(
        self, query: ListImpactMetrics
    ) -> Page[ImpactMetricSummary]:
        """Filter, order by code and page like the SQL implementation.

        Args:
            query: Filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        specification = query.to_specification()
        cursor = query.page.decode_cursor()
        after = "" if cursor is None else cursor.sort_key
        matching = [
            (metric, summary)
            for metric in sorted(
                self._repository.committed.values(), key=lambda item: item.code
            )
            if metric.code > after
            for summary in (ImpactMetricSummary.from_entity(metric),)
            if specification is None or specification.is_satisfied_by(summary)
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1][0]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[ImpactMetricSummary](
            items=tuple(summary for _, summary in window), next_cursor=next_cursor
        )

    async def get(self, code: str) -> ImpactMetricDetail | None:
        """Return the detail view of one committed metric.

        Args:
            code: The metric code.

        Returns:
            The detail view, or ``None``.
        """
        metric = self._repository.committed.get(code)
        return None if metric is None else ImpactMetricDetail.from_entity(metric)


class InMemoryImpactQueryService:
    """``ImpactQueryService`` over a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryImpactsUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def list_claims(self, query: ListClaims) -> Page[ImpactClaimSummary]:
        """Filter, order by recording time and page like the SQL implementation.

        Args:
            query: The event, filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        matching = [
            claim
            for claim in sorted(
                self._uow.impact_claims.committed.values(),
                key=lambda claim: (claim.created_at, claim.id),
            )
            if claim.event_id == query.event_id
            and (query.metric_code is None or claim.metric.code == query.metric_code)
            and (query.include_retracted or claim.is_active)
            and (
                cursor is None
                or (claim.created_at.isoformat(), claim.id)
                > (cursor.sort_key, cursor.last_id)
            )
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[ImpactClaimSummary](
            items=tuple(ImpactClaimSummary.from_entity(claim) for claim in window),
            next_cursor=next_cursor,
        )

    async def get_asset(self, asset_id: EntityId) -> InfrastructureAssetDetail | None:
        """Return the detail view of one committed asset.

        Args:
            asset_id: The asset.

        Returns:
            The detail view, or ``None``.
        """
        asset = self._uow.infrastructure_assets.committed.get(asset_id)
        return None if asset is None else InfrastructureAssetDetail.from_entity(asset)


class FakeHazardEventDirectory:
    """``HazardEventDirectory`` knowing fixed sets of events.

    Implements: Fake.
    """

    def __init__(
        self,
        existing: Iterable[EntityId] = (),
        public: Iterable[EntityId] = (),
    ) -> None:
        """Create the directory.

        Args:
            existing: Events that exist but are not publicly visible.
            public: Events that exist and are publicly visible.
        """
        self._public = frozenset(public)
        self._existing = frozenset(existing) | self._public

    async def exists(self, event_id: EntityId) -> bool:
        """Tell whether the event is known.

        Args:
            event_id: The event.

        Returns:
            ``True`` if known.
        """
        return event_id in self._existing

    async def is_publicly_visible(self, event_id: EntityId) -> bool:
        """Tell whether the event is publicly visible.

        Args:
            event_id: The event.

        Returns:
            ``True`` if it is in ``public``.
        """
        return event_id in self._public


class RecordingImpactSourceMarker:
    """``ImpactSourceMarker`` over fixed source types that records every mark.

    Implements: Fake.

    Attributes:
        marked: Source ids marked, in call order.
    """

    def __init__(
        self, sources: Mapping[EntityId, SourceTypeName] | None = None
    ) -> None:
        """Create the marker.

        Args:
            sources: The type of every source that exists.
        """
        self._sources = dict(sources or {})
        self.marked: list[EntityId] = []

    async def mark_referenced(
        self, source_id: EntityId, *, actor: Actor
    ) -> SourceTypeName:
        """Record the mark and return the source's type.

        Args:
            source_id: The source.
            actor: Ignored.

        Returns:
            Its type.

        Raises:
            NotFoundError: If the source is unknown.
        """
        source_type = self._sources.get(source_id)
        if source_type is None:
            message = "the source does not exist"
            raise NotFoundError(message)
        self.marked.append(source_id)
        return source_type
