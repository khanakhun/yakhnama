"""Public facade of the ``impacts`` module.

Other modules and the seed orchestrator import only this file. It re-exports the
values other contexts may hold (``ImpactMetricRef``), the read side (queries, DTOs,
the query-service port) and what the reference-data seed needs (the load command, its
report and the reference file model). Nothing from infrastructure is exported.

Patterns: Facade.
"""

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
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceFile
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    MetricCategory,
    MetricCode,
    MetricStatus,
    ValueKind,
)

__all__ = [
    "GetImpactMetric",
    "ImpactMetricDetail",
    "ImpactMetricQueryService",
    "ImpactMetricRef",
    "ImpactMetricReferenceFile",
    "ImpactMetricSummary",
    "ListImpactMetrics",
    "LoadReferenceImpactMetrics",
    "LoadReport",
    "MetricCategory",
    "MetricCode",
    "MetricStatus",
    "SkippedChange",
    "ValueKind",
]
