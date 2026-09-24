"""Public facade of the ``provenance`` module.

Other modules, the API and the composition root import only this file: the
``Source`` value types other modules store or build (``SourceRef``,
``SourceDetails``, ``SourceType``), the commands, queries, DTOs and handlers, the
ports the composition root binds, and the ``SourceRegistrar`` and
``SourceReferenceMarker`` ports other modules depend on to register and cite
sources.

Patterns: Facade.
"""

from yakhnama.modules.provenance.application.authorisation import (
    SELF_REGISTERED_SOURCE_TYPES,
    reference_policy,
    registration_policy,
    source_editor_policy,
    source_listing_specification,
    source_read_policy,
)
from yakhnama.modules.provenance.application.commands import (
    MarkSourceReferenced,
    RegisterSource,
    UpdateSourceDetails,
)
from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.application.handlers import (
    MarkSourceReferencedHandler,
    RegisterSourceHandler,
    UpdateSourceDetailsHandler,
)
from yakhnama.modules.provenance.application.ports import (
    ProvenanceUnitOfWork,
    ProvenanceUnitOfWorkFactory,
    SourceCitationChecker,
    SourceQueryService,
    SourceReferenceMarker,
    SourceRegistrar,
    SourceRepository,
)
from yakhnama.modules.provenance.application.queries import GetSource, ListSources
from yakhnama.modules.provenance.application.query_services import (
    AuthorisedSourceQueryService,
)
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import (
    InvalidSourceUrlError,
    SourceImmutableError,
    SourceNotFoundError,
)
from yakhnama.modules.provenance.domain.value_objects import (
    Licence,
    SourceDetails,
    SourceRef,
    SourceType,
)

__all__ = [
    "SELF_REGISTERED_SOURCE_TYPES",
    "AuthorisedSourceQueryService",
    "GetSource",
    "InvalidSourceUrlError",
    "Licence",
    "ListSources",
    "MarkSourceReferenced",
    "MarkSourceReferencedHandler",
    "ProvenanceUnitOfWork",
    "ProvenanceUnitOfWorkFactory",
    "RegisterSource",
    "RegisterSourceHandler",
    "Source",
    "SourceCitationChecker",
    "SourceDetail",
    "SourceDetails",
    "SourceImmutableError",
    "SourceNotFoundError",
    "SourceQueryService",
    "SourceRef",
    "SourceReferenceMarker",
    "SourceRegistrar",
    "SourceRepository",
    "SourceSummary",
    "SourceType",
    "SourceTypeSpecification",
    "UpdateSourceDetails",
    "UpdateSourceDetailsHandler",
    "reference_policy",
    "registration_policy",
    "source_editor_policy",
    "source_listing_specification",
    "source_read_policy",
]
