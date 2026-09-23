"""Cross-cutting infrastructure used by every bounded context.

Settings, structured logging and health endpoints live here today; the database engine,
unit of work, outbox, authentication, object storage, telemetry and the dependency
container join in later phases. ``platform`` never imports ``yakhnama.main`` so that the
application factory stays the single top of the dependency graph.

Patterns: Composition Root (``container.py``, from Phase 1), Unit of Work, Transactional
Outbox, Adapter, Decorator.
"""
