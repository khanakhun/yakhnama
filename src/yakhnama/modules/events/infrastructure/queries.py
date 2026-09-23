"""SQL implementation of the ``EventQueryService`` port.

**Verification state.** The event aggregate does not know whether it is verified;
the ``verification`` module owns that. Every read here selects the state of the
event's case as a correlated scalar subquery on ``verification_cases`` (target kind
``event``, target id the event's id), taking the most recently opened case first, so
the answer stays defined even if the one-case-per-target rule were ever relaxed.
The table is referenced as a lightweight ``table()`` clause with only the columns
read here, never through the verification module's ORM model: modules share no
Python internals (``AGENTS.md`` §2.1), and the unique index on
``(target_kind, target_id)`` from migration 0013 serves the lookup.
``VerifiedEventSpecification`` compiles to a test on that same expression.

**Specifications.** The six ``EventSearchCandidate`` leaves compile to:

- ``EventBboxSpecification``: ``ST_Intersects(centroid, ST_MakeEnvelope(...))``,
  edges included like ``BoundingBox.contains``; no centroid never matches.
- ``EventHazardTypeSpecification``: ``hazard_code = :code``.
- ``EventPlaceSpecification``: ``affected_places @> '[{"place_code": :code}]'``
  (served by the GIN index), whatever the place's kind.
- ``EventStatusSpecification``: ``status = :status``.
- ``EventPeriodOverlapsSpecification``: ``period_earliest_at <= :end AND
  period_latest_at >= :start`` on the mapper-derived bounds, which are exactly
  ``EventPeriod.earliest_instant`` and ``latest_instant``.
- ``VerifiedEventSpecification``: ``verification_state = 'verified'``.

A negation wraps its operand in ``coalesce(..., false)`` first, so an event without
a centroid or without a case matches ``NOT bbox`` or ``NOT verified`` exactly as the
in-memory ``NOT is_satisfied_by`` does, instead of SQL's unknown dropping it.

**Citations.** ``is_source_cited_by_public_event`` is one ``EXISTS`` over
``events``: ``source_ids @> '["<id>"]'`` (the ids are stored as canonical UUID
strings by the mapper), ``status = 'published'`` and the same verification-state
subquery equal to ``verified``. ``source_ids`` has no GIN index yet (migration 0012),
so the test on it is a filter over the published rows.

**Order and paging.** Newest first by ``period_earliest_at`` then id, both
descending, by keyset; the cursor's ``sort_key`` is the earliest instant in ISO 8601
and ``last_id`` the last event's id, as the port requires. Search never loads the
footprint (``geometry`` is not selected), which can hold up to a million positions.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    ColumnElement,
    Row,
    String,
    Uuid,
    and_,
    column,
    exists,
    false,
    func,
    not_,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.types import DateTime

from yakhnama.modules.events.application.dto import EventDetail, EventSummary
from yakhnama.modules.events.domain.specifications import (
    VERIFIED_STATE,
    EventBboxSpecification,
    EventHazardTypeSpecification,
    EventPeriodOverlapsSpecification,
    EventPlaceSpecification,
    EventSearchCandidate,
    EventStatusSpecification,
    VerifiedEventSpecification,
)
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.events.infrastructure.mappers import (
    affected_places_from_column,
    element_to_coordinates,
    period_from_columns,
    row_to_event,
    row_to_relation,
)
from yakhnama.modules.events.infrastructure.orm import (
    WGS84_SRID,
    EventRelationRow,
    EventRow,
)
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

if TYPE_CHECKING:
    from collections.abc import Sequence

EVENT_TARGET_KIND: Final = "event"
"""The ``verification`` module's ``TargetKind.EVENT`` value, held as text so this
module does not import the verification module."""

VERIFICATION_CASES: Final = table(
    "verification_cases",
    column("id", Uuid()),
    column("target_kind", String()),
    column("target_id", Uuid()),
    column("state", String()),
    column("created_at", DateTime(timezone=True)),
)
"""The columns of the verification module's table this read model joins."""


def verification_state_of_event() -> ColumnElement[str | None]:
    """Return the state of the event's latest verification case, as a scalar.

    Returns:
        A correlated scalar subquery; ``NULL`` when the event has no case.
    """
    cases = VERIFICATION_CASES
    return (
        select(cases.c.state)
        .where(
            cases.c.target_kind == EVENT_TARGET_KIND,
            cases.c.target_id == EventRow.id,
        )
        .order_by(cases.c.created_at.desc(), cases.c.id.desc())
        .limit(1)
        .correlate(EventRow)
        .scalar_subquery()
    )


def decode_since(sort_key: str) -> datetime:
    """Parse the earliest-instant key an event-search cursor carries.

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
    # Comparing a naive instant with timestamptz would assume the session zone.
    if since.utcoffset() is None:
        raise _invalid_cursor()
    return since


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


def _period_condition(
    specification: EventPeriodOverlapsSpecification,
) -> ColumnElement[bool]:
    conditions: list[ColumnElement[bool]] = []
    if specification.end is not None:
        conditions.append(EventRow.period_earliest_at <= specification.end)
    if specification.start is not None:
        conditions.append(EventRow.period_latest_at >= specification.start)
    return and_(true(), *conditions)


def _bbox_condition(specification: EventBboxSpecification) -> ColumnElement[bool]:
    bbox = specification.bbox
    envelope = func.ST_MakeEnvelope(
        bbox.min_longitude,
        bbox.min_latitude,
        bbox.max_longitude,
        bbox.max_latitude,
        WGS84_SRID,
    )
    return func.ST_Intersects(EventRow.centroid, envelope)


class EventSpecificationCompiler:
    """Compiles an event specification tree to a boolean SQL expression.

    Implements: Specification (SQL compiler visitor).
    """

    def __init__(self, verification_state: ColumnElement[str | None]) -> None:
        """Create the compiler.

        Args:
            verification_state: The expression ``VerifiedEventSpecification``
                tests, the same one the query selects.
        """
        self._verification_state = verification_state

    def visit_and(
        self, specification: AndSpecification[EventSearchCandidate]
    ) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(
        self, specification: OrSpecification[EventSearchCandidate]
    ) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(
        self, specification: NotSpecification[EventSearchCandidate]
    ) -> ColumnElement[bool]:
        """Compile a negation, turning SQL's unknown into false first.

        Args:
            specification: The negation.

        Returns:
            ``NOT coalesce(operand, false)``.
        """
        return not_(func.coalesce(specification.operand.accept(self), false()))

    def visit_leaf(
        self, specification: Specification[EventSearchCandidate]
    ) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: An events leaf or a constant specification.

        Returns:
            The SQL condition of the leaf.

        Raises:
            TypeError: If the leaf has no SQL translation.
        """
        match specification:
            case TrueSpecification():
                return true()
            case FalseSpecification():
                return false()
            case _:
                return self._event_leaf_condition(specification)

    def _event_leaf_condition(
        self, specification: Specification[EventSearchCandidate]
    ) -> ColumnElement[bool]:
        match specification:
            case EventBboxSpecification():
                return _bbox_condition(specification)
            case EventHazardTypeSpecification():
                return EventRow.hazard_code == specification.code
            case EventPlaceSpecification():
                return EventRow.affected_places.contains(
                    [{"place_code": specification.place_code}]
                )
            case EventStatusSpecification():
                return EventRow.status == specification.status.value
            case EventPeriodOverlapsSpecification():
                return _period_condition(specification)
            case VerifiedEventSpecification():
                return self._verification_state == VERIFIED_STATE
        message = f"no SQL translation for {type(specification).__name__}"
        raise TypeError(message)


def _summary(row: Row[tuple[object, ...]]) -> EventSummary:
    places = affected_places_from_column(row.affected_places)
    return EventSummary(
        id=row.id,
        hazard_code=row.hazard_code,
        title=row.title,
        period=period_from_columns(
            row.started_at,
            row.started_at_precision,
            row.ended_at,
            row.ended_at_precision,
        ),
        centroid=element_to_coordinates(row.centroid),
        status=row.status,
        verification_state=row.verification_state,
        place_codes=tuple(sorted({place.place_code for place in places})),
    )


class SqlAlchemyEventQueryService:
    """PostgreSQL-backed ``EventQueryService`` and ``EventCitationQueryService``.

    Implements: Query Service (adapter side of both ports).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def search(
        self,
        specification: Specification[EventSearchCandidate],
        page: PageRequest,
    ) -> Page[EventSummary]:
        """Return one page of matching events, newest first.

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
        state = verification_state_of_event()
        statement = (
            select(
                EventRow.id,
                EventRow.hazard_code,
                EventRow.title,
                EventRow.started_at,
                EventRow.started_at_precision,
                EventRow.ended_at,
                EventRow.ended_at_precision,
                EventRow.period_earliest_at,
                EventRow.centroid,
                EventRow.status,
                EventRow.affected_places,
                state.label("verification_state"),
            )
            .where(specification.accept(EventSpecificationCompiler(state)))
            .order_by(EventRow.period_earliest_at.desc(), EventRow.id.desc())
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    EventRow.period_earliest_at < since,
                    and_(
                        EventRow.period_earliest_at == since,
                        EventRow.id < cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows: Sequence[Row[tuple[object, ...]]] = (
                await session.execute(statement)
            ).all()
        window = rows[: page.limit]
        next_cursor = None
        if len(rows) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(
                    sort_key=last.period_earliest_at.isoformat(), last_id=last.id
                )
            )
        return Page[EventSummary](
            items=tuple(_summary(row) for row in window), next_cursor=next_cursor
        )

    async def get(self, event_id: EntityId) -> EventDetail | None:
        """Return one event with its direct relations, whatever its status.

        Args:
            event_id: The event.

        Returns:
            The detail view, or ``None``.
        """
        statement = select(
            EventRow, verification_state_of_event().label("verification_state")
        ).where(EventRow.id == event_id)
        relations = (
            select(EventRelationRow)
            .where(
                or_(
                    EventRelationRow.from_event_id == event_id,
                    EventRelationRow.to_event_id == event_id,
                )
            )
            .order_by(EventRelationRow.related_at, EventRelationRow.id)
        )
        async with self._session_factory() as session:
            found = (await session.execute(statement)).tuples().one_or_none()
            if found is None:
                return None
            relation_rows = (await session.execute(relations)).scalars().all()
        row, verification_state = found
        return EventDetail.from_entity(
            row_to_event(row),
            verification_state=verification_state,
            relations=[row_to_relation(relation) for relation in relation_rows],
        )

    async def is_source_cited_by_public_event(self, source_id: EntityId) -> bool:
        """Tell whether a published and verified event lists ``source_id``.

        Args:
            source_id: The source.

        Returns:
            ``True`` if such an event exists.
        """
        statement = select(
            exists().where(
                EventRow.source_ids.contains([str(source_id)]),
                EventRow.status == EventStatus.PUBLISHED.value,
                verification_state_of_event() == VERIFIED_STATE,
            )
        )
        async with self._session_factory() as session:
            return bool((await session.execute(statement)).scalar_one())
