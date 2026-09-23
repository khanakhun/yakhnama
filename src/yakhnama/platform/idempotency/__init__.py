"""``Idempotency-Key`` support for creating ``POST`` requests (ADR 0016).

A client that retries a ``POST`` with the same ``Idempotency-Key`` gets the first
response again instead of creating a second resource. ``IdempotencyMiddleware``
reserves the key before the route runs, stores a successful response and replays it;
``IdempotencyStore`` is the port, ``SqlAlchemyIdempotencyStore`` the PostgreSQL
adapter over the ``idempotency_keys`` table.

Patterns: Adapter, Decorator, DTO.
"""
