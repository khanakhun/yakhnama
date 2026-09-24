"""Public facade of the ``ingestion`` module.

Other modules, the seed, the API and the composition root import only this file:
the commands, queries, DTOs and handlers of the catalog and of runs, the ports the
composition root binds, the ``IngestionPipeline`` base and registries a source is
added with, and the reference-file model the seed loads.

Patterns: Facade.
"""

from yakhnama.modules.ingestion.application.authorisation import catalog_policy
from yakhnama.modules.ingestion.application.commands import (
    CatalogueRasterAsset,
    DeprecateDataset,
    ExecuteIngestionRun,
    LoadReferenceDatasets,
    RecordDatasetVersion,
    RegisterDataset,
    RetireDataset,
    RunIngestion,
)
from yakhnama.modules.ingestion.application.dto import (
    DatasetDetail,
    DatasetSummary,
    DatasetVersionSummary,
    IngestionOutcome,
    LoadReport,
    ObservationRecord,
    RasterAssetSummary,
    RunDetail,
    RunSummary,
    SkippedChange,
)
from yakhnama.modules.ingestion.application.handlers import (
    CatalogueRasterAssetHandler,
    DeprecateDatasetHandler,
    ExecuteIngestionRunHandler,
    LoadReferenceDatasetsHandler,
    RecordDatasetVersionHandler,
    RegisterDatasetHandler,
    RetireDatasetHandler,
    RunIngestionHandler,
)
from yakhnama.modules.ingestion.application.pipeline import (
    PERSIST_BATCH_SIZE,
    IngestionPipeline,
    ObservationDraft,
    ValidationCollector,
    select_outcome,
)
from yakhnama.modules.ingestion.application.ports import (
    INGESTION_RUN_TASK,
    DatasetRepository,
    DatasetVersionRepository,
    IngestionQueryService,
    IngestionRunRepository,
    IngestionUnitOfWork,
    IngestionUnitOfWorkFactory,
    ObservationRepository,
    PipelineFactory,
    RasterAssetCatalog,
    RawPayload,
    RunnablePipeline,
    SourceAdapter,
)
from yakhnama.modules.ingestion.application.queries import (
    GetDataset,
    GetRun,
    ListDatasets,
    ListRasterAssets,
    ListRuns,
    QueryObservations,
)
from yakhnama.modules.ingestion.application.registry import (
    PipelineClassRegistry,
    PipelineConstructor,
    SourceAdapterRegistry,
)
from yakhnama.modules.ingestion.domain.reference import (
    DatasetReferenceEntry,
    DatasetReferenceFile,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetDetails,
    DatasetLicence,
    DatasetStatus,
    DatasetVersionDetails,
    RasterAssetDescription,
    RunStatus,
)

__all__ = [
    "INGESTION_RUN_TASK",
    "PERSIST_BATCH_SIZE",
    "CatalogueRasterAsset",
    "CatalogueRasterAssetHandler",
    "DatasetDetail",
    "DatasetDetails",
    "DatasetLicence",
    "DatasetReferenceEntry",
    "DatasetReferenceFile",
    "DatasetRepository",
    "DatasetStatus",
    "DatasetSummary",
    "DatasetVersionDetails",
    "DatasetVersionRepository",
    "DatasetVersionSummary",
    "DeprecateDataset",
    "DeprecateDatasetHandler",
    "ExecuteIngestionRun",
    "ExecuteIngestionRunHandler",
    "GetDataset",
    "GetRun",
    "IngestionOutcome",
    "IngestionPipeline",
    "IngestionQueryService",
    "IngestionRunRepository",
    "IngestionUnitOfWork",
    "IngestionUnitOfWorkFactory",
    "ListDatasets",
    "ListRasterAssets",
    "ListRuns",
    "LoadReferenceDatasets",
    "LoadReferenceDatasetsHandler",
    "LoadReport",
    "ObservationDraft",
    "ObservationRecord",
    "ObservationRepository",
    "PipelineClassRegistry",
    "PipelineConstructor",
    "PipelineFactory",
    "QueryObservations",
    "RasterAssetCatalog",
    "RasterAssetDescription",
    "RasterAssetSummary",
    "RawPayload",
    "RecordDatasetVersion",
    "RecordDatasetVersionHandler",
    "RegisterDataset",
    "RegisterDatasetHandler",
    "RetireDataset",
    "RetireDatasetHandler",
    "RunDetail",
    "RunIngestion",
    "RunIngestionHandler",
    "RunStatus",
    "RunSummary",
    "RunnablePipeline",
    "SkippedChange",
    "SourceAdapter",
    "SourceAdapterRegistry",
    "ValidationCollector",
    "catalog_policy",
    "select_outcome",
]
