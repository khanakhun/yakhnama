"""The ``HazardType`` aggregate and the ``HazardTaxonomy`` it lives in.

A hazard type is a node of the IRDR-aligned taxonomy with a stable code. Codes are
immutable and never deleted: a type that should no longer be used is retired (with a
reason and optionally a replacement) and stays resolvable, so historical events keep
their meaning. Aggregates are frozen; every state change returns an ``AggregateChange``
with the new instance and the events it produced, and bumps ``version``.

Rules that span several hazard types (unique codes, existing parents, no cycles) live in
``HazardTaxonomy``; the application layer passes a changed type through
``HazardTaxonomy.with_hazard_type`` before saving it.

Patterns: Aggregate Root, Value Object.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from yakhnama.modules.hazards.domain.errors import (
    HazardCodeAlreadyUsedError,
    HazardTypeNotFoundError,
    HazardTypeNotRetiredError,
    HazardTypeRetiredError,
    InvalidTaxonomyError,
)
from yakhnama.modules.hazards.domain.events import (
    HazardTypeReactivated,
    HazardTypeRelabelled,
    HazardTypeReparented,
    HazardTypeRetired,
)
from yakhnama.modules.hazards.domain.value_objects import (
    HazardCode,
    HazardTypeRef,
    HazardTypeStatus,
    IrdrAlignment,
    RetirementReason,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import LocalizedText


class HazardType(BaseModel):
    """A node in the hazard taxonomy, identified by its stable code.

    Implements: Aggregate Root.

    Attributes:
        id: Surrogate identifier (UUIDv7).
        code: Stable machine code; immutable and never reused.
        parent_code: Code of the broader type, or ``None`` for a root.
        labels: Display labels per language; English at least (open question Q2).
        description: Optional longer explanation per language.
        alignment: Placement in the IRDR 2014 peril classification.
        attributes_schema: Registry code of the attribute schema events of this type
            use, or ``None`` for abstract parents without attributes.
        status: Whether new events may still be classified with this type.
        retirement: Why and in favour of what it was retired; set exactly when retired.
        version: Starts at 1 and grows by one with every change.
        created_at: When the type was created, UTC.
        updated_at: When the type last changed, UTC; never before ``created_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: HazardCode
    parent_code: HazardCode | None = None
    labels: LocalizedText
    description: LocalizedText | None = None
    alignment: IrdrAlignment
    attributes_schema: HazardCode | None = None
    status: HazardTypeStatus = HazardTypeStatus.ACTIVE
    retirement: RetirementReason | None = None
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.parent_code == self.code:
            message = f"hazard type {self.code!r} cannot be its own parent"
            raise ValueError(message)
        if self.is_retired != (self.retirement is not None):
            message = "retirement is required exactly when the status is retired"
            raise ValueError(message)
        if self.retirement is not None and self.retirement.replaced_by == self.code:
            message = f"hazard type {self.code!r} cannot be replaced by itself"
            raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    @property
    def ref(self) -> HazardTypeRef:
        """Return the reference other records use to point at this type."""
        return HazardTypeRef(code=self.code)

    @property
    def is_retired(self) -> bool:
        """Return ``True`` once the type no longer accepts new classifications."""
        return self.status is HazardTypeStatus.RETIRED

    def retire(
        self, reason: RetirementReason, *, clock: Clock, ids: IdGenerator
    ) -> "AggregateChange[HazardType]":
        """Retire the type; its code stays reserved and resolvable forever.

        Args:
            reason: Why it is retired and, optionally, which code replaces it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The retired type and a ``HazardTypeRetired`` event.

        Raises:
            HazardTypeRetiredError: If the type is already retired.
            InvalidTaxonomyError: If ``reason.replaced_by`` is this type's own code.
        """
        if self.is_retired:
            raise HazardTypeRetiredError(self.code, "retire")
        if reason.replaced_by == self.code:
            message = f"hazard type {self.code!r} cannot be replaced by itself"
            raise InvalidTaxonomyError(message, details={"hazard_code": self.code})
        now = clock.now()
        state = self._changed(now, status=HazardTypeStatus.RETIRED, retirement=reason)
        event = HazardTypeRetired(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            reason=reason.text,
            replaced_by=reason.replaced_by,
        )
        return _change(state, event)

    def reactivate(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> "AggregateChange[HazardType]":
        """Make a retired type active again, for example after a mistaken retirement.

        Proposed rule (not yet confirmed by the maintainer): reactivation is allowed
        because the code was never reused, and it needs a reason for the audit trail.

        Args:
            reason: Why the type is reactivated; 1 to 500 characters.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The active type, without a retirement, and a ``HazardTypeReactivated``
            event.

        Raises:
            HazardTypeNotRetiredError: If the type is active.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        if not self.is_retired:
            raise HazardTypeNotRetiredError(self.code)
        now = clock.now()
        state = self._changed(now, status=HazardTypeStatus.ACTIVE, retirement=None)
        event = HazardTypeReactivated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            reason=reason,
        )
        return _change(state, event)

    def relabel(
        self, labels: LocalizedText, *, clock: Clock, ids: IdGenerator
    ) -> "AggregateChange[HazardType]":
        """Replace the display labels; allowed for retired types too.

        Labels are presentation only, so correcting a retired type's label keeps
        historical events readable without changing what they mean.

        Args:
            labels: The new labels, in full.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The relabelled type and a ``HazardTypeRelabelled`` event, or the unchanged
            type and no event when ``labels`` equal the current labels.
        """
        if labels == self.labels:
            return _change(self)
        now = clock.now()
        state = self._changed(now, labels=labels)
        event = HazardTypeRelabelled(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            labels=labels,
        )
        return _change(state, event)

    def reparent(
        self, parent_code: str | None, *, clock: Clock, ids: IdGenerator
    ) -> "AggregateChange[HazardType]":
        """Move the type under another parent, or make it a root.

        Only the self-parent case is checked here; that the parent exists and that no
        cycle appears is checked by ``HazardTaxonomy.with_hazard_type``.

        Args:
            parent_code: The new parent's code, or ``None`` for a root.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The moved type and a ``HazardTypeReparented`` event, or the unchanged type
            and no event when the parent does not change.

        Raises:
            HazardTypeRetiredError: If the type is retired.
            InvalidTaxonomyError: If ``parent_code`` is this type's own code.
            pydantic.ValidationError: If ``parent_code`` is not a valid hazard code.
        """
        if self.is_retired:
            raise HazardTypeRetiredError(self.code, "reparent")
        if parent_code == self.parent_code:
            return _change(self)
        if parent_code == self.code:
            message = f"hazard type {self.code!r} cannot be its own parent"
            raise InvalidTaxonomyError(message, details={"hazard_code": self.code})
        now = clock.now()
        state = self._changed(now, parent_code=parent_code)
        event = HazardTypeReparented(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            previous_parent_code=self.parent_code,
            parent_code=parent_code,
        )
        return _change(state, event)

    def _changed(self, now: datetime, **changes: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation, and the new
        # state must satisfy every invariant checked on construction.
        return self.model_validate(
            {
                **dict(self),
                **changes,
                "version": self.version + 1,
                "updated_at": now,
            }
        )


def _change(state: HazardType, *events: DomainEvent) -> AggregateChange[HazardType]:
    return AggregateChange[HazardType](state=state, events=events)


def find_parent_cycle(parents: Mapping[str, str | None]) -> tuple[str, ...] | None:
    """Return one cycle in a child-to-parent mapping, if there is any.

    Parents missing from the mapping end a walk; detecting them is the caller's job.

    Args:
        parents: Parent code (or ``None``) per code.

    Returns:
        The codes forming a cycle, in child-to-parent order starting with the
        alphabetically first code walked into it, or ``None`` if the mapping is a
        forest.
    """
    settled: set[str] = set()
    for start in sorted(parents):
        path: list[str] = []
        on_path: set[str] = set()
        node: str | None = start
        while node is not None and node not in settled:
            if node in on_path:
                return tuple(path[path.index(node) :])
            path.append(node)
            on_path.add(node)
            node = parents.get(node)
        settled.update(path)
    return None


class HazardTaxonomy(BaseModel):
    """Every hazard type ever defined, active or retired, as one consistent tree.

    Construction checks that codes and ids are unique, that every parent and every
    replacement code exists, and that the parent links contain no cycle; a violation
    raises ``InvalidTaxonomyError`` directly (not a Pydantic error) because the
    taxonomy is assembled by code from stored aggregates, not parsed from user input.

    Implements: Value Object.

    Attributes:
        hazard_types: The hazard types, sorted by code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hazard_types: tuple[HazardType, ...] = ()

    _by_code: dict[str, HazardType] = PrivateAttr(default_factory=dict)

    @field_validator("hazard_types", mode="after")
    @classmethod
    def _sort_by_code(
        cls, hazard_types: tuple[HazardType, ...]
    ) -> tuple[HazardType, ...]:
        # A canonical order makes two taxonomies with the same members equal.
        return tuple(sorted(hazard_types, key=lambda hazard_type: hazard_type.code))

    @model_validator(mode="after")
    def _check_tree(self) -> Self:
        by_code = _index_unique(self.hazard_types)
        dangling = sorted(
            f"{hazard_type.code} -> {reference}"
            for hazard_type in self.hazard_types
            for reference in _references(hazard_type)
            if reference not in by_code
        )
        if dangling:
            message = f"hazard types refer to unknown codes: {dangling}"
            raise InvalidTaxonomyError(message, details={"references": dangling})
        cycle = find_parent_cycle(
            {code: hazard_type.parent_code for code, hazard_type in by_code.items()}
        )
        if cycle is not None:
            message = f"hazard type parents form a cycle: {list(cycle)}"
            raise InvalidTaxonomyError(message, details={"cycle": list(cycle)})
        self._by_code = by_code
        return self

    @classmethod
    def of(cls, hazard_types: Iterable[HazardType]) -> Self:
        """Build a taxonomy from any iterable of hazard types.

        Args:
            hazard_types: The hazard types, in any order.

        Returns:
            The validated taxonomy.

        Raises:
            InvalidTaxonomyError: If the types do not form a consistent tree.
        """
        return cls(hazard_types=tuple(hazard_types))

    def has_code(self, code: str) -> bool:
        """Tell whether ``code`` belongs to any hazard type, including retired ones.

        Args:
            code: A hazard code.

        Returns:
            ``True`` if the code is taken.
        """
        return code in self._by_code

    def codes(self) -> frozenset[str]:
        """Return every code, active or retired.

        Returns:
            The taken codes.
        """
        return frozenset(self._by_code)

    def get(self, code: str) -> HazardType:
        """Return the hazard type with ``code``, active or retired.

        Args:
            code: A hazard code.

        Returns:
            The hazard type.

        Raises:
            HazardTypeNotFoundError: If no type has that code.
        """
        hazard_type = self._by_code.get(code)
        if hazard_type is None:
            raise HazardTypeNotFoundError(code)
        return hazard_type

    def resolve(self, ref: HazardTypeRef) -> HazardType:
        """Return the hazard type a reference points at, even if it is retired.

        Args:
            ref: The reference stored on an event or report.

        Returns:
            The hazard type.

        Raises:
            HazardTypeNotFoundError: If no type has the referenced code.
        """
        return self.get(ref.code)

    def roots(self) -> tuple[HazardType, ...]:
        """Return the types without a parent, sorted by code.

        Returns:
            The root hazard types.
        """
        return tuple(
            hazard_type
            for hazard_type in self.hazard_types
            if hazard_type.parent_code is None
        )

    def children(self, code: str) -> tuple[HazardType, ...]:
        """Return the direct children of ``code``, sorted by code.

        Args:
            code: The parent's code.

        Returns:
            The child hazard types, possibly empty.

        Raises:
            HazardTypeNotFoundError: If no type has that code.
        """
        self.get(code)
        return tuple(
            hazard_type
            for hazard_type in self.hazard_types
            if hazard_type.parent_code == code
        )

    def ancestors(self, code: str) -> tuple[HazardType, ...]:
        """Return the chain of parents of ``code``, nearest first.

        Args:
            code: The starting type's code.

        Returns:
            The ancestors from the direct parent up to the root; empty for a root.

        Raises:
            HazardTypeNotFoundError: If no type has that code.
        """
        chain: list[HazardType] = []
        parent_code = self.get(code).parent_code
        while parent_code is not None:
            parent = self._by_code[parent_code]
            chain.append(parent)
            parent_code = parent.parent_code
        return tuple(chain)

    def with_hazard_type(self, hazard_type: HazardType) -> Self:
        """Return a taxonomy where ``hazard_type`` is added or replaces its old state.

        Args:
            hazard_type: A new type, or a changed state of an existing one.

        Returns:
            The new taxonomy.

        Raises:
            HazardCodeAlreadyUsedError: If another aggregate (different id) already
                has the code.
            InvalidTaxonomyError: If the result would not be a consistent tree.
        """
        existing = self._by_code.get(hazard_type.code)
        if existing is not None and existing.id != hazard_type.id:
            raise HazardCodeAlreadyUsedError(hazard_type.code)
        others = (
            other for other in self.hazard_types if other.code != hazard_type.code
        )
        return self.of((*others, hazard_type))


def _index_unique(hazard_types: Iterable[HazardType]) -> dict[str, HazardType]:
    by_code: dict[str, HazardType] = {}
    seen_ids: set[object] = set()
    for hazard_type in hazard_types:
        if hazard_type.code in by_code:
            message = f"hazard code {hazard_type.code!r} appears more than once"
            raise InvalidTaxonomyError(
                message, details={"hazard_code": hazard_type.code}
            )
        if hazard_type.id in seen_ids:
            message = f"hazard type id {hazard_type.id} appears more than once"
            raise InvalidTaxonomyError(message, details={"id": str(hazard_type.id)})
        by_code[hazard_type.code] = hazard_type
        seen_ids.add(hazard_type.id)
    return by_code


def _references(hazard_type: HazardType) -> tuple[str, ...]:
    replaced_by = (
        hazard_type.retirement.replaced_by
        if hazard_type.retirement is not None
        else None
    )
    return tuple(
        code for code in (hazard_type.parent_code, replaced_by) if code is not None
    )
