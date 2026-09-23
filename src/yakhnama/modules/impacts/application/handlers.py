"""Write-side use cases of the impacts module.

Every handler asks its ``AdminOnlyPolicy`` first and raises ``PermissionDeniedError``
before opening a unit of work, so nothing is read or staged for a refused actor. A
metric's definition (category, value kind, unit, currency, mappings, aggregation)
never changes in place, because stored claims would silently change meaning; only
labels and retirement do.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.impacts.application.authorisation import AdminOnlyPolicy
from yakhnama.modules.impacts.application.commands import (
    LoadReferenceImpactMetrics,
    RetireImpactMetric,
)
from yakhnama.modules.impacts.application.dto import LoadReport, SkippedChange
from yakhnama.modules.impacts.application.ports import (
    ImpactsUnitOfWork,
    ImpactsUnitOfWorkFactory,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    ImpactMetricNotFoundError,
    InconsistentMetricDefinitionError,
)
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceEntry
from yakhnama.modules.impacts.domain.value_objects import MetricStatus
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

# Fields that define what a claim's value means; compared, never changed in place.
_DEFINITION_FIELDS = (
    "description",
    "category",
    "value_kind",
    "unit",
    "currency",
    "sendai",
    "desinventar",
    "aggregation",
)


def _require_allowed(
    policy: AdminOnlyPolicy, actor_id: EntityId | None, action: str
) -> None:
    # The actor id is deliberately left out of the message and details: the error
    # may be logged or returned, and who was refused is the audit log's business.
    if not policy.is_allowed(actor_id):
        message = f"the actor may not {action}"
        raise PermissionDeniedError(message, details={"action": action})


def _not_found(code: str) -> ImpactMetricNotFoundError:
    return ImpactMetricNotFoundError(
        f"no impact metric with code {code!r}", details={"code": code}
    )


class RetireImpactMetricHandler:
    """Retire an impact metric; its code stays taken for ever.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: ImpactsUnitOfWorkFactory,
        policy: AdminOnlyPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an impacts unit of work per call.
            policy: Decides whether the actor may retire metrics.
            clock: Source of ``updated_at`` and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RetireImpactMetric) -> None:
        """Retire the metric named by ``command``.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
            ImpactMetricNotFoundError: If the metric, or the replacement named in
                ``reason.replaced_by``, does not exist.
            InconsistentMetricDefinitionError: If ``reason.replaced_by`` is the
                metric's own code.
            ImpactMetricRetiredError: If the metric is already retired.
        """
        _require_allowed(self._policy, command.actor_id, "retire impact metrics")
        replaced_by = command.reason.replaced_by
        # Checked here so the caller gets a domain error, not the Pydantic error the
        # aggregate's invariant would raise when the retired state is built.
        if replaced_by == command.ref.code:
            message = f"impact metric {replaced_by!r} cannot be replaced by itself"
            raise InconsistentMetricDefinitionError(
                message, details={"code": replaced_by}
            )
        async with self._uow_factory() as uow:
            current = await uow.impact_metrics.get_by_code(command.ref.code)
            if current is None:
                raise _not_found(command.ref.code)
            if (
                replaced_by is not None
                and await uow.impact_metrics.get_by_code(replaced_by) is None
            ):
                raise _not_found(replaced_by)
            change = current.retire(
                command.reason, clock=self._clock, id_generator=self._ids
            )
            await uow.impact_metrics.save(change.record_into(uow))
            await uow.commit()


class LoadReferenceImpactMetricsHandler:
    """Load the impact metric reference file idempotently.

    New codes are created (retired entries are created and then retired). Existing
    codes get only the changes the aggregate allows in place: new labels while the
    metric is active, and retirement. Every other difference is reported in
    ``skipped_with_reason``; nothing is deleted and a retirement is never undone.
    Loading the same file twice changes nothing the second time.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: ImpactsUnitOfWorkFactory,
        policy: AdminOnlyPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an impacts unit of work per call.
            policy: Decides whether the actor may load reference data.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
        """
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock
        self._ids = ids
        self._factory = ImpactMetricFactory(clock=clock, id_generator=ids)

    async def __call__(self, command: LoadReferenceImpactMetrics) -> LoadReport:
        """Create or update every metric of the file.

        Args:
            command: The validated command with the parsed file.

        Returns:
            What was created, updated, unchanged or skipped.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor_id``.
        """
        _require_allowed(
            self._policy, command.actor_id, "load impact metric reference data"
        )
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        skipped: list[SkippedChange] = []
        async with self._uow_factory() as uow:
            for entry in command.file.entries:
                current = await uow.impact_metrics.get_by_code(entry.code)
                if current is None:
                    state = self._factory.create_from_reference(entry).record_into(uow)
                    await uow.impact_metrics.add(state)
                    created.append(entry.code)
                    continue
                reasons, state = self._update(uow, current, entry)
                skipped.extend(
                    SkippedChange(code=entry.code, reason=reason) for reason in reasons
                )
                if state is current:
                    unchanged.append(entry.code)
                else:
                    await uow.impact_metrics.save(state)
                    updated.append(entry.code)
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
        uow: ImpactsUnitOfWork,
        current: ImpactMetric,
        entry: ImpactMetricReferenceEntry,
    ) -> tuple[list[str], ImpactMetric]:
        reasons = [
            f"{field} differs; a metric's definition is never changed in place"
            for field in _DEFINITION_FIELDS
            if getattr(current, field) != getattr(entry, field)
        ]
        state = current
        # Relabel before retiring: a retired metric's labels are frozen.
        if entry.labels != current.labels:
            if current.is_active:
                state = current.relabel(
                    entry.labels, clock=self._clock, id_generator=self._ids
                ).record_into(uow)
            else:
                reasons.append("labels differ but the stored metric is retired")
        is_file_retired = entry.status is MetricStatus.RETIRED
        if entry.retirement is not None and state.is_active:
            state = state.retire(
                entry.retirement, clock=self._clock, id_generator=self._ids
            ).record_into(uow)
        elif not is_file_retired and not current.is_active:
            reasons.append(
                "stored metric is retired but the file says active; retirement is final"
            )
        elif is_file_retired and current.retirement != entry.retirement:
            reasons.append("stored retirement differs from the file; not rewritten")
        return reasons, state
