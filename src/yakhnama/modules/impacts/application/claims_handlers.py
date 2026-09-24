"""Write-side use cases for impact claims, infrastructure assets and damage.

Every handler asks its ``AuthorisationPolicy`` (``CanModerate``) first and raises
``PermissionDeniedError`` before reading or staging anything. Facts are
append-only: a claim or damage record is retracted with a reason, and a claim is
corrected by a new claim that supersedes it, both saved in one unit of work.

Every recorded fact cites a source, which is marked referenced (and so becomes
immutable) through ``ImpactSourceMarker`` inside the unit of work, after the
domain has validated the new fact, so an invalid request does not freeze a source.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.authorisation import (
    AuthorisationPolicy,
    acting_user_id,
    require_allowed,
)
from yakhnama.modules.impacts.application.claims_commands import (
    CorrectImpactClaim,
    RecordDamage,
    RecordImpactClaim,
    RegisterInfrastructureAsset,
    RetractDamage,
    RetractImpactClaim,
)
from yakhnama.modules.impacts.application.claims_ports import (
    HazardEventDirectory,
    ImpactClaimsUnitOfWork,
    ImpactClaimsUnitOfWorkFactory,
    ImpactSourceMarker,
)
from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import (
    ImpactClaim,
    ensure_metric_accepts_claims,
    ensure_value_fits_metric,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    AssetNotFoundError,
    DamageRecordNotFoundError,
    ImpactClaimNotFoundError,
    ImpactMetricNotFoundError,
)
from yakhnama.modules.impacts.domain.factories import (
    DamageRecordFactory,
    ImpactClaimFactory,
    InfrastructureAssetFactory,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


class ImpactClaimHandlerDependencies:
    """What every claim, asset and damage handler is built from.

    Implements: Dependency Injection.

    Attributes:
        uow_factory: Opens a claims unit of work per call.
        policy: Decides whether an actor may moderate (``CanModerate``).
        clock: Source of timestamps and event times.
        ids: Source of aggregate and event ids.
        events: Tells whether a hazard event exists.
        sources: Marks cited sources referenced and tells their type.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: ImpactClaimsUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
        events: HazardEventDirectory,
        sources: ImpactSourceMarker,
    ) -> None:
        """Group the dependencies.

        Args:
            uow_factory: Opens a claims unit of work per call.
            policy: Decides whether an actor may moderate.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
            events: Tells whether a hazard event exists.
            sources: Marks cited sources referenced and tells their type.
        """
        self.uow_factory = uow_factory
        self.policy = policy
        self.clock = clock
        self.ids = ids
        self.events = events
        self.sources = sources


def _authorise(
    deps: ImpactClaimHandlerDependencies, actor: Actor, action: str
) -> EntityId:
    require_allowed(deps.policy, actor, action=action)
    return acting_user_id(actor)


async def _require_event(
    deps: ImpactClaimHandlerDependencies, event_id: EntityId
) -> None:
    if not await deps.events.exists(event_id):
        message = "the hazard event does not exist"
        raise NotFoundError(message, details={"event_id": str(event_id)})


async def _load_metric(uow: ImpactClaimsUnitOfWork, code: str) -> ImpactMetric:
    metric = await uow.impact_metrics.get_by_code(code)
    if metric is None:
        message = f"no impact metric with code {code!r}"
        raise ImpactMetricNotFoundError(message, details={"code": code})
    return metric


async def _load_claim(uow: ImpactClaimsUnitOfWork, claim_id: EntityId) -> ImpactClaim:
    claim = await uow.impact_claims.get(claim_id)
    if claim is None:
        message = "the impact claim does not exist"
        raise ImpactClaimNotFoundError(message, details={"claim_id": str(claim_id)})
    return claim


async def _load_asset(
    uow: ImpactClaimsUnitOfWork, asset_id: EntityId
) -> InfrastructureAsset:
    asset = await uow.infrastructure_assets.get(asset_id)
    if asset is None:
        message = "the infrastructure asset does not exist"
        raise AssetNotFoundError(message, details={"asset_id": str(asset_id)})
    return asset


class RecordImpactClaimHandler:
    """Record one metric value for one event from one source.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RecordImpactClaim) -> EntityId:
        """Record the claim.

        Args:
            command: The validated command.

        Returns:
            The id of the new claim.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            NotFoundError: If the event or the source does not exist.
            ImpactMetricNotFoundError: If the metric does not exist.
            ImpactMetricRetiredError: If the metric is retired.
            ClaimValueKindMismatchError: If the value's kind is not the metric's.
            ClaimValueUnitMismatchError: If its unit or currency is not the metric's.
            AssetNotFoundError: If the scope names a missing asset.
        """
        deps = self._deps
        recorded_by = _authorise(deps, command.actor, "record impact claims")
        await _require_event(deps, command.event_id)
        async with deps.uow_factory() as uow:
            metric = await _load_metric(uow, command.metric_code)
            # Checked before the source is marked, so a refused claim leaves the
            # source as it was; the factory repeats the same checks.
            ensure_metric_accepts_claims(metric)
            ensure_value_fits_metric(metric, command.value)
            if command.scope is not None and command.scope.asset_id is not None:
                await _load_asset(uow, command.scope.asset_id)
            source_type = await deps.sources.mark_referenced(
                command.source_id, actor=command.actor
            )
            claim = (
                ImpactClaimFactory(clock=deps.clock, id_generator=deps.ids)
                .record(
                    metric=metric,
                    event_id=command.event_id,
                    value=command.value,
                    confidence=command.confidence,
                    source_id=command.source_id,
                    source_type=source_type,
                    claimed_at=command.claimed_at,
                    recorded_by=recorded_by,
                    scope=command.scope,
                    note=command.note,
                )
                .record_into(uow)
            )
            await uow.impact_claims.add(claim)
            await uow.commit()
        return claim.id


class RetractImpactClaimHandler:
    """Retract a claim; it stays stored and stops counting.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RetractImpactClaim) -> None:
        """Retract the claim.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ImpactClaimNotFoundError: If the claim does not exist.
            ClaimImmutableError: If it is already retracted.
        """
        deps = self._deps
        retracted_by = _authorise(deps, command.actor, "retract impact claims")
        async with deps.uow_factory() as uow:
            claim = await _load_claim(uow, command.claim_id)
            retracted = claim.retract(
                command.reason,
                retracted_by=retracted_by,
                clock=deps.clock,
                id_generator=deps.ids,
            ).record_into(uow)
            await uow.impact_claims.save(retracted)
            await uow.commit()


class CorrectImpactClaimHandler:
    """Correct a claim: a new claim supersedes it and the old one is retracted.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: CorrectImpactClaim) -> EntityId:
        """Correct the claim.

        Args:
            command: The validated command.

        Returns:
            The id of the new, superseding claim.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ImpactClaimNotFoundError: If the claim does not exist.
            ClaimImmutableError: If it is already retracted.
            ImpactMetricRetiredError: If its metric was retired since.
            ClaimValueKindMismatchError: If the value's kind is not the metric's.
            ClaimValueUnitMismatchError: If its unit or currency is not the metric's.
        """
        deps = self._deps
        recorded_by = _authorise(deps, command.actor, "correct impact claims")
        async with deps.uow_factory() as uow:
            claim = await _load_claim(uow, command.claim_id)
            metric = await _load_metric(uow, claim.metric.code)
            superseded, replacement = claim.correct(
                command.value,
                metric=metric,
                reason=command.reason,
                recorded_by=recorded_by,
                clock=deps.clock,
                id_generator=deps.ids,
                confidence=command.confidence,
                claimed_at=command.claimed_at,
                note=command.note,
            ).record_into(uow)
            await uow.impact_claims.save(superseded)
            await uow.impact_claims.add(replacement)
            await uow.commit()
        return replacement.id


class RegisterInfrastructureAssetHandler:
    """Register an infrastructure asset; one asset per OpenStreetMap element.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RegisterInfrastructureAsset) -> EntityId:
        """Register the asset.

        Args:
            command: The validated command.

        Returns:
            The id of the new asset.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ConflictError: If an asset already has the OpenStreetMap element.
            NotFoundError: If the source does not exist.
        """
        deps = self._deps
        _authorise(deps, command.actor, "register infrastructure assets")
        async with deps.uow_factory() as uow:
            if (
                command.osm_id is not None
                and await uow.infrastructure_assets.get_by_osm_id(command.osm_id)
                is not None
            ):
                message = "an asset is already registered for this OpenStreetMap id"
                raise ConflictError(message, details={"osm_id": command.osm_id})
            asset = (
                InfrastructureAssetFactory(clock=deps.clock, id_generator=deps.ids)
                .register(
                    kind=command.kind,
                    name=command.name,
                    source_id=command.source_id,
                    osm_id=command.osm_id,
                    location=command.location,
                    place_code=command.place_code,
                )
                .record_into(uow)
            )
            await deps.sources.mark_referenced(command.source_id, actor=command.actor)
            await uow.infrastructure_assets.add(asset)
            await uow.commit()
        return asset.id


class RecordDamageHandler:
    """Record one source's statement that an asset was damaged during an event.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RecordDamage) -> EntityId:
        """Record the damage.

        Args:
            command: The validated command.

        Returns:
            The id of the new damage record.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            NotFoundError: If the event or the source does not exist.
            AssetNotFoundError: If the asset does not exist.
        """
        deps = self._deps
        recorded_by = _authorise(deps, command.actor, "record damage")
        await _require_event(deps, command.event_id)
        async with deps.uow_factory() as uow:
            await _load_asset(uow, command.asset_id)
            record = (
                DamageRecordFactory(clock=deps.clock, id_generator=deps.ids)
                .record(
                    event_id=command.event_id,
                    asset_id=command.asset_id,
                    level=command.level,
                    confidence=command.confidence,
                    source_id=command.source_id,
                    recorded_at=command.recorded_at,
                    recorded_by=recorded_by,
                    note=command.note,
                )
                .record_into(uow)
            )
            await deps.sources.mark_referenced(command.source_id, actor=command.actor)
            await uow.damage_records.add(record)
            await uow.commit()
        return record.id


class RetractDamageHandler:
    """Retract a damage record; it stays stored and no longer stands.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ImpactClaimHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RetractDamage) -> None:
        """Retract the record.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            DamageRecordNotFoundError: If the record does not exist.
            ClaimImmutableError: If it is already retracted.
        """
        deps = self._deps
        retracted_by = _authorise(deps, command.actor, "retract damage records")
        async with deps.uow_factory() as uow:
            record = await uow.damage_records.get(command.damage_id)
            if record is None:
                message = "the damage record does not exist"
                raise DamageRecordNotFoundError(
                    message, details={"damage_id": str(command.damage_id)}
                )
            retracted = record.retract(
                command.reason,
                retracted_by=retracted_by,
                clock=deps.clock,
                id_generator=deps.ids,
            ).record_into(uow)
            await uow.damage_records.save(retracted)
            await uow.commit()
