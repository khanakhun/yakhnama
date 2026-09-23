"""Read requests accepted by the provenance query services.

Patterns: Query, Specification.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification, TrueSpecification


class GetSource(BaseModel):
    """Ask for one source by id.

    Implements: Query.

    Attributes:
        actor: Who asks.
        source_id: The source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    source_id: EntityId


class ListSources(BaseModel):
    """Ask for one page of sources, newest first, optionally of one type.

    Implements: Query.

    Attributes:
        actor: Who asks.
        source_type: Only sources of this type, if set.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    source_type: SourceType | None = None
    page: PageRequest = PageRequest()

    def to_specification(self) -> Specification[Source]:
        """Combine the set filters into one specification.

        Returns:
            ``SourceTypeSpecification`` when a type is set, otherwise a
            specification every source satisfies.
        """
        if self.source_type is None:
            return TrueSpecification[Source]()
        return SourceTypeSpecification(self.source_type)
