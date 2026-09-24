"""The ``domain`` layer of the ``impacts`` module.

The impact metric registry (the ``ImpactMetric`` aggregate, the in-memory
``ImpactMetricRegistry`` and the reference file schema) and, from Phase 3, the
recording aggregates: append-only ``ImpactClaim`` (``claims.py``),
``InfrastructureAsset`` (``assets.py``), append-only ``DamageRecord``
(``damage.py``) and the ``BestFigurePolicy`` read-model policy (``best_figure.py``),
with their value objects, events, errors and factories.

Patterns: Value Object, Entity, Aggregate Root, Registry, Factory, Policy, Domain
Events, Domain Error.
"""
