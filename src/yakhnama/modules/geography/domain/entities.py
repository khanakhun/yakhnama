"""The ``Place`` aggregate root of the ``geography`` bounded context.

A place is frozen. Every state-changing method validates the new state, bumps
``version`` by one, stamps ``updated_at`` from the injected ``Clock`` and returns an
``AggregateChange`` holding the new place and the events the change produced; the
caller's instance is never modified. A method asked to make a change that is already
in effect (the same geometry, an already preferred name) returns the place unchanged
with no events, so reference-data loaders can re-apply the same data idempotently.

Patterns: Entity, Aggregate Root, Domain Events.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from yakhnama.modules.geography.domain.errors import (
    DuplicatePlaceNameError,
    PlaceNameNotFoundError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.events import (
    PlaceCentroidChanged,
    PlaceEvent,
    PlaceGeometryChanged,
    PlaceMerged,
    PlaceNameAdded,
    PlacePreferredNameChanged,
    PlaceRetired,
)
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceCode,
    PlaceGeometry,
    PlaceName,
    PlaceNameText,
    PlaceStatus,
    PlaceVersion,
    ScriptCode,
    StatusReason,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    LanguageCode,
)

# Proposed bound: generous for seven regional languages plus spellings and historical
# names, small enough that one place never becomes an unbounded list.
PLACE_MAX_NAMES = 64

_LANGUAGE_CODE: TypeAdapter[str] = TypeAdapter(LanguageCode)
_NAME_TEXT: TypeAdapter[str] = TypeAdapter(PlaceNameText)
_STATUS_REASON: TypeAdapter[str] = TypeAdapter(StatusReason)


class Place(BaseModel):
    """A named location in the administrative hierarchy (glossary: Place).

    Invariants, checked on every construction:

    - at least one name; no two names share ``(text, language, script)``; at most
      one preferred name per language;
    - ``parent_id`` is ``None`` exactly when ``level`` is ``country`` (the level of
      the parent itself is checked by ``PlaceFactory``, which can see the parent);
    - ``merged_into_id`` is set exactly when ``status`` is ``merged``, and
      ``status_reason`` exactly when ``status`` is not ``active``;
    - a place is never its own parent or merge target;
    - ``updated_at`` is never before ``created_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        code: Stable machine code, unique among places, never reused.
        level: Administrative level.
        parent_id: The enclosing place; ``None`` only for a country.
        names: Every name of the place, in insertion order.
        geometry: WGS84 footprint, or ``None`` until a boundary source is loaded.
        centroid: Representative WGS84 point, or ``None``.
        status: ``active``, or the final ``merged`` / ``retired``.
        status_reason: Why the place was merged or retired; ``None`` while active.
        merged_into_id: The place that replaced this one when merged.
        version: Optimistic-concurrency version, 1 at creation, +1 per change.
        created_at: When the place was first recorded, UTC.
        updated_at: When the place last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: PlaceCode
    level: AdminLevel
    parent_id: EntityId | None
    names: tuple[PlaceName, ...] = Field(min_length=1, max_length=PLACE_MAX_NAMES)
    geometry: PlaceGeometry | None = None
    centroid: Coordinates | None = None
    status: PlaceStatus = "active"
    status_reason: StatusReason | None = None
    merged_into_id: EntityId | None = None
    version: PlaceVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("names", mode="after")
    @classmethod
    def _check_names(cls, names: tuple[PlaceName, ...]) -> tuple[PlaceName, ...]:
        duplicates = [
            key for key, count in Counter(n.key for n in names).items() if count > 1
        ]
        if duplicates:
            message = f"duplicate place names (text, language, script): {duplicates}"
            raise ValueError(message)
        preferred = Counter(name.language for name in names if name.is_preferred)
        crowded = sorted(language for language, count in preferred.items() if count > 1)
        if crowded:
            message = f"more than one preferred name for languages {crowded}"
            raise ValueError(message)
        return names

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        if (self.parent_id is None) != (self.level is AdminLevel.COUNTRY):
            message = (
                "parent_id must be None for a country and set for every other level"
            )
            raise ValueError(message)
        if self.id in (self.parent_id, self.merged_into_id):
            message = "a place cannot be its own parent or merge target"
            raise ValueError(message)
        if (self.merged_into_id is not None) != (self.status == "merged"):
            message = "merged_into_id must be set exactly when status is 'merged'"
            raise ValueError(message)
        if (self.status_reason is not None) != (self.status != "active"):
            message = "status_reason must be set exactly when status is not 'active'"
            raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    # ----------------------------------------------------------------------- #
    # Queries                                                                 #
    # ----------------------------------------------------------------------- #

    @property
    def is_active(self) -> bool:
        """Tell whether the place can still change.

        Returns:
            ``True`` while ``status`` is ``active``.
        """
        return self.status == "active"

    def names_in(self, language: str) -> tuple[PlaceName, ...]:
        """Return every name in ``language``, in insertion order.

        Args:
            language: A language code; normalised like ``LanguageCode``.

        Returns:
            The matching names, possibly none.

        Raises:
            pydantic.ValidationError: If ``language`` is not a valid language code.
        """
        normalised = _LANGUAGE_CODE.validate_python(language)
        return tuple(name for name in self.names if name.language == normalised)

    def preferred_name(
        self, language: str, fallback_order: Sequence[str] = ()
    ) -> PlaceName | None:
        """Return the name to display in the first available language.

        For each language in ``(language, *fallback_order)`` the preferred name wins;
        a language with names but no preferred one yields its first-recorded name
        rather than being skipped, because a name in the requested language is more
        useful to a reader than one in a fallback language.

        Args:
            language: The requested language code.
            fallback_order: Further language codes to try, in order.

        Returns:
            The chosen name, or ``None`` if the place has no name in any of the
            requested languages.

        Raises:
            pydantic.ValidationError: If a language code is invalid.
        """
        for candidate in (language, *fallback_order):
            names = self.names_in(candidate)
            chosen = next((name for name in names if name.is_preferred), None)
            if chosen is not None:
                return chosen
            if names:
                return names[0]
        return None

    def is_within(self, box: BoundingBox) -> bool:
        """Tell whether the place lies inside ``box`` (edges included).

        The centroid decides when there is one. Without a centroid, the geometry's
        bounding box must lie entirely inside ``box``. A place with neither has no
        known location and is never within any box.

        Args:
            box: The WGS84 rectangle to test against.

        Returns:
            ``True`` if the place is known to be inside ``box``.
        """
        if self.centroid is not None:
            return box.contains(self.centroid)
        if self.geometry is None:
            return False
        own = self.geometry.bounding_box()
        return box.contains(
            Coordinates(longitude=own.min_longitude, latitude=own.min_latitude)
        ) and box.contains(
            Coordinates(longitude=own.max_longitude, latitude=own.max_latitude)
        )

    def level_is_below(self, other: "Place | AdminLevel") -> bool:
        """Tell whether this place's level is strictly lower than ``other``'s.

        Args:
            other: A place or a level.

        Returns:
            ``True`` if this level is nested under ``other``'s level.
        """
        other_level = other.level if isinstance(other, Place) else other
        return self.level.is_below(other_level)

    # ----------------------------------------------------------------------- #
    # Changes                                                                 #
    # ----------------------------------------------------------------------- #

    def add_name(
        self, name: PlaceName, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Place"]:
        """Add a name.

        If ``name`` is preferred and the place already has a preferred name in the
        same language, the existing one is demoted, so "add this as the preferred
        name" is a single change rather than an error.

        Args:
            name: The name to add.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The new place and ``PlaceNameAdded``, followed by
            ``PlacePreferredNameChanged`` when ``name`` is preferred.

        Raises:
            PlaceRetiredError: If the place is merged or retired.
            DuplicatePlaceNameError: If a name with the same text, language and
                script exists.
            pydantic.ValidationError: If the place would exceed
                ``PLACE_MAX_NAMES`` names or the clock is behind ``created_at``.
        """
        self._require_active()
        if any(existing.key == name.key for existing in self.names):
            message = f"place {self.code!r} already has the name {name.text!r}"
            raise DuplicatePlaceNameError(
                message,
                details={"code": self.code, "language": name.language},
            )
        previous = self._preferred_in(name.language)
        names = self.names
        if name.is_preferred and previous is not None:
            names = _replace(
                names, previous, previous.with_preference(is_preferred=False)
            )
        now = clock.now()
        state = self._evolve(now, names=(*names, name))
        events: list[PlaceEvent] = [state._event(PlaceNameAdded, ids, now, name=name)]
        if name.is_preferred:
            events.append(
                state._event(
                    PlacePreferredNameChanged,
                    ids,
                    now,
                    language=name.language,
                    previous_text=None if previous is None else previous.text,
                    text=name.text,
                    script=name.script,
                )
            )
        return AggregateChange[Place](state=state, events=tuple(events))

    def set_preferred_name(
        self,
        language: str,
        text: str,
        *,
        clock: Clock,
        ids: IdGenerator,
        script: ScriptCode | None = None,
    ) -> AggregateChange["Place"]:
        """Make an existing name the preferred one for its language.

        Args:
            language: Language of the name; normalised like ``LanguageCode``.
            text: Text of the name; normalised like ``PlaceName.text``.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.
            script: Script of the name; needed only when several names share the
                language and text in different scripts.

        Returns:
            The new place and ``PlacePreferredNameChanged``, or the unchanged place
            and no events if the name is already preferred.

        Raises:
            PlaceRetiredError: If the place is merged or retired.
            PlaceNameNotFoundError: If no name matches.
            ValidationError: If several names match and ``script`` does not choose
                one.
            pydantic.ValidationError: If ``language`` or ``text`` is malformed.
        """
        self._require_active()
        normalised_text = _NAME_TEXT.validate_python(text)
        matches = [
            name
            for name in self.names_in(language)
            if name.text == normalised_text
            and (script is None or name.script == script)
        ]
        if not matches:
            message = f"place {self.code!r} has no such name in {language!r}"
            raise PlaceNameNotFoundError(
                message, details={"code": self.code, "language": language}
            )
        if len(matches) > 1:
            message = "several names match; pass script to choose one"
            raise ValidationError(message, details={"code": self.code})
        target = matches[0]
        if target.is_preferred:
            return AggregateChange[Place](state=self)
        previous = self._preferred_in(target.language)
        names = _replace(self.names, target, target.with_preference(is_preferred=True))
        if previous is not None:
            names = _replace(
                names, previous, previous.with_preference(is_preferred=False)
            )
        now = clock.now()
        state = self._evolve(now, names=names)
        event = state._event(
            PlacePreferredNameChanged,
            ids,
            now,
            language=target.language,
            previous_text=None if previous is None else previous.text,
            text=target.text,
            script=target.script,
        )
        return AggregateChange[Place](state=state, events=(event,))

    def set_geometry(
        self, geometry: PlaceGeometry | None, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Place"]:
        """Set, replace or remove the geometry.

        Args:
            geometry: The new WGS84 footprint, or ``None`` to remove it.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The new place and ``PlaceGeometryChanged``, or the unchanged place and no
            events if the geometry is the same.

        Raises:
            PlaceRetiredError: If the place is merged or retired.
        """
        self._require_active()
        if geometry == self.geometry:
            return AggregateChange[Place](state=self)
        now = clock.now()
        state = self._evolve(now, geometry=geometry)
        event = state._event(
            PlaceGeometryChanged,
            ids,
            now,
            previous_geometry_type=(
                None if self.geometry is None else self.geometry.geometry_type
            ),
            geometry_type=None if geometry is None else geometry.geometry_type,
            bounding_box=None if geometry is None else geometry.bounding_box(),
        )
        return AggregateChange[Place](state=state, events=(event,))

    def set_centroid(
        self, centroid: Coordinates | None, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Place"]:
        """Set, replace or remove the representative point.

        Args:
            centroid: The new WGS84 point, or ``None`` to remove it.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The new place and ``PlaceCentroidChanged``, or the unchanged place and no
            events if the centroid is the same.

        Raises:
            PlaceRetiredError: If the place is merged or retired.
        """
        self._require_active()
        if centroid == self.centroid:
            return AggregateChange[Place](state=self)
        now = clock.now()
        state = self._evolve(now, centroid=centroid)
        event = state._event(
            PlaceCentroidChanged,
            ids,
            now,
            previous_centroid=self.centroid,
            centroid=centroid,
        )
        return AggregateChange[Place](state=state, events=(event,))

    def retire(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Place"]:
        """Retire the place: it no longer exists as an administrative unit.

        The place is kept, never deleted, so every record that refers to it stays
        resolvable.

        Args:
            reason: Why, 1 to 500 characters after stripping.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The retired place and ``PlaceRetired``.

        Raises:
            PlaceRetiredError: If the place is already merged or retired.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        self._require_active()
        normalised_reason = _STATUS_REASON.validate_python(reason)
        now = clock.now()
        state = self._evolve(now, status="retired", status_reason=normalised_reason)
        event = state._event(PlaceRetired, ids, now, reason=normalised_reason)
        return AggregateChange[Place](state=state, events=(event,))

    def merge_into(
        self, target_id: EntityId, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Place"]:
        """Mark the place as merged into ``target_id``, which replaces it.

        Only this side of the merge is recorded here; checking that the target exists
        and is active needs a repository and belongs to the command handler.

        Args:
            target_id: The replacing place.
            reason: Why, 1 to 500 characters after stripping.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The merged place and ``PlaceMerged``.

        Raises:
            PlaceRetiredError: If the place is already merged or retired.
            InvariantViolationError: If ``target_id`` is the place's own id.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        self._require_active()
        if target_id == self.id:
            message = f"place {self.code!r} cannot be merged into itself"
            raise InvariantViolationError(message, details={"code": self.code})
        normalised_reason = _STATUS_REASON.validate_python(reason)
        now = clock.now()
        state = self._evolve(
            now,
            status="merged",
            status_reason=normalised_reason,
            merged_into_id=target_id,
        )
        event = state._event(
            PlaceMerged,
            ids,
            now,
            target_id=target_id,
            reason=normalised_reason,
        )
        return AggregateChange[Place](state=state, events=(event,))

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _require_active(self) -> None:
        if not self.is_active:
            message = f"place {self.code!r} is {self.status} and cannot change"
            raise PlaceRetiredError(
                message, details={"code": self.code, "status": self.status}
            )

    def _preferred_in(self, language: str) -> PlaceName | None:
        return next(
            (
                name
                for name in self.names
                if name.language == language and name.is_preferred
            ),
            None,
        )

    def _evolve(self, now: datetime, **updates: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant.
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return self.model_validate(
            {**fields, **updates, "version": self.version + 1, "updated_at": now}
        )

    def _event[EventT: PlaceEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "place_code": self.code,
                "version": self.version,
                **fields,
            }
        )


def _replace(
    names: tuple[PlaceName, ...], old: PlaceName, new: PlaceName
) -> tuple[PlaceName, ...]:
    return tuple(new if name is old else name for name in names)
