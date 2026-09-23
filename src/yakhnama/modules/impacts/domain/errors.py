"""Errors raised by the impact metric registry.

Each error subclasses a kernel family so the API maps it without importing this
module (``AGENTS.md`` §2.3) and overrides ``code`` with a specific, stable slug.

Patterns: Domain Error.
"""

from typing import ClassVar

from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
)


class ImpactMetricNotFoundError(NotFoundError):
    """No metric with the requested code exists in the registry.

    Implements: Domain Error.

    Attributes:
        code: ``"impact_metric_not_found"``.
    """

    code: ClassVar[str] = "impact_metric_not_found"


class MetricCodeAlreadyUsedError(ConflictError):
    """A metric code is already taken, by an active or a retired metric.

    Implements: Domain Error.

    Attributes:
        code: ``"metric_code_already_used"``.
    """

    code: ClassVar[str] = "metric_code_already_used"


class ImpactMetricRetiredError(InvalidTransitionError):
    """A retired metric was asked to change; retired metrics are final.

    Implements: Domain Error.

    Attributes:
        code: ``"impact_metric_retired"``.
    """

    code: ClassVar[str] = "impact_metric_retired"


class InconsistentMetricDefinitionError(InvariantViolationError):
    """A metric's value kind, unit and currency contradict each other.

    Implements: Domain Error.

    Attributes:
        code: ``"inconsistent_metric_definition"``.
    """

    code: ClassVar[str] = "inconsistent_metric_definition"
