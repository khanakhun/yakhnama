"""Bounded contexts of the modular monolith.

Each sub-package is one bounded context with ``domain``, ``application``,
``infrastructure`` and ``api`` layers. Other modules may import only its ``public.py``.

Patterns: Facade (``public.py`` per module), hexagonal layering per ``AGENTS.md`` §2.
"""
