"""Unit tests for ``yakhnama.modules.ingestion.domain.errors``."""

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.errors import (
    UNPRINTABLE_VALUE,
    DatasetNotFoundError,
    DatasetStatusError,
    DatasetVersionNotFoundError,
    IngestionRunNotFoundError,
    LicenceRequiredError,
    ObservationUnitMismatchError,
    RunOutcomeError,
    RunStateError,
    UnknownVariableError,
    VersionDatasetMismatchError,
)
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)

IDS = SequentialIdGenerator()
FIRST_ID = IDS.new_id()
SECOND_ID = IDS.new_id()


def test_not_found_errors_carry_the_looked_up_key() -> None:
    errors = [
        DatasetNotFoundError.for_id(FIRST_ID),
        DatasetNotFoundError.for_code("test_dataset"),
        DatasetVersionNotFoundError.for_id(FIRST_ID),
        IngestionRunNotFoundError.for_id(FIRST_ID),
    ]

    details = [dict(error.details) for error in errors]

    assert all(isinstance(error, NotFoundError) for error in errors)
    assert details == [
        {"dataset_id": str(FIRST_ID)},
        {"code": "test_dataset"},
        {"dataset_version_id": str(FIRST_ID)},
        {"run_id": str(FIRST_ID)},
    ]


def test_licence_required_error_is_an_invariant_violation_with_code() -> None:
    error = LicenceRequiredError.for_code("test_dataset")

    assert isinstance(error, InvariantViolationError)
    assert error.details == {"code": "test_dataset"}
    assert "licence" in error.message


def test_dataset_status_error_is_an_invalid_transition_with_context() -> None:
    error = DatasetStatusError.refused(FIRST_ID, "retired", "deprecate")

    assert isinstance(error, InvalidTransitionError)
    assert error.details == {
        "dataset_id": str(FIRST_ID),
        "status": "retired",
        "action": "deprecate",
    }


def test_run_state_error_names_both_statuses() -> None:
    error = RunStateError.for_move(FIRST_ID, "succeeded", "running")

    assert isinstance(error, InvalidTransitionError)
    assert error.details == {
        "run_id": str(FIRST_ID),
        "from_status": "succeeded",
        "to_status": "running",
    }


def test_run_outcome_error_carries_the_rule() -> None:
    error = RunOutcomeError.because(FIRST_ID, "a failed run reports an error")

    assert isinstance(error, InvariantViolationError)
    assert error.details["reason"] == "a failed run reports an error"


def test_version_dataset_mismatch_error_carries_both_ids() -> None:
    error = VersionDatasetMismatchError.for_ids(FIRST_ID, SECOND_ID)

    assert isinstance(error, InvariantViolationError)
    assert error.details == {
        "dataset_id": str(FIRST_ID),
        "dataset_version_id": str(SECOND_ID),
    }


def test_unknown_variable_error_repeats_identifier_shaped_code() -> None:
    error = UnknownVariableError.for_code("wind_speed")

    assert isinstance(error, ValidationError)
    assert error.details == {"variable": "wind_speed"}


def test_unknown_variable_error_masks_free_text() -> None:
    error = UnknownVariableError.for_code("Temp (°C)\n<script>")

    assert error.details == {"variable": UNPRINTABLE_VALUE}


def test_observation_unit_mismatch_error_masks_non_identifier_unit() -> None:
    error = ObservationUnitMismatchError.for_units("air_temperature", "kelvin", "°C")

    assert isinstance(error, ValidationError)
    assert error.details == {
        "variable": "air_temperature",
        "expected_unit": "kelvin",
        "actual_unit": UNPRINTABLE_VALUE,
    }
