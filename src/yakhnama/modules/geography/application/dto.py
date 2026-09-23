"""Read models returned by the geography query services and load handlers.

DTOs are frozen and carry only values readers need. ``from_entity`` builds them from
the aggregate for in-memory implementations; the SQL query service builds them from
selected columns instead, with the same field meanings. Geometry is not part of these
DTOs: boundaries can hold many thousands of positions and get their own read model
once a boundary source is chosen (open question Q1).

Patterns: DTO.
"""

from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.geography.domain.entities import PLACE_MAX_NAMES, Place
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceCode,
    PlaceName,
    PlaceStatus,
    PlaceVersion,
    StatusReason,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates

SKIP_REASON_MAX_LENGTH = 500
LOAD_REPORT_MAX_ENTRIES = 10_000

DISPLAY_FALLBACK_LANGUAGE: Final = "en"
"""Language tried after the requested one when choosing a display name.

Proposed default: English, because the reference files ship English names for every
place and names in other languages only with a source.
"""


def display_name(place: Place, language: str | None) -> PlaceName:
    """Choose the name to show for ``place``.

    The preferred (or first) name in ``language`` wins, then the same in
    ``DISPLAY_FALLBACK_LANGUAGE``, then the place's first name, so every place has a
    display name.

    Args:
        place: The place.
        language: The reader's language code, if known.

    Returns:
        The chosen name.

    Raises:
        pydantic.ValidationError: If ``language`` is not a valid language code.
    """
    requested = DISPLAY_FALLBACK_LANGUAGE if language is None else language
    chosen = place.preferred_name(requested, (DISPLAY_FALLBACK_LANGUAGE,))
    return place.names[0] if chosen is None else chosen


class PlaceSummary(BaseModel):
    """One place in a search result.

    Implements: DTO.

    Attributes:
        id: Stable identity (UUIDv7).
        code: Stable machine code.
        level: Administrative level.
        parent_code: Code of the enclosing place, ``None`` for a country.
        name: The display name chosen by ``display_name``.
        centroid: Representative WGS84 point, if known.
        status: ``active``, ``merged`` or ``retired``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: PlaceCode
    level: AdminLevel
    parent_code: PlaceCode | None
    name: PlaceName
    centroid: Coordinates | None
    status: PlaceStatus

    @classmethod
    def from_entity(
        cls, place: Place, *, parent_code: str | None, language: str | None
    ) -> Self:
        """Build the summary of a place.

        Args:
            place: The aggregate.
            parent_code: Code of its parent, resolved by the caller.
            language: The reader's language, for the display name.

        Returns:
            Its summary.
        """
        return cls(
            id=place.id,
            code=place.code,
            level=place.level,
            parent_code=parent_code,
            name=display_name(place, language),
            centroid=place.centroid,
            status=place.status,
        )


class PlaceDetail(BaseModel):
    """One place with every name and its lifecycle.

    Implements: DTO.

    Attributes:
        id: Stable identity (UUIDv7).
        code: Stable machine code.
        level: Administrative level.
        parent_id: The enclosing place, ``None`` for a country.
        parent_code: Code of the enclosing place, ``None`` for a country.
        names: Every name, in insertion order.
        centroid: Representative WGS84 point, if known.
        status: ``active``, ``merged`` or ``retired``.
        status_reason: Why it was merged or retired; ``None`` while active.
        merged_into_id: The replacing place when merged.
        version: Optimistic-concurrency version.
        created_at: When the place was first recorded, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: PlaceCode
    level: AdminLevel
    parent_id: EntityId | None
    parent_code: PlaceCode | None
    names: tuple[PlaceName, ...] = Field(min_length=1, max_length=PLACE_MAX_NAMES)
    centroid: Coordinates | None
    status: PlaceStatus
    status_reason: StatusReason | None
    merged_into_id: EntityId | None
    version: PlaceVersion
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, place: Place, *, parent_code: str | None) -> Self:
        """Build the detail view of a place.

        Args:
            place: The aggregate.
            parent_code: Code of its parent, resolved by the caller.

        Returns:
            Its detail view.
        """
        return cls(
            id=place.id,
            code=place.code,
            level=place.level,
            parent_id=place.parent_id,
            parent_code=parent_code,
            names=place.names,
            centroid=place.centroid,
            status=place.status,
            status_reason=place.status_reason,
            merged_into_id=place.merged_into_id,
            version=place.version,
            created_at=place.created_at,
            updated_at=place.updated_at,
        )


class SkippedChange(BaseModel):
    """A difference between the reference file and the stored state left unapplied.

    Implements: DTO.

    Attributes:
        code: The place code the difference concerns.
        reason: Why the loader did not apply it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PlaceCode
    reason: str = Field(min_length=1, max_length=SKIP_REASON_MAX_LENGTH)


class LoadReport(BaseModel):
    """What loading a place reference file did.

    Every code of the file appears in exactly one of ``created``, ``updated`` and
    ``unchanged``. ``skipped_with_reason`` lists differences the loader refused to
    apply; their codes also appear in ``updated`` or ``unchanged``.

    Implements: DTO.

    Attributes:
        data_version: The ``data_version`` of the loaded file.
        dry_run: Whether the changes were rolled back instead of committed.
        created: Codes created by this load, parents first.
        updated: Codes changed by this load.
        unchanged: Codes already matching the file.
        skipped_with_reason: Differences left unapplied, with the reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_version: str = Field(min_length=1, max_length=32)
    dry_run: bool
    created: tuple[PlaceCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    updated: tuple[PlaceCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    unchanged: tuple[PlaceCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    skipped_with_reason: tuple[SkippedChange, ...] = Field(
        max_length=LOAD_REPORT_MAX_ENTRIES
    )

    @property
    def is_unchanged(self) -> bool:
        """Return ``True`` if the load created and updated nothing."""
        return not self.created and not self.updated
