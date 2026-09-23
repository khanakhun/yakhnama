"""Reference-data seed: loads the versioned ``data/reference`` files into the modules.

``application`` holds the orchestrating ``SeedReferenceDataHandler`` and its ports; it
depends only on the modules' ``public.py`` facades and the shared kernel. The YAML
reader adapter and the ``python -m yakhnama.seed`` entry point are wired in the
composition root (Phase 1 task T11).

Patterns: Command Handler, Composition Root (entry point, T11).
"""
