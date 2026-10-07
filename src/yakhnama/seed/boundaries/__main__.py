"""Entry point of ``python -m yakhnama.seed.boundaries`` (``poe load-boundaries``).

Deliberately thin: argument parsing, wiring and exit codes live in
``yakhnama.seed.boundaries.cli``, where they are unit-tested and measured by
coverage.

Patterns: Composition Root.
"""

from yakhnama.seed.boundaries.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
