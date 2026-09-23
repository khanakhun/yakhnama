"""SQL implementation of the ``SourceQueryService`` port.

Each query opens its own short read session. Listings compile the source
specification tree to one SQL condition (``SourceSpecificationCompiler``) and page
by keyset on ``(created_at, id)`` descending, newest first, one bounded query per
page: the next page holds rows with ``(created_at, id) < (since, last_id)``. The
cursor's ``sort_key`` is ``created_at`` in ISO 8601 (microsecond precision, like
``timestamptz``) and its ``last_id`` the last source's id, as the port requires.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, false, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.infrastructure.mappers import row_to_source
from yakhnama.modules.provenance.infrastructure.orm import SourceRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    FalseSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    TrueSpecification,
)


def decode_since(sort_key: str) -> datetime:
    """Parse the ``created_at`` instant a source-listing cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        since = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # A naive instant never comes from encode_cursor here, and comparing it with
    # timestamptz would silently assume the session time zone.
    if since.utcoffset() is None:
        raise _invalid_cursor()
    return since


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class SourceSpecificationCompiler:
    """Compiles a source specification tree to a boolean SQL expression.

    Implements: Specification (SQL compiler visitor).
    """

    def visit_and(self, specification: AndSpecification[Source]) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(self, specification: OrSpecification[Source]) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(self, specification: NotSpecification[Source]) -> ColumnElement[bool]:
        """Compile a negation.

        Args:
            specification: The negation.

        Returns:
            ``NOT operand``.
        """
        return not_(specification.operand.accept(self))

    def visit_leaf(self, specification: Specification[Source]) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: A provenance leaf or a constant specification.

        Returns:
            The SQL condition of the leaf.

        Raises:
            TypeError: If the leaf has no SQL translation.
        """
        match specification:
            case SourceTypeSpecification():
                return SourceRow.source_type == specification.source_type.value
            case TrueSpecification():
                return true()
            case FalseSpecification():
                return false()
            case _:
                message = f"no SQL translation for {type(specification).__name__}"
                raise TypeError(message)


class SqlAlchemySourceQueryService:
    """PostgreSQL-backed implementation of ``SourceQueryService``.

    Implements: Query Service (port ``SourceQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def get_source(self, source_id: EntityId) -> SourceDetail | None:
        """Return one source.

        Args:
            source_id: The source.

        Returns:
            The detail view, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(SourceRow, source_id)
        return None if row is None else SourceDetail.from_entity(row_to_source(row))

    async def list_sources(
        self, specification: Specification[Source], page: PageRequest
    ) -> Page[SourceSummary]:
        """Return one page of the sources ``specification`` accepts, newest first.

        Args:
            specification: The filter, compiled to SQL.
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
            TypeError: If the specification holds a leaf with no SQL translation.
        """
        cursor = page.decode_cursor()
        statement = (
            select(SourceRow)
            .where(specification.accept(SourceSpecificationCompiler()))
            .order_by(SourceRow.created_at.desc(), SourceRow.id.desc())
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    SourceRow.created_at < since,
                    and_(SourceRow.created_at == since, SourceRow.id < cursor.last_id),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        summaries = [SourceSummary.from_entity(row_to_source(row)) for row in rows]
        window = summaries[: page.limit]
        next_cursor = None
        if len(summaries) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[SourceSummary](items=tuple(window), next_cursor=next_cursor)
