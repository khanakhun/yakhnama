"""Entry point of ``python -m yakhnama.seed`` (``poetry run poe seed``).

Deliberately thin: argument parsing, wiring and exit codes live in
``yakhnama.seed.cli``, where they are unit-tested and measured by coverage.

Patterns: Composition Root.
"""

from yakhnama.seed.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
