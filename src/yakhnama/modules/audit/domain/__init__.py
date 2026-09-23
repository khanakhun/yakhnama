"""The ``domain`` layer of the ``audit`` module.

The append-only ``AuditEntry``, its value objects, its errors and the factory that
turns a domain event into an entry. Framework-free. The audit log **records** domain
events and emits none of its own: an audit entry about an audit entry would recurse,
and the log is the end of the line for every event, so there is no ``events.py``
content beyond its docstring.

Patterns: Value Object, Entity, Domain Error, Factory.
"""
