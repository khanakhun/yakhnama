"""SQL implementation of the geography query service port.

Search compiles the ``SearchPlaces`` specification tree to one SQL ``WHERE`` clause
with ``PlaceSpecificationCompiler`` (a ``SpecificationVisitor``). A text leaf matches
a place when one of its names (in the requested language, if any) matches the search
text through either

- the ``pg_trgm`` similarity operator ``text_folded % :form`` (fuzzy: tolerates
  misspellings and alternative romanisations), or
- ``text_folded ILIKE '%form%'`` (substring: guarantees every match of the in-memory
  rule in ``PlaceTextSpecification`` is also found, prefixes included).

Both are served by the GIN trigram index on ``place_names.text_folded``. The search
forms are the folded search text and, when that text is in Latin script, its
``unaccent`` form, matching how ``mappers.names_to_rows`` stores Latin names. The
``%`` operator uses the server's ``pg_trgm.similarity_threshold``, left at its default
of 0.3 on purpose: the substring branch already catches short prefixes, so no
per-session ``SET LOCAL`` is needed, and one global threshold keeps results
reproducible between sessions.

Results are ranked by the best ``similarity(text_folded, form)`` over the place's
names, then by id, and paged by keyset on ``(score, id)``: the cursor carries the last
score (as ``repr`` of the float, which round-trips exactly) and the last id.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    ColumnElement,
    Double,
    and_,
    cast,
    exists,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from yakhnama.modules.geography.application.dto import PlaceDetail, PlaceSummary
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.modules.geography.application.specifications import (
    ActivePlaceSpecification,
    PlaceLevelSpecification,
    PlaceTextSpecification,
    fold_search_text,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.infrastructure.mappers import (
    is_latin_text,
    row_to_place,
)
from yakhnama.modules.geography.infrastructure.orm import PlaceNameRow, PlaceRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    FalseSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    SpecificationVisitor,
    TrueSpecification,
)

ACTIVE_STATUS: Final = "active"
_LIKE_ESCAPE: Final = "\\"


def escape_like(text: str) -> str:
    r"""Escape ``LIKE`` wildcards so ``text`` matches only literally.

    Args:
        text: The text to embed in a ``LIKE`` pattern.

    Returns:
        ``text`` with ``\\``, ``%`` and ``_`` escaped by a backslash.
    """
    return (
        text.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


class SearchTextCollector:
    """Collects every text leaf of a place specification tree.

    The query service needs the search forms of each text before it compiles the
    tree, because the ``unaccent`` form is computed by the database.

    Implements: Specification (visitor collecting text leaves).
    """

    def visit_and(
        self, specification: AndSpecification[Place]
    ) -> tuple[PlaceTextSpecification, ...]:
        """Collect from both operands.

        Args:
            specification: The conjunction.

        Returns:
            The text leaves of both operands, left first.
        """
        return (*specification.left.accept(self), *specification.right.accept(self))

    def visit_or(
        self, specification: OrSpecification[Place]
    ) -> tuple[PlaceTextSpecification, ...]:
        """Collect from both operands.

        Args:
            specification: The disjunction.

        Returns:
            The text leaves of both operands, left first.
        """
        return (*specification.left.accept(self), *specification.right.accept(self))

    def visit_not(
        self, specification: NotSpecification[Place]
    ) -> tuple[PlaceTextSpecification, ...]:
        """Collect from the operand.

        Args:
            specification: The negation.

        Returns:
            The text leaves of the operand.
        """
        return specification.operand.accept(self)

    def visit_leaf(
        self, specification: Specification[Place]
    ) -> tuple[PlaceTextSpecification, ...]:
        """Return the leaf itself if it is a text leaf.

        Args:
            specification: Any leaf.

        Returns:
            A one-element tuple for a ``PlaceTextSpecification``, else empty.
        """
        if isinstance(specification, PlaceTextSpecification):
            return (specification,)
        return ()


class PlaceSpecificationCompiler:
    """Compiles a place specification tree to a boolean SQL expression over places.

    Implements: Specification (SQL compiler visitor).
    """

    def __init__(self, search_forms: Mapping[str, Sequence[str]]) -> None:
        """Create the compiler.

        Args:
            search_forms: For each search text of the tree (as given), the forms to
                match ``text_folded`` against; built by ``search_forms_for``.
        """
        self._search_forms = search_forms

    def visit_and(self, specification: AndSpecification[Place]) -> ColumnElement[bool]:
        """Compile a conjunction.

        Args:
            specification: The conjunction.

        Returns:
            ``left AND right``.
        """
        return and_(specification.left.accept(self), specification.right.accept(self))

    def visit_or(self, specification: OrSpecification[Place]) -> ColumnElement[bool]:
        """Compile a disjunction.

        Args:
            specification: The disjunction.

        Returns:
            ``left OR right``.
        """
        return or_(specification.left.accept(self), specification.right.accept(self))

    def visit_not(self, specification: NotSpecification[Place]) -> ColumnElement[bool]:
        """Compile a negation.

        Args:
            specification: The negation.

        Returns:
            ``NOT operand``.
        """
        return not_(specification.operand.accept(self))

    def visit_leaf(self, specification: Specification[Place]) -> ColumnElement[bool]:
        """Compile a leaf.

        Args:
            specification: A geography leaf or a constant specification.

        Returns:
            The SQL condition of the leaf.

        Raises:
            TypeError: If the leaf has no SQL translation.
            KeyError: If a text leaf's search forms were not prepared.
        """
        match specification:
            case PlaceTextSpecification():
                return self._text_condition(specification)
            case PlaceLevelSpecification():
                return PlaceRow.level == specification.level.value
            case ActivePlaceSpecification():
                return PlaceRow.status == ACTIVE_STATUS
            case TrueSpecification():
                return true()
            case FalseSpecification():
                return false()
            case _:
                message = f"no SQL translation for {type(specification).__name__}"
                raise TypeError(message)

    def _text_condition(
        self, specification: PlaceTextSpecification
    ) -> ColumnElement[bool]:
        forms = self._search_forms.get(specification.text)
        if forms is None:
            message = f"no search forms prepared for {specification.text!r}"
            raise KeyError(message)
        matches = [
            condition
            for form in forms
            for condition in (
                PlaceNameRow.text_folded.op("%", is_comparison=True)(form),
                PlaceNameRow.text_folded.ilike(
                    f"%{escape_like(form)}%", escape=_LIKE_ESCAPE
                ),
            )
        ]
        return exists(
            select(PlaceNameRow.id).where(
                PlaceNameRow.place_id == PlaceRow.id,
                *_language_filter(specification.language),
                or_(*matches),
            )
        )


def _language_filter(language: str | None) -> tuple[ColumnElement[bool], ...]:
    return () if language is None else (PlaceNameRow.language == language,)


async def search_forms_for(session: AsyncSession, text: str) -> tuple[str, ...]:
    """Return the forms ``text_folded`` is compared with for one search text.

    Args:
        session: An open session, used to run ``unaccent`` for Latin text.
        text: The search text as given.

    Returns:
        The folded text and, for Latin text, its ``unaccent`` form if different.
    """
    folded = fold_search_text(text)
    if not is_latin_text(folded):
        return (folded,)
    unaccented = await session.scalar(select(func.unaccent(folded)))
    if unaccented is None or unaccented == folded:
        return (folded,)
    return (folded, unaccented)


def decode_score(sort_key: str) -> float:
    """Parse the score a search cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``.

    Returns:
        The finite score.

    Raises:
        ValidationError: If ``sort_key`` is not a finite number.
    """
    try:
        score = float(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    if not math.isfinite(score):
        raise _invalid_cursor()
    return score


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class SqlAlchemyPlaceQueryService:
    """PostGIS-backed implementation of ``PlaceQueryService``.

    Implements: Query Service (port ``PlaceQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def search(self, query: SearchPlaces) -> Page[PlaceSummary]:
        """Return one page of places matching ``query``, most relevant first.

        Args:
            query: Search text, filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        after_score = None if cursor is None else decode_score(cursor.sort_key)
        specification = query.to_specification()
        async with self._session_factory() as session:
            forms = {
                leaf.text: await search_forms_for(session, leaf.text)
                for leaf in specification.accept(SearchTextCollector())
            }
            condition = specification.accept(PlaceSpecificationCompiler(forms))
            score = _score(forms[query.text], query.language)
            ranked = (
                select(PlaceRow.id.label("id"), score.label("score"))
                .where(condition)
                .subquery("ranked")
            )
            statement = select(ranked.c.id, ranked.c.score)
            if cursor is not None and after_score is not None:
                after: ColumnElement[float] = literal(after_score, Double())
                statement = statement.where(
                    or_(
                        ranked.c.score < after,
                        and_(ranked.c.score == after, ranked.c.id > cursor.last_id),
                    )
                )
            # One extra row tells whether a next page exists without COUNT(*).
            statement = statement.order_by(ranked.c.score.desc(), ranked.c.id).limit(
                query.page.limit + 1
            )
            hits = (await session.execute(statement)).tuples().all()
            window = hits[: query.page.limit]
            places = await _load_places(session, [place_id for place_id, _ in window])
            parent_codes = await _load_codes(
                session, [place.parent_id for place in places.values()]
            )
        items = tuple(
            PlaceSummary.from_entity(
                places[place_id],
                parent_code=_parent_code(places[place_id], parent_codes),
                language=query.language,
            )
            for place_id, _ in window
        )
        next_cursor = None
        if len(hits) > query.page.limit:
            last_id, last_score = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=repr(last_score), last_id=last_id)
            )
        return Page[PlaceSummary](items=items, next_cursor=next_cursor)

    async def get(self, place_id: EntityId) -> PlaceDetail | None:
        """Return one place, whatever its status.

        Args:
            place_id: The place id.

        Returns:
            The detail view, or ``None`` if it does not exist.
        """
        async with self._session_factory() as session:
            places = await _load_places(session, [place_id])
            place = places.get(place_id)
            if place is None:
                return None
            parent_codes = await _load_codes(session, [place.parent_id])
        return PlaceDetail.from_entity(
            place, parent_code=_parent_code(place, parent_codes)
        )


def _score(forms: Sequence[str], language: str | None) -> ColumnElement[float]:
    # The best similarity over the place's (language-filtered) names; places that
    # matched through another branch of an OR tree without such names score 0.
    best = (
        select(
            func.max(
                func.greatest(
                    *(func.similarity(PlaceNameRow.text_folded, form) for form in forms)
                )
            )
        )
        .where(PlaceNameRow.place_id == PlaceRow.id, *_language_filter(language))
        .scalar_subquery()
    )
    # Double precision so the cursor's Python float compares exactly.
    return cast(func.coalesce(best, 0.0), Double())


async def _load_places(
    session: AsyncSession, place_ids: Sequence[EntityId]
) -> dict[EntityId, Place]:
    if not place_ids:
        return {}
    # Read models never show the footprint, which can hold many thousands of
    # positions, so the column is not even fetched; raiseload makes any access fail
    # loudly instead of lazily loading it.
    rows = (
        await session.execute(
            select(PlaceRow)
            .options(defer(PlaceRow.geometry, raiseload=True))
            .where(PlaceRow.id.in_(place_ids))
        )
    ).scalars()
    name_rows = (
        await session.execute(
            select(PlaceNameRow).where(PlaceNameRow.place_id.in_(place_ids))
        )
    ).scalars()
    names_by_place: dict[EntityId, list[PlaceNameRow]] = {}
    for name_row in name_rows:
        names_by_place.setdefault(name_row.place_id, []).append(name_row)
    return {
        row.id: row_to_place(
            row, names_by_place.get(row.id, ()), include_geometry=False
        )
        for row in rows
    }


async def _load_codes(
    session: AsyncSession, place_ids: Iterable[EntityId | None]
) -> dict[EntityId, str]:
    wanted = {place_id for place_id in place_ids if place_id is not None}
    if not wanted:
        return {}
    rows = await session.execute(
        select(PlaceRow.id, PlaceRow.code).where(PlaceRow.id.in_(wanted))
    )
    return dict(rows.tuples().all())


def _parent_code(place: Place, codes: Mapping[EntityId, str]) -> str | None:
    return None if place.parent_id is None else codes[place.parent_id]


if TYPE_CHECKING:
    # Let mypy prove that both visitors satisfy the kernel's visitor protocol.
    _COMPILER_CHECK: SpecificationVisitor[Place, ColumnElement[bool]] = (
        PlaceSpecificationCompiler({})
    )
    _COLLECTOR_CHECK: SpecificationVisitor[
        Place, tuple[PlaceTextSpecification, ...]
    ] = SearchTextCollector()
