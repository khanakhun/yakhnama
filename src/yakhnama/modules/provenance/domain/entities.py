"""The ``Source`` aggregate: the provenance record every fact links to.

Every model is frozen. A state-changing method validates the new state, bumps
``version`` by one, stamps ``updated_at`` from the injected ``Clock`` and returns an
``AggregateChange`` holding the new instance and its events; the caller's instance is
never modified. A change already in effect (the same details, a second
``mark_referenced``) returns the source unchanged with no events, so a retried command
is harmless.

**Immutable once referenced** (``AGENTS.md`` §7): the first time a claim, event or
report cites a source, ``mark_referenced`` flips ``is_referenced``; from then on every
change is refused with ``SourceImmutableError``. A correction to a referenced source
is a new source, so the provenance of facts that already cite the old one never moves
under them. ``source_type`` is fixed at registration for the same reason: it feeds
the best-figure source ranking (**proposed**, see the data dictionary).

**Citizen sources.** A ``citizen`` source must not carry a URL or citation that
identifies the person (a social-media profile, a phone number, a full name). This is
a documented rule for moderators and importers only: the domain cannot tell a
person's name from a place name, so it does not try (**proposed**, open question in
the data dictionary).

Patterns: Entity, Aggregate Root, Domain Events.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from yakhnama.modules.provenance.domain.errors import SourceImmutableError
from yakhnama.modules.provenance.domain.events import (
    SourceDetailsUpdated,
    SourceReferenced,
)
from yakhnama.modules.provenance.domain.value_objects import (
    Citation,
    Licence,
    Publisher,
    RecordVersion,
    RetrievalTime,
    SourceDetails,
    SourceOwner,
    SourceRef,
    SourceTitle,
    SourceType,
    SourceUrl,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import LanguageCode


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class Source(BaseModel):
    """One provenance record: who or what said something, where and under which terms.

    Invariants, checked on every construction:

    - ``updated_at`` is never before ``created_at``;
    - a referenced source never changes again (enforced by the methods, because a
      frozen model cannot be changed any other way).

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        source_type: The kind of source; fixed at registration.
        title: Short title.
        citation: How to cite the source.
        url: Where it can be found online, if anywhere.
        licence: Reuse terms, if known.
        retrieved_at: When it was retrieved, with its precision, if known.
        publisher: Who published it, if known.
        language: Language of the source's content, if known.
        owner_actor_id: The user who registered it; ``None`` when the system did
            (for example an importer).
        organization_id: The organisation it was registered for, if any.
        is_referenced: ``True`` once any fact cites it; it is then immutable.
        version: Optimistic-concurrency version, 1 at creation, +1 per change.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    source_type: SourceType
    title: SourceTitle
    citation: Citation
    url: SourceUrl | None = None
    licence: Licence | None = None
    retrieved_at: RetrievalTime | None = None
    publisher: Publisher | None = None
    language: LanguageCode | None = None
    owner_actor_id: EntityId | None = None
    organization_id: EntityId | None = None
    is_referenced: bool = False
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    # ----------------------------------------------------------------------- #
    # Queries                                                                 #
    # ----------------------------------------------------------------------- #

    @property
    def details(self) -> SourceDetails:
        """Return the descriptive fields as one value.

        Returns:
            The source's current details.
        """
        return SourceDetails(
            title=self.title,
            citation=self.citation,
            url=self.url,
            licence=self.licence,
            retrieved_at=self.retrieved_at,
            publisher=self.publisher,
            language=self.language,
        )

    @property
    def ref(self) -> SourceRef:
        """Return the reference other modules store to cite this source.

        Returns:
            A ``SourceRef`` to this source.
        """
        return SourceRef(source_id=self.id)

    @property
    def owner(self) -> SourceOwner:
        """Return who registered the source and for which organisation.

        Returns:
            The source's owner.
        """
        return SourceOwner(
            actor_id=self.owner_actor_id, organization_id=self.organization_id
        )

    @property
    def is_mutable(self) -> bool:
        """Tell whether the source's details may still change.

        Returns:
            ``True`` while no fact references the source.
        """
        return not self.is_referenced

    # ----------------------------------------------------------------------- #
    # Changes                                                                 #
    # ----------------------------------------------------------------------- #

    def update_details(
        self, details: SourceDetails, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Source"]:
        """Replace the descriptive details of a source nothing references yet.

        Every detail field is replaced, so a field left ``None`` in ``details`` is
        cleared; an application layer offering partial updates merges with
        ``Source.details`` first.

        Args:
            details: The new details.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The updated source and ``SourceDetailsUpdated``, or the unchanged source
            and no events if ``details`` equals the current details.

        Raises:
            SourceImmutableError: If the source is referenced, even when the
                details are unchanged, so a client learns the rule at once.
        """
        if self.is_referenced:
            raise SourceImmutableError.for_id(self.id)
        changed_fields = details.changed_fields(self.details)
        if not changed_fields:
            return AggregateChange[Source](state=self)
        now = clock.now()
        state = self._evolve(now, **details.as_fields())
        event = SourceDetailsUpdated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
            changed_fields=changed_fields,
        )
        return AggregateChange[Source](state=state, events=(event,))

    def mark_referenced(
        self, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Source"]:
        """Record that a fact now cites the source, freezing it for good.

        Idempotent: marking a source that is already referenced returns it unchanged
        with no events, so every fact that cites the source may call this without
        first checking.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The referenced source and ``SourceReferenced``, or the unchanged source
            and no events if it was already referenced.
        """
        if self.is_referenced:
            return AggregateChange[Source](state=self)
        now = clock.now()
        state = self._evolve(now, is_referenced=True)
        event = SourceReferenced(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
        )
        return AggregateChange[Source](state=state, events=(event,))

    def _evolve(self, now: datetime, **updates: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant. A clock behind created_at is refused by the
        # invariant check instead of producing a record that went back in time.
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return self.model_validate(
            {**fields, **updates, "version": self.version + 1, "updated_at": now}
        )
