"""The ``InfrastructureAsset`` aggregate: a bridge, road segment or other asset.

An asset is registered once and then referenced by damage records and claim scopes,
so a bridge washed away in 2010 and damaged again in 2022 is one asset with two
damage records. Its kind never changes (a different kind is a different asset); its
name and its location may be corrected.

Patterns: Entity, Aggregate Root.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.impacts.domain.events import (
    InfrastructureAssetRelocated,
    InfrastructureAssetRenamed,
)
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    AssetName,
    OsmId,
    PlaceCodeRef,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import Coordinates


class InfrastructureAsset(BaseModel):
    """One piece of infrastructure that hazards can damage.

    Implements: Aggregate Root.

    Attributes:
        id: Identity of the asset (UUIDv7).
        kind: What the asset is; never changes.
        name: Display name, 1 to 200 characters of safe text.
        osm_id: Its OpenStreetMap element, if known.
        location: A representative WGS84 point, if known.
        place_code: The geography place it lies in, if known.
        source_id: The provenance source describing the asset.
        version: Optimistic-concurrency version, 1 on creation, +1 per change.
        created_at: When the asset was registered, UTC.
        updated_at: When it last changed, UTC, never before ``created_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    kind: AssetKind
    name: AssetName
    osm_id: OsmId | None = None
    location: Coordinates | None = None
    place_code: PlaceCodeRef | None = None
    source_id: EntityId
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    def rename(
        self, name: str, *, clock: Clock, id_generator: IdGenerator
    ) -> "AggregateChange[InfrastructureAsset]":
        """Change the name; the same name (after normalisation) is a no-op.

        Args:
            name: The new name, 1 to 200 characters of safe text.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The renamed asset and one ``InfrastructureAssetRenamed`` event, or the
            unchanged asset and no event.

        Raises:
            pydantic.ValidationError: If ``name`` is empty, too long or unsafe.
        """
        now = clock.now()
        state = self._changed(now, name=name)
        if state.name == self.name:
            return AggregateChange[InfrastructureAsset](state=self)
        event = InfrastructureAssetRenamed(
            event_id=id_generator.new_id(), occurred_at=now, aggregate_id=self.id
        )
        return AggregateChange[InfrastructureAsset](state=state, events=(event,))

    def relocate(
        self,
        *,
        location: Coordinates | None,
        place_code: str | None,
        osm_id: str | None,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> "AggregateChange[InfrastructureAsset]":
        """Replace the location, place and OpenStreetMap element together.

        All three are given each time, so clearing a wrong value is explicit; the
        same three values are a no-op.

        Args:
            location: The new point, or ``None`` if unknown.
            place_code: The new place code, or ``None`` if unknown.
            osm_id: The new OpenStreetMap element, or ``None`` if unknown.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The relocated asset and one ``InfrastructureAssetRelocated`` event, or
            the unchanged asset and no event.

        Raises:
            pydantic.ValidationError: If ``place_code`` or ``osm_id`` is malformed.
        """
        now = clock.now()
        state = self._changed(
            now, location=location, place_code=place_code, osm_id=osm_id
        )
        if (state.location, state.place_code, state.osm_id) == (
            self.location,
            self.place_code,
            self.osm_id,
        ):
            return AggregateChange[InfrastructureAsset](state=self)
        event = InfrastructureAssetRelocated(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            location=state.location,
            place_code=state.place_code,
            osm_id=state.osm_id,
        )
        return AggregateChange[InfrastructureAsset](state=state, events=(event,))

    def _changed(self, now: datetime, **changes: object) -> Self:
        # Re-validate rather than model_copy(update=...): model_copy skips validators,
        # and every new state must satisfy the invariants.
        data = self.model_dump()
        data.update(changes, version=self.version + 1, updated_at=now)
        return self.model_validate(data)
