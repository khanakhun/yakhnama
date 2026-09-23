---
name: add-command
description: Add a write use case to a module - an imperative Pydantic command, a handler with __call__ behind a unit of work, a deny-by-default Policy check, a domain event, port and fake updates, and unit tests that assert committed state and events.
---

# add-command

## When to use

- Anything that changes state: create, correct, retire, verify, merge, attach.
- Not for reads: use `add-query`.

## Preconditions

- The module exists (`new-module`) and the entity the command changes exists
  (`add-entity`), including the method that performs the change and returns a new
  instance.
- The authorisation rule is known: which actors may run this command. The identity
  facade `yakhnama.modules.identity.public` exports `Actor` and a `Policy` Protocol with
  `is_satisfied_by(actor) -> bool` (check the real names). The concrete policy is chosen
  in the composition root; the handler only receives it.
- `yakhnama.shared_kernel.clock.Clock` (`now() -> datetime`, UTC) exists, and the UoW
  Protocol has `record_event(event)` so events reach the transactional outbox in the
  same transaction.
- These names are this skill's assumptions: where the real shared kernel or identity
  facade names differ (`Clock`, `record_event`, `Policy.is_satisfied_by`), the real names
  win and this skill must be corrected.
- Shared fakes exist or are added with test-engineer: `tests/fakes/identity.py`
  (`FixedPolicy`, `allow_all`, `deny_all`), `tests/fakes/clock.py` (`FixedClock`),
  `tests/factories/identity.py` (`ActorFactory`).

## Owning subagent

`application-engineer` (commands, handlers, ports, fakes, unit tests). `domain-modeler`
adds the domain event and any new error. `test-engineer` owns the shared fakes and
factories.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/<m>/application/commands.py` | modify | Imperative, frozen command model |
| `src/yakhnama/modules/<m>/application/handlers.py` | modify | `<Command>Handler` with `__call__` |
| `src/yakhnama/modules/<m>/application/ports.py` | modify | Only if the handler needs a new repository method |
| `src/yakhnama/modules/<m>/domain/events.py` | create/modify | Past-tense domain event |
| `src/yakhnama/modules/<m>/domain/errors.py` | modify | `...DeniedError(PermissionDeniedError)` and any new error |
| `src/yakhnama/modules/<m>/public.py` | modify | Export the command if another module sends it |
| `tests/fakes/<m>.py` | modify | Fake methods for any new port method |
| `tests/fakes/identity.py`, `tests/fakes/clock.py` | create if missing | Shared fakes (test-engineer) |
| `tests/factories/<m>.py`, `tests/factories/identity.py` | create/modify | Factories |
| `tests/unit/modules/<m>/application/test_handlers.py` | modify | Handler tests |
| `docs/data-dictionary/<m>.md` | modify | The event's fields, if new |

## Steps

1. Name the command in the imperative (`RetireHazardType`), the event in the past tense
   (`HazardTypeRetired`), the handler `<Command>Handler`.
2. Write the command (`Implements: Command`): `ConfigDict(frozen=True, extra="forbid")`,
   bounded fields, no actor inside (the actor comes from authentication and is a separate argument).
3. Write the event in `domain/events.py`, deriving from `DomainEvent`
   (`Implements: Domain Events`); errors declare `Implements: Domain Error`. Carry ids and
   values, never personal data.
4. Write the handler. Order inside `__call__` is fixed:
   1. check the Policy and raise the module's `...DeniedError` (deny by default, before
      any read);
   2. open the unit of work;
   3. load, raise `...NotFoundError` if missing;
   4. call the entity method (it enforces invariants and returns a new instance);
   5. `save` it;
   6. `uow.record_event(...)` with `occurred_at=self._clock.now()`;
   7. `await uow.commit()`.
5. Update ports and fakes only if a new repository method is needed.
6. Write the tests below: every branch of the handler, asserting committed state,
   recorded events and that nothing was committed on failure.
7. Wire the handler in `platform/container.py` (architect) with the real policy; report
   that as an open question if you do not own the file.

## Templates

### `src/yakhnama/modules/hazards/application/commands.py`
```python
"""Commands accepted by the hazards write side.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.domain.entities import RetirementReason
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef


class RetireHazardType(BaseModel):
    """Ask to retire a hazard type so it can no longer classify new events.

    Implements: Command.

    Attributes:
        ref: The hazard type to retire.
        reason: Why it is retired; kept for the audit trail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementReason
```

### `src/yakhnama/modules/hazards/domain/events.py`
```python
"""Domain events raised by the hazards bounded context.

Patterns: Domain Events.
"""

from yakhnama.modules.hazards.domain.entities import RetirementReason
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.events import DomainEvent


class HazardTypeRetired(DomainEvent):
    """A hazard type stopped accepting new classifications.

    Implements: Domain Events.

    Attributes:
        ref: The retired hazard type.
        reason: Why it was retired.
    """

    ref: HazardTypeRef
    reason: RetirementReason
```

### `src/yakhnama/modules/hazards/domain/errors.py`
```python
"""Errors raised by the hazards domain and application layers.

Patterns: Domain Error (rooted at ``YakhnamaError``).
"""

from uuid import UUID

from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)


class HazardTypeNotFoundError(NotFoundError):
    """No hazard type matches the given reference.

    Implements: Domain Error.

    Attributes:
        ref: The reference that did not resolve.
    """

    def __init__(self, ref: HazardTypeRef) -> None:
        """Create the error.

        Args:
            ref: The reference that did not resolve.
        """
        super().__init__(f"hazard type {ref.code!r} does not exist")
        self.ref = ref


class HazardTypeAlreadyRetiredError(InvalidTransitionError):
    """A retired hazard type cannot be retired again.

    Implements: Domain Error.

    Attributes:
        ref: The hazard type that is already retired.
    """

    def __init__(self, ref: HazardTypeRef) -> None:
        """Create the error.

        Args:
            ref: The hazard type that is already retired.
        """
        super().__init__(f"hazard type {ref.code!r} is already retired")
        self.ref = ref


class HazardTypeRetirementDeniedError(PermissionDeniedError):
    """The actor may not retire hazard types.

    Implements: Domain Error.

    Attributes:
        actor_id: The actor that was denied. Never log more than the id.
    """

    def __init__(self, actor_id: UUID) -> None:
        """Create the error.

        Args:
            actor_id: The actor that was denied.
        """
        super().__init__("actor may not retire hazard types")
        self.actor_id = actor_id
```

### `src/yakhnama/modules/hazards/application/handlers.py`
```python
"""Write-side use cases for the hazards module.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.hazards.application.commands import RetireHazardType
from yakhnama.modules.hazards.application.ports import HazardsUnitOfWorkFactory
from yakhnama.modules.hazards.domain.errors import (
    HazardTypeNotFoundError,
    HazardTypeRetirementDeniedError,
)
from yakhnama.modules.hazards.domain.events import HazardTypeRetired
from yakhnama.modules.identity.public import Actor, Policy
from yakhnama.shared_kernel.clock import Clock


class RetireHazardTypeHandler:
    """Retire a hazard type so no new events can be classified with it.

    Implements: Command Handler.

    Retired types stay resolvable so historical events keep their meaning.
    """

    def __init__(
        self,
        uow_factory: HazardsUnitOfWorkFactory,
        policy: Policy,
        clock: Clock,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a unit of work scoped to the hazards module.
            policy: Decides whether an actor may retire hazard types.
            clock: Source of the event timestamp (UTC).
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock

    async def __call__(self, command: RetireHazardType, actor: Actor) -> None:
        """Retire the hazard type referenced by ``command``.

        Args:
            command: The validated retire command.
            actor: The authenticated caller.

        Raises:
            HazardTypeRetirementDeniedError: If ``policy`` denies ``actor``.
            HazardTypeNotFoundError: If no hazard type matches ``command.ref``.
            HazardTypeAlreadyRetiredError: If the hazard type is already retired.
        """
        # Deny before opening a transaction: nothing may be read or staged for an
        # actor who is not allowed to act.
        if not self._policy.is_satisfied_by(actor):
            raise HazardTypeRetirementDeniedError(actor.id)
        async with self._uow_factory() as uow:
            hazard_type = await uow.hazard_types.get(command.ref)
            if hazard_type is None:
                raise HazardTypeNotFoundError(command.ref)
            await uow.hazard_types.save(hazard_type.retire(reason=command.reason))
            uow.record_event(
                HazardTypeRetired(
                    ref=command.ref,
                    reason=command.reason,
                    occurred_at=self._clock.now(),
                ),
            )
            await uow.commit()
```

### `tests/fakes/identity.py` (test-engineer; create if missing)
```python
"""Fakes for the identity facade used by other modules' tests.

Patterns: Fake.
"""

from yakhnama.modules.identity.public import Actor


class FixedPolicy:
    """``Policy`` with a fixed answer that records every actor it was asked about.

    Implements: Fake.

    Attributes:
        is_allowed: The answer returned for every actor.
        checked: Actors passed to ``is_satisfied_by``, in call order.
    """

    def __init__(self, *, is_allowed: bool) -> None:
        """Create the policy.

        Args:
            is_allowed: The answer returned for every actor.
        """
        self.is_allowed = is_allowed
        self.checked: list[Actor] = []

    def is_satisfied_by(self, actor: Actor) -> bool:
        """Record ``actor`` and return the fixed answer."""
        self.checked.append(actor)
        return self.is_allowed


def allow_all() -> FixedPolicy:
    """Return a policy that allows every actor."""
    return FixedPolicy(is_allowed=True)


def deny_all() -> FixedPolicy:
    """Return a policy that denies every actor."""
    return FixedPolicy(is_allowed=False)
```

### `tests/fakes/clock.py` (test-engineer; create if missing)
```python
"""Deterministic clock for tests.

Patterns: Fake.
"""

from datetime import UTC, datetime


class FixedClock:
    """``Clock`` that always returns the same UTC instant.

    Implements: Fake.

    Attributes:
        instant: The value returned by ``now``.
    """

    def __init__(self, instant: datetime | None = None) -> None:
        """Create the clock.

        Args:
            instant: Timezone-aware instant; defaults to 2025-01-01T00:00:00Z.

        Raises:
            ValueError: If ``instant`` is naive.
        """
        chosen = instant or datetime(2025, 1, 1, tzinfo=UTC)
        if chosen.tzinfo is None:
            message = "FixedClock needs a timezone-aware instant"
            raise ValueError(message)
        self.instant = chosen

    def now(self) -> datetime:
        """Return the fixed instant."""
        return self.instant
```

### `tests/factories/identity.py` (test-engineer; create if missing)
```python
"""Polyfactory factories for identity facade models.

Patterns: Factory.
"""

from polyfactory.factories.pydantic_factory import ModelFactory

from yakhnama.modules.identity.public import Actor


class ActorFactory(ModelFactory[Actor]):
    """Build valid ``Actor`` values.

    Implements: Factory.
    """

    __check_model__ = True
```

### `tests/factories/hazards.py`
```python
"""Polyfactory factories for the hazards domain.

Patterns: Factory.
"""

from polyfactory import Use
from polyfactory.factories.pydantic_factory import ModelFactory

from yakhnama.modules.hazards.domain.entities import HazardType, HazardTypeStatus
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef


class HazardTypeFactory(ModelFactory[HazardType]):
    """Build valid, active ``HazardType`` entities.

    Implements: Factory.
    """

    __check_model__ = True

    ref = Use(lambda: HazardTypeRef(code="glof"))
    parent = None
    status = HazardTypeStatus.ACTIVE
    retired_reason = None
```

The fake unit of work and repository are the ones in `new-module`
(`tests/fakes/hazards.py`): writes are staged until `commit`, events are published only
on `commit`, and `__aexit__` rolls back anything uncommitted.

### `tests/unit/modules/hazards/application/test_handlers.py`
```python
"""Unit tests for the hazards command handlers."""

import pytest

from tests.factories.hazards import HazardTypeFactory
from tests.factories.identity import ActorFactory
from tests.fakes.clock import FixedClock
from tests.fakes.hazards import FakeHazardsUnitOfWorkFactory
from tests.fakes.identity import allow_all, deny_all
from yakhnama.modules.hazards.application.commands import RetireHazardType
from yakhnama.modules.hazards.application.handlers import RetireHazardTypeHandler
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.errors import (
    HazardTypeAlreadyRetiredError,
    HazardTypeNotFoundError,
    HazardTypeRetirementDeniedError,
)
from yakhnama.modules.hazards.domain.events import HazardTypeRetired
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef

GLOF = HazardTypeRef(code="glof")


def make_handler(uow_factory: FakeHazardsUnitOfWorkFactory) -> RetireHazardTypeHandler:
    """Build the handler with an allow-all policy and a fixed clock."""
    return RetireHazardTypeHandler(uow_factory, allow_all(), FixedClock())


async def test_retire_hazard_type_when_active_commits_retired_type() -> None:
    uow_factory = FakeHazardsUnitOfWorkFactory([HazardTypeFactory.build(ref=GLOF)])
    handler = make_handler(uow_factory)
    command = RetireHazardType(ref=GLOF, reason="merged")

    await handler(command, ActorFactory.build())

    stored = uow_factory.hazard_types.committed_rows["glof"]
    assert stored.status is HazardTypeStatus.RETIRED
    assert stored.retired_reason == "merged"
    assert uow_factory.committed is True


async def test_retire_hazard_type_when_active_records_hazard_type_retired() -> None:
    clock = FixedClock()
    uow_factory = FakeHazardsUnitOfWorkFactory([HazardTypeFactory.build(ref=GLOF)])
    handler = RetireHazardTypeHandler(uow_factory, allow_all(), clock)
    command = RetireHazardType(ref=GLOF, reason="merged")

    await handler(command, ActorFactory.build())

    assert uow_factory.events == [
        HazardTypeRetired(ref=GLOF, reason="merged", occurred_at=clock.instant),
    ]


async def test_retire_hazard_type_when_missing_raises_not_found() -> None:
    uow_factory = FakeHazardsUnitOfWorkFactory()
    handler = make_handler(uow_factory)
    command = RetireHazardType(ref=GLOF, reason="merged")

    with pytest.raises(HazardTypeNotFoundError):
        await handler(command, ActorFactory.build())

    assert uow_factory.committed is False


async def test_retire_hazard_type_when_policy_denies_raises_permission_denied() -> None:
    uow_factory = FakeHazardsUnitOfWorkFactory([HazardTypeFactory.build(ref=GLOF)])
    policy = deny_all()
    handler = RetireHazardTypeHandler(uow_factory, policy, FixedClock())
    actor = ActorFactory.build()

    with pytest.raises(HazardTypeRetirementDeniedError):
        await handler(RetireHazardType(ref=GLOF, reason="merged"), actor)

    assert policy.checked == [actor]
    assert uow_factory.committed is False
    assert uow_factory.hazard_types.committed_rows["glof"].is_retired is False


async def test_retire_hazard_type_when_already_retired_raises_invalid_transition() -> (
    None
):
    retired = HazardTypeFactory.build(ref=GLOF).retire(reason="merged")
    uow_factory = FakeHazardsUnitOfWorkFactory([retired])
    handler = make_handler(uow_factory)
    command = RetireHazardType(ref=GLOF, reason="again")

    with pytest.raises(HazardTypeAlreadyRetiredError):
        await handler(command, ActorFactory.build())

    assert uow_factory.committed is False
    assert uow_factory.events == []
```

## Required tests

One per branch of the handler, named `test_<unit>_<scenario>_<expected_outcome>`:

- `test_retire_hazard_type_when_active_commits_retired_type` — committed state.
- `test_retire_hazard_type_when_active_records_hazard_type_retired` — exact event,
  timestamp from the fixed clock.
- `test_retire_hazard_type_when_missing_raises_not_found` — no commit.
- `test_retire_hazard_type_when_policy_denies_raises_permission_denied` — the policy was
  asked about this actor; nothing committed; state unchanged.
- `test_retire_hazard_type_when_already_retired_raises_invalid_transition` — no commit,
  no event.
- A command validation test if the command has constraints beyond its field types.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/hazards/application -q
poetry run poe test-unit
poetry run poe cov
poetry run poe diff-cover
poetry run poe check
```

## Definition of Done

- [ ] Command is imperative, frozen, `extra="forbid"`, bounded; event is past tense.
- [ ] Handler checks the Policy first, uses the UoW, records the event and commits once.
- [ ] Handler docstring lists every error it raises under `Raises:`.
- [ ] Fakes updated; no mocks of our own ports.
- [ ] Tests cover every branch and assert committed state and events; ≥ 95 % coverage
      in `application`.
- [ ] `standards-reviewer` approved; `security-reviewer` approved if the command touches
      authorisation, personal data or media.
- [ ] Conventional Commit, for example `feat(hazards): retire hazard types`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Policy after the read.** Checking permission after loading leaks existence through
  timing and error type. Check first.
- **Naive timestamps.** Events take `occurred_at` from the injected `Clock`, never
  `datetime.now()`.
- **Committing twice or not at all.** One `commit` at the end; an exception inside the
  `async with` must leave nothing committed (the fake proves it).
- **Editing append-only records.** Reports and impact claims are never updated; a
  "correction" command creates a new revision or claim.
- **Returning entities.** A handler returns nothing or an id; reads go through a query
  service.
- **Decorators change types.** If the composition root wraps the handler (idempotency,
  retry), declare a callable alias in `ports.py`, for example
  `RetireHazardTypeUseCase = Callable[[RetireHazardType, Actor], Awaitable[None]]`, and
  depend on the alias in the API.
