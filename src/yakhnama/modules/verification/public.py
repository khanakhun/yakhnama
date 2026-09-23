"""Public facade of the ``verification`` module.

Other modules, the API and the composition root import only this file: the states,
targets and transition table other contexts may reason about, the commands, queries,
DTOs, handlers and read services the API wires, and the ports the composition root
binds, including ``VerificationStateReadModel`` through which other modules read
the current state of their records.

Patterns: Facade.
"""

from yakhnama.modules.verification.application.authorisation import (
    moderation_policy,
)
from yakhnama.modules.verification.application.commands import (
    AssignVerificationCase,
    OpenVerificationCase,
    TransitionVerification,
)
from yakhnama.modules.verification.application.dto import (
    VerificationCaseDetail,
    VerificationCaseSummary,
)
from yakhnama.modules.verification.application.handlers import (
    AssignVerificationCaseHandler,
    OpenVerificationCaseHandler,
    TransitionVerificationHandler,
    VerificationHandlerDependencies,
)
from yakhnama.modules.verification.application.ports import (
    ReportOwnerLookup,
    ReviewerEligibility,
    VerificationCaseRepository,
    VerificationQueryService,
    VerificationStateReadModel,
    VerificationUnitOfWork,
    VerificationUnitOfWorkFactory,
)
from yakhnama.modules.verification.application.queries import (
    GetVerificationCase,
    ListVerificationCases,
)
from yakhnama.modules.verification.application.query_services import (
    VerificationCaseQueryService,
)
from yakhnama.modules.verification.domain.state_machine import (
    TRANSITIONS,
    next_states,
)
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    Transition,
    VerificationState,
    VerificationTarget,
)

__all__ = [
    "TRANSITIONS",
    "AssignVerificationCase",
    "AssignVerificationCaseHandler",
    "GetVerificationCase",
    "ListVerificationCases",
    "OpenVerificationCase",
    "OpenVerificationCaseHandler",
    "ReportOwnerLookup",
    "ReviewerEligibility",
    "TargetKind",
    "Transition",
    "TransitionVerification",
    "TransitionVerificationHandler",
    "VerificationCaseDetail",
    "VerificationCaseQueryService",
    "VerificationCaseRepository",
    "VerificationCaseSummary",
    "VerificationHandlerDependencies",
    "VerificationQueryService",
    "VerificationState",
    "VerificationStateReadModel",
    "VerificationTarget",
    "VerificationUnitOfWork",
    "VerificationUnitOfWorkFactory",
    "moderation_policy",
    "next_states",
]
