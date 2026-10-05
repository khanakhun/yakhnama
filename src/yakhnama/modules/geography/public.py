"""Public facade of the ``geography`` module.

Other modules and the seed orchestrator import only this file. It re-exports the
values other contexts may hold (``PlaceCode``, ``AdminLevel``), the read side
(queries, DTOs, the query-service port) and what the reference-data seed needs (the
load commands, their reports and the reference file models, district boundaries
included). Nothing from infrastructure is exported.

Patterns: Facade.
"""

from yakhnama.modules.geography.application.commands import (
    LoadDistrictBoundaries,
    LoadReferencePlaces,
)
from yakhnama.modules.geography.application.dto import (
    BoundaryLoadReport,
    LoadReport,
    PlaceDetail,
    PlaceSummary,
    SkippedChange,
)
from yakhnama.modules.geography.application.ports import PlaceQueryService
from yakhnama.modules.geography.application.queries import GetPlace, SearchPlaces
from yakhnama.modules.geography.domain.boundaries import DistrictBoundarySource
from yakhnama.modules.geography.domain.reference import PlaceReferenceFile
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceCode,
    PlaceName,
    PlaceStatus,
)

__all__ = [
    "AdminLevel",
    "BoundaryLoadReport",
    "DistrictBoundarySource",
    "GetPlace",
    "LoadDistrictBoundaries",
    "LoadReferencePlaces",
    "LoadReport",
    "PlaceCode",
    "PlaceDetail",
    "PlaceName",
    "PlaceQueryService",
    "PlaceReferenceFile",
    "PlaceStatus",
    "PlaceSummary",
    "SearchPlaces",
    "SkippedChange",
]
