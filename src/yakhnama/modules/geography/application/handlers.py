"""Write-side use cases of the geography module.

Every handler asks its ``AuthorisationPolicy`` first and raises
``PermissionDeniedError`` before opening a unit of work, so nothing is read or staged
for a refused actor. Places are never deleted: retirement and merging are status changes
with a reason.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.application.authorisation import (
    AuthorisationPolicy,
    require_allowed,
)
from yakhnama.modules.geography.application.commands import (
    LoadDistrictBoundaries,
    LoadReferencePlaces,
    MergePlace,
    RetirePlace,
)
from yakhnama.modules.geography.application.dto import (
    BoundaryLoadReport,
    DistrictEdgeFeatureCollection,
    LoadReport,
    SkippedChange,
    display_name,
)
from yakhnama.modules.geography.application.ports import (
    BoundaryLoader,
    GeographyUnitOfWork,
    GeographyUnitOfWorkFactory,
    SharedEdgeCalculator,
)
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    DistrictBoundarySource,
    DistrictCentroid,
    DistrictEdge,
    DistrictEdgeSet,
    DistrictMatch,
    GazetteerDistrict,
    match_district_boundaries,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import (
    PlaceNotFoundError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.factories import PlaceDraft, PlaceFactory
from yakhnama.modules.geography.domain.value_objects import AdminLevel
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import Coordinates


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
        policy: AuthorisationPolicy,
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
            PermissionDeniedError: If the policy refuses ``command.actor``.
            PlaceNotFoundError: If the place does not exist.
            PlaceRetiredError: If the place is already merged or retired.
        """
        require_allowed(self._policy, command.actor, action="retire places")
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
        policy: AuthorisationPolicy,
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
            PermissionDeniedError: If the policy refuses ``command.actor``.
            PlaceNotFoundError: If the place or the target does not exist.
            PlaceRetiredError: If the place or the target is merged or retired.
            InvariantViolationError: If the place would be merged into itself.
        """
        require_allowed(self._policy, command.actor, action="merge places")
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
        policy: AuthorisationPolicy,
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
            PermissionDeniedError: If the policy refuses ``command.actor``.
            InvalidPlaceHierarchyError: If a new place does not fit under its
                stored parent.
            PlaceRetiredError: If a new place's stored parent is merged or retired.
        """
        require_allowed(self._policy, command.actor, action="load place reference data")
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


# Proposed default: the language of the gazetteer name a dataset name is compared
# with; the reference files ship an English name for every place.
_COMPARISON_LANGUAGE = "en"


class LoadDistrictBoundariesHandler:
    """Load a region's district polygons and publish the edges they share.

    The file is obtained and verified by the ``BoundaryLoader`` and the edges are
    computed by the ``SharedEdgeCalculator`` before any transaction opens. Inside
    one unit of work the districts are matched against the committed link table and
    the gazetteer, every linked active place gets the district's polygon as its
    footprint (the full polygon is stored, never published), and the edges become a
    new ``DistrictEdgeSet`` unless the current one has the same fingerprint. Nothing
    is matched by name and nothing is deleted. Loading the same file twice changes
    nothing the second time.

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port
        self,
        *,
        uow_factory: GeographyUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        loader: BoundaryLoader,
        calculator: SharedEdgeCalculator,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a geography unit of work per call.
            policy: Decides whether the actor may load reference data.
            loader: Obtains and reads the boundary file.
            calculator: Computes the shared edges.
            clock: Source of timestamps and event times.
            ids: Source of snapshot and event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._loader = loader
        self._calculator = calculator
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: LoadDistrictBoundaries) -> BoundaryLoadReport:
        """Load the boundaries ``command.source`` describes.

        Args:
            command: The validated command.

        Returns:
            What was matched, stored and published.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            BoundarySourceError: If the file cannot be obtained or read.
        """
        require_allowed(self._policy, command.actor, action="load district boundaries")
        boundary_set = await self._loader.load(command.source)
        computation = self._calculator.compute(boundary_set)
        async with self._uow_factory() as uow:
            places = await _linked_places(uow, command.source)
            match = match_district_boundaries(
                command.source,
                boundary_set,
                places={
                    code: GazetteerDistrict(
                        code=place.code,
                        name=display_name(place, _COMPARISON_LANGUAGE).text,
                        is_active=place.is_active,
                    )
                    for code, place in places.items()
                },
                region_districts=await _region_districts(
                    uow, command.source.region_place_code
                ),
            )
            current = await uow.district_edge_sets.get_current()
            footprints = await self._set_footprints(
                uow, boundary_set, match, places, current
            )
            edges = tuple(
                DistrictEdge.from_shared_edge(edge, match) for edge in computation.edges
            )
            fingerprint = DistrictEdgeSet.fingerprint_of(
                boundary_set.region_code,
                boundary_set.sha256,
                edges,
                footprints.centroids,
            )
            is_created = current is None or current.fingerprint != fingerprint
            edge_set = current
            if edge_set is None or is_created:
                edge_set = DistrictEdgeSet(
                    id=self._ids.new_id(),
                    region_code=boundary_set.region_code,
                    attribution=boundary_set.attribution,
                    sha256=boundary_set.sha256,
                    fingerprint=fingerprint,
                    edges=edges,
                    centroids=footprints.centroids,
                    created_at=self._clock.now(),
                )
                await uow.district_edge_sets.add(edge_set)
            if not command.dry_run:
                await uow.commit()
        payload = DistrictEdgeFeatureCollection.from_edge_set(edge_set)
        return BoundaryLoadReport(
            dry_run=command.dry_run,
            dataset_version=boundary_set.attribution.dataset_version,
            sha256=boundary_set.sha256,
            region_code=boundary_set.region_code,
            districts_in_source=len(boundary_set.districts),
            match=match,
            is_coverage_valid=computation.is_coverage_valid,
            dropped_parts=computation.dropped_parts,
            edges=len(edge_set.edges),
            edges_with_unlinked_district=sum(
                1 for edge in edge_set.edges if not edge.is_fully_linked
            ),
            positions=edge_set.position_count,
            payload_bytes=len(payload.model_dump_json().encode()),
            geometry_updated=footprints.geometry_updated,
            geometry_unchanged=footprints.geometry_unchanged,
            centroid_updated=footprints.centroid_updated,
            centroid_unchanged=footprints.centroid_unchanged,
            centroid_kept=footprints.centroid_kept,
            edge_set_id=edge_set.id,
            is_edge_set_created=is_created,
        )

    async def _set_footprints(
        self,
        uow: GeographyUnitOfWork,
        boundary_set: DistrictBoundarySet,
        match: DistrictMatch,
        places: dict[str, Place],
        previous: DistrictEdgeSet | None,
    ) -> "_Footprints":
        outcomes: dict[str, list[str]] = {
            key: [] for key in ("geometry_updated", "geometry_unchanged", *_CENTROID)
        }
        centroids: list[DistrictCentroid] = []
        districts = {district.code: district for district in boundary_set.districts}
        for link in match.linked:
            # ``linked`` holds only rows whose place exists and is active.
            place = places[str(link.place_code)]
            district = districts[link.source_code]
            state = place.set_geometry(
                district.geometry, clock=self._clock, ids=self._ids
            ).record_into(uow)
            outcomes[
                "geometry_unchanged" if state is place else "geometry_updated"
            ].append(place.code)
            state, outcome = self._set_centroid(
                uow, state, district.representative_point, previous
            )
            outcomes[outcome].append(place.code)
            if outcome != "centroid_kept":
                centroids.append(
                    DistrictCentroid(
                        source_code=district.code,
                        place_code=place.code,
                        point=district.representative_point,
                    )
                )
            if state is not place:
                await uow.places.save(state)
        return _Footprints(
            geometry_updated=tuple(outcomes["geometry_updated"]),
            geometry_unchanged=tuple(outcomes["geometry_unchanged"]),
            centroid_updated=tuple(outcomes["centroid_updated"]),
            centroid_unchanged=tuple(outcomes["centroid_unchanged"]),
            centroid_kept=tuple(outcomes["centroid_kept"]),
            centroids=tuple(centroids),
        )

    def _set_centroid(
        self,
        uow: GeographyUnitOfWork,
        place: Place,
        point: Coordinates,
        previous: DistrictEdgeSet | None,
    ) -> tuple[Place, str]:
        # A centroid is the load's to replace only while it is missing or still the
        # one the previous load recorded; anyone else's centroid is kept (Q239).
        recorded = None if previous is None else previous.centroid_set_for(place.code)
        if place.centroid is not None and place.centroid != recorded:
            return place, "centroid_kept"
        if place.centroid == point:
            return place, "centroid_unchanged"
        state = place.set_centroid(point, clock=self._clock, ids=self._ids)
        return state.record_into(uow), "centroid_updated"


_CENTROID = ("centroid_updated", "centroid_unchanged", "centroid_kept")


class _Footprints(BaseModel):
    """What one boundary load did to the linked places.

    Implements: DTO.

    Attributes:
        geometry_updated: Places whose footprint was set or replaced.
        geometry_unchanged: Places whose footprint already matched.
        centroid_updated: Places whose centroid was set or replaced.
        centroid_unchanged: Places whose centroid already was the load's point.
        centroid_kept: Places whose centroid came from elsewhere and was kept.
        centroids: The centroids the load owns, as provenance for the next load.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    geometry_updated: tuple[str, ...]
    geometry_unchanged: tuple[str, ...]
    centroid_updated: tuple[str, ...]
    centroid_unchanged: tuple[str, ...]
    centroid_kept: tuple[str, ...]
    centroids: tuple[DistrictCentroid, ...]


async def _linked_places(
    uow: GeographyUnitOfWork, source: DistrictBoundarySource
) -> dict[str, Place]:
    places: dict[str, Place] = {}
    for link in source.links:
        if link.place_code is None:
            continue
        place = await uow.places.get_by_code(link.place_code)
        if place is not None:
            places[link.place_code] = place
    return places


async def _region_districts(
    uow: GeographyUnitOfWork, region_place_code: str
) -> tuple[str, ...]:
    region = await uow.places.get_by_code(region_place_code)
    if region is None:
        return ()
    # Ancestry, not the dotted code, decides membership: the code scheme is a
    # convention the domain does not enforce (open question Q17).
    known: dict[EntityId, bool] = {region.id: True}

    async def is_inside(place: Place) -> bool:
        chain: list[EntityId] = []
        current: Place | None = place
        while current is not None and current.id not in known:
            chain.append(current.id)
            parent_id = current.parent_id
            current = None if parent_id is None else await uow.places.get(parent_id)
        verdict = current is not None and known[current.id]
        known.update(dict.fromkeys(chain, verdict))
        return verdict

    return tuple(
        [
            place.code
            for place in await uow.places.list_at_level(AdminLevel.DISTRICT)
            if await is_inside(place)
        ]
    )
