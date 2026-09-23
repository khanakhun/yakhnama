"""The ``DamageRecord`` aggregate: one source's statement that an asset was hit.

Like an impact claim, a damage record is append-only: it is never edited or deleted,
and its only change is one retraction with a reason. A different level from the same
or another source is a new record, so the history shows every account.

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

from yakhnama.modules.impacts.domain.errors import ClaimImmutableError
from yakhnama.modules.impacts.domain.events import DamageRetracted
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimNote,
    ClaimStatus,
    DamageLevel,
    RetractionReason,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import Confidence, DateWithPrecision


class DamageRecord(BaseModel):
    """Damage to one infrastructure asset during one event, from one source.

    Implements: Aggregate Root.

    Attributes:
        id: Identity of the record (UUIDv7).
        event_id: The hazard event that caused the damage.
        asset_id: The damaged infrastructure asset.
        level: How badly it was hit.
        confidence: How far the source can be trusted.
        source_id: The provenance source of the record.
        recorded_at: When the source recorded the damage, with its precision.
        recorded_by: The account that entered the record.
        note: A moderator's note, if any.
        status: ``active`` or ``retracted``.
        retraction_reason: Why it was retracted; set exactly when retracted.
        retracted_by: Who retracted it; set exactly when retracted.
        version: Optimistic-concurrency version, 1 on creation, +1 per change.
        created_at: When the record was entered in Yakhnama, UTC.
        updated_at: When it last changed, UTC, never before ``created_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    event_id: EntityId
    asset_id: EntityId
    level: DamageLevel
    confidence: Confidence
    source_id: EntityId
    recorded_at: DateWithPrecision
    recorded_by: EntityId
    note: ClaimNote | None = None
    status: ClaimStatus = ClaimStatus.ACTIVE
    retraction_reason: RetractionReason | None = None
    retracted_by: EntityId | None = None
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        problems: list[str] = []
        is_retracted = self.status is ClaimStatus.RETRACTED
        if is_retracted != (self.retraction_reason is not None):
            problems.append("a retraction reason is required exactly when retracted")
        if is_retracted != (self.retracted_by is not None):
            problems.append("retracted_by is required exactly when retracted")
        if self.updated_at < self.created_at:
            problems.append("updated_at must not be earlier than created_at")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_active(self) -> bool:
        """Tell whether the record still stands.

        Returns:
            ``True`` unless the record is retracted.
        """
        return self.status is ClaimStatus.ACTIVE

    def retract(
        self,
        reason: str,
        *,
        retracted_by: EntityId,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> "AggregateChange[DamageRecord]":
        """Retract the record; it stays stored and no longer stands.

        Args:
            reason: Why, 1 to 1000 characters of safe text.
            retracted_by: The account retracting it.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The retracted record and one ``DamageRetracted`` event.

        Raises:
            ClaimImmutableError: If the record is already retracted.
            pydantic.ValidationError: If ``reason`` is empty, too long or unsafe.
        """
        if not self.is_active:
            message = "cannot retract a retracted damage record"
            raise ClaimImmutableError(message, details={"damage_id": str(self.id)})
        now = clock.now()
        data = self.model_dump()
        # Re-validate rather than model_copy(update=...): model_copy skips validators.
        data.update(
            status=ClaimStatus.RETRACTED,
            retraction_reason=reason,
            retracted_by=retracted_by,
            version=self.version + 1,
            updated_at=now,
        )
        state = self.model_validate(data)
        event = DamageRetracted(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            hazard_event_id=self.event_id,
            asset_id=self.asset_id,
            retracted_by=retracted_by,
        )
        return AggregateChange[DamageRecord](state=state, events=(event,))
