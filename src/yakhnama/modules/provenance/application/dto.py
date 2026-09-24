"""Read models returned by the provenance query service and command handlers.

DTOs are frozen and carry what a reader needs. The owning user's id is deliberately
absent: a source is public provenance, and which user registered a citizen source
would link a person to the reports it backs (``AGENTS.md`` §5). ``from_entity``
builds a DTO from the aggregate for in-memory implementations; the SQL query
service builds the same DTO from selected columns, with the same meanings.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import (
    Citation,
    Licence,
    Publisher,
    RecordVersion,
    RetrievalTime,
    SourceTitle,
    SourceType,
    SourceUrl,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import LanguageCode


class SourceSummary(BaseModel):
    """One source in a listing.

    Implements: DTO.

    Attributes:
        id: The source's id.
        source_type: The kind of source.
        title: Short title.
        publisher: Who published it, if known.
        is_referenced: Whether any fact cites it (it is then immutable).
        created_at: When it was registered, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    source_type: SourceType
    title: SourceTitle
    publisher: Publisher | None
    is_referenced: bool
    created_at: AwareDatetime

    @classmethod
    def from_entity(cls, source: Source) -> Self:
        """Build the summary of a source.

        Args:
            source: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=source.id,
            source_type=source.source_type,
            title=source.title,
            publisher=source.publisher,
            is_referenced=source.is_referenced,
            created_at=source.created_at,
        )


class SourceDetail(BaseModel):
    """One source with every descriptive field.

    Implements: DTO.

    Attributes:
        id: The source's id.
        source_type: The kind of source.
        title: Short title.
        citation: How to cite it.
        url: Where it can be found online, if anywhere.
        licence: Reuse terms, if known.
        retrieved_at: When it was retrieved, if known.
        publisher: Who published it, if known.
        language: Language of its content, if known.
        organization_id: The organisation it was registered for, if any.
        is_referenced: Whether any fact cites it (it is then immutable).
        version: Optimistic-concurrency version, for ``ETag``.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    source_type: SourceType
    title: SourceTitle
    citation: Citation
    url: SourceUrl | None
    licence: Licence | None
    retrieved_at: RetrievalTime | None
    publisher: Publisher | None
    language: LanguageCode | None
    organization_id: EntityId | None
    is_referenced: bool
    version: RecordVersion
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, source: Source) -> Self:
        """Build the detail view of a source.

        Args:
            source: The aggregate.

        Returns:
            Its detail view.
        """
        return cls(
            id=source.id,
            source_type=source.source_type,
            title=source.title,
            citation=source.citation,
            url=source.url,
            licence=source.licence,
            retrieved_at=source.retrieved_at,
            publisher=source.publisher,
            language=source.language,
            organization_id=source.organization_id,
            is_referenced=source.is_referenced,
            version=source.version,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )
