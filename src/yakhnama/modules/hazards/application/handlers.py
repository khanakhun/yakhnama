"""Write-side use cases of the hazards module.

Every handler asks its ``AuthorisationPolicy`` first and raises
``PermissionDeniedError`` before opening a unit of work, so nothing is read or staged
for a refused actor. Each change goes through ``HazardTaxonomy.with_hazard_type`` before
it is staged, because rules spanning several types (existing parents and replacements,
no cycles, unique codes) live in the taxonomy, not in the single aggregate.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from collections.abc import Iterable

from yakhnama.modules.hazards.application.authorisation import (
    AuthorisationPolicy,
    require_allowed,
)
from yakhnama.modules.hazards.application.commands import (
    LoadReferenceHazardTypes,
    ReactivateHazardType,
    RetireHazardType,
)
from yakhnama.modules.hazards.application.dto import LoadReport, SkippedChange
from yakhnama.modules.hazards.application.ports import (
    HazardsUnitOfWork,
    HazardsUnitOfWorkFactory,
)
from yakhnama.modules.hazards.domain.attributes import (
    DEFAULT_REGISTRY,
    HazardAttributeRegistry,
)
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.modules.hazards.domain.factories import HazardTypeFactory
from yakhnama.modules.hazards.domain.reference import HazardTypeReferenceEntry
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator


class RetireHazardTypeHandler:
    """Retire a hazard type; its code stays reserved and resolvable.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: HazardsUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a hazards unit of work per call.
            policy: Decides whether the actor may retire hazard types.
            clock: Source of ``updated_at`` and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RetireHazardType) -> None:
        """Retire the hazard type named by ``command``.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            HazardTypeNotFoundError: If the code does not exist.
            HazardTypeRetiredError: If the type is already retired.
            InvalidTaxonomyError: If ``reason.replaced_by`` is the type itself or
                does not exist.
        """
        require_allowed(self._policy, command.actor, action="retire hazard types")
        async with self._uow_factory() as uow:
            taxonomy = await uow.hazard_types.list_all()
            current = taxonomy.resolve(command.ref)
            change = current.retire(command.reason, clock=self._clock, ids=self._ids)
            taxonomy.with_hazard_type(change.state)
            await uow.hazard_types.save(change.record_into(uow))
            await uow.commit()


class ReactivateHazardTypeHandler:
    """Make a retired hazard type active again.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: HazardsUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a hazards unit of work per call.
            policy: Decides whether the actor may reactivate hazard types.
            clock: Source of ``updated_at`` and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: ReactivateHazardType) -> None:
        """Reactivate the hazard type named by ``command``.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            HazardTypeNotFoundError: If the code does not exist.
            HazardTypeNotRetiredError: If the type is active.
        """
        require_allowed(self._policy, command.actor, action="reactivate hazard types")
        async with self._uow_factory() as uow:
            taxonomy = await uow.hazard_types.list_all()
            current = taxonomy.resolve(command.ref)
            change = current.reactivate(
                command.reason, clock=self._clock, ids=self._ids
            )
            taxonomy.with_hazard_type(change.state)
            await uow.hazard_types.save(change.record_into(uow))
            await uow.commit()


class _LoadRun:
    """Mutable bookkeeping of one load: the evolving taxonomy and the outcome per code.

    Implements: Command Handler (internal state of ``LoadReferenceHazardTypesHandler``).
    """

    def __init__(self, uow: HazardsUnitOfWork, taxonomy: HazardTaxonomy) -> None:
        self.uow = uow
        self.taxonomy = taxonomy
        self.created: list[str] = []
        self.updated: list[str] = []
        self.skipped: list[SkippedChange] = []

    def apply(self, change: AggregateChange[HazardType], *, is_new: bool) -> None:
        # Validate against the whole tree before recording, so an inconsistent
        # change raises before any of its events reach the unit of work.
        if not change.events:
            return
        self.taxonomy = self.taxonomy.with_hazard_type(change.state)
        change.record_into(self.uow)
        code = change.state.code
        if is_new:
            self.created.append(code)
        # A code created in this run is added once in its final state, so a later
        # change in the same run (a retirement) does not make it "updated".
        elif code not in self.created and code not in self.updated:
            self.updated.append(code)

    def skip(self, code: str, reason: str) -> None:
        self.skipped.append(SkippedChange(code=code, reason=reason))


class LoadReferenceHazardTypesHandler:
    """Load the hazard taxonomy reference file idempotently.

    New codes are created; existing codes get only the changes the aggregate allows
    in place (labels, retirement). Every other difference is reported in
    ``skipped_with_reason`` and left for a human, because it would change what
    stored events mean. Nothing is ever deleted, and a stored retirement is never
    undone by the file. Loading the same file twice changes nothing the second time.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: HazardsUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
        registry: HazardAttributeRegistry = DEFAULT_REGISTRY,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a hazards unit of work per call.
            policy: Decides whether the actor may load reference data.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
            registry: Attribute schemas new hazard types may name.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids
        self._factory = HazardTypeFactory(ids, clock, registry)

    async def __call__(self, command: LoadReferenceHazardTypes) -> LoadReport:
        """Create or update every hazard type of the file.

        Args:
            command: The validated command with the parsed file.

        Returns:
            What was created, updated, unchanged or skipped.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            HazardTypeRetiredError: If a new entry sits under a stored retired type.
            InvalidTaxonomyError: If the file and the stored taxonomy together would
                not form a consistent tree.
        """
        require_allowed(
            self._policy, command.actor, action="load hazard type reference data"
        )
        entries = _parents_first(command.file.entries)
        async with self._uow_factory() as uow:
            run = _LoadRun(uow, await uow.hazard_types.list_all())
            for entry in entries:
                self._create_or_update(run, entry)
            # Retirements come after every entry exists, so a replacement code
            # defined later in the file is already in the taxonomy.
            for entry in entries:
                self._retire_if_needed(run, entry)
            for code in run.created:
                await uow.hazard_types.add(run.taxonomy.get(code))
            for code in run.updated:
                await uow.hazard_types.save(run.taxonomy.get(code))
            if not command.dry_run:
                await uow.commit()
        changed = {*run.created, *run.updated}
        return LoadReport(
            data_version=command.file.data_version,
            dry_run=command.dry_run,
            created=tuple(run.created),
            updated=tuple(run.updated),
            unchanged=tuple(
                entry.code
                for entry in command.file.entries
                if entry.code not in changed
            ),
            skipped_with_reason=tuple(run.skipped),
        )

    def _create_or_update(self, run: _LoadRun, entry: HazardTypeReferenceEntry) -> None:
        if not run.taxonomy.has_code(entry.code):
            change = self._factory.create(
                code=entry.code,
                labels=entry.localized_labels(),
                alignment=entry.alignment,
                taxonomy=run.taxonomy,
                parent_code=entry.parent,
                description=entry.localized_description(),
                attributes_schema=entry.attributes_schema,
            )
            run.apply(change, is_new=True)
            return
        current = run.taxonomy.get(entry.code)
        for reason in _unsupported_differences(current, entry):
            run.skip(entry.code, reason)
        run.apply(
            current.relabel(entry.localized_labels(), clock=self._clock, ids=self._ids),
            is_new=False,
        )

    def _retire_if_needed(self, run: _LoadRun, entry: HazardTypeReferenceEntry) -> None:
        current = run.taxonomy.get(entry.code)
        if entry.retirement is None or current.is_retired:
            return
        change = current.retire(entry.retirement, clock=self._clock, ids=self._ids)
        run.apply(change, is_new=False)


def _unsupported_differences(
    current: HazardType, entry: HazardTypeReferenceEntry
) -> list[str]:
    reasons: list[str] = []
    if current.parent_code != entry.parent:
        reasons.append(
            f"parent differs (stored {current.parent_code!r}, file {entry.parent!r}); "
            "moving a type is left to a moderator"
        )
    if current.alignment != entry.alignment:
        reasons.append("IRDR alignment differs; it is not changed in place")
    if current.attributes_schema != entry.attributes_schema:
        reasons.append("attributes schema differs; it is not changed in place")
    if current.description != entry.localized_description():
        reasons.append("description differs; hazard types have no description change")
    is_file_retired = entry.status is HazardTypeStatus.RETIRED
    if current.is_retired and not is_file_retired:
        reasons.append(
            "stored type is retired but the file says active; the loader never "
            "reactivates, use ReactivateHazardType"
        )
    if (
        current.is_retired
        and is_file_retired
        and current.retirement != entry.retirement
    ):
        reasons.append("stored retirement differs from the file; it is not rewritten")
    return reasons


def _parents_first(
    entries: Iterable[HazardTypeReferenceEntry],
) -> tuple[HazardTypeReferenceEntry, ...]:
    # The file model guarantees every parent is in the file and there is no cycle,
    # so each depth is finite; a stable sort keeps file order within a depth.
    collected = tuple(entries)
    parents = {entry.code: entry.parent for entry in collected}

    def depth(code: str) -> int:
        levels = 0
        parent = parents[code]
        while parent is not None:
            levels += 1
            parent = parents[parent]
        return levels

    return tuple(sorted(collected, key=lambda entry: depth(entry.code)))
