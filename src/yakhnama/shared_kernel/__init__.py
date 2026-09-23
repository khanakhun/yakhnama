"""Framework-free building blocks shared by every bounded context.

This package holds the error hierarchy, value objects, the Specification combinators,
domain events, the unit-of-work protocol, id and clock abstractions and pagination. It
imports only the standard library, ``pydantic`` and ``geojson-pydantic`` so that every
domain layer can depend on it without pulling in infrastructure; the rule is enforced by
``import-linter`` and by ``tests/architecture/test_structure.py``.

Nothing is re-exported here: import from the submodule that owns a name (for example
``from yakhnama.shared_kernel.errors import NotFoundError``) so every dependency on the
kernel is explicit and greppable.

Patterns: Value Object, Specification, Domain Events, Unit of Work (protocol only),
Domain Error, Adapter (``Clock`` and ``IdGenerator`` ports with their default adapters).
"""
