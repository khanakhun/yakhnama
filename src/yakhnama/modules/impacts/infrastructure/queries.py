"""SQL implementation of the impacts query service port.

Listings select only the columns ``ImpactMetricSummary`` needs and page by keyset on
the unique ``code`` (no ``OFFSET``, no ``COUNT(*)``): the cursor carries the last code
and id, and the next page starts strictly after that code.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from typing import TYPE_CHECKING, Final

from sqlalchemy import ColumnElement, and_, false, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.modules.impacts.application.specifications import (
    ActiveImpactMetricSpecification,
    ImpactMetricCategorySpecification,
)
from yakhnama.modules.impacts.domain.value_objects import MetricStatus
from yakhnama.modules.impacts.infrastructure.mappers import row_to_metric
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    FalseSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    SpecificationVisitor,
    TrueSpecification,
)

_SUMMARY_COLUMNS: Final = (
    ImpactMetricRow.id,
    ImpactMetricRow.code,
    ImpactMetricRow.labels,
    ImpactMetricRow.category,
    ImpactMetricRow.value_kind,
    ImpactMetricRow.unit,
    ImpactMetricRow.currency,
    ImpactMetricRow.status,
)


# Binary ("C") collation: the database default (en_US.utf8 in the PostGIS image)
# ignores "_" at the first comparison level, so "flood_x" would sort after
# "floodplain". Code-point order matches Python's sorted(), which the fakes use, and
# the keyset comparison must use the same collation as the ORDER BY.
_CODE_ORDER: Final = ImpactMetricRow.code.collate("C")


class ImpactMetricSpecificationCompiler:
    """Compiles an impact metric specification tree to a boolean SQL expression.

    Implements: Specification (SQL compiler visitor).
    """

    def visit_and(
        self, specification: AndSpecification[ImpactMetricSummary]
    ) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(
        self, specification: OrSpecification[ImpactMetricSummary]
    ) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(
        self, specification: NotSpecification[ImpactMetricSummary]
    ) -> ColumnElement[bool]:
        """Compile a negation.

        Args:
            specification: The negation.

        Returns:
            ``NOT operand``.
        """
        return not_(specification.operand.accept(self))

    def visit_leaf(
        self, specification: Specification[ImpactMetricSummary]
    ) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: An impacts leaf or a constant specification.

        Returns:
            The SQL condition of the leaf.

        Raises:
            TypeError: If the leaf has no SQL translation.
        """
        match specification:
            case ActiveImpactMetricSpecification():
                return ImpactMetricRow.status == MetricStatus.ACTIVE.value
            case ImpactMetricCategorySpecification():
                return ImpactMetricRow.category == specification.category.value
            case TrueSpecification():
                return true()
            case FalseSpecification():
                return false()
            case _:
                message = f"no SQL translation for {type(specification).__name__}"
                raise TypeError(message)


class SqlAlchemyImpactMetricQueryService:
    """PostgreSQL-backed implementation of ``ImpactMetricQueryService``.

    Implements: Query Service (port ``ImpactMetricQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def list_impact_metrics(
        self, query: ListImpactMetrics
    ) -> Page[ImpactMetricSummary]:
        """Return one page of metrics matching ``query``, ordered by code.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = select(*_SUMMARY_COLUMNS).order_by(_CODE_ORDER)
        specification = query.to_specification()
        if specification is not None:
            statement = statement.where(
                specification.accept(ImpactMetricSpecificationCompiler())
            )
        if cursor is not None:
            statement = statement.where(cursor.sort_key < _CODE_ORDER)
        # One extra row tells whether a next page exists without COUNT(*).
        statement = statement.limit(query.page.limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        window = rows[: query.page.limit]
        items = tuple(
            ImpactMetricSummary.model_validate(
                {
                    "code": row.code,
                    "labels": row.labels,
                    "category": row.category,
                    "value_kind": row.value_kind,
                    "unit": row.unit,
                    "currency": row.currency,
                    "status": row.status,
                }
            )
            for row in window
        )
        next_cursor = None
        if len(rows) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[ImpactMetricSummary](items=items, next_cursor=next_cursor)

    async def get(self, code: str) -> ImpactMetricDetail | None:
        """Return one metric, active or retired.

        Args:
            code: The metric code.

        Returns:
            The detail view, or ``None`` if no metric has that code.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ImpactMetricRow).where(ImpactMetricRow.code == code)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return ImpactMetricDetail.from_entity(row_to_metric(row))


if TYPE_CHECKING:
    # Let mypy prove that the compiler satisfies the kernel's visitor protocol.
    _COMPILER_CHECK: SpecificationVisitor[ImpactMetricSummary, ColumnElement[bool]] = (
        ImpactMetricSpecificationCompiler()
    )
