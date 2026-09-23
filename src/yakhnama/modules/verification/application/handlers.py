"""Write-side use cases of the verification module.

Every handler asks its ``AuthorisationPolicy`` (``CanModerate`` in the composition
root) before reading or staging anything. The one exception is the reporter's
resubmission (**proposed**): a non-moderator may move the case of their own report
from ``needs_information`` to ``submitted``. That rule depends on the case, so the
handler loads it first, and refuses a non-moderator with the same
``PermissionDeniedError`` whether the case is missing or not theirs, so case ids
cannot be probed.

The transition table, the reason rule and the human rule are the domain's
(``VerificationCase.transition``); handlers never re-implement them.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.verification.application.authorisation import (
    AuthorisationPolicy,
    acting_user_id,
    require_allowed,
)
from yakhnama.modules.verification.application.commands import (
    AssignVerificationCase,
    OpenVerificationCase,
    TransitionVerification,
)
from yakhnama.modules.verification.application.dto import VerificationCaseDetail
from yakhnama.modules.verification.application.ports import (
    ReportOwnerLookup,
    ReviewerEligibility,
    VerificationUnitOfWork,
    VerificationUnitOfWorkFactory,
)
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import VerificationCaseNotFoundError
from yakhnama.modules.verification.domain.factories import VerificationCaseFactory
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

_TRANSITION_ACTION = "transition verification cases"


class VerificationHandlerDependencies:
    """What every verification command handler is built from.

    Grouped so the composition root and the tests wire the module once, instead of
    repeating the same ports for every handler.

    Implements: Dependency Injection.

    Attributes:
        uow_factory: Opens a verification unit of work per call.
        policy: Decides whether an actor may moderate (``CanModerate``).
        clock: Source of transition times and event times.
        ids: Source of case and event ids.
        report_owners: Tells who submitted a report.
        reviewers: Tells whether a user may review cases.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: VerificationUnitOfWorkFactory,
        policy: AuthorisationPolicy,
        clock: Clock,
        ids: IdGenerator,
        report_owners: ReportOwnerLookup,
        reviewers: ReviewerEligibility,
    ) -> None:
        """Group the dependencies.

        Args:
            uow_factory: Opens a verification unit of work per call.
            policy: Decides whether an actor may moderate.
            clock: Source of transition times and event times.
            ids: Source of case and event ids.
            report_owners: Tells who submitted a report.
            reviewers: Tells whether a user may review cases.
        """
        self.uow_factory = uow_factory
        self.policy = policy
        self.clock = clock
        self.ids = ids
        self.report_owners = report_owners
        self.reviewers = reviewers


async def _load_case(
    uow: VerificationUnitOfWork, case_id: EntityId
) -> VerificationCase:
    case = await uow.verification_cases.get(case_id)
    if case is None:
        raise VerificationCaseNotFoundError(case_id)
    return case


class OpenVerificationCaseHandler:
    """Open the verification case of a target in its initial state.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: VerificationHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: OpenVerificationCase) -> EntityId:
        """Open the case, or return the existing one when ``if_absent`` is set.

        Args:
            command: The validated command.

        Returns:
            The id of the new case, or of the existing case with ``if_absent``.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            CaseAlreadyOpenError: If the target has a case and ``if_absent`` is
                ``False``.
        """
        deps = self._deps
        require_allowed(deps.policy, command.actor, action="open verification cases")
        opened_by = acting_user_id(command.actor)
        async with deps.uow_factory() as uow:
            existing = await uow.verification_cases.get_for_target(command.target)
            if existing is not None and command.if_absent:
                return existing.id
            case = VerificationCaseFactory.open(
                command.target,
                opened_by=opened_by,
                clock=deps.clock,
                ids=deps.ids,
                existing_case=existing,
            ).record_into(uow)
            await uow.verification_cases.add(case)
            await uow.commit()
        return case.id


class TransitionVerificationHandler:
    """Move a case along the transition table.

    Moderators may request any move the table allows. A non-moderator may only
    resubmit the case of their own report (``needs_information`` to
    ``submitted``, **proposed**). Automated moves (``is_human=False``) are refused
    for non-moderators, and the domain refuses them into ``verified`` for everyone.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: VerificationHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: TransitionVerification) -> VerificationCaseDetail:
        """Apply the transition.

        Args:
            command: The validated command.

        Returns:
            The case after the move.

        Raises:
            PermissionDeniedError: If the actor may neither moderate nor use the
                reporter's resubmission rule for this case.
            VerificationCaseNotFoundError: If a moderator names a missing case.
            InvalidTransitionError: If the table does not allow the move.
            HumanRequiredError: If ``verified`` is requested with ``is_human``
                ``False``.
            ReasonRequiredError: If the move needs a reason and none is given.
        """
        deps = self._deps
        actor = command.actor
        is_moderator = deps.policy.is_allowed(actor)
        if not is_moderator and (not actor.is_authenticated or not command.is_human):
            require_allowed(deps.policy, actor, action=_TRANSITION_ACTION)
        actor_id = acting_user_id(actor)
        async with deps.uow_factory() as uow:
            case = await uow.verification_cases.get(command.case_id)
            if not is_moderator and (
                case is None
                or not await self._is_own_resubmission(case, actor, command.to_state)
            ):
                require_allowed(deps.policy, actor, action=_TRANSITION_ACTION)
            if case is None:
                raise VerificationCaseNotFoundError(command.case_id)
            moved = case.transition(
                command.to_state,
                actor_id=actor_id,
                reason=command.reason,
                is_human=command.is_human,
                clock=deps.clock,
                ids=deps.ids,
            ).record_into(uow)
            await uow.verification_cases.save(moved)
            await uow.commit()
        return VerificationCaseDetail.from_entity(moved)

    async def _is_own_resubmission(
        self, case: VerificationCase, actor: Actor, to_state: VerificationState
    ) -> bool:
        if not (
            case.target.kind is TargetKind.REPORT
            and case.state is VerificationState.NEEDS_INFORMATION
            and to_state is VerificationState.SUBMITTED
        ):
            return False
        reporter_id = await self._deps.report_owners.reporter_of(case.target.target_id)
        return reporter_id is not None and reporter_id == actor.user_id


class AssignVerificationCaseHandler:
    """Make a reviewer responsible for a case.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: VerificationHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: AssignVerificationCase) -> VerificationCaseDetail:
        """Assign the case; assigning the current reviewer again changes nothing.

        Args:
            command: The validated command.

        Returns:
            The case after the assignment.

        Raises:
            PermissionDeniedError: If the policy refuses ``command.actor``.
            ValidationError: If the reviewer may not review cases.
            VerificationCaseNotFoundError: If the case does not exist.
            InvalidTransitionError: If the case is rejected or retracted.
        """
        deps = self._deps
        require_allowed(deps.policy, command.actor, action="assign verification cases")
        actor_id = acting_user_id(command.actor)
        if not await deps.reviewers.can_review(command.reviewer_id):
            message = "the reviewer may not review verification cases"
            raise ValidationError(message, details={"field": "reviewer_id"})
        async with deps.uow_factory() as uow:
            case = await _load_case(uow, command.case_id)
            assigned = case.assign(
                command.reviewer_id, actor_id=actor_id, clock=deps.clock, ids=deps.ids
            ).record_into(uow)
            # A repeated assignment returns the same version and no event.
            if assigned.version != case.version:
                await uow.verification_cases.save(assigned)
            await uow.commit()
        return VerificationCaseDetail.from_entity(assigned)
