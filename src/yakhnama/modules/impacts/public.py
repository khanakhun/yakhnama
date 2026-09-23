"""Public facade of the ``impacts`` module.

Other modules and the seed orchestrator import only this file. It re-exports the
values other contexts may hold (``ImpactMetricRef``), the read side (queries, DTOs,
the query-service port) and what the reference-data seed needs (the load command, its
report and the reference file model), and the claims side: commands, handlers,
queries, DTOs, the read service and the ports the composition root binds, including
those towards other modules (``HazardEventDirectory``, ``ImpactSourceMarker``).
Nothing from infrastructure is exported.

Patterns: Facade.
"""

from yakhnama.modules.impacts.application.authorisation import moderation_policy
from yakhnama.modules.impacts.application.claims_commands import (
    CorrectImpactClaim,
    RecordDamage,
    RecordImpactClaim,
    RegisterInfrastructureAsset,
    RetractDamage,
    RetractImpactClaim,
)
from yakhnama.modules.impacts.application.claims_dto import (
    EventImpacts,
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_handlers import (
    CorrectImpactClaimHandler,
    ImpactClaimHandlerDependencies,
    RecordDamageHandler,
    RecordImpactClaimHandler,
    RegisterInfrastructureAssetHandler,
    RetractDamageHandler,
    RetractImpactClaimHandler,
)
from yakhnama.modules.impacts.application.claims_ports import (
    DamageRecordRepository,
    HazardEventDirectory,
    ImpactClaimRepository,
    ImpactClaimsUnitOfWork,
    ImpactClaimsUnitOfWorkFactory,
    ImpactQueryService,
    ImpactSourceMarker,
    InfrastructureAssetRepository,
)
from yakhnama.modules.impacts.application.claims_queries import (
    GetEventImpacts,
    GetInfrastructureAsset,
    ListClaims,
)
from yakhnama.modules.impacts.application.claims_query_services import (
    EventImpactsQueryService,
)
from yakhnama.modules.impacts.application.commands import LoadReferenceImpactMetrics
from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
    LoadReport,
    SkippedChange,
)
from yakhnama.modules.impacts.application.ports import ImpactMetricQueryService
from yakhnama.modules.impacts.application.queries import (
    GetImpactMetric,
    ListImpactMetrics,
)
from yakhnama.modules.impacts.domain.best_figure import BestFigure, BestFigureBasis
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceFile
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    CountValue,
    DamageLevel,
    ImpactMetricRef,
    MeasurementValue,
    MetricCategory,
    MetricCode,
    MetricStatus,
    MonetaryValue,
    SourceTypeName,
    ValueKind,
)

__all__ = [
    "AssetKind",
    "BestFigure",
    "BestFigureBasis",
    "ClaimScope",
    "ClaimStatus",
    "ClaimValue",
    "CorrectImpactClaim",
    "CorrectImpactClaimHandler",
    "CountValue",
    "DamageLevel",
    "DamageRecordRepository",
    "EventImpacts",
    "EventImpactsQueryService",
    "GetEventImpacts",
    "GetImpactMetric",
    "GetInfrastructureAsset",
    "HazardEventDirectory",
    "ImpactClaimHandlerDependencies",
    "ImpactClaimRepository",
    "ImpactClaimSummary",
    "ImpactClaimsUnitOfWork",
    "ImpactClaimsUnitOfWorkFactory",
    "ImpactMetricDetail",
    "ImpactMetricQueryService",
    "ImpactMetricRef",
    "ImpactMetricReferenceFile",
    "ImpactMetricSummary",
    "ImpactQueryService",
    "ImpactSourceMarker",
    "InfrastructureAssetDetail",
    "InfrastructureAssetRepository",
    "ListClaims",
    "ListImpactMetrics",
    "LoadReferenceImpactMetrics",
    "LoadReport",
    "MeasurementValue",
    "MetricCategory",
    "MetricCode",
    "MetricStatus",
    "MonetaryValue",
    "RecordDamage",
    "RecordDamageHandler",
    "RecordImpactClaim",
    "RecordImpactClaimHandler",
    "RegisterInfrastructureAsset",
    "RegisterInfrastructureAssetHandler",
    "RetractDamage",
    "RetractDamageHandler",
    "RetractImpactClaim",
    "RetractImpactClaimHandler",
    "SkippedChange",
    "SourceTypeName",
    "ValueKind",
    "moderation_policy",
]
