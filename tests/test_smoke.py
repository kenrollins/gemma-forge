"""Phase 0 smoke test.

Just verifies the package imports and the CLI entry point is callable.
Real test coverage arrives with the harness in Phase 3.
"""

from __future__ import annotations

import gemma_forge
from gemma_forge.cli import main


def test_package_imports() -> None:
    assert gemma_forge.__version__


def test_cli_main_returns_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "GemmaForge" in captured.out
