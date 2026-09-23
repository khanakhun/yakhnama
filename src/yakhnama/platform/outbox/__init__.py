"""Transactional outbox: domain events stored with the change, relayed after commit.

``OutboxWriter`` turns the events a unit of work collected into ``outbox_messages``
rows inside the same transaction as the aggregate write (ADR 0007). ``OutboxRelay``
later claims unpublished rows, hands each one to the subscribers registered for its
``event_type`` in a ``SubscriberRegistry`` and records the outcome. Delivery is at
least once, so every subscriber must be idempotent, keyed by
``OutboxEnvelope.event_id``.

There is no broker and no background loop yet: the relay is triggered by the task queue
that arrives in Phase 3 (ADR 0008).

Patterns: Transactional Outbox, Observer, Registry.
"""
