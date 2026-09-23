"""Transactional outbox: domain events stored with the change, relayed after commit.

``OutboxWriter`` turns the events a unit of work collected into ``outbox_messages``
rows inside the same transaction as the aggregate write (ADR 0007). ``OutboxRelay``
later leases unpublished rows through the ``OutboxStore`` port, hands each one to the
subscribers registered for its ``event_type`` in a ``SubscriberRegistry`` and records
the outcome. Delivery is at least once, so every subscriber must be idempotent, keyed
by ``OutboxEnvelope.event_id``.

The scheduler in ``yakhnama.platform.tasks`` enqueues ``outbox.relay_once`` every
``outbox_relay_interval_seconds`` and ``outbox.purge_published`` daily (ADR 0008).

Patterns: Transactional Outbox, Observer, Registry.
"""
