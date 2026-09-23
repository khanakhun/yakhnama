"""Cross-cutting infrastructure used by every bounded context.

Settings, structured logging, health endpoints, the database engine and declarative
base (``db``), the SQLAlchemy unit of work (``uow``), the transactional outbox
(``outbox``), telemetry, Problem Details bodies and the dependency container
(``container``) live here; authentication and object storage join in later phases.
``platform`` never imports ``yakhnama.main`` so that the application factory stays the
single top of the dependency graph.

Patterns: Composition Root (``container.py``), Unit of Work, Transactional Outbox,
Observer, Adapter, API Schema, Settings.
"""
