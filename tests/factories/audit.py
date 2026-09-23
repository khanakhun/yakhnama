"""Factories for the ``audit`` domain.

Entries are built directly, bypassing ``from_domain_event``, so they arrange state
(for query and repository tests); they do not test how entries are derived.

Patterns: Factory.
"""

from polyfactory import Use

from tests.factories.base import FACTORY_IDS, YakhnamaModelFactory, random_instant
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget, compute_digest

TEST_PAYLOAD_DIGEST = compute_digest(b"test payload")
"""A well-formed digest for entries whose payload does not matter."""


def _target() -> AuditTarget:
    return AuditTarget(target_type="source", target_id=FACTORY_IDS.new_id())


class AuditEntryTestFactory(YakhnamaModelFactory[AuditEntry]):
    """Builds system entries for ``provenance.source_registered`` on a new target.

    Pass ``actor_id=`` together with ``actor_kind="user"`` for a user entry.

    Implements: Factory.
    """

    __model__ = AuditEntry

    id = Use(FACTORY_IDS.new_id)
    occurred_at = Use(random_instant)
    actor_id = None
    actor_kind = "system"
    action = "provenance.source_registered"
    target = Use(_target)
    event_id = Use(FACTORY_IDS.new_id)
    payload_digest = TEST_PAYLOAD_DIGEST
