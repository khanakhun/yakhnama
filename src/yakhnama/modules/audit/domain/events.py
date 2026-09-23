"""Domain events of the ``audit`` bounded context: deliberately none.

The audit log records the domain events of every other module (see
``factories.from_domain_event``) and emits no events of its own. Emitting one per
entry would feed the outbox subscriber that writes entries, recording entries about
entries without end. This module exists so that the absence is a documented decision
rather than an omission.

Patterns: Domain Events (none emitted).
"""
