"""SQL implementation of the hazards query service port.

Listings select only the columns ``HazardTypeSummary`` needs and page by keyset on
the unique ``code`` (no ``OFFSET``, no ``COUNT(*)``): the cursor carries the last code
and id, and the next page starts strictly after that code.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from typing import TYPE_CHECKING, Final

from sqlalchemy import ColumnElement, and_, false, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
)
from yakhnama.modules.hazards.application.queries import ListHazardTypes
from yakhnama.modules.hazards.application.specifications import (
    ActiveHazardTypeSpecification,
)
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus
from yakhnama.modules.hazards.infrastructure.mappers import row_to_hazard_type
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
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
    HazardTypeRow.id,
    HazardTypeRow.code,
    HazardTypeRow.parent_code,
    HazardTypeRow.labels,
    HazardTypeRow.status,
    HazardTypeRow.attributes_schema,
)


class HazardTypeSpecificationCompiler:
    """Compiles a hazard type specification tree to a boolean SQL expression.

    Implements: Specification (SQL compiler visitor).
    """

    def visit_and(
        self, specification: AndSpecification[HazardTypeSummary]
    ) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(
        self, specification: OrSpecification[HazardTypeSummary]
    ) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(
        self, specification: NotSpecification[HazardTypeSummary]
    ) -> ColumnElement[bool]:
        """Compile a negation.

        Args:
            specification: The negation.

        Returns:
            ``NOT operand``.
        """
        return not_(specification.operand.accept(self))

    def visit_leaf(
        self, specification: Specification[HazardTypeSummary]
    ) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: A hazards leaf or a constant specification.

        Returns:
            The SQL condition of the leaf.

        Raises:
            TypeError: If the leaf has no SQL translation.
        """
        match specification:
            case ActiveHazardTypeSpecification():
                return HazardTypeRow.status == HazardTypeStatus.ACTIVE.value
            case TrueSpecification():
                return true()
            case FalseSpecification():
                return false()
            case _:
                message = f"no SQL translation for {type(specification).__name__}"
                raise TypeError(message)


class SqlAlchemyHazardTypeQueryService:
    """PostgreSQL-backed implementation of ``HazardTypeQueryService``.

    Implements: Query Service (port ``HazardTypeQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def list_hazard_types(
        self, query: ListHazardTypes
    ) -> Page[HazardTypeSummary]:
        """Return one page of hazard types matching ``query``, ordered by code.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = select(*_SUMMARY_COLUMNS).order_by(HazardTypeRow.code)
        specification = query.to_specification()
        if specification is not None:
            statement = statement.where(
                specification.accept(HazardTypeSpecificationCompiler())
            )
        if cursor is not None:
            statement = statement.where(HazardTypeRow.code > cursor.sort_key)
        # One extra row tells whether a next page exists without COUNT(*).
        statement = statement.limit(query.page.limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        window = rows[: query.page.limit]
        items = tuple(
            HazardTypeSummary.model_validate(
                {
                    "code": row.code,
                    "parent_code": row.parent_code,
                    "labels": row.labels,
                    "status": row.status,
                    "attributes_schema": row.attributes_schema,
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
        return Page[HazardTypeSummary](items=items, next_cursor=next_cursor)

    async def get(self, code: str) -> HazardTypeDetail | None:
        """Return one hazard type, active or retired.

        Args:
            code: The hazard code.

        Returns:
            The detail view, or ``None`` if no type has that code.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(HazardTypeRow).where(HazardTypeRow.code == code)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return HazardTypeDetail.from_entity(row_to_hazard_type(row))


if TYPE_CHECKING:
    # Let mypy prove that the compiler satisfies the kernel's visitor protocol.
    _COMPILER_CHECK: SpecificationVisitor[HazardTypeSummary, ColumnElement[bool]] = (
        HazardTypeSpecificationCompiler()
    )
