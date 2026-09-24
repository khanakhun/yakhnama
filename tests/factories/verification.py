"""Factories for the ``verification`` domain: targets and freshly opened cases.

``VerificationCaseTestFactory`` is suffixed ``TestFactory`` because the domain already
has a ``VerificationCaseFactory`` (``yakhnama.modules.verification.domain.factories``)
that applies the opening rules; this factory builds a case directly for arranging
state. Cases are built with an empty history, so they are always consistent; walk them
through ``VerificationCase.transition`` to reach other states.

Patterns: Factory.
"""

from collections.abc import Mapping

from polyfactory import PostGenerated, Use

from tests.factories.base import FACTORY_IDS, YakhnamaModelFactory, pick, random_instant
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A case at version 1 has not changed since it was opened.
    return values["created_at"]


def _same_as_initial_state(_name: str, values: Mapping[str, object]) -> object:
    # Without history the current state is the state the case was opened in.
    return values["initial_state"]


class VerificationTargetTestFactory(YakhnamaModelFactory[VerificationTarget]):
    """Builds targets of a random kind with a fresh id.

    Implements: Factory.
    """

    __model__ = VerificationTarget

    kind = pick(list(TargetKind))
    target_id = Use(FACTORY_IDS.new_id)


class VerificationCaseTestFactory(YakhnamaModelFactory[VerificationCase]):
    """Builds unassigned cases opened in ``submitted``, with no history.

    Pass ``initial_state=VerificationState.DRAFT`` for a draft case.

    Implements: Factory.
    """

    __model__ = VerificationCase

    id = Use(FACTORY_IDS.new_id)
    target = Use(VerificationTargetTestFactory.build)
    initial_state = VerificationState.SUBMITTED
    state = PostGenerated(_same_as_initial_state)
    history = ()
    assigned_to = None
    opened_by = Use(FACTORY_IDS.new_id)
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
