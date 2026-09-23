"""Framework-free building blocks shared by every bounded context.

This package will hold the error hierarchy, value objects, the Specification
combinators, domain events, the unit-of-work protocol, id and clock abstractions and
pagination. It imports only the standard library, ``pydantic`` and ``geojson-pydantic``
so that every domain layer can depend on it without pulling in infrastructure; the rule
is enforced by ``import-linter`` and by ``tests/architecture/test_structure.py``.

Patterns: Value Object, Specification, Domain Events, Unit of Work (protocol only).
"""
