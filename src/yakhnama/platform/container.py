"""The composition root for infrastructure: one ``Container`` per application.

``build_container`` binds the kernel ports to their adapters (``Clock`` to
``SystemClock``, ``IdGenerator`` to ``Uuid7Generator``) and builds the engine, session
factory, outbox and unit-of-work factory from ``Settings``. ``yakhnama.main`` stores
the container on ``app.state.container``; routes reach it through the ``get_container``
dependency. Only this module and ``yakhnama.main`` bind ports to adapters
(``AGENTS.md`` §2.1).

Each module's ports are bound here too: its ``UnitOfWorkFactory`` port to
``SqlAlchemyUnitOfWorkFactory(<its SQLAlchemy unit of work>, ...)``, its query service
port to the SQLAlchemy query service, and (from later phases) its outbox subscribers
on ``subscriber_registry``. Importing module infrastructure is what a composition root
is for; nothing inside a module imports this file. ``build_seed_handler`` wires the
reference-data seed used by ``python -m yakhnama.seed``.

``build_task_handlers`` binds background task names to handlers (ADR 0008); see
``yakhnama.platform.tasks.handlers`` for the contract a handler follows.

The Phase 3 recording modules (provenance, reports, media, events, verification,
impact claims, audit) are bound the same way. Where one module's use case needs
another module, its port is answered by an adapter from
``yakhnama.platform.wiring`` built over the other module's facade. The API routers
read the use cases (``submit_report_handler``, ``event_handler_dependencies``, ...)
from this container by field name. ``AuditSubscriber`` is subscribed on the relay
to every domain event type of every module (``wiring.audit``).

The Phase 4 modules follow the same shape: ``build_exchange_ports`` and
``build_ingestion_ports`` bind each module's own ports, ``build_exchange_worker_ports``
answers the exchange runs' ports towards events, impacts, reports, provenance,
hazards and geography with the adapters of ``wiring.exchange``, and
``build_exchange_services`` and ``build_ingestion_services`` wire the use cases the
routers read (``request_export_handler``, ``run_ingestion_handler``, ...) and the
worker runs (``run_export_handler``, ``run_import_handler``,
``execute_ingestion_run_handler``).

Internal (non-facade) imports this composition root needs besides module
infrastructure: the ``*UnitOfWorkFactory``/query-service ports not exported by
older facades (geography, hazards, impacts, identity; unchanged since Phase 1),
adapters' own internal imports named in their module docstrings.

Patterns: Composition Root, Dependency Injection.
"""

import dataclasses
from collections.abc import Mapping
from datetime import timedelta
from importlib.metadata import version
from types import MappingProxyType

import httpx
import structlog
from fastapi import Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from taskiq import AsyncBroker

from yakhnama.modules.audit.infrastructure.queries import SqlAlchemyAuditQueryService
from yakhnama.modules.audit.infrastructure.uow import SqlAlchemyAuditUnitOfWork
from yakhnama.modules.audit.public import (
    AuditQueryService,
    AuditSubscriber,
    AuditUnitOfWorkFactory,
)
from yakhnama.modules.events.infrastructure.queries import SqlAlchemyEventQueryService
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.events.public import (
    EventCitationQueryService,
    EventHandlerDependencies,
    EventQueryService,
    EventRecordQueryService,
    EventsUnitOfWorkFactory,
)
from yakhnama.modules.events.public import (
    moderation_policy as events_moderation_policy,
)
from yakhnama.modules.exchange.infrastructure.adapters.registry import (
    default_format_adapters,
)
from yakhnama.modules.exchange.infrastructure.adapters.s3_artifact_store import (
    S3ArtifactStore,
)
from yakhnama.modules.exchange.infrastructure.queries import (
    SqlAlchemyExchangeQueryService,
)
from yakhnama.modules.exchange.infrastructure.uow import SqlAlchemyExchangeUnitOfWork
from yakhnama.modules.exchange.public import (
    ArtifactStore,
    BackfillReferenceChecker,
    CancelExportHandler,
    ExchangeHandlerDependencies,
    ExchangeJobQueryService,
    ExchangeQueryService,
    ExchangeUnitOfWorkFactory,
    ExportRowSource,
    FormatAdapterRegistry,
    HistoricalEventWriter,
    LineageSourceRegistrar,
    RequestExportHandler,
    RequestImportHandler,
    RunExportHandler,
    RunImportHandler,
)
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.geography.application.ports import (
    GeographyUnitOfWorkFactory,
    PlaceQueryService,
)
from yakhnama.modules.geography.infrastructure.queries import (
    SqlAlchemyPlaceQueryService,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.hazards.application.ports import (
    HazardsUnitOfWorkFactory,
    HazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.queries import (
    SqlAlchemyHazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.modules.identity.application.ports import (
    IdentityQueryService,
    IdentityUnitOfWorkFactory,
)
from yakhnama.modules.identity.infrastructure.queries import (
    SqlAlchemyIdentityQueryService,
)
from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.modules.identity.public import CanManageReferenceData
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.modules.impacts.application.ports import (
    ImpactMetricQueryService,
    ImpactsUnitOfWorkFactory,
)
from yakhnama.modules.impacts.infrastructure.claims_queries import (
    SqlAlchemyImpactQueryService,
)
from yakhnama.modules.impacts.infrastructure.claims_uow import (
    SqlAlchemyImpactClaimsUnitOfWork,
)
from yakhnama.modules.impacts.infrastructure.queries import (
    SqlAlchemyImpactMetricQueryService,
)
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.modules.impacts.public import (
    EventImpactsQueryService,
    ImpactClaimHandlerDependencies,
    ImpactClaimsUnitOfWorkFactory,
    ImpactQueryService,
)
from yakhnama.modules.impacts.public import (
    moderation_policy as impacts_moderation_policy,
)
from yakhnama.modules.ingestion.infrastructure.adapters.reference import (
    reference_adapters,
    reference_pipelines,
)
from yakhnama.modules.ingestion.infrastructure.queries import (
    SqlAlchemyIngestionQueryService,
)
from yakhnama.modules.ingestion.infrastructure.uow import (
    SqlAlchemyIngestionUnitOfWork,
)
from yakhnama.modules.ingestion.public import (
    CatalogueRasterAssetHandler,
    DeprecateDatasetHandler,
    ExecuteIngestionRunHandler,
    IngestionQueryService,
    IngestionUnitOfWorkFactory,
    LoadReferenceDatasetsHandler,
    PipelineClassRegistry,
    PipelineFactory,
    RecordDatasetVersionHandler,
    RegisterDatasetHandler,
    RetireDatasetHandler,
    RunIngestionHandler,
    SourceAdapterRegistry,
)
from yakhnama.modules.media.application.ports import MalwareScanner
from yakhnama.modules.media.infrastructure.adapters.exif import PillowExifReader
from yakhnama.modules.media.infrastructure.adapters.mime import FiletypeMimeSniffer
from yakhnama.modules.media.infrastructure.adapters.s3_storage import S3StoragePort
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    ClamAvScanner,
    NoOpMalwareScanner,
    tcp_connector,
)
from yakhnama.modules.media.infrastructure.queries import SqlAlchemyMediaQueryService
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.modules.media.public import (
    AuthorisedMediaQueryService,
    CompleteUploadHandler,
    ExifReader,
    MediaQueryService,
    MediaUnitOfWorkFactory,
    MimeSniffer,
    ModerateMediaHandler,
    RecordScanResultHandler,
    RequestUploadHandler,
    StoragePort,
)
from yakhnama.modules.provenance.infrastructure.queries import (
    SqlAlchemySourceQueryService,
)
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.modules.provenance.public import (
    AuthorisedSourceQueryService,
    MarkSourceReferencedHandler,
    ProvenanceUnitOfWorkFactory,
    RegisterSourceHandler,
    SourceQueryService,
    SourceRegistrar,
)
from yakhnama.modules.reports.infrastructure.queries import (
    SqlAlchemyNearbyReportsFinder,
    SqlAlchemyReportQueryService,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.modules.reports.public import (
    AuthorisedReportQueryService,
    NearbyReportsFinder,
    ReportQueryService,
    ReportsUnitOfWorkFactory,
    ReviseReportHandler,
    RunTriageHandler,
    SubmitReportHandler,
    WithdrawReportHandler,
)
from yakhnama.modules.verification.infrastructure.queries import (
    SqlAlchemyVerificationQueryService,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.modules.verification.public import (
    OpenVerificationCaseHandler,
    VerificationCaseQueryService,
    VerificationHandlerDependencies,
    VerificationQueryService,
    VerificationUnitOfWorkFactory,
)
from yakhnama.modules.verification.public import (
    moderation_policy as verification_moderation_policy,
)
from yakhnama.platform.auth.jwks import HttpJwksClient
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.db import create_engine, create_session_factory
from yakhnama.platform.idempotency.sqlalchemy_store import SqlAlchemyIdempotencyStore
from yakhnama.platform.idempotency.store import IdempotencyStore
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.sqlalchemy_store import SqlAlchemyOutboxStore
from yakhnama.platform.outbox.store import OutboxStore
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.ratelimit.limiter import InMemoryRateLimiter, RateLimiter
from yakhnama.platform.ratelimit.redis_limiter import RedisRateLimiter
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.broker import build_broker
from yakhnama.platform.tasks.handlers import (
    EXCHANGE_RUN_EXPORT_TASK,
    EXCHANGE_RUN_IMPORT_TASK,
    IDEMPOTENCY_PURGE_TASK,
    INGESTION_RUN_TASK,
    MEDIA_SCAN_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
    REPORTS_TRIAGE_TASK,
    TaskHandler,
    TaskHandlerRegistry,
)
from yakhnama.platform.tasks.taskiq_adapter import TaskiqTaskQueue, register_tasks
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.platform.wiring.audit import (
    EVENT_MODULES,
    DomainEventTypeRegistry,
    subscribe_to_every_event,
)
from yakhnama.platform.wiring.events import (
    EventSourceMarkerAdapter,
    EventTimelineAdapter,
    PlaceDirectoryAdapter,
    ReportFactsAdapter,
    VerificationCaseOpenerAdapter,
)
from yakhnama.platform.wiring.exchange import (
    BatchReferencePorts,
    FacadeExportRowSource,
    IdentityActorLookupAdapter,
    ProvenanceLineageRegistrarAdapter,
    RegistryReferenceCheckerAdapter,
    RunExportTaskAdapter,
    RunImportTaskAdapter,
    SessionBatchEventWriter,
)
from yakhnama.platform.wiring.impacts import (
    HazardEventDirectoryAdapter,
    ImpactSourceMarkerAdapter,
)
from yakhnama.platform.wiring.ingestion import ExecuteIngestionRunTaskAdapter
from yakhnama.platform.wiring.media import ReportSourceAdapter, ScanTaskAdapter
from yakhnama.platform.wiring.provenance import SourceCitationCheckerAdapter
from yakhnama.platform.wiring.reports import (
    MediaOwnershipAdapter,
    PhotoEvidenceAdapter,
    RunTriageTaskAdapter,
)
from yakhnama.platform.wiring.verification import (
    ReportOwnerAdapter,
    ReviewerEligibilityAdapter,
)
from yakhnama.seed.application import DatasetSeedStep, SeedReferenceDataHandler
from yakhnama.seed.infrastructure import YamlReferenceFileReader
from yakhnama.shared_kernel.clock import Clock, SystemClock
from yakhnama.shared_kernel.ids import IdGenerator, Uuid7Generator
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskQueue


# A frozen dataclass rather than a Pydantic model: the container holds live resources
# (an engine with a connection pool, factories), not data, and nothing about it is
# validated or serialised.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Every long-lived dependency of one application instance.

    Implements: Composition Root.

    Attributes:
        settings: The settings everything below was built from.
        clock: The ``Clock`` port bound for this application.
        id_generator: The ``IdGenerator`` port bound for this application.
        engine: The async database engine; disposed when the application stops.
        session_factory: Opens one ``AsyncSession`` per unit of work.
        outbox_writer: Stages domain events as outbox rows on commit.
        uow_factory: Opens a plain ``SqlAlchemyUnitOfWork``; modules bind their own
            factories next to it.
        subscriber_registry: Outbox subscribers per ``event_type``.
        outbox_store: The ``OutboxStore`` port (PostgreSQL) the relay records on.
        outbox_relay: Delivers pending outbox messages to ``subscriber_registry``.
        geography_uow_factory: The geography ``UnitOfWorkFactory`` port.
        hazards_uow_factory: The hazards ``UnitOfWorkFactory`` port.
        impacts_uow_factory: The impacts ``UnitOfWorkFactory`` port.
        place_query_service: The geography ``PlaceQueryService`` port.
        hazard_type_query_service: The hazards ``HazardTypeQueryService`` port.
        impact_metric_query_service: The impacts ``ImpactMetricQueryService`` port.
        identity_uow_factory: The identity ``UnitOfWorkFactory`` port.
        identity_query_service: The identity ``IdentityQueryService`` port.
        provenance_uow_factory: The provenance ``UnitOfWorkFactory`` port.
        reports_uow_factory: The reports ``UnitOfWorkFactory`` port.
        media_uow_factory: The media ``UnitOfWorkFactory`` port.
        events_uow_factory: The events ``UnitOfWorkFactory`` port.
        verification_uow_factory: The verification ``UnitOfWorkFactory`` port.
        impact_claims_uow_factory: The impact claims ``UnitOfWorkFactory`` port.
        audit_uow_factory: The audit ``UnitOfWorkFactory`` port.
        public_coordinates: The rounding applied to every published reporter
            position, from ``public_coordinate_decimals``.
        source_query_service: The provenance ``SourceQueryService`` port.
        report_query_service: The reports ``ReportQueryService`` port (exact
            positions; internal).
        nearby_reports_finder: The reports ``NearbyReportsFinder`` port.
        media_query_service: The media ``MediaQueryService`` port (internal).
        event_query_service: The events ``EventQueryService`` port.
        verification_query_service: The verification ``VerificationQueryService``
            port.
        impact_query_service: The impact claims ``ImpactQueryService`` port.
        audit_query_service: The audit ``AuditQueryService`` port.
        storage: The media ``StoragePort`` (S3-compatible).
        exif_reader: The media ``ExifReader`` over ``storage``.
        mime_sniffer: The media ``MimeSniffer`` over ``storage``.
        malware_scanner: The media ``MalwareScanner`` selected by
            ``malware_scanner``.
        source_registrar: Registers sources (provenance).
        source_queries: Authorised source reads.
        submit_report_handler: Submits reports.
        revise_report_handler: Revises reports.
        withdraw_report_handler: Withdraws reports.
        report_queries: Authorised report reads.
        run_triage_handler: Triages a report; run by ``reports.run_triage``.
        request_upload_handler: Grants presigned uploads.
        complete_upload_handler: Completes uploads.
        moderate_media_handler: Moderates media.
        record_scan_result_handler: Stores a scan verdict; run by ``media.scan``.
        media_queries: Authorised media reads.
        event_handler_dependencies: What every events handler is built from.
        event_queries: Authorised events reads.
        verification_handler_dependencies: What every verification handler is
            built from.
        verification_queries: Authorised verification reads.
        impact_claim_handler_dependencies: What every claims handler is built from.
        event_impacts_queries: Authorised impacts reads.
        event_types: Every module's domain event classes by ``event_type``.
        audit_subscriber: Writes the audit log; subscribed to every event type.
        exchange_uow_factory: The exchange ``UnitOfWorkFactory`` port.
        exchange_query_service: The exchange ``ExchangeQueryService`` port.
        artifact_store: The exchange ``ArtifactStore`` (S3-compatible, private
            bucket, ``exports/`` and ``imports/`` keys only).
        exchange_handler_dependencies: What every exchange handler is built from.
        request_export_handler: Queues exports.
        cancel_export_handler: Cancels queued exports.
        request_import_handler: Queues imports.
        exchange_queries: Authorised export and import job reads.
        run_export_handler: Writes an export; run by ``exchange.run_export``.
        run_import_handler: Validates and writes an import; run by
            ``exchange.run_import``.
        ingestion_uow_factory: The ingestion ``UnitOfWorkFactory`` port.
        source_adapters: The registered ingestion source adapters.
        pipeline_factory: Builds the ingestion pipeline of each source adapter.
        register_dataset_handler: Registers datasets.
        record_dataset_version_handler: Records dataset versions.
        deprecate_dataset_handler: Deprecates datasets.
        retire_dataset_handler: Retires datasets.
        run_ingestion_handler: Requests ingestion runs.
        execute_ingestion_run_handler: Executes a run; run by ``ingestion.run``.
        catalogue_raster_asset_handler: Catalogues rasters.
        ingestion_queries: The ingestion read port (open data).
        token_validator: Checks bearer tokens; ``None`` when ``oidc_issuer`` is not
            set, in which case every protected route answers 401.
        rate_limiter: The ``RateLimiter`` port (in memory or Redis).
        idempotency_store: The ``IdempotencyStore`` port (PostgreSQL).
        task_broker: The Taskiq broker ``task_queue`` sends to; shut down on close.
        task_handlers: Handlers for tasks that run in this process (with the
            ``memory`` backend, every task); bound by ``build_container``.
        task_queue: The ``TaskQueue`` port (Taskiq).
        http_client: The HTTP client of the JWKS client, closed on shutdown;
            ``None`` without OIDC.
        redis: The Redis client of the rate limiter, closed on shutdown; ``None``
            with the in-memory backend.
    """

    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    outbox_writer: OutboxWriter
    uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyUnitOfWork]
    subscriber_registry: SubscriberRegistry
    outbox_store: OutboxStore
    outbox_relay: OutboxRelay
    geography_uow_factory: GeographyUnitOfWorkFactory
    hazards_uow_factory: HazardsUnitOfWorkFactory
    impacts_uow_factory: ImpactsUnitOfWorkFactory
    place_query_service: PlaceQueryService
    hazard_type_query_service: HazardTypeQueryService
    impact_metric_query_service: ImpactMetricQueryService
    identity_uow_factory: IdentityUnitOfWorkFactory
    identity_query_service: IdentityQueryService
    provenance_uow_factory: ProvenanceUnitOfWorkFactory
    reports_uow_factory: ReportsUnitOfWorkFactory
    media_uow_factory: MediaUnitOfWorkFactory
    events_uow_factory: EventsUnitOfWorkFactory
    verification_uow_factory: VerificationUnitOfWorkFactory
    impact_claims_uow_factory: ImpactClaimsUnitOfWorkFactory
    audit_uow_factory: AuditUnitOfWorkFactory
    public_coordinates: PublicCoordinatePolicy
    source_query_service: SourceQueryService
    report_query_service: ReportQueryService
    nearby_reports_finder: NearbyReportsFinder
    media_query_service: MediaQueryService
    event_query_service: EventQueryService
    verification_query_service: VerificationQueryService
    impact_query_service: ImpactQueryService
    audit_query_service: AuditQueryService
    storage: StoragePort
    exif_reader: ExifReader
    mime_sniffer: MimeSniffer
    malware_scanner: MalwareScanner
    source_registrar: SourceRegistrar
    source_queries: AuthorisedSourceQueryService
    submit_report_handler: SubmitReportHandler
    revise_report_handler: ReviseReportHandler
    withdraw_report_handler: WithdrawReportHandler
    report_queries: AuthorisedReportQueryService
    run_triage_handler: RunTriageHandler
    request_upload_handler: RequestUploadHandler
    complete_upload_handler: CompleteUploadHandler
    moderate_media_handler: ModerateMediaHandler
    record_scan_result_handler: RecordScanResultHandler
    media_queries: AuthorisedMediaQueryService
    event_handler_dependencies: EventHandlerDependencies
    event_queries: EventRecordQueryService
    verification_handler_dependencies: VerificationHandlerDependencies
    verification_queries: VerificationCaseQueryService
    impact_claim_handler_dependencies: ImpactClaimHandlerDependencies
    event_impacts_queries: EventImpactsQueryService
    event_types: DomainEventTypeRegistry
    audit_subscriber: AuditSubscriber
    exchange_uow_factory: ExchangeUnitOfWorkFactory
    exchange_query_service: ExchangeQueryService
    artifact_store: ArtifactStore
    exchange_handler_dependencies: ExchangeHandlerDependencies
    request_export_handler: RequestExportHandler
    cancel_export_handler: CancelExportHandler
    request_import_handler: RequestImportHandler
    exchange_queries: ExchangeJobQueryService
    run_export_handler: RunExportHandler
    run_import_handler: RunImportHandler
    ingestion_uow_factory: IngestionUnitOfWorkFactory
    source_adapters: SourceAdapterRegistry
    pipeline_factory: PipelineFactory
    register_dataset_handler: RegisterDatasetHandler
    record_dataset_version_handler: RecordDatasetVersionHandler
    deprecate_dataset_handler: DeprecateDatasetHandler
    retire_dataset_handler: RetireDatasetHandler
    run_ingestion_handler: RunIngestionHandler
    execute_ingestion_run_handler: ExecuteIngestionRunHandler
    catalogue_raster_asset_handler: CatalogueRasterAssetHandler
    ingestion_queries: IngestionQueryService
    token_validator: TokenValidator | None
    rate_limiter: RateLimiter
    idempotency_store: IdempotencyStore
    task_broker: AsyncBroker
    task_handlers: TaskHandlerRegistry
    task_queue: TaskQueue
    http_client: httpx.AsyncClient | None = None
    redis: Redis | None = None

    async def aclose(self) -> None:
        """Release every resource the container opened: clients and the engine."""
        await self.task_broker.shutdown()
        if self.http_client is not None:
            await self.http_client.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


def build_token_validator(
    settings: Settings, clock: Clock
) -> tuple[TokenValidator | None, httpx.AsyncClient | None]:
    """Bind the bearer-token validator to the configured identity provider.

    No request is made here; the JWKS is fetched on the first token.

    Args:
        settings: Supplies the ``oidc_*`` and ``jwks_cache_ttl_seconds`` settings.
        clock: The JWKS cache clock.

    Returns:
        The validator and the HTTP client it owns, or ``(None, None)`` when
        ``oidc_issuer`` is not set.
    """
    if settings.oidc_issuer is None:
        return None, None
    # No redirects: a JWKS or discovery URL that redirects elsewhere is a
    # misconfiguration or an attack, never something to follow silently.
    http_client = httpx.AsyncClient(
        timeout=settings.oidc_http_timeout_seconds, follow_redirects=False
    )
    jwks_client = HttpJwksClient(
        http_client=http_client,
        clock=clock,
        issuer=settings.oidc_issuer,
        jwks_url=settings.oidc_jwks_url,
        cache_ttl=timedelta(seconds=settings.jwks_cache_ttl_seconds),
    )
    validator = TokenValidator(
        jwks_client=jwks_client,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        algorithms=settings.oidc_allowed_algorithms,
        leeway_seconds=settings.oidc_leeway_seconds,
        clock=clock,
        roles_claim=settings.oidc_roles_claim,
        accepted_token_types=settings.oidc_accepted_token_types,
    )
    return validator, http_client


def build_rate_limiter(
    settings: Settings, clock: Clock
) -> tuple[RateLimiter, Redis | None]:
    """Bind the ``RateLimiter`` port to the configured backend.

    Args:
        settings: Supplies ``rate_limit_backend``, ``redis_url`` and
            ``redis_socket_timeout_seconds``.
        clock: The in-memory limiter's clock.

    Returns:
        The limiter and the Redis client it owns (``None`` for ``memory``).
    """
    if settings.rate_limit_backend == "redis" and settings.redis_url is not None:
        # from_url connects lazily, on the first command. Short timeouts and no
        # retries: the limiter fails open (ADR 0017), so a slow or unreachable
        # Redis must cost each request a bounded fraction of a second, not stall it.
        timeout = settings.redis_socket_timeout_seconds
        redis = Redis.from_url(
            str(settings.redis_url),
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
        )
        return RedisRateLimiter(redis), redis
    return InMemoryRateLimiter(clock), None


# Frozen dataclasses like ``Container``: intermediate groups of live resources that
# ``build_container`` unpacks into the container's flat fields, which is what the
# API routers read by name.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CorePorts:
    """The kernel and Phase 1 and 2 ports the Phase 3 use cases depend on.

    Implements: Composition Root.

    Attributes:
        clock: The ``Clock`` port.
        id_generator: The ``IdGenerator`` port.
        public_coordinates: The published-position rounding.
        identity_uow_factory: Opens identity units of work (reviewer checks).
        geography_uow_factory: Opens geography units of work (place codes).
        hazard_type_query_service: Hazard type reads (event hazard codes).
    """

    clock: Clock
    id_generator: IdGenerator
    public_coordinates: PublicCoordinatePolicy
    identity_uow_factory: IdentityUnitOfWorkFactory
    geography_uow_factory: GeographyUnitOfWorkFactory
    hazard_type_query_service: HazardTypeQueryService


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordingUnits:
    """The unit-of-work factories of the Phase 3 recording modules.

    Implements: Composition Root.

    Attributes:
        provenance: Opens provenance units of work.
        reports: Opens reports units of work.
        media: Opens media units of work.
        events: Opens events units of work.
        verification: Opens verification units of work.
        impact_claims: Opens impact claims units of work.
        audit: Opens audit units of work.
    """

    provenance: ProvenanceUnitOfWorkFactory
    reports: ReportsUnitOfWorkFactory
    media: MediaUnitOfWorkFactory
    events: EventsUnitOfWorkFactory
    verification: VerificationUnitOfWorkFactory
    impact_claims: ImpactClaimsUnitOfWorkFactory
    audit: AuditUnitOfWorkFactory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordingReads:
    """The read ports of the Phase 3 recording modules.

    Implements: Composition Root.

    Attributes:
        sources: Provenance reads.
        reports: Report reads with exact positions (internal).
        nearby_reports: The triage duplicate finder.
        media: Media reads with keys and EXIF (internal).
        events: Event reads joined with their verification state.
        event_citations: Whether a public event cites a source, for the
            provenance read side; ``None`` leaves citizen and organisation
            sources private to non-members (fail closed).
        verification: Verification case reads.
        impacts: Impact claim and asset reads.
        audit: Audit log reads.
    """

    sources: SourceQueryService
    reports: ReportQueryService
    nearby_reports: NearbyReportsFinder
    media: MediaQueryService
    events: EventQueryService
    verification: VerificationQueryService
    impacts: ImpactQueryService
    audit: AuditQueryService
    event_citations: EventCitationQueryService | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MediaAdapters:
    """Object storage and the file inspectors built over it.

    Implements: Composition Root.

    Attributes:
        storage: The ``StoragePort``.
        exif_reader: Reads EXIF from stored originals.
        mime_sniffer: Detects stored originals' media types.
        malware_scanner: Scans stored originals.
    """

    storage: StoragePort
    exif_reader: ExifReader
    mime_sniffer: MimeSniffer
    malware_scanner: MalwareScanner


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordingServices:
    """The Phase 3 use cases, bound to their ports and cross-module adapters.

    Each attribute becomes the ``Container`` field of the same name.

    Implements: Composition Root.

    Attributes:
        source_registrar: Registers sources.
        source_queries: Authorised source reads.
        submit_report_handler: Submits reports.
        revise_report_handler: Revises reports.
        withdraw_report_handler: Withdraws reports.
        report_queries: Authorised report reads.
        run_triage_handler: Triages a report.
        request_upload_handler: Grants presigned uploads.
        complete_upload_handler: Completes uploads.
        moderate_media_handler: Moderates media.
        record_scan_result_handler: Stores a scan verdict.
        media_queries: Authorised media reads.
        event_handler_dependencies: What every events handler is built from.
        event_queries: Authorised events reads.
        verification_handler_dependencies: What every verification handler is
            built from.
        verification_queries: Authorised verification reads.
        impact_claim_handler_dependencies: What every claims handler is built from.
        event_impacts_queries: Authorised impacts reads.
    """

    source_registrar: SourceRegistrar
    source_queries: AuthorisedSourceQueryService
    submit_report_handler: SubmitReportHandler
    revise_report_handler: ReviseReportHandler
    withdraw_report_handler: WithdrawReportHandler
    report_queries: AuthorisedReportQueryService
    run_triage_handler: RunTriageHandler
    request_upload_handler: RequestUploadHandler
    complete_upload_handler: CompleteUploadHandler
    moderate_media_handler: ModerateMediaHandler
    record_scan_result_handler: RecordScanResultHandler
    media_queries: AuthorisedMediaQueryService
    event_handler_dependencies: EventHandlerDependencies
    event_queries: EventRecordQueryService
    verification_handler_dependencies: VerificationHandlerDependencies
    verification_queries: VerificationCaseQueryService
    impact_claim_handler_dependencies: ImpactClaimHandlerDependencies
    event_impacts_queries: EventImpactsQueryService


def build_recording_units(
    session_factory: async_sessionmaker[AsyncSession], outbox_writer: OutboxWriter
) -> RecordingUnits:
    """Bind each Phase 3 module's unit-of-work port to its SQLAlchemy class.

    Args:
        session_factory: Opens one session per unit of work.
        outbox_writer: Stages each unit of work's domain events on commit.

    Returns:
        The factories.
    """

    def factory[UnitOfWorkT: SqlAlchemyUnitOfWork](
        unit_of_work_class: type[UnitOfWorkT],
    ) -> SqlAlchemyUnitOfWorkFactory[UnitOfWorkT]:
        return SqlAlchemyUnitOfWorkFactory(
            unit_of_work_class,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        )

    return RecordingUnits(
        provenance=factory(SqlAlchemyProvenanceUnitOfWork),
        reports=factory(SqlAlchemyReportsUnitOfWork),
        media=factory(SqlAlchemyMediaUnitOfWork),
        events=factory(SqlAlchemyEventsUnitOfWork),
        verification=factory(SqlAlchemyVerificationUnitOfWork),
        impact_claims=factory(SqlAlchemyImpactClaimsUnitOfWork),
        audit=factory(SqlAlchemyAuditUnitOfWork),
    )


def build_recording_reads(
    session_factory: async_sessionmaker[AsyncSession],
    public_coordinates: PublicCoordinatePolicy,
) -> RecordingReads:
    """Bind each Phase 3 read port to its SQL query service.

    Args:
        session_factory: Opens one read session per query.
        public_coordinates: The rounding the reports read side applies in SQL.

    Returns:
        The read ports.
    """
    # One instance answers both events read ports.
    events = SqlAlchemyEventQueryService(session_factory)
    return RecordingReads(
        sources=SqlAlchemySourceQueryService(session_factory),
        reports=SqlAlchemyReportQueryService(session_factory, public_coordinates),
        nearby_reports=SqlAlchemyNearbyReportsFinder(session_factory),
        media=SqlAlchemyMediaQueryService(session_factory),
        events=events,
        event_citations=events,
        verification=SqlAlchemyVerificationQueryService(session_factory),
        impacts=SqlAlchemyImpactQueryService(session_factory),
        audit=SqlAlchemyAuditQueryService(session_factory),
    )


def build_malware_scanner(settings: Settings, storage: S3StoragePort) -> MalwareScanner:
    """Bind the ``MalwareScanner`` port to the configured backend.

    Args:
        settings: Supplies ``malware_scanner`` and the ``clamav_*`` settings.
        storage: Streams originals to clamd.

    Returns:
        ``ClamAvScanner`` for ``clamav``; otherwise ``NoOpMalwareScanner``, which
        answers ``unavailable`` so nothing it "scanned" can be published (the
        production guard refuses ``noop``).
    """
    # clamav_host is required for "clamav" by Settings; the second test narrows the
    # type for mypy.
    if settings.malware_scanner == "clamav" and settings.clamav_host is not None:
        return ClamAvScanner(
            connect=tcp_connector(settings.clamav_host, settings.clamav_port),
            read_chunks=storage.iter_original,
            timeout_seconds=settings.clamav_timeout_seconds,
        )
    return NoOpMalwareScanner()


def build_media_adapters(settings: Settings, clock: Clock) -> MediaAdapters:
    """Bind storage and the file inspectors; nothing connects until first use.

    Args:
        settings: Supplies the ``storage_*`` and scanner settings.
        clock: Source of presigned URL expiries.

    Returns:
        The adapters, all reading the same ``S3StoragePort``.
    """
    storage = S3StoragePort.from_settings(settings, clock)
    return MediaAdapters(
        storage=storage,
        exif_reader=PillowExifReader(storage.read_original),
        mime_sniffer=FiletypeMimeSniffer(storage.read_original_prefix),
        malware_scanner=build_malware_scanner(settings, storage),
    )


def build_recording_services(
    *,
    core: CorePorts,
    units: RecordingUnits,
    reads: RecordingReads,
    media: MediaAdapters,
    task_queue: TaskQueue,
) -> RecordingServices:
    """Wire the Phase 3 use cases to their ports and cross-module adapters.

    Args:
        core: The clock, ids, coordinate policy and the Phase 1 and 2 ports
            (identity, geography, hazard types).
        units: The Phase 3 unit-of-work factories.
        reads: The Phase 3 read ports.
        media: Storage and the file inspectors.
        task_queue: Schedules triage and scans.

    Returns:
        The use cases, ready to become ``Container`` fields.
    """
    clock, ids, coordinates = core.clock, core.id_generator, core.public_coordinates
    registrar = RegisterSourceHandler(units.provenance, clock, ids)
    marker = MarkSourceReferencedHandler(units.provenance, clock, ids)
    media_ownership = MediaOwnershipAdapter(reads.media)
    report_owners = ReportOwnerAdapter(reads.reports)
    report_facts = ReportFactsAdapter(reads.reports, coordinates)
    verification_dependencies = VerificationHandlerDependencies(
        uow_factory=units.verification,
        policy=verification_moderation_policy(),
        clock=clock,
        ids=ids,
        report_owners=report_owners,
        reviewers=ReviewerEligibilityAdapter(core.identity_uow_factory),
    )
    events_directory = HazardEventDirectoryAdapter(reads.events)
    return RecordingServices(
        source_registrar=registrar,
        source_queries=AuthorisedSourceQueryService(
            reads.sources,
            citation_checker=(
                None
                if reads.event_citations is None
                else SourceCitationCheckerAdapter(reads.event_citations)
            ),
        ),
        submit_report_handler=SubmitReportHandler(
            uow_factory=units.reports,
            source_registrar=registrar,
            source_marker=marker,
            media_checker=media_ownership,
            task_queue=task_queue,
            clock=clock,
            ids=ids,
        ),
        revise_report_handler=ReviseReportHandler(
            uow_factory=units.reports,
            media_checker=media_ownership,
            task_queue=task_queue,
            clock=clock,
            ids=ids,
        ),
        withdraw_report_handler=WithdrawReportHandler(units.reports, clock, ids),
        report_queries=AuthorisedReportQueryService(reads.reports, coordinates),
        run_triage_handler=RunTriageHandler(
            uow_factory=units.reports,
            nearby_reports=reads.nearby_reports,
            photos=PhotoEvidenceAdapter(reads.media),
            clock=clock,
            ids=ids,
        ),
        request_upload_handler=RequestUploadHandler(
            uow_factory=units.media,
            storage=media.storage,
            report_sources=ReportSourceAdapter(reads.reports),
            source_registrar=registrar,
            source_marker=marker,
            clock=clock,
            ids=ids,
        ),
        complete_upload_handler=CompleteUploadHandler(
            uow_factory=units.media,
            storage=media.storage,
            exif_reader=media.exif_reader,
            mime_sniffer=media.mime_sniffer,
            task_queue=task_queue,
            clock=clock,
            ids=ids,
        ),
        moderate_media_handler=ModerateMediaHandler(
            uow_factory=units.media, storage=media.storage, clock=clock, ids=ids
        ),
        record_scan_result_handler=RecordScanResultHandler(units.media, clock, ids),
        media_queries=AuthorisedMediaQueryService(reads.media, media.storage),
        event_handler_dependencies=EventHandlerDependencies(
            uow_factory=units.events,
            policy=events_moderation_policy(),
            clock=clock,
            ids=ids,
            reports=report_facts,
            sources=EventSourceMarkerAdapter(marker),
            cases=VerificationCaseOpenerAdapter(
                OpenVerificationCaseHandler(verification_dependencies)
            ),
            places=PlaceDirectoryAdapter(core.geography_uow_factory),
            hazard_types=core.hazard_type_query_service,
            coordinates=coordinates,
        ),
        event_queries=EventRecordQueryService(
            reads.events,
            policy=events_moderation_policy(),
            reports=report_facts,
            timeline_sources=EventTimelineAdapter(reads.verification, reads.impacts),
        ),
        verification_handler_dependencies=verification_dependencies,
        verification_queries=VerificationCaseQueryService(
            reads.verification, verification_moderation_policy()
        ),
        impact_claim_handler_dependencies=ImpactClaimHandlerDependencies(
            uow_factory=units.impact_claims,
            policy=impacts_moderation_policy(),
            clock=clock,
            ids=ids,
            events=events_directory,
            sources=ImpactSourceMarkerAdapter(marker),
        ),
        event_impacts_queries=EventImpactsQueryService(
            uow_factory=units.impact_claims,
            reads=reads.impacts,
            events=events_directory,
            policy=impacts_moderation_policy(),
            clock=clock,
        ),
    )


# --------------------------------------------------------------------------- #
# Phase 4: exchange and ingestion                                             #
# --------------------------------------------------------------------------- #

EXPORT_GENERATOR_PREFIX = "yakhnama/"
"""Start of the ``generator`` every export sidecar names; the version follows."""


def export_generator() -> str:
    """Return the ``generator`` written into every export sidecar.

    Returns:
        ``yakhnama/<installed package version>``.
    """
    return f"{EXPORT_GENERATOR_PREFIX}{version('yakhnama')}"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExchangePorts:
    """The exchange module's own ports: storage, persistence and formats.

    Implements: Composition Root.

    Attributes:
        uow_factory: Opens exchange units of work.
        reads: The exchange job read port.
        artifacts: Object storage for export and import files.
        formats: The exporter and importer strategies.
    """

    uow_factory: ExchangeUnitOfWorkFactory
    reads: ExchangeQueryService
    artifacts: ArtifactStore
    formats: FormatAdapterRegistry


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExchangeWorkerPorts:
    """The ports the export and import runs reach other modules through.

    Implements: Composition Root.

    Attributes:
        rows: Streams export rows through the events, impacts and reports reads.
        references: Checks an import row's hazard, metric and place codes.
        lineage: Registers an import's ``dataset`` source.
        writer: Opens the batches an import writes events and claims in.
        coordinates: Rounds exported report positions.
        generator: ``yakhnama/<version>``, written into every sidecar.
    """

    rows: ExportRowSource
    references: BackfillReferenceChecker
    lineage: LineageSourceRegistrar
    writer: HistoricalEventWriter
    coordinates: PublicCoordinatePolicy
    generator: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExchangeServices:
    """The exchange use cases, bound to their ports and cross-module adapters.

    Each attribute but ``dependencies`` becomes the ``Container`` field of the
    same name; ``dependencies`` becomes ``exchange_handler_dependencies``.

    Implements: Composition Root.

    Attributes:
        dependencies: What every exchange handler is built from.
        request_export_handler: Queues exports.
        cancel_export_handler: Cancels queued exports.
        request_import_handler: Queues imports.
        exchange_queries: Authorised export and import job reads.
        artifact_store: The exchange ``ArtifactStore`` (presigned import uploads).
        run_export_handler: Writes an export; run by ``exchange.run_export``.
        run_import_handler: Validates and writes an import; run by
            ``exchange.run_import``.
    """

    dependencies: ExchangeHandlerDependencies
    request_export_handler: RequestExportHandler
    cancel_export_handler: CancelExportHandler
    request_import_handler: RequestImportHandler
    exchange_queries: ExchangeJobQueryService
    artifact_store: ArtifactStore
    run_export_handler: RunExportHandler
    run_import_handler: RunImportHandler


def build_exchange_ports(
    settings: Settings,
    clock: Clock,
    session_factory: async_sessionmaker[AsyncSession],
    outbox_writer: OutboxWriter,
) -> ExchangePorts:
    """Bind the exchange module's own ports to their production adapters.

    Args:
        settings: Supplies the ``storage_*`` settings of the artifact store.
        clock: Source of the upload grants' expiries.
        session_factory: Opens one session per unit of work or query.
        outbox_writer: Stages each unit of work's domain events on commit.

    Returns:
        The ports; nothing connects until first use.
    """
    return ExchangePorts(
        uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyExchangeUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        reads=SqlAlchemyExchangeQueryService(session_factory),
        artifacts=S3ArtifactStore.from_settings(settings, clock),
        formats=default_format_adapters(),
    )


def build_exchange_worker_ports(  # noqa: PLR0913  # reason: one keyword per port group the runs reach
    *,
    core: CorePorts,
    engine: AsyncEngine,
    outbox_writer: OutboxWriter,
    reads: RecordingReads,
    services: RecordingServices,
    impact_metrics: ImpactMetricQueryService,
) -> ExchangeWorkerPorts:
    """Bind the ports the export and import runs reach other modules through.

    Args:
        core: The clock, ids, coordinate policy and the Phase 1 and 2 ports.
        engine: Each import batch takes one connection and transaction from it.
        outbox_writer: Stages the batch units of work's domain events.
        reads: The Phase 3 read ports (report facts and owners).
        services: The Phase 3 use cases (authorised reads, source registrar).
        impact_metrics: The impact metric read port (reference checks).

    Returns:
        The worker ports.
    """
    places = PlaceDirectoryAdapter(core.geography_uow_factory)
    return ExchangeWorkerPorts(
        rows=FacadeExportRowSource(
            events=services.event_queries,
            impacts=services.event_impacts_queries,
            reports=services.report_queries,
        ),
        references=RegistryReferenceCheckerAdapter(
            hazard_types=core.hazard_type_query_service,
            impact_metrics=impact_metrics,
            places=places,
        ),
        lineage=ProvenanceLineageRegistrarAdapter(services.source_registrar),
        writer=SessionBatchEventWriter(
            engine,
            BatchReferencePorts(
                outbox_writer=outbox_writer,
                clock=core.clock,
                ids=core.id_generator,
                coordinates=core.public_coordinates,
                hazard_types=core.hazard_type_query_service,
                geography=core.geography_uow_factory,
                identity=core.identity_uow_factory,
                report_facts=ReportFactsAdapter(reads.reports, core.public_coordinates),
                report_owners=ReportOwnerAdapter(reads.reports),
            ),
        ),
        coordinates=core.public_coordinates,
        generator=export_generator(),
    )


def build_exchange_services(
    *,
    core: CorePorts,
    ports: ExchangePorts,
    worker: ExchangeWorkerPorts,
    task_queue: TaskQueue,
) -> ExchangeServices:
    """Wire the exchange use cases to their ports and cross-module adapters.

    The requesting actor of every run is rebuilt through
    ``IdentityActorLookupAdapter`` over ``core.identity_uow_factory``.

    Args:
        core: The clock, ids and identity unit-of-work factory.
        ports: The exchange module's own ports.
        worker: The ports the runs reach other modules through.
        task_queue: Schedules the export and import runs.

    Returns:
        The use cases, ready to become ``Container`` fields.
    """
    dependencies = ExchangeHandlerDependencies(
        uow_factory=ports.uow_factory,
        clock=core.clock,
        ids=core.id_generator,
        tasks=task_queue,
        formats=ports.formats,
        artifacts=ports.artifacts,
        actors=IdentityActorLookupAdapter(core.identity_uow_factory),
    )
    return ExchangeServices(
        dependencies=dependencies,
        request_export_handler=RequestExportHandler(dependencies),
        cancel_export_handler=CancelExportHandler(dependencies),
        request_import_handler=RequestImportHandler(dependencies),
        exchange_queries=ExchangeJobQueryService(ports.reads, ports.artifacts),
        artifact_store=ports.artifacts,
        run_export_handler=RunExportHandler(
            dependencies,
            rows=worker.rows,
            coordinates=worker.coordinates,
            generator=worker.generator,
        ),
        run_import_handler=RunImportHandler(
            dependencies,
            references=worker.references,
            lineage=worker.lineage,
            writer=worker.writer,
        ),
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class IngestionPorts:
    """The ingestion module's ports: persistence, reads, sources and pipelines.

    Implements: Composition Root.

    Attributes:
        uow_factory: Opens ingestion units of work.
        reads: The ingestion read port (open data).
        adapters: The registered source adapters.
        pipelines: Builds the pipeline of each adapter.
    """

    uow_factory: IngestionUnitOfWorkFactory
    reads: IngestionQueryService
    adapters: SourceAdapterRegistry
    pipelines: PipelineFactory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class IngestionServices:
    """The ingestion use cases, bound to their ports.

    Each attribute becomes the ``Container`` field of the same name.

    Implements: Composition Root.

    Attributes:
        register_dataset_handler: Registers datasets.
        record_dataset_version_handler: Records dataset versions.
        deprecate_dataset_handler: Deprecates datasets.
        retire_dataset_handler: Retires datasets.
        run_ingestion_handler: Requests runs (enqueues ``ingestion.run``).
        execute_ingestion_run_handler: Executes a run; run by ``ingestion.run``.
        catalogue_raster_asset_handler: Catalogues rasters.
        ingestion_queries: The ingestion read port.
    """

    register_dataset_handler: RegisterDatasetHandler
    record_dataset_version_handler: RecordDatasetVersionHandler
    deprecate_dataset_handler: DeprecateDatasetHandler
    retire_dataset_handler: RetireDatasetHandler
    run_ingestion_handler: RunIngestionHandler
    execute_ingestion_run_handler: ExecuteIngestionRunHandler
    catalogue_raster_asset_handler: CatalogueRasterAssetHandler
    ingestion_queries: IngestionQueryService


def build_ingestion_ports(
    settings: Settings,
    clock: Clock,
    ids: IdGenerator,
    session_factory: async_sessionmaker[AsyncSession],
    outbox_writer: OutboxWriter,
) -> IngestionPorts:
    """Bind the ingestion ports and register the built-in reference sources.

    Only the synthetic fixture source exists in this version:
    ``reference_adapters(settings.ingestion_fixtures_dir)`` and
    ``reference_pipelines()``; a real source is registered here beside them.

    Args:
        settings: Supplies ``ingestion_fixtures_dir``.
        clock: Handed to the adapters and every pipeline.
        ids: Handed to every pipeline.
        session_factory: Opens one session per unit of work or query.
        outbox_writer: Stages each unit of work's domain events on commit.

    Returns:
        The ports; the fixture directory is read only when a run fetches.
    """
    return IngestionPorts(
        uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyIngestionUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        reads=SqlAlchemyIngestionQueryService(session_factory),
        adapters=SourceAdapterRegistry(
            reference_adapters(settings.ingestion_fixtures_dir, clock=clock)
        ),
        pipelines=PipelineClassRegistry(
            clock=clock, ids=ids, pipelines=reference_pipelines()
        ),
    )


def build_ingestion_services(
    *, core: CorePorts, ports: IngestionPorts, task_queue: TaskQueue
) -> IngestionServices:
    """Wire the ingestion use cases to their ports.

    Args:
        core: Supplies the clock and ids.
        ports: The ingestion ports.
        task_queue: Schedules ``ingestion.run``.

    Returns:
        The use cases, ready to become ``Container`` fields.
    """
    clock, ids, uow_factory = core.clock, core.id_generator, ports.uow_factory
    return IngestionServices(
        register_dataset_handler=RegisterDatasetHandler(uow_factory, clock, ids),
        record_dataset_version_handler=RecordDatasetVersionHandler(
            uow_factory, clock, ids
        ),
        deprecate_dataset_handler=DeprecateDatasetHandler(uow_factory, clock, ids),
        retire_dataset_handler=RetireDatasetHandler(uow_factory, clock, ids),
        run_ingestion_handler=RunIngestionHandler(
            uow_factory=uow_factory,
            adapters=ports.adapters,
            pipelines=ports.pipelines,
            task_queue=task_queue,
            clock=clock,
            ids=ids,
        ),
        execute_ingestion_run_handler=ExecuteIngestionRunHandler(
            uow_factory=uow_factory,
            adapters=ports.adapters,
            pipelines=ports.pipelines,
            clock=clock,
            ids=ids,
        ),
        catalogue_raster_asset_handler=CatalogueRasterAssetHandler(
            uow_factory, clock, ids
        ),
        ingestion_queries=ports.reads,
    )


def build_container(settings: Settings) -> Container:
    """Bind the ports to their production adapters.

    No I/O happens here: the engine, Redis, storage and the scanner connect lazily
    on first use.

    Args:
        settings: The application settings.

    Returns:
        A container ready to be stored on ``app.state.container``.
    """
    clock = SystemClock()
    id_generator = Uuid7Generator(clock)
    token_validator, http_client = build_token_validator(settings, clock)
    rate_limiter, redis = build_rate_limiter(settings, clock)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    outbox_writer = OutboxWriter(clock)
    subscriber_registry = SubscriberRegistry()
    outbox_store = SqlAlchemyOutboxStore(session_factory)
    task_broker = build_broker(settings)
    task_handlers = TaskHandlerRegistry()
    task_queue = TaskiqTaskQueue(
        task_broker, register_tasks(task_broker, task_handlers)
    )
    core = CorePorts(
        clock=clock,
        id_generator=id_generator,
        public_coordinates=PublicCoordinatePolicy(
            decimals=settings.public_coordinate_decimals
        ),
        identity_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyIdentityUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        geography_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyGeographyUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        hazard_type_query_service=SqlAlchemyHazardTypeQueryService(session_factory),
    )
    units = build_recording_units(session_factory, outbox_writer)
    reads = build_recording_reads(session_factory, core.public_coordinates)
    media = build_media_adapters(settings, clock)
    services = build_recording_services(
        core=core, units=units, reads=reads, media=media, task_queue=task_queue
    )
    impact_metric_query_service = SqlAlchemyImpactMetricQueryService(session_factory)
    exchange_ports = build_exchange_ports(
        settings, clock, session_factory, outbox_writer
    )
    exchange = build_exchange_services(
        core=core,
        ports=exchange_ports,
        worker=build_exchange_worker_ports(
            core=core,
            engine=engine,
            outbox_writer=outbox_writer,
            reads=reads,
            services=services,
            impact_metrics=impact_metric_query_service,
        ),
        task_queue=task_queue,
    )
    ingestion_ports = build_ingestion_ports(
        settings, clock, id_generator, session_factory, outbox_writer
    )
    ingestion = build_ingestion_services(
        core=core, ports=ingestion_ports, task_queue=task_queue
    )
    event_types = DomainEventTypeRegistry.from_modules(EVENT_MODULES)
    audit_subscriber = AuditSubscriber(units.audit, event_types, id_generator)
    subscribe_to_every_event(subscriber_registry, event_types, audit_subscriber)
    container = Container(
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        engine=engine,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
        uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        subscriber_registry=subscriber_registry,
        outbox_store=outbox_store,
        outbox_relay=OutboxRelay(
            subscriber_registry,
            clock,
            outbox_store,
            max_attempts=settings.outbox_max_attempts,
            lease_seconds=settings.outbox_lease_seconds,
            subscriber_timeout_seconds=settings.outbox_subscriber_timeout_seconds,
        ),
        geography_uow_factory=core.geography_uow_factory,
        hazards_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyHazardsUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        impacts_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyImpactsUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        place_query_service=SqlAlchemyPlaceQueryService(session_factory),
        hazard_type_query_service=core.hazard_type_query_service,
        impact_metric_query_service=impact_metric_query_service,
        identity_uow_factory=core.identity_uow_factory,
        identity_query_service=SqlAlchemyIdentityQueryService(session_factory),
        provenance_uow_factory=units.provenance,
        reports_uow_factory=units.reports,
        media_uow_factory=units.media,
        events_uow_factory=units.events,
        verification_uow_factory=units.verification,
        impact_claims_uow_factory=units.impact_claims,
        audit_uow_factory=units.audit,
        public_coordinates=core.public_coordinates,
        source_query_service=reads.sources,
        report_query_service=reads.reports,
        nearby_reports_finder=reads.nearby_reports,
        media_query_service=reads.media,
        event_query_service=reads.events,
        verification_query_service=reads.verification,
        impact_query_service=reads.impacts,
        audit_query_service=reads.audit,
        storage=media.storage,
        exif_reader=media.exif_reader,
        mime_sniffer=media.mime_sniffer,
        malware_scanner=media.malware_scanner,
        source_registrar=services.source_registrar,
        source_queries=services.source_queries,
        submit_report_handler=services.submit_report_handler,
        revise_report_handler=services.revise_report_handler,
        withdraw_report_handler=services.withdraw_report_handler,
        report_queries=services.report_queries,
        run_triage_handler=services.run_triage_handler,
        request_upload_handler=services.request_upload_handler,
        complete_upload_handler=services.complete_upload_handler,
        moderate_media_handler=services.moderate_media_handler,
        record_scan_result_handler=services.record_scan_result_handler,
        media_queries=services.media_queries,
        event_handler_dependencies=services.event_handler_dependencies,
        event_queries=services.event_queries,
        verification_handler_dependencies=services.verification_handler_dependencies,
        verification_queries=services.verification_queries,
        impact_claim_handler_dependencies=services.impact_claim_handler_dependencies,
        event_impacts_queries=services.event_impacts_queries,
        event_types=event_types,
        audit_subscriber=audit_subscriber,
        exchange_uow_factory=exchange_ports.uow_factory,
        exchange_query_service=exchange_ports.reads,
        artifact_store=exchange.artifact_store,
        exchange_handler_dependencies=exchange.dependencies,
        request_export_handler=exchange.request_export_handler,
        cancel_export_handler=exchange.cancel_export_handler,
        request_import_handler=exchange.request_import_handler,
        exchange_queries=exchange.exchange_queries,
        run_export_handler=exchange.run_export_handler,
        run_import_handler=exchange.run_import_handler,
        ingestion_uow_factory=ingestion_ports.uow_factory,
        source_adapters=ingestion_ports.adapters,
        pipeline_factory=ingestion_ports.pipelines,
        register_dataset_handler=ingestion.register_dataset_handler,
        record_dataset_version_handler=ingestion.record_dataset_version_handler,
        deprecate_dataset_handler=ingestion.deprecate_dataset_handler,
        retire_dataset_handler=ingestion.retire_dataset_handler,
        run_ingestion_handler=ingestion.run_ingestion_handler,
        execute_ingestion_run_handler=ingestion.execute_ingestion_run_handler,
        catalogue_raster_asset_handler=ingestion.catalogue_raster_asset_handler,
        ingestion_queries=ingestion.ingestion_queries,
        token_validator=token_validator,
        rate_limiter=rate_limiter,
        idempotency_store=SqlAlchemyIdempotencyStore(session_factory, id_generator),
        task_broker=task_broker,
        task_handlers=task_handlers,
        task_queue=task_queue,
        http_client=http_client,
        redis=redis,
    )
    # With the memory backend tasks run in this process, so they need handlers
    # here; with redis they run in the worker, and these are simply never called.
    task_handlers.bind(build_task_handlers(container))
    return container


def build_task_handlers(container: Container) -> Mapping[str, TaskHandler]:
    """Bind every task name this build can serve to its handler.

    The platform tasks and the module tasks are bound here:
    ``reports.run_triage`` runs ``run_triage_handler``; ``media.scan`` streams
    the original to ``malware_scanner`` and stores the verdict with
    ``record_scan_result_handler``; ``exchange.run_export`` and
    ``exchange.run_import`` run ``run_export_handler`` and ``run_import_handler``;
    ``ingestion.run`` runs ``execute_ingestion_run_handler``. Every handler reads
    the container it is given, so a test that replaces a field (for example the
    scanner) and calls this function gets handlers over its replacement.

    Args:
        container: Supplies the relay, the stores, the module handlers, the
            scanner, the clock and the settings.

    Returns:
        A read-only mapping from task name to handler.
    """
    settings = container.settings

    async def relay_outbox(_task: ScheduledTask) -> None:
        outcome = await container.outbox_relay.relay_once(settings.outbox_batch_size)
        if outcome.claimed:
            structlog.get_logger(__name__).info(
                "outbox_relayed", **outcome.model_dump()
            )

    async def purge_outbox(_task: ScheduledTask) -> None:
        cutoff = container.clock.now() - timedelta(days=settings.outbox_retention_days)
        await container.outbox_relay.purge_published(cutoff)

    async def purge_idempotency_keys(_task: ScheduledTask) -> None:
        deleted = await container.idempotency_store.purge_expired(container.clock.now())
        structlog.get_logger(__name__).info("idempotency_purged", deleted=deleted)

    return MappingProxyType(
        {
            OUTBOX_RELAY_TASK: relay_outbox,
            OUTBOX_PURGE_TASK: purge_outbox,
            IDEMPOTENCY_PURGE_TASK: purge_idempotency_keys,
            REPORTS_TRIAGE_TASK: RunTriageTaskAdapter(container.run_triage_handler),
            MEDIA_SCAN_TASK: ScanTaskAdapter(
                media=container.media_query_service,
                scanner=container.malware_scanner,
                record_scan_result=container.record_scan_result_handler,
            ),
            EXCHANGE_RUN_EXPORT_TASK: RunExportTaskAdapter(
                container.run_export_handler
            ),
            EXCHANGE_RUN_IMPORT_TASK: RunImportTaskAdapter(
                container.run_import_handler
            ),
            INGESTION_RUN_TASK: ExecuteIngestionRunTaskAdapter(
                container.execute_ingestion_run_handler
            ),
        }
    )


def includes_fixture_datasets(settings: Settings) -> bool:
    """Tell whether the seed registers the catalog's synthetic fixture datasets.

    Fixtures let a development or test database run the reference ingestion end to
    end; a production catalog must never list synthetic data as if it were real.

    Args:
        settings: Supplies ``environment``.

    Returns:
        ``True`` unless ``environment`` is ``production``.
    """
    return settings.environment != "production"


def build_seed_handler(container: Container) -> SeedReferenceDataHandler:
    """Wire the reference-data seed to the container's ports.

    One ``CanManageReferenceData`` policy (admins only) guards the seed and every
    module load it calls, so the seed changes nothing unless its actor is an admin;
    the command line runs it as a synthetic system actor holding ``admin``.
    The reader reads ``settings.reference_data_dir`` when the handler runs, not now.
    The dataset catalog is loaded last by ``LoadReferenceDatasetsHandler``, which
    ``catalog_policy`` (admins) guards on its own; synthetic fixture entries are
    included unless ``environment`` is ``production``, so a development or test
    database can run the fixture ingestion while a real catalog never lists them.

    Args:
        container: The container whose units of work, clock and ids the loads use.

    Returns:
        The seed handler, ready to be called with ``SeedReferenceData``.
    """
    policy = CanManageReferenceData()
    reader = YamlReferenceFileReader(container.settings.reference_data_dir)
    return SeedReferenceDataHandler(
        reader=reader,
        policy=policy,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            container.hazards_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            container.impacts_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
        load_places=LoadReferencePlacesHandler(
            container.geography_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
        datasets=DatasetSeedStep(
            reader=reader,
            load=LoadReferenceDatasetsHandler(
                container.ingestion_uow_factory,
                container.clock,
                container.id_generator,
            ),
            include_fixtures=includes_fixture_datasets(container.settings),
        ),
    )


def get_container(request: Request) -> Container:
    """Return the application's container (a FastAPI dependency).

    Args:
        request: The current request.

    Returns:
        The container stored on ``request.app.state.container``.

    Raises:
        RuntimeError: If the application was not built by ``create_app``; this is a
            wiring bug, so it surfaces as a server error rather than a domain error.
    """
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, Container):
        message = "app.state.container is missing; build the app with create_app"
        raise RuntimeError(message)
    return container
