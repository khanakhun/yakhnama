"""Translate between the ``VerificationCase`` aggregate and its row model.

Every read goes through ``VerificationCase.model_validate``, which replays the whole
history against the state table, so a stored case that no longer satisfies the
domain's invariants fails loudly instead of producing an invalid aggregate.

Patterns: Anti-Corruption Layer (mapper).
"""

from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.infrastructure.orm import VerificationCaseRow


def case_to_values(case: VerificationCase) -> dict[str, object]:
    """Return every column value of ``case`` except the primary key.

    Args:
        case: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "target_kind": case.target.kind.value,
        "target_id": case.target.target_id,
        "initial_state": case.initial_state.value,
        "state": case.state.value,
        "history": [step.model_dump(mode="json") for step in case.history],
        "assigned_to": case.assigned_to,
        "opened_by": case.opened_by,
        "version": case.version,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def case_to_row(case: VerificationCase) -> VerificationCaseRow:
    """Build the ``verification_cases`` row of ``case``.

    Args:
        case: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return VerificationCaseRow(id=case.id, **case_to_values(case))


def row_to_case(row: VerificationCaseRow) -> VerificationCase:
    """Rebuild a case from its row.

    Args:
        row: A fully loaded row of ``verification_cases``.

    Returns:
        The validated ``VerificationCase``.
    """
    return VerificationCase.model_validate(
        {
            "id": row.id,
            "target": {"kind": row.target_kind, "target_id": row.target_id},
            "initial_state": row.initial_state,
            "state": row.state,
            "history": row.history,
            "assigned_to": row.assigned_to,
            "opened_by": row.opened_by,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
