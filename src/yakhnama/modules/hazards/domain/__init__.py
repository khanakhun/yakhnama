"""The ``domain`` layer of the ``hazards`` module.

The hazard taxonomy (``HazardType`` aggregate, ``HazardTaxonomy``), the per-hazard
attribute schemas and their registry, glacier and glacial lake references, domain
events, errors, the ``HazardTypeFactory`` and the reference-file models. Imports only
the standard library, ``pydantic`` and ``yakhnama.shared_kernel``.

Patterns: Value Object, Aggregate Root, Strategy, Registry, Domain Events, Factory,
Domain Error.
"""
