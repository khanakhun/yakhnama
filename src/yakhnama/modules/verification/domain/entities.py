"""The ``VerificationCase`` aggregate: one target's path through the §6.3 table.

A case is frozen. ``transition`` and ``assign`` return an ``AggregateChange`` with the
new case and its events and bump ``version``. The whole history is re-checked on every
construction, so a case loaded from storage with an impossible history is rejected
just like an illegal move requested through the API.

Patterns: Aggregate Root, State.
"""

from datetime import UTC, datetime
from typing import Final, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.verification.domain.errors import (
    HumanRequiredError,
    ReasonRequiredError,
)
from yakhnama.modules.verification.domain.events import (
    VerificationAssigned,
    VerificationTransitioned,
)
from yakhnama.modules.verification.domain.state_machine import (
    can_transition,
    is_terminal,
    requires_human,
    requires_reason,
)
from yakhnama.modules.verification.domain.value_objects import (
    Transition,
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import InvalidTransitionError
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.text import normalise_multiline_text

ENTRY_STATES: Final = frozenset({VerificationState.DRAFT, VerificationState.SUBMITTED})
"""States a case may be opened in (see ``factories.initial_state_for``)."""


class VerificationCase(BaseModel):
    """The verification record of one report, event or impact claim.

    Implements: Aggregate Root, State.

    Attributes:
        id: Identifier of the case (UUIDv7).
        target: The record under verification.
        initial_state: The state the case was opened in, ``draft`` or ``submitted``.
        state: The current state; always the end of ``history``.
        history: Every transition since opening, oldest first; append-only.
        assigned_to: The reviewer currently responsible, if any.
        opened_by: Who opened the case.
        version: Starts at 1 and grows by one with every change.
        created_at: When the case was opened, UTC.
        updated_at: When the case last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    target: VerificationTarget
    initial_state: VerificationState
    state: VerificationState
    history: tuple[Transition, ...] = ()
    assigned_to: EntityId | None = None
    opened_by: EntityId
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_history(self) -> Self:
        if self.initial_state not in ENTRY_STATES:
            message = "a case can only be opened in draft or submitted"
            raise ValueError(message)
        current = self.initial_state
        previous_moment = self.created_at
        for step in self.history:
            _check_step(step, current, previous_moment)
            current = step.to_state
            previous_moment = step.occurred_at
        if self.state is not current:
            message = "state must be the to_state of the last transition"
            raise ValueError(message)
        if self.updated_at < previous_moment:
            message = "updated_at must not be earlier than the last change"
            raise ValueError(message)
        return self

    @property
    def is_verified(self) -> bool:
        """Return ``True`` while the target is in ``verified``."""
        return self.state is VerificationState.VERIFIED

    @property
    def is_closed(self) -> bool:
        """Return ``True`` once no transition can leave the current state."""
        return is_terminal(self.state)

    def transition(  # noqa: PLR0913  # reason: keyword-only inputs of one move, per the brief
        self,
        to_state: VerificationState,
        *,
        actor_id: UUID,
        reason: str | None,
        is_human: bool,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["VerificationCase"]:
        """Move the case to ``to_state`` and record the move in ``history``.

        Checks run in this order: the table, the human rule, the reason rule. A
        reason that is blank after normalisation counts as missing.

        Args:
            to_state: The requested state.
            actor_id: Who makes the move.
            reason: Why; required unless ``to_state`` is ``submitted``.
            is_human: Whether a person makes the move.
            clock: Source of the transition time.
            ids: Source of the event id.

        Returns:
            The moved case and a ``VerificationTransitioned`` event.

        Raises:
            InvalidTransitionError: If the table does not allow the move.
            HumanRequiredError: If ``to_state`` is ``verified`` and ``is_human`` is
                ``False``.
            ReasonRequiredError: If a reason is required and missing or blank.
            pydantic.ValidationError: If the reason is too long or contains unsafe
                characters.
        """
        if not can_transition(self.state, to_state):
            message = (
                f"a case cannot move from {self.state.value!r} to {to_state.value!r}"
            )
            raise InvalidTransitionError(
                message,
                details={"from_state": self.state.value, "to_state": to_state.value},
            )
        if requires_human(to_state) and not is_human:
            raise HumanRequiredError(to_state)
        if reason is not None and not normalise_multiline_text(reason):
            reason = None
        if reason is None and requires_reason(to_state):
            raise ReasonRequiredError(to_state)
        now = clock.now()
        step = Transition(
            from_state=self.state,
            to_state=to_state,
            actor_id=actor_id,
            reason=reason,
            occurred_at=now,
            is_human=is_human,
        )
        state = self._changed(now, state=to_state, history=(*self.history, step))
        event = VerificationTransitioned(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            target_kind=self.target.kind,
            target_id=self.target.target_id,
            from_state=step.from_state,
            to_state=step.to_state,
            actor_id=actor_id,
            is_human=is_human,
            has_reason=step.reason is not None,
        )
        return _change(state, event)

    def assign(
        self, reviewer_id: UUID, *, actor_id: UUID, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["VerificationCase"]:
        """Make ``reviewer_id`` responsible for the case.

        Assigning the current reviewer again is a no-op without an event. Whether
        the reviewer holds a moderator role is an authorisation rule checked by the
        application layer, not here.

        Args:
            reviewer_id: The new reviewer.
            actor_id: Who makes the assignment.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The assigned case and a ``VerificationAssigned`` event.

        Raises:
            InvalidTransitionError: If the case is ``rejected`` or ``retracted``;
                a closed case has nothing left to review.
        """
        if self.is_closed:
            message = f"a {self.state.value!r} case cannot be assigned"
            raise InvalidTransitionError(message, details={"state": self.state.value})
        if reviewer_id == self.assigned_to:
            return _change(self)
        now = clock.now()
        state = self._changed(now, assigned_to=reviewer_id)
        event = VerificationAssigned(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            target_kind=self.target.kind,
            target_id=self.target.target_id,
            reviewer_id=reviewer_id,
            previous_reviewer_id=self.assigned_to,
            assigned_by=actor_id,
        )
        return _change(state, event)

    def _changed(self, now: datetime, **changes: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation, and the new
        # state must pass the full history check.
        return self.model_validate(
            {
                **dict(self),
                **changes,
                "version": self.version + 1,
                "updated_at": now,
            }
        )


def _check_step(
    step: Transition, current: VerificationState, previous_moment: datetime
) -> None:
    if step.from_state is not current:
        message = "each transition must start where the previous one ended"
        raise ValueError(message)
    if not can_transition(step.from_state, step.to_state):
        message = (
            f"transition {step.from_state.value!r} -> {step.to_state.value!r} "
            "is not in the table"
        )
        raise ValueError(message)
    if requires_human(step.to_state) and not step.is_human:
        message = f"only a person may move a case to {step.to_state.value!r}"
        raise ValueError(message)
    if requires_reason(step.to_state) and step.reason is None:
        message = f"moving a case to {step.to_state.value!r} requires a reason"
        raise ValueError(message)
    if step.occurred_at < previous_moment:
        message = "transitions must be in chronological order"
        raise ValueError(message)


def _change(
    state: VerificationCase, *events: DomainEvent
) -> AggregateChange[VerificationCase]:
    return AggregateChange[VerificationCase](state=state, events=events)
