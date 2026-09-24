"""Authorised read use cases for impacts: best figures, claims and assets.

Visibility follows the event (**proposed**): an actor the moderation policy allows
reads the impacts of any existing event; everyone else only those of an event that
is publicly visible (published and verified). A hidden or missing event raises the
same ``NotFoundError``, so hidden event ids cannot be probed.

Best figures are a derived read model: they are computed here, at read time, from
the event's claims by ``BestFigurePolicy``, never stored as facts.

Patterns: Query Service, Policy.
"""

from collections import defaultdict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.authorisation import AuthorisationPolicy
from yakhnama.modules.impacts.application.claims_dto import (
    EventImpacts,
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_ports import (
    HazardEventDirectory,
    ImpactClaimsUnitOfWorkFactory,
    ImpactQueryService,
)
from yakhnama.modules.impacts.application.claims_queries import (
    GetEventImpacts,
    GetInfrastructureAsset,
    ListClaims,
)
from yakhnama.modules.impacts.domain.best_figure import BestFigurePolicy
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.errors import AssetNotFoundError
from yakhnama.modules.impacts.domain.value_objects import ImpactMetricRef
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page


class EventImpactsQueryService:
    """Answers impacts queries with the event's visibility applied.

    Implements: Query Service.
    """

    def __init__(
        self,
        *,
        uow_factory: ImpactClaimsUnitOfWorkFactory,
        reads: ImpactQueryService,
        events: HazardEventDirectory,
        policy: AuthorisationPolicy,
        clock: Clock,
    ) -> None:
        """Create the service.

        Args:
            uow_factory: Opens a read-only claims unit of work for best figures.
            reads: The claims and assets read port.
            events: Tells whether an event exists and is publicly visible.
            policy: Decides whether an actor sees every event (``CanModerate``).
            clock: Source of ``computed_at`` of best figures.
        """
        self._uow_factory = uow_factory
        self._reads = reads
        self._events = events
        self._policy = policy
        self._best_figures = BestFigurePolicy(clock=clock)

    async def get_event_impacts(self, query: GetEventImpacts) -> EventImpacts:
        """Return the best figure per claimed metric and every claim of an event.

        Args:
            query: The event and the actor.

        Returns:
            The figures, ordered by metric code, and the claims in recording order.

        Raises:
            NotFoundError: If the event does not exist or the actor may not see it.
        """
        await self._require_visible(query.actor, query.event_id)
        # Read-only: the unit of work is left without commit, so it rolls back.
        async with self._uow_factory() as uow:
            claims = await uow.impact_claims.list_for_event(query.event_id)
            registry = await uow.impact_metrics.list_all()
        by_metric = defaultdict[str, list[ImpactClaim]](list)
        for claim in claims:
            by_metric[claim.metric.code].append(claim)
        figures = tuple(
            self._best_figures.compute(
                registry.get(ImpactMetricRef(code=code)), by_metric[code]
            )
            for code in sorted(by_metric)
        )
        ordered = sorted(claims, key=lambda claim: (claim.created_at, claim.id))
        return EventImpacts(
            event_id=query.event_id,
            best_figures=figures,
            claims=tuple(ImpactClaimSummary.from_entity(claim) for claim in ordered),
        )

    async def list_claims(self, query: ListClaims) -> Page[ImpactClaimSummary]:
        """Return one page of an event's claims.

        Args:
            query: The event, filters, page request and the actor.

        Returns:
            The page.

        Raises:
            NotFoundError: If the event does not exist or the actor may not see it.
            ValidationError: If the cursor is invalid.
        """
        await self._require_visible(query.actor, query.event_id)
        return await self._reads.list_claims(query)

    async def get_asset(
        self, query: GetInfrastructureAsset
    ) -> InfrastructureAssetDetail:
        """Return one infrastructure asset.

        Args:
            query: The asset.

        Returns:
            The detail view.

        Raises:
            AssetNotFoundError: If the asset does not exist.
        """
        asset = await self._reads.get_asset(query.asset_id)
        if asset is None:
            message = "the infrastructure asset does not exist"
            raise AssetNotFoundError(message, details={"asset_id": str(query.asset_id)})
        return asset

    async def _require_visible(self, actor: Actor, event_id: EntityId) -> None:
        is_visible = (
            await self._events.exists(event_id)
            if self._policy.is_allowed(actor)
            else await self._events.is_publicly_visible(event_id)
        )
        if not is_visible:
            message = "the hazard event does not exist"
            raise NotFoundError(message, details={"event_id": str(event_id)})
