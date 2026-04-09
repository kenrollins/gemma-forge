"""GemmaForge CLI entry point.

Phase 0 stub. Real subcommands (run, validate, list-skills, etc.) are
added in their respective phases. The entry point exists now so that
``pip install -e .`` succeeds and the ``gemma-forge`` shim is on PATH.
"""

from __future__ import annotations

import sys

from gemma_forge import __version__


def main() -> int:
    """CLI entry point referenced by ``pyproject.toml``'s ``[project.scripts]``."""
    print(f"GemmaForge {__version__} — Phase 0 scaffold.")
    print("Real CLI subcommands arrive in later phases. See README for status.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
