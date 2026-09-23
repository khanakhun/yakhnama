"""Public facade of the ``hazards`` module.

Other modules and the seed orchestrator import only this file. It re-exports the
values other contexts may hold (``HazardTypeRef``), the read side (queries, DTOs, the
query-service port) and what the reference-data seed needs (the load command, its
report and the reference file model). Nothing from infrastructure is exported.

Patterns: Facade.
"""

from yakhnama.modules.hazards.application.commands import LoadReferenceHazardTypes
from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
    LoadReport,
    SkippedChange,
)
from yakhnama.modules.hazards.application.ports import HazardTypeQueryService
from yakhnama.modules.hazards.application.queries import (
    GetHazardType,
    ListHazardTypes,
)
from yakhnama.modules.hazards.domain.attributes import (
    DEFAULT_REGISTRY,
    HazardAttributeRegistry,
    HazardAttributes,
    HazardAttributesUnion,
)
from yakhnama.modules.hazards.domain.reference import HazardTypeReferenceFile
from yakhnama.modules.hazards.domain.value_objects import (
    HazardCode,
    HazardTypeRef,
    HazardTypeStatus,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "GetHazardType",
    "HazardAttributeRegistry",
    "HazardAttributes",
    "HazardAttributesUnion",
    "HazardCode",
    "HazardTypeDetail",
    "HazardTypeQueryService",
    "HazardTypeRef",
    "HazardTypeReferenceFile",
    "HazardTypeStatus",
    "HazardTypeSummary",
    "ListHazardTypes",
    "LoadReferenceHazardTypes",
    "LoadReport",
    "SkippedChange",
]
