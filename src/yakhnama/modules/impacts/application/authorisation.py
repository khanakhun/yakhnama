"""Placeholder authorisation port for the impacts write side.

The identity module arrives in Phase 2 with composable policies over an authenticated
``Actor``. Until then every impacts command handler receives an ``AdminOnlyPolicy``
and asks it about the command's ``actor_id`` before reading or staging anything. The
composition root chooses the implementation; the handlers deny by default, so an
unknown or absent actor is refused unless the policy explicitly allows it. Phase 2
replaces this protocol with the identity policies.

Patterns: Policy.
"""

from typing import Protocol

from yakhnama.shared_kernel.ids import EntityId


class AdminOnlyPolicy(Protocol):
    """Decides whether an actor may change the impact metric registry.

    Placeholder until the identity policies of Phase 2 replace it.

    Implements: Policy.
    """

    def is_allowed(self, actor_id: EntityId | None) -> bool:
        """Tell whether ``actor_id`` may run an administrative command.

        Args:
            actor_id: The acting user or system, or ``None`` if unknown.

        Returns:
            ``True`` only if the actor is explicitly allowed.
        """
        ...
