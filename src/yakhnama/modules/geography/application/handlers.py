"""Write-side use cases of the geography module.

Every handler asks its ``AdminOnlyPolicy`` first and raises ``PermissionDeniedError``
before opening a unit of work, so nothing is read or staged for a refused actor.
Places are never deleted: retirement and merging are status changes with a reason.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.geography.application.authorisation import AdminOnlyPolicy
from yakhnama.modules.geography.application.commands import (
    LoadReferencePlaces,
    MergePlace,
    RetirePlace,
)
from yakhnama.modules.geography.application.dto import LoadReport, SkippedChange
from yakhnama.modules.geography.application.ports import (
    GeographyUnitOfWork,
    GeographyUnitOfWorkFactory,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import (
    PlaceNotFoundError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.factories import PlaceDraft, PlaceFactory
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


def _require_allowed(
    policy: AdminOnlyPolicy, actor_id: EntityId | None, action: str
) -> None:
    # The actor id is deliberately left out of the message and details: the error
    # may be logged or returned, and who was refused is the audit log's business.
    if not policy.is_allowed(actor_id):
        message = f"the actor may not {action}"
        raise PermissionDeniedError(message, details={"action": action})


async def _load(uow: GeographyUnitOfWork, place_id: EntityId) -> Place:
    place = await uow.places.get(place_id)
    if place is None:
        raise PlaceNotFoundError.for_id(place_id)
    return place


class RetirePlaceHandler:
    """Retire a place; it stays resolvable for every record that refers to it.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: GeographyUnitOfWorkFactory,
        policy: AdminOnlyPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a geography unit of work per call.
            policy: Decides whether the actor may retire places.
            clock: Source of ``updated_at`` and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RetirePlace) -> None:
        """Retire the place named by ``command``.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
            PlaceNotFoundError: If the place does not exist.
            PlaceRetiredError: If the place is already merged or retired.
        """
        _require_allowed(self._policy, command.actor_id, "retire places")
        async with self._uow_factory() as uow:
            place = await _load(uow, command.place_id)
            change = place.retire(command.reason, clock=self._clock, ids=self._ids)
            await uow.places.save(change.record_into(uow))
            await uow.commit()


class MergePlaceHandler:
    """Merge a place into an active place that replaces it.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: GeographyUnitOfWorkFactory,
        policy: AdminOnlyPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a geography unit of work per call.
            policy: Decides whether the actor may merge places.
            clock: Source of ``updated_at`` and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: MergePlace) -> None:
        """Merge the place named by ``command`` into its target.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
            PlaceNotFoundError: If the place or the target does not exist.
            PlaceRetiredError: If the place or the target is merged or retired.
            InvariantViolationError: If the place would be merged into itself.
        """
        _require_allowed(self._policy, command.actor_id, "merge places")
        async with self._uow_factory() as uow:
            place = await _load(uow, command.place_id)
            target = await _load(uow, command.target_id)
            # A merged or retired target would leave records pointing at a place
            # that itself no longer exists.
            if not target.is_active:
                message = f"target place {target.code!r} is {target.status}"
                raise PlaceRetiredError(
                    message, details={"code": place.code, "target_code": target.code}
                )
            change = place.merge_into(
                target.id, command.reason, clock=self._clock, ids=self._ids
            )
            await uow.places.save(change.record_into(uow))
            await uow.commit()


class LoadReferencePlacesHandler:
    """Load a place reference file idempotently.

    New codes are created, parents first. Existing active places get only additive
    changes: missing names are added, a name the file marks preferred becomes
    preferred, and the file's centroid is set where none is stored. Names and
    centroids the file lacks are kept, because other sources may have added them.
    A different level, parent, name kind or stored centroid, and any change to a
    merged or retired place, is reported in ``skipped_with_reason`` and left for a
    moderator. Nothing is deleted. Loading the same file twice changes nothing the
    second time.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: GeographyUnitOfWorkFactory,
        policy: AdminOnlyPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a geography unit of work per call.
            policy: Decides whether the actor may load reference data.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids
        self._factory = PlaceFactory()

    async def __call__(self, command: LoadReferencePlaces) -> LoadReport:
        """Create or update every place of the file.

        Args:
            command: The validated command with the parsed file.

        Returns:
            What was created, updated, unchanged or skipped.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
            InvalidPlaceHierarchyError: If a new place does not fit under its
                stored parent.
            PlaceRetiredError: If a new place's stored parent is merged or retired.
        """
        _require_allowed(self._policy, command.actor_id, "load place reference data")
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        skipped: list[SkippedChange] = []
        # Places of this run by code, so children find parents staged moments ago.
        known: dict[str, Place] = {}
        async with self._uow_factory() as uow:
            for draft in command.file.to_factory_inputs():
                # The file guarantees every parent is an earlier entry of the same
                # file (``PlaceReferenceFile`` and ``to_factory_inputs``).
                parent = None if draft.parent_code is None else known[draft.parent_code]
                current = await uow.places.get_by_code(draft.code)
                if current is None:
                    state = self._factory.create(
                        draft, parent=parent, ids=self._ids, clock=self._clock
                    ).record_into(uow)
                    await uow.places.add(state)
                    created.append(draft.code)
                else:
                    reasons, state = self._update(uow, current, draft, parent)
                    skipped.extend(
                        SkippedChange(code=draft.code, reason=reason)
                        for reason in reasons
                    )
                    if state is current:
                        unchanged.append(draft.code)
                    else:
                        await uow.places.save(state)
                        updated.append(draft.code)
                known[draft.code] = state
            if not command.dry_run:
                await uow.commit()
        return LoadReport(
            data_version=command.file.data_version,
            dry_run=command.dry_run,
            created=tuple(created),
            updated=tuple(updated),
            unchanged=tuple(unchanged),
            skipped_with_reason=tuple(skipped),
        )

    def _update(
        self,
        uow: GeographyUnitOfWork,
        current: Place,
        draft: PlaceDraft,
        parent: Place | None,
    ) -> tuple[list[str], Place]:
        reasons: list[str] = []
        if current.level is not draft.level:
            reasons.append(
                f"level differs (stored {current.level.value}, file "
                f"{draft.level.value}); it is not changed in place"
            )
        if current.parent_id != (None if parent is None else parent.id):
            reasons.append("parent differs; moving a place is left to a moderator")
        for name in draft.names:
            stored = next(
                (existing for existing in current.names if existing.key == name.key),
                None,
            )
            if stored is not None and stored.kind != name.kind:
                reasons.append(
                    f"name {name.text!r} ({name.language}) has kind {stored.kind!r}, "
                    f"file says {name.kind!r}; kinds are not changed in place"
                )
        # A stored centroid may come from a better source than the reference file,
        # so the file only fills a missing one.
        if (
            draft.centroid is not None
            and current.centroid is not None
            and draft.centroid != current.centroid
        ):
            reasons.append("centroid differs; it is not changed in place")
        if not current.is_active:
            if _has_additions(current, draft):
                reasons.append(
                    f"place is {current.status}; the file's additions are not applied"
                )
            return reasons, current
        return reasons, self._apply_additions(uow, current, draft)

    def _apply_additions(
        self, uow: GeographyUnitOfWork, current: Place, draft: PlaceDraft
    ) -> Place:
        state = current
        for name in draft.names:
            stored = next(
                (existing for existing in state.names if existing.key == name.key),
                None,
            )
            if stored is None:
                state = state.add_name(
                    name, clock=self._clock, ids=self._ids
                ).record_into(uow)
            elif name.is_preferred and not stored.is_preferred:
                state = state.set_preferred_name(
                    name.language,
                    name.text,
                    script=name.script,
                    clock=self._clock,
                    ids=self._ids,
                ).record_into(uow)
        if draft.centroid is not None and state.centroid is None:
            state = state.set_centroid(
                draft.centroid, clock=self._clock, ids=self._ids
            ).record_into(uow)
        return state


def _has_additions(place: Place, draft: PlaceDraft) -> bool:
    stored = {name.key: name for name in place.names}
    for name in draft.names:
        match = stored.get(name.key)
        if match is None or (name.is_preferred and not match.is_preferred):
            return True
    return draft.centroid is not None and place.centroid is None
