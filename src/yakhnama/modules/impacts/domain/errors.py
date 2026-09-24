"""Errors raised by the impacts domain: metrics, claims, assets and damage records.

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
    ValidationError,
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


class ImpactClaimNotFoundError(NotFoundError):
    """No impact claim with the requested id exists.

    Implements: Domain Error.

    Attributes:
        code: ``"impact_claim_not_found"``.
    """

    code: ClassVar[str] = "impact_claim_not_found"


class ClaimImmutableError(InvalidTransitionError):
    """A retracted claim or damage record was asked to change.

    Claims and damage records are append-only: the only change is one retraction,
    and a retracted record is final.

    Implements: Domain Error.

    Attributes:
        code: ``"claim_immutable"``.
    """

    code: ClassVar[str] = "claim_immutable"


class ClaimValueKindMismatchError(ValidationError):
    """A claim value's kind differs from its metric's ``value_kind``.

    Implements: Domain Error.

    Attributes:
        code: ``"claim_value_kind_mismatch"``.
    """

    code: ClassVar[str] = "claim_value_kind_mismatch"


class ClaimValueUnitMismatchError(ValidationError):
    """A claim value's unit or currency differs from its metric's.

    Implements: Domain Error.

    Attributes:
        code: ``"claim_value_unit_mismatch"``.
    """

    code: ClassVar[str] = "claim_value_unit_mismatch"


class ClaimMetricMismatchError(ValidationError):
    """A claim was combined or corrected under a metric it does not belong to.

    Implements: Domain Error.

    Attributes:
        code: ``"claim_metric_mismatch"``.
    """

    code: ClassVar[str] = "claim_metric_mismatch"


class AssetNotFoundError(NotFoundError):
    """No infrastructure asset with the requested id exists.

    Implements: Domain Error.

    Attributes:
        code: ``"asset_not_found"``.
    """

    code: ClassVar[str] = "asset_not_found"


class DamageRecordNotFoundError(NotFoundError):
    """No damage record with the requested id exists.

    Implements: Domain Error.

    Attributes:
        code: ``"damage_record_not_found"``.
    """

    code: ClassVar[str] = "damage_record_not_found"
