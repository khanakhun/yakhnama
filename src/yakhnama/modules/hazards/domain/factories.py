"""Creation of new hazard types.

Patterns: Factory.
"""

from yakhnama.modules.hazards.domain.attributes import (
    DEFAULT_REGISTRY,
    HazardAttributeRegistry,
)
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.modules.hazards.domain.errors import (
    HazardCodeAlreadyUsedError,
    HazardTypeRetiredError,
)
from yakhnama.modules.hazards.domain.events import HazardTypeCreated
from yakhnama.modules.hazards.domain.value_objects import IrdrAlignment
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator
from yakhnama.shared_kernel.value_objects import LocalizedText


class HazardTypeFactory:
    """Creates hazard types that fit the existing taxonomy.

    The factory, not the caller, enforces the rules a new type must meet against the
    rest of the taxonomy, so no code path can skip them: the code has never been used
    (retired codes included), the parent exists and is active, and the attribute
    schema is registered.

    Implements: Factory.
    """

    def __init__(
        self,
        ids: IdGenerator,
        clock: Clock,
        registry: HazardAttributeRegistry = DEFAULT_REGISTRY,
    ) -> None:
        """Create the factory.

        Args:
            ids: Source of the aggregate and event ids.
            clock: Source of ``created_at``, ``updated_at`` and the event time.
            registry: Attribute schemas a new type may name.
        """
        self._ids = ids
        self._clock = clock
        self._registry = registry

    def create(  # noqa: PLR0913  # reason: keyword-only aggregate fields, no hidden coupling
        self,
        *,
        code: str,
        labels: LocalizedText,
        alignment: IrdrAlignment,
        taxonomy: HazardTaxonomy,
        parent_code: str | None = None,
        description: LocalizedText | None = None,
        attributes_schema: str | None = None,
    ) -> AggregateChange[HazardType]:
        """Create an active hazard type at version 1.

        Proposed rule: a new type may not be placed under a retired parent, because a
        retired branch no longer accepts classifications.

        Args:
            code: The new, never used code.
            labels: Display labels; English at least.
            alignment: IRDR placement.
            taxonomy: Every existing hazard type, including retired ones.
            parent_code: The parent's code, or ``None`` for a root.
            description: Optional longer explanation per language.
            attributes_schema: Registry code of the attribute schema, if any.

        Returns:
            The new hazard type and a ``HazardTypeCreated`` event.

        Raises:
            HazardCodeAlreadyUsedError: If ``code`` is taken, even by a retired type.
            HazardTypeNotFoundError: If ``parent_code`` does not exist.
            HazardTypeRetiredError: If the parent is retired.
            UnknownHazardAttributesError: If ``attributes_schema`` is not registered.
            pydantic.ValidationError: If a field is malformed, for example the code.
        """
        if taxonomy.has_code(code):
            raise HazardCodeAlreadyUsedError(code)
        if parent_code is not None and taxonomy.get(parent_code).is_retired:
            raise HazardTypeRetiredError(parent_code, "accept new children")
        if attributes_schema is not None:
            self._registry.schema_for(attributes_schema)
        now = self._clock.now()
        hazard_type = HazardType(
            id=self._ids.new_id(),
            code=code,
            parent_code=parent_code,
            labels=labels,
            description=description,
            alignment=alignment,
            attributes_schema=attributes_schema,
            created_at=now,
            updated_at=now,
        )
        event = HazardTypeCreated(
            event_id=self._ids.new_id(),
            occurred_at=now,
            aggregate_id=hazard_type.id,
            code=hazard_type.code,
            parent_code=hazard_type.parent_code,
            attributes_schema=hazard_type.attributes_schema,
        )
        return AggregateChange[HazardType](state=hazard_type, events=(event,))
