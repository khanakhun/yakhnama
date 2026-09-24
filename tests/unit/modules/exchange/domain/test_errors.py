"""Unit tests for ``yakhnama.modules.exchange.domain.errors``."""

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportContractError,
    ImportJobNotFoundError,
    JobStateError,
    UnsupportedFormatError,
)
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)


def test_export_job_not_found_error_carries_id() -> None:
    job_id = FACTORY_IDS.new_id()

    error = ExportJobNotFoundError.for_id(job_id)

    assert isinstance(error, NotFoundError)
    assert error.details == {"export_job_id": str(job_id)}


def test_import_job_not_found_error_carries_id() -> None:
    job_id = FACTORY_IDS.new_id()

    error = ImportJobNotFoundError.for_id(job_id)

    assert isinstance(error, NotFoundError)
    assert error.details == {"import_job_id": str(job_id)}


def test_job_state_error_is_invalid_transition_with_context() -> None:
    job_id = FACTORY_IDS.new_id()

    error = JobStateError.for_job("export_job", job_id, "completed", "start")

    assert isinstance(error, InvalidTransitionError)
    assert error.details == {
        "job_kind": "export_job",
        "job_id": str(job_id),
        "status": "completed",
        "action": "start",
    }


def test_unsupported_format_error_cuts_long_code() -> None:
    error = UnsupportedFormatError.for_code("x" * 100, "import")

    assert isinstance(error, ValidationError)
    assert error.details["format"] == "x" * 32


def test_import_contract_error_for_header_counts_unknown_columns() -> None:
    missing = [f"column_{index}" for index in range(30)]

    error = ImportContractError.for_header(missing, 2, ["title"])

    assert isinstance(error, ValidationError)
    assert error.details["missing_columns"] == tuple(missing[:20])
    assert error.details["unknown_column_count"] == 2  # reason: arranged
    assert error.details["duplicated_columns"] == ("title",)


def test_import_contract_error_too_many_rows_carries_limit() -> None:
    error = ImportContractError.too_many_rows(10_000)

    assert error.details == {"max_rows": 10_000}
