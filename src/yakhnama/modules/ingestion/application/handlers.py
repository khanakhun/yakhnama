"""Write-side use cases of the ingestion module.

Every handler with an actor checks ``catalog_policy`` (administrators) before it
reads anything, so a refused actor learns nothing and nothing is staged.

**Running an ingestion** takes two steps that never share a transaction:

1. ``RunIngestionHandler`` checks the adapter and its pipeline are registered,
   opens a ``pending`` run with ``IngestionRunRequested``, commits, and enqueues
   ``ingestion.run`` with ``{run_id}``; the HTTP request never waits for a fetch.
2. ``ExecuteIngestionRunHandler`` (the task) reloads the run. A finished run is a
   repeated delivery and is returned unchanged. A run whose dataset stopped
   accepting data, or whose adapter or pipeline is gone, fails while pending
   with an issue saying why. Otherwise it starts the run and commits, so a
   long run is visible as ``running``; then the pipeline stages the observations
   and the finished run in one transaction. If anything escapes the pipeline,
   that transaction rolls back, the run is marked ``failed`` in a new one with an
   issue naming the stage, and the exception is re-raised so the worker logs it.

An enqueue failure after step 1 leaves a pending run that no task executes, and a
worker dying mid-run leaves a ``running`` one; both are visible in the run list
(open question: a sweeper that fails or re-enqueues them).

Patterns: Command Handler, Unit of Work, Policy, Domain Events, Template Method.
"""

from yakhnama.modules.ingestion.application.authorisation import (
    catalog_policy,
    require_allowed,
)
from yakhnama.modules.ingestion.application.commands import (
    CatalogueRasterAsset,
    DeprecateDataset,
    ExecuteIngestionRun,
    LoadReferenceDatasets,
    RecordDatasetVersion,
    RegisterDataset,
    RetireDataset,
    RunIngestion,
)
from yakhnama.modules.ingestion.application.dto import (
    DatasetSummary,
    DatasetVersionSummary,
    LoadReport,
    RasterAssetSummary,
    RunDetail,
    SkippedChange,
)
from yakhnama.modules.ingestion.application.ports import (
    INGESTION_RUN_TASK,
    IngestionUnitOfWork,
    IngestionUnitOfWorkFactory,
    PipelineFactory,
    RunnablePipeline,
)
from yakhnama.modules.ingestion.application.registry import SourceAdapterRegistry
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetNotFoundError,
    DatasetVersionNotFoundError,
    IngestionRunNotFoundError,
)
from yakhnama.modules.ingestion.domain.factories import (
    DatasetFactory,
    DatasetVersionFactory,
    IngestionRunFactory,
    RasterAssetFactory,
)
from yakhnama.modules.ingestion.domain.reference import DatasetReferenceEntry
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetStatus,
    IngestionIssue,
    IngestionReport,
    RunCounts,
    RunRequest,
    can_move_dataset,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    ConflictError,
    NotFoundError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.tasks import TaskQueue

# Fields a reference entry and a stored dataset are compared on but that are never
# changed in place (**proposed**): the catalog records what was registered, and new
# terms or a new publisher are a new dataset code.
_FIXED_FIELDS = (
    "title",
    "publisher",
    "licence",
    "update_frequency",
    "description",
    "homepage_url",
)


async def _load_dataset(uow: IngestionUnitOfWork, dataset_id: EntityId) -> Dataset:
    dataset = await uow.datasets.get(dataset_id)
    if dataset is None:
        raise DatasetNotFoundError.for_id(dataset_id)
    return dataset


async def _load_version(
    uow: IngestionUnitOfWork, dataset_version_id: EntityId
) -> DatasetVersion:
    version = await uow.dataset_versions.get(dataset_version_id)
    if version is None:
        raise DatasetVersionNotFoundError.for_id(dataset_version_id)
    return version


async def _load_run(uow: IngestionUnitOfWork, run_id: EntityId) -> IngestionRun:
    run = await uow.ingestion_runs.get(run_id)
    if run is None:
        raise IngestionRunNotFoundError.for_id(run_id)
    return run


def _check_version(expected: int | None, current: int) -> None:
    # Compared inside the unit of work, after the load, so the gap between the
    # client's read and this write is closed.
    if expected is not None and expected != current:
        message = "the record has changed since the client read it"
        raise PreconditionFailedError(
            message,
            details={"expected_version": expected, "current_version": current},
        )


class RegisterDatasetHandler:
    """Register a dataset; the code must be new and the licence present.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of the dataset id and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RegisterDataset) -> DatasetSummary:
        """Register the dataset.

        Args:
            command: The validated command.

        Returns:
            The new dataset.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            ConflictError: If the code is taken.
            LicenceRequiredError: If the details carry no licence.
        """
        require_allowed(catalog_policy(), command.actor, action="register datasets")
        async with self._uow_factory() as uow:
            if await uow.datasets.get_by_code(command.details.code) is not None:
                message = "a dataset with this code exists; codes are never reused"
                raise ConflictError(
                    message,
                    details={
                        "reason": "dataset_code_taken",
                        "code": command.details.code,
                    },
                )
            dataset = (
                DatasetFactory()
                .register(command.details, clock=self._clock, ids=self._ids)
                .record_into(uow)
            )
            await uow.datasets.add(dataset)
            await uow.commit()
        return DatasetSummary.from_entity(dataset)


class RecordDatasetVersionHandler:
    """Record a release of an active dataset under a new label.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of the version id and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RecordDatasetVersion) -> DatasetVersionSummary:
        """Record the version.

        Args:
            command: The validated command.

        Returns:
            The new version.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            DatasetNotFoundError: If the dataset does not exist.
            ConflictError: If the dataset already has a version with this label.
            DatasetStatusError: If the dataset is deprecated or retired.
        """
        require_allowed(
            catalog_policy(), command.actor, action="record dataset versions"
        )
        async with self._uow_factory() as uow:
            dataset = await _load_dataset(uow, command.dataset_id)
            label = command.details.label
            if await uow.dataset_versions.get_by_label(dataset.id, label) is not None:
                message = "the dataset already has a version with this label"
                raise ConflictError(
                    message,
                    details={"reason": "version_label_taken", "label": label},
                )
            version = (
                DatasetVersionFactory()
                .record(dataset, command.details, clock=self._clock, ids=self._ids)
                .record_into(uow)
            )
            await uow.dataset_versions.add(version)
            await uow.commit()
        return DatasetVersionSummary.from_entity(version)


class DeprecateDatasetHandler:
    """Deprecate a dataset: history kept, no new versions or runs.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: DeprecateDataset) -> DatasetSummary:
        """Deprecate the dataset; a no-op if it already is.

        Args:
            command: The validated command.

        Returns:
            The dataset after the change.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            DatasetNotFoundError: If the dataset does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            DatasetStatusError: If the dataset is retired.
        """
        require_allowed(catalog_policy(), command.actor, action="deprecate datasets")
        async with self._uow_factory() as uow:
            dataset = await _load_dataset(uow, command.dataset_id)
            _check_version(command.expected_version, dataset.version)
            change = dataset.deprecate(clock=self._clock, ids=self._ids)
            if change.events:
                dataset = change.record_into(uow)
                await uow.datasets.save(dataset)
            await uow.commit()
        return DatasetSummary.from_entity(dataset)


class RetireDatasetHandler:
    """Retire a dataset for good; its records stay.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RetireDataset) -> DatasetSummary:
        """Retire the dataset; a no-op if it already is.

        Args:
            command: The validated command.

        Returns:
            The dataset after the change.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            DatasetNotFoundError: If the dataset does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
        """
        require_allowed(catalog_policy(), command.actor, action="retire datasets")
        async with self._uow_factory() as uow:
            dataset = await _load_dataset(uow, command.dataset_id)
            _check_version(command.expected_version, dataset.version)
            change = dataset.retire(clock=self._clock, ids=self._ids)
            if change.events:
                dataset = change.record_into(uow)
                await uow.datasets.save(dataset)
            await uow.commit()
        return DatasetSummary.from_entity(dataset)


class RunIngestionHandler:
    """Open a pending ingestion run and enqueue its execution.

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: IngestionUnitOfWorkFactory,
        adapters: SourceAdapterRegistry,
        pipelines: PipelineFactory,
        task_queue: TaskQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            adapters: The registered source adapters.
            pipelines: Builds the pipeline of each adapter.
            task_queue: Schedules the ``ingestion.run`` task.
            clock: Source of timestamps and event times.
            ids: Source of the run id and event ids.
        """
        self._uow_factory = uow_factory
        self._adapters = adapters
        self._pipelines = pipelines
        self._task_queue = task_queue
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RunIngestion) -> EntityId:
        """Request the run.

        Args:
            command: The validated command.

        Returns:
            The id of the pending run.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            NotFoundError: If the adapter, or a pipeline for it, is not registered.
            DatasetNotFoundError: If the dataset does not exist.
            DatasetVersionNotFoundError: If the version does not exist.
            VersionDatasetMismatchError: If the version belongs to another dataset.
            DatasetStatusError: If the dataset is deprecated or retired.
        """
        require_allowed(catalog_policy(), command.actor, action="run ingestion")
        adapter = self._adapters.get(command.adapter_name)
        if not self._pipelines.supports(adapter.name):
            message = "no pipeline is registered for this source adapter"
            raise NotFoundError(message, details={"adapter_name": adapter.name})
        request = RunRequest(
            adapter_name=command.adapter_name, triggered_by=command.actor.user_id
        )
        async with self._uow_factory() as uow:
            dataset = await _load_dataset(uow, command.dataset_id)
            version = await _load_version(uow, command.version_id)
            run = (
                IngestionRunFactory()
                .start(dataset, version, request, clock=self._clock, ids=self._ids)
                .record_into(uow)
            )
            await uow.ingestion_runs.add(run)
            await uow.commit()
        # The key names the run, so a repeated enqueue is recognisable; a retry of
        # the ingestion is a new run with a new id.
        await self._task_queue.enqueue(
            INGESTION_RUN_TASK,
            {"run_id": run.id},
            idempotency_key=f"{INGESTION_RUN_TASK}:{run.id}",
        )
        return run.id


class ExecuteIngestionRunHandler:
    """Execute one requested run through its pipeline (the ``ingestion.run`` task).

    A system task, so there is no actor and no policy: it is never routed from
    the API and only runs what an administrator requested. Delivery is at least
    once; a finished run is returned unchanged.

    Implements: Command Handler, Template Method.
    """

    def __init__(
        self,
        *,
        uow_factory: IngestionUnitOfWorkFactory,
        adapters: SourceAdapterRegistry,
        pipelines: PipelineFactory,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per step.
            adapters: The registered source adapters.
            pipelines: Builds the pipeline of each adapter.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._adapters = adapters
        self._pipelines = pipelines
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: ExecuteIngestionRun) -> RunDetail:
        """Run the pipeline and store its outcome.

        Args:
            command: The run to execute.

        Returns:
            The finished run.

        Raises:
            IngestionRunNotFoundError: If the run does not exist.
            DatasetVersionNotFoundError: If its version does not exist.
            DatasetNotFoundError: If its dataset does not exist.
            RunStateError: If the run is already running (another worker has it).
            Exception: Whatever escaped the pipeline, after the run was marked
                ``failed``.
        """
        async with self._uow_factory() as uow:
            run = await _load_run(uow, command.run_id)
            if run.is_finished:
                return RunDetail.from_entity(run)
            version = await _load_version(uow, run.dataset_version_id)
            dataset = await _load_dataset(uow, version.dataset_id)
            refusal = self._refusal(dataset, run)
            if refusal is not None:
                failed = self._fail_pending(uow, run, refusal)
                await uow.ingestion_runs.save(failed)
                await uow.commit()
                return RunDetail.from_entity(failed)
            running = run.start(clock=self._clock, ids=self._ids).record_into(uow)
            await uow.ingestion_runs.save(running)
            await uow.commit()
        pipeline = self._pipelines.create(self._adapters.get(running.adapter_name))
        try:
            async with self._uow_factory() as uow:
                outcome = await pipeline.run(dataset, version, running, uow)
                await uow.commit()
        except Exception as error:
            await self._record_failure(running, pipeline, error)
            raise
        return RunDetail.from_entity(outcome.run)

    def _refusal(self, dataset: Dataset, run: IngestionRun) -> str | None:
        if not dataset.accepts_new_data:
            return f"the dataset is {dataset.status.value} and takes no new data"
        if not self._adapters.is_registered(run.adapter_name):
            return "the source adapter is no longer registered"
        if not self._pipelines.supports(run.adapter_name):
            return "no pipeline is registered for the source adapter"
        return None

    def _fail_pending(
        self, uow: IngestionUnitOfWork, run: IngestionRun, reason: str
    ) -> IngestionRun:
        report = IngestionReport.collect(
            [IngestionIssue(stage="fetch", message=reason)], RunCounts()
        )
        return run.fail(report, clock=self._clock, ids=self._ids).record_into(uow)

    async def _record_failure(
        self, running: IngestionRun, pipeline: RunnablePipeline, error: Exception
    ) -> None:
        # The pipeline's transaction rolled back, so the stored run is ``running``
        # exactly as committed above.
        async with self._uow_factory() as uow:
            failed = running.fail(
                pipeline.failure_report(error),
                input_checksum=pipeline.input_checksum,
                clock=self._clock,
                ids=self._ids,
            ).record_into(uow)
            await uow.ingestion_runs.save(failed)
            await uow.commit()


class CatalogueRasterAssetHandler:
    """Add a STAC-aligned raster of a dataset version to the catalog.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of the asset id and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: CatalogueRasterAsset) -> RasterAssetSummary:
        """Catalogue the raster.

        Args:
            command: The validated command.

        Returns:
            The new raster asset.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
            DatasetNotFoundError: If the dataset does not exist.
            DatasetVersionNotFoundError: If the version does not exist.
            VersionDatasetMismatchError: If the version belongs to another dataset.
            DatasetStatusError: If the dataset is deprecated or retired.
            ConflictError: If the version already has a raster with this STAC id.
        """
        require_allowed(catalog_policy(), command.actor, action="catalogue rasters")
        async with self._uow_factory() as uow:
            dataset = await _load_dataset(uow, command.dataset_id)
            version = await _load_version(uow, command.version_id)
            asset = (
                RasterAssetFactory()
                .catalogue(
                    dataset,
                    version,
                    command.description,
                    clock=self._clock,
                    ids=self._ids,
                )
                .record_into(uow)
            )
            await uow.raster_assets.add(asset)
            await uow.commit()
        return RasterAssetSummary.from_entity(asset)


class LoadReferenceDatasetsHandler:
    """Load ``data/reference/datasets.yaml`` idempotently.

    New codes are registered and then deprecated or retired if the file says so,
    so the history records each move. For existing codes only what the aggregate
    allows in place is applied: a forward status move and new coverages. Every
    other difference is reported in ``skipped_with_reason``; nothing is deleted and
    a status move is never undone. Loading the same file twice changes nothing
    the second time. Fixture entries are left out unless ``include_fixtures``.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: IngestionUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an ingestion unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of dataset ids and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: LoadReferenceDatasets) -> LoadReport:
        """Register or update every entry of the file.

        Args:
            command: The validated command with the parsed file.

        Returns:
            What was created, updated, unchanged, excluded or skipped.

        Raises:
            PermissionDeniedError: If the actor is not an administrator.
        """
        require_allowed(
            catalog_policy(), command.actor, action="load dataset reference data"
        )
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        excluded: list[str] = []
        skipped: list[SkippedChange] = []
        async with self._uow_factory() as uow:
            for entry in command.file.datasets:
                if entry.is_fixture and not command.include_fixtures:
                    excluded.append(entry.code)
                    continue
                current = await uow.datasets.get_by_code(entry.code)
                if current is None:
                    await self._register(uow, entry)
                    created.append(entry.code)
                    continue
                reasons, is_changed = await self._update(uow, current, entry)
                skipped.extend(
                    SkippedChange(code=entry.code, reason=reason) for reason in reasons
                )
                (updated if is_changed else unchanged).append(entry.code)
            if not command.dry_run:
                await uow.commit()
        return LoadReport(
            dry_run=command.dry_run,
            created=tuple(created),
            updated=tuple(updated),
            unchanged=tuple(unchanged),
            excluded=tuple(excluded),
            skipped_with_reason=tuple(skipped),
        )

    async def _register(
        self, uow: IngestionUnitOfWork, entry: DatasetReferenceEntry
    ) -> None:
        dataset = (
            DatasetFactory()
            .register(entry.to_details(), clock=self._clock, ids=self._ids)
            .record_into(uow)
        )
        await uow.datasets.add(dataset)
        moved = self._move(uow, dataset, entry.status)
        if moved is not dataset:
            await uow.datasets.save(moved)

    async def _update(
        self, uow: IngestionUnitOfWork, current: Dataset, entry: DatasetReferenceEntry
    ) -> tuple[list[str], bool]:
        # Each change is saved on its own: the repository accepts exactly one
        # version step per save.
        reasons = [
            f"{field} differs; a dataset's registration is never changed in place"
            for field in _FIXED_FIELDS
            if getattr(current, field) != getattr(entry, field)
        ]
        state = current
        has_new_coverage = (current.spatial_coverage, current.temporal_coverage) != (
            entry.spatial_coverage,
            entry.temporal_coverage,
        )
        if has_new_coverage and current.status is DatasetStatus.RETIRED:
            reasons.append("coverage differs but the stored dataset is retired")
        elif has_new_coverage:
            state = current.update_coverage(
                entry.spatial_coverage,
                entry.temporal_coverage,
                clock=self._clock,
                ids=self._ids,
            ).record_into(uow)
            await uow.datasets.save(state)
        if entry.status is not state.status and not can_move_dataset(
            state.status, entry.status
        ):
            reasons.append(
                f"the stored dataset is {state.status.value}; a status move is "
                "never undone"
            )
            return reasons, state is not current
        moved = self._move(uow, state, entry.status)
        if moved is not state:
            await uow.datasets.save(moved)
        return reasons, moved is not current

    def _move(
        self, uow: IngestionUnitOfWork, dataset: Dataset, target: DatasetStatus
    ) -> Dataset:
        if target is DatasetStatus.DEPRECATED:
            return dataset.deprecate(clock=self._clock, ids=self._ids).record_into(uow)
        if target is DatasetStatus.RETIRED:
            return dataset.retire(clock=self._clock, ids=self._ids).record_into(uow)
        return dataset
