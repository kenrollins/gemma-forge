"""Pytest configuration for GemmaForge tests.

Slow (integration) tests are excluded from the default run. To run them
explicitly: `pytest -v -m slow --run-slow`.
"""

# Note: do NOT add `from __future__ import annotations` — ADK tool parser breakage.
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow integration tests (require real LLM + VM)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow test; use --run-slow to include")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
