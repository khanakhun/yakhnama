"""Yakhnama backend: the open system of record for high-mountain hazards and disasters.

The package is a modular monolith. ``main`` is the application factory and composition
root, ``shared_kernel`` holds framework-free building blocks, ``platform`` holds
cross-cutting infrastructure and ``modules`` holds the bounded contexts.

Patterns: Composition Root (``yakhnama.main``); see ``AGENTS.md`` §3 for the catalog.
"""
