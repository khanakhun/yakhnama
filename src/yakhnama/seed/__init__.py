"""Reference-data seed: loads the versioned ``data/reference`` files into the modules.

- ``application`` holds the orchestrating ``SeedReferenceDataHandler`` and its ports.
  It depends only on the modules' ``public.py`` facades and the shared kernel.
- ``infrastructure`` holds ``YamlReferenceFileReader``, the YAML adapter of the
  ``ReferenceFileReader`` port.
- ``cli`` is the command line (``python -m yakhnama.seed [--dry-run]
  [--reference-dir PATH]``, ``poe seed``). It gets the wiring from
  ``platform/container.py`` (``build_seed_handler``), and ``__main__`` only calls it.

Patterns: Command Handler, Adapter, Composition Root.
"""
