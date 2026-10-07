"""SQL implementations of the ``ReportQueryService`` and ``NearbyReportsFinder`` ports.

**Rounding in SQL.** A reporter's position is private; public listings show it
rounded to ``public_coordinate_decimals`` (``shared_kernel.privacy``). The listing
query therefore never loads the exact point: ``observation`` and
``accuracy_metres`` are deferred with ``raiseload`` and the position is selected as
``rounded_degrees(ST_X(observation))`` and ``rounded_degrees(ST_Y(observation))``.
The records a listing returns carry that rounded point and no accuracy; rounding is
idempotent, so ``ReportSummary.from_record`` rounds them again to the same value.
``get_report`` returns the exact point and accuracy, because the application decides
per caller whether a detail view is exact.

``rounded_degrees`` reproduces ``round_coordinates`` exactly: it rounds the
coordinate's **shortest decimal text** (``float8::text``, which since PostgreSQL 12
is the shortest round-trip representation, as Python's ``repr``) as ``numeric`` with
``round(numeric, d)``, which rounds ties away from zero like ``ROUND_HALF_UP``. So
``36.125`` rounds to ``36.13`` in both, although the binary double is
``36.12499...``; the integration tests pin that agreement.

**Bounding boxes test the rounded point** (``ReportInBoundingBoxSpecification``). A
point just outside the box whose rounded position is inside matches, and a point
just inside whose rounded position is outside does not, exactly like the in-memory
specification; testing the exact point would let a caller shrink boxes around one
report until its private position is known. So that the GiST index still serves the
query, the compiled condition first tests the exact point against the box widened by
one rounding step (``observation && ST_MakeEnvelope(...)``): rounding moves a point
by at most half a step per axis, so the prefilter never drops a match.

Listings page by keyset on ``(created_at, id)`` descending, newest first; the
cursor's ``sort_key`` is ``created_at`` in ISO 8601 and ``last_id`` the last report's
id, as the port requires.

**Review marks** (ADR 0022). Both reads left-join the lineage's row in
``report_reviews`` (primary key ``lineage_id``) and select the lineage's newest
revision as a correlated ``max(revision)`` over ``ix_reports_lineage_id``, so each
record carries its ``ReportReviewRecord`` without a second round trip.
``ReportReviewStateSpecification`` compiles to ``coalesce(state, 'new') = :state``:
a lineage without a review is ``new``. The history is read newest first from
``report_review_marks`` over its ``(lineage_id, marked_at, id)`` index.

**Linked events** are read from ``event_report_links``, the events module's
projection of its report links, as a lightweight ``table()`` clause with only the
columns read here (never the events module's ORM model; modules share no Python
internals, ``AGENTS.md`` §2.1), joined to every revision of the lineage.

**Nearby reports.** ``find_nearby`` selects current (``submitted``) reports with
``ST_DWithin(observation::geography, center::geography, radius, false)`` and
``observed_at`` within the window on either side, nearest first by
``ST_Distance`` on the same sphere, then by id. ``use_spheroid = false`` measures on
PostGIS's mean-radius sphere (6 371 008.8 m), the same sphere the domain's haversine
rule uses (``triage.MEAN_EARTH_RADIUS_METRES``), so the adapter's radius agrees with
the rule that filters again; the WGS84 spheroid would differ by up to about 0.5 % and
could drop a candidate the rule accepts. The cast to ``geography`` cannot use the
geometry GiST index; the ``observed_at`` index bounds the scan to the time window.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from geoalchemy2 import Geography
from sqlalchemy import (
    ColumnElement,
    Float,
    Numeric,
    String,
    Text,
    Uuid,
    and_,
    cast,
    column,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased, defer
from sqlalchemy.types import DateTime

from yakhnama.modules.reports.application.dto import (
    LinkedEvent,
    ReportRecord,
    ReportReviewRecord,
)
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.application.specifications import (
    ReportChannelSpecification,
    ReportHazardCodeSpecification,
    ReportInBoundingBoxSpecification,
    ReportObservedFromSpecification,
    ReportObservedToSpecification,
    ReportReporterSpecification,
    ReportReviewStateSpecification,
    ReportStatusSpecification,
    ReportTriageFlagSpecification,
)
from yakhnama.modules.reports.domain.reviews import ReviewMark, ReviewState
from yakhnama.modules.reports.domain.triage import ReportSummaryForTriage
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.modules.reports.infrastructure.mappers import (
    element_to_coordinates,
    observed_at_from_columns,
    row_to_exact_record,
    row_to_review_mark,
    row_to_rounded_record,
)
from yakhnama.modules.reports.infrastructure.orm import (
    WGS84_SRID,
    ReportReviewMarkRow,
    ReportReviewRow,
    ReportRow,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    FalseSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    TrueSpecification,
)
from yakhnama.shared_kernel.value_objects import Coordinates

_DEGREES_BASE: Final = 10

EVENT_REPORT_LINKS: Final = table(
    "event_report_links",
    column("event_id", Uuid()),
    column("report_id", Uuid()),
    column("role", String()),
    column("linked_at", DateTime(timezone=True)),
)
"""The columns of the events module's link projection this read model joins."""

_LINEAGE_REVISIONS = aliased(ReportRow, name="lineage_revisions")
# ST_DWithin / ST_Distance on geography: False selects the mean-radius sphere.
_USE_SPHEROID: Final = False


def rounded_degrees(axis: ColumnElement[float], decimals: int) -> ColumnElement[float]:
    """Return ``axis`` rounded like ``shared_kernel.privacy.round_coordinates``.

    Args:
        axis: A ``float8`` coordinate expression, for example ``ST_X(observation)``.
        decimals: Decimal places to keep, 0 to 6.

    Returns:
        ``round(axis::text::numeric, decimals)::float8``.
    """
    # float8::text is the shortest round-trip decimal (PostgreSQL 12+), like repr();
    # float8::numeric would use 15 significant digits and could round differently.
    return cast(func.round(cast(cast(axis, Text), Numeric), decimals), Float)


def rounded_point(
    decimals: int,
) -> tuple[ColumnElement[float], ColumnElement[float]]:
    """Return the rounded longitude and latitude of ``reports.observation``.

    Args:
        decimals: Decimal places to keep, 0 to 6.

    Returns:
        ``(longitude, latitude)`` expressions rounded as the public payloads are.
    """
    return (
        rounded_degrees(func.ST_X(ReportRow.observation), decimals),
        rounded_degrees(func.ST_Y(ReportRow.observation), decimals),
    )


def decode_since(sort_key: str) -> datetime:
    """Parse the ``created_at`` instant a report-listing cursor carries.

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


def _in_rounded_bbox(
    specification: ReportInBoundingBoxSpecification,
) -> ColumnElement[bool]:
    bbox = specification.bbox
    decimals = specification.public_coordinates.decimals
    longitude, latitude = rounded_point(decimals)
    # One full rounding step of margin; rounding moves a point by at most half.
    step = float(_DEGREES_BASE**-decimals)
    widened = func.ST_MakeEnvelope(
        bbox.min_longitude - step,
        bbox.min_latitude - step,
        bbox.max_longitude + step,
        bbox.max_latitude + step,
        WGS84_SRID,
    )
    return and_(
        ReportRow.observation.op("&&")(widened),
        longitude.between(bbox.min_longitude, bbox.max_longitude),
        latitude.between(bbox.min_latitude, bbox.max_latitude),
    )


def _report_leaf_condition(
    specification: Specification[ReportRecord],
) -> ColumnElement[bool]:
    """Compile one of the nine report leaves.

    Raises:
        TypeError: If the leaf has no SQL translation.
    """
    condition: ColumnElement[bool]
    match specification:
        case ReportStatusSpecification():
            condition = ReportRow.status == specification.status.value
        case ReportChannelSpecification():
            condition = ReportRow.channel == specification.channel.value
        case ReportHazardCodeSpecification():
            condition = ReportRow.hazard_code == specification.hazard_code
        case ReportInBoundingBoxSpecification():
            condition = _in_rounded_bbox(specification)
        case ReportObservedFromSpecification():
            condition = ReportRow.observed_at >= specification.instant
        case ReportObservedToSpecification():
            condition = ReportRow.observed_at <= specification.instant
        case ReportReporterSpecification():
            condition = ReportRow.reporter_id == specification.reporter_id
        case ReportReviewStateSpecification():
            condition = (
                func.coalesce(ReportReviewRow.state, literal(ReviewState.NEW.value))
                == specification.state.value
            )
        case ReportTriageFlagSpecification():
            # Containment on the whole column, so the GIN index
            # ix_reports_triage_gin (jsonb_path_ops, migration 0026) serves it.
            condition = ReportRow.triage.op("@>")(
                literal({"flags": [{"kind": specification.kind}]}, JSONB)
            )
        case _:
            message = f"no SQL translation for {type(specification).__name__}"
            raise TypeError(message)
    return condition


def latest_revision_of_lineage() -> ColumnElement[int]:
    """Return the newest revision number of the row's lineage, as a scalar.

    Returns:
        A correlated ``max(revision)`` over the lineage's rows.
    """
    return (
        select(func.max(_LINEAGE_REVISIONS.revision))
        .where(_LINEAGE_REVISIONS.lineage_id == ReportRow.lineage_id)
        .correlate(ReportRow)
        .scalar_subquery()
    )


def review_record_from(found: Sequence[object]) -> ReportReviewRecord | None:
    """Build the review a joined row carries, if the lineage has one.

    Args:
        found: A row whose last seven columns are the ``REVIEW_COLUMNS``.

    Returns:
        The review, or ``None`` when the outer join found none.
    """
    (
        state,
        reviewed_report_id,
        reviewed_revision,
        updated_at,
        updated_by,
        version,
        latest_revision,
    ) = found[-len(REVIEW_COLUMNS) :]
    if state is None:
        return None
    return ReportReviewRecord.model_validate(
        {
            "state": state,
            "reviewed_report_id": reviewed_report_id,
            "reviewed_revision": reviewed_revision,
            "updated_at": updated_at,
            "updated_by": updated_by,
            "version": version,
            "latest_revision": latest_revision,
        }
    )


REVIEW_COLUMNS: Final = (
    ReportReviewRow.state,
    ReportReviewRow.reviewed_report_id,
    ReportReviewRow.reviewed_revision,
    ReportReviewRow.updated_at,
    ReportReviewRow.updated_by,
    ReportReviewRow.version,
    latest_revision_of_lineage(),
)
"""The review columns both reads select after the report's own."""

_REVIEW_JOIN: Final = ReportReviewRow.lineage_id == ReportRow.lineage_id


class ReportSpecificationCompiler:
    """Compiles a report specification tree to a boolean SQL expression.

    Implements: Specification (SQL compiler visitor).
    """

    def visit_and(
        self, specification: AndSpecification[ReportRecord]
    ) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(
        self, specification: OrSpecification[ReportRecord]
    ) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(
        self, specification: NotSpecification[ReportRecord]
    ) -> ColumnElement[bool]:
        """Compile a negation.

        ``hazard_code`` is the only nullable column a leaf compares, so a negated
        hazard filter must keep reports without a guess, as the in-memory
        ``NOT is_satisfied_by`` does; ``coalesce`` turns SQL's unknown into false
        before the negation.

        Args:
            specification: The negation.

        Returns:
            ``NOT coalesce(operand, false)``.
        """
        return not_(func.coalesce(specification.operand.accept(self), false()))

    def visit_leaf(
        self, specification: Specification[ReportRecord]
    ) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: A reports leaf or a constant specification.

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
                return _report_leaf_condition(specification)


class SqlAlchemyReportQueryService:
    """PostgreSQL-backed implementation of ``ReportQueryService``.

    Implements: Query Service (port ``ReportQueryService``).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        public_coordinates: PublicCoordinatePolicy,
    ) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
            public_coordinates: The rounding applied to every listed position,
                built from ``Settings.public_coordinate_decimals``.
        """
        self._session_factory = session_factory
        self._public_coordinates = public_coordinates

    async def get_report(self, report_id: EntityId) -> ReportRecord | None:
        """Return one report with its exact position, whatever its status.

        Args:
            report_id: The report.

        Returns:
            The record, or ``None``.
        """
        statement = (
            select(ReportRow, *REVIEW_COLUMNS)
            .outerjoin(ReportReviewRow, _REVIEW_JOIN)
            .where(ReportRow.id == report_id)
        )
        async with self._session_factory() as session:
            found = (await session.execute(statement)).one_or_none()
        if found is None:
            return None
        return row_to_exact_record(found[0], review_record_from(found))

    async def list_reports(
        self, specification: Specification[ReportRecord], page: PageRequest
    ) -> Page[ReportRecord]:
        """Return one page of matching reports, newest first, positions rounded.

        Args:
            specification: The filter, compiled to SQL.
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` records (rounded point, no accuracy) and the next
            cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
            TypeError: If the specification holds a leaf with no SQL translation.
        """
        cursor = page.decode_cursor()
        longitude, latitude = rounded_point(self._public_coordinates.decimals)
        statement = (
            select(ReportRow, longitude, latitude, *REVIEW_COLUMNS)
            .outerjoin(ReportReviewRow, _REVIEW_JOIN)
            # raiseload: reading the exact point on this path is a bug, not a query.
            .options(
                defer(ReportRow.observation, raiseload=True),
                defer(ReportRow.accuracy_metres, raiseload=True),
            )
            .where(specification.accept(ReportSpecificationCompiler()))
            .order_by(ReportRow.created_at.desc(), ReportRow.id.desc())
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    ReportRow.created_at < since,
                    and_(ReportRow.created_at == since, ReportRow.id < cursor.last_id),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        records = [
            row_to_rounded_record(
                found[0],
                Coordinates(longitude=found[1], latitude=found[2]),
                review_record_from(found),
            )
            for found in rows
        ]
        window = records[: page.limit]
        next_cursor = None
        if len(records) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[ReportRecord](items=tuple(window), next_cursor=next_cursor)

    async def list_review_marks(
        self, lineage_id: EntityId, limit: int
    ) -> tuple[ReviewMark, ...]:
        """Return a lineage's review marks, newest first.

        Args:
            lineage_id: The id of the lineage's revision 1.
            limit: Most marks returned.

        Returns:
            Up to ``limit`` marks, by ``marked_at`` then id, descending.
        """
        statement = (
            select(ReportReviewMarkRow)
            .where(ReportReviewMarkRow.lineage_id == lineage_id)
            .order_by(
                ReportReviewMarkRow.marked_at.desc(), ReportReviewMarkRow.id.desc()
            )
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return tuple(row_to_review_mark(row) for row in rows)

    async def list_linked_events(
        self, lineage_id: EntityId, limit: int
    ) -> tuple[LinkedEvent, ...]:
        """Return the event links of every revision of a lineage, oldest first.

        Args:
            lineage_id: The id of the lineage's revision 1.
            limit: Most links returned.

        Returns:
            Up to ``limit`` links.
        """
        links = EVENT_REPORT_LINKS
        statement = (
            select(links.c.event_id, links.c.report_id, links.c.role)
            .join(ReportRow, ReportRow.id == links.c.report_id)
            .where(ReportRow.lineage_id == lineage_id)
            .order_by(links.c.linked_at, links.c.event_id, links.c.report_id)
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).tuples().all()
        return tuple(
            LinkedEvent.model_validate(
                {"event_id": event_id, "report_id": report_id, "role": role}
            )
            for event_id, report_id, role in rows
        )


class SqlAlchemyNearbyReportsFinder:
    """PostgreSQL-backed implementation of ``NearbyReportsFinder``.

    Implements: Query Service (port ``NearbyReportsFinder``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the finder.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def find_nearby(
        self, query: FindNearbyReports
    ) -> tuple[ReportSummaryForTriage, ...]:
        """Return current reports near a point and time, nearest first.

        Args:
            query: The centre, time, radius, window, limit and excluded report.

        Returns:
            Up to ``query.limit`` candidates with their exact positions.
        """
        center = cast(
            func.ST_SetSRID(
                func.ST_MakePoint(query.center.longitude, query.center.latitude),
                WGS84_SRID,
            ),
            Geography,
        )
        observation = cast(ReportRow.observation, Geography)
        distance = func.ST_Distance(observation, center, _USE_SPHEROID)
        statement = (
            select(
                ReportRow.id,
                ReportRow.observed_at,
                ReportRow.observed_at_precision,
                ReportRow.observation,
            )
            .where(
                ReportRow.status == ReportStatus.SUBMITTED.value,
                ReportRow.id != query.exclude_report_id,
                ReportRow.observed_at.between(
                    query.observed_at - query.window, query.observed_at + query.window
                ),
                func.ST_DWithin(
                    observation, center, query.radius_metres, _USE_SPHEROID
                ),
            )
            .order_by(distance, ReportRow.id)
            .limit(query.limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).tuples().all()
        return tuple(
            ReportSummaryForTriage(
                report_id=report_id,
                observed_at=observed_at_from_columns(observed_at, precision),
                coordinates=element_to_coordinates(point),
            )
            for report_id, observed_at, precision, point in rows
        )
