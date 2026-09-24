"""The ``domain`` layer of the ``provenance`` module.

The ``Source`` aggregate, its value objects, events, errors and factory.
Framework-free. There are no policies here: who may register or edit a source is
decided by the identity module's authorisation policies.

Patterns: Value Object, Entity, Aggregate Root, Domain Events, Domain Error, Factory.
"""
