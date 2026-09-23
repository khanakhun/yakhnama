"""Cross-module adapters of the composition root, one module per consuming module.

When a module's application layer needs something another module holds, it declares
a port (``reports.public.PhotoEvidenceProvider``, ``events.public.PlaceDirectory``,
...). The adapters here answer those ports from the other module's facade, so the
two modules never import each other beyond ``public.py``. Each file is named after
the module that **consumes** the port (``wiring/reports.py`` answers the reports
ports). ``yakhnama.platform.container`` builds them; nothing inside a module
imports this package.

Composition-root code may import module internals; these adapters keep to the
facades except where a module docstring names an internal import and why.

Patterns: Adapter, Composition Root.
"""
