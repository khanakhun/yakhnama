"""The ``infrastructure`` layer of the ``impacts`` module.

Adapters implementing the application ports on PostgreSQL/PostGIS: row models
(``orm.py``), mappers between rows and aggregates (``mappers.py``), repositories
(``repositories.py``), the module's unit of work (``uow.py``) and query services
with their specification compilers (``queries.py``).

Patterns: Repository, Unit of Work, Query Service, Specification, Adapter,
Anti-Corruption Layer.
"""
