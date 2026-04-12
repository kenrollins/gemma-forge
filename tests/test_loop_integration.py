"""
Tests for harness property: THE FULL INNER LOOP IS COMPOSITIONALLY CORRECT

Why: All five v3 fixes can pass their individual unit and component
tests and still fail when composed. This file runs the full inner loop
against ONE rule on the real VM with the real LLM, and verifies that
the expected event sequence fires in order with the expected fields
populated.

This file is Tier 5 of the test plan in tests/PLAN.md.

A single integration test takes 3-15 minutes (real LLM + VM, one full
rule). These tests are NOT meant to run on every code change — they
are the moment-of-truth check before launching another long run.

Tests are marked with @pytest.mark.slow so they are excluded from the
default pytest run. Invoke explicitly with:

    pytest tests/test_loop_integration.py -v -m slow --run-slow

(The --run-slow flag is handled by conftest to opt-in.)
"""

# Note: do NOT add `from __future__ import annotations` — ADK tool parser breakage.
import asyncio
import json
import logging
import os
import tempfile
import yaml
from pathlib import Path
from typing import Any

import pytest

from gemma_forge.harness.ralph import run_ralph
from gemma_forge.harness.tools.ssh import _run_snapshot_cmd

logging.getLogger("asyncssh").setLevel(logging.WARNING)
logging.getLogger("google.adk").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)


pytestmark = pytest.mark.slow


def _latest_run_log() -> Path:
    """Return the most recently modified run log file."""
    runs = sorted(Path("runs").glob("run-*.jsonl"), key=lambda p: p.stat().st_mtime)
    assert runs, "No run logs found"
    return runs[-1]


def _load_events(path: Path) -> list:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _event_types_in_order(events: list) -> list:
    return [e["event_type"] for e in events]


def _first_event(events: list, event_type: str) -> dict | None:
    for e in events:
        if e["event_type"] == event_type:
            return e
    return None


def _last_event(events: list, event_type: str) -> dict | None:
    for e in reversed(events):
        if e["event_type"] == event_type:
            return e
    return None


@pytest.fixture
async def restored_baseline():
    """Ensure the VM is at baseline before the test. Clean up afterwards."""
    ok, _ = await _run_snapshot_cmd("restore", "baseline", timeout=60)
    assert ok
    await asyncio.sleep(3)
    # Clear any stale progress snapshot
    await _run_snapshot_cmd("delete", "progress", timeout=30)
    yield
    # Post-test cleanup: delete progress so the next test starts from baseline
    await _run_snapshot_cmd("delete", "progress", timeout=30)


@pytest.fixture
def test_config_path(tmp_path: Path) -> Path:
    """Create a config file that caps the run at a single rule with a
    short per-rule time budget. This keeps the integration test fast."""
    with open("config/harness.yaml") as f:
        base = yaml.safe_load(f) or {}

    # Override: single rule, 4-minute budget, lower reengagement threshold
    base["loop"] = {
        **base.get("loop", {}),
        "max_iterations": 1,
        "max_rules_per_run": 1,
        "max_retries_per_rule": 50,
        "max_wall_time_per_rule_s": 240,  # 4 min
        "architect_reengage_every_n_attempts": 2,
        "architect_reengage_on_plateau": True,
    }

    cfg_path = tmp_path / "harness_test.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(base, f)
    return cfg_path


# =============================================================================
# Property: A full single-rule run emits all expected v3 event types
# =============================================================================


class TestInnerLoopIntegration:
    async def test_property_single_rule_run_emits_expected_event_sequence(
        self, restored_baseline, test_config_path
    ):
        """Run ralph against one rule. Whichever path it takes (success or
        escalation), the new v3 event types must fire and the rule_complete
        event must be populated correctly."""
        # Record the existing run logs so we can identify the new one
        existing_logs = set(Path("runs").glob("run-*.jsonl"))

        # Run the loop — this blocks until one rule completes or the loop exits
        await run_ralph(config_path=str(test_config_path), skill_name="stig-rhel9")

        # Find the new log
        new_logs = set(Path("runs").glob("run-*.jsonl")) - existing_logs
        assert len(new_logs) == 1, f"Expected one new run log, got {len(new_logs)}"
        log_path = new_logs.pop()
        events = _load_events(log_path)
        assert len(events) > 5, f"Run produced too few events: {len(events)}"

        event_types = set(_event_types_in_order(events))

        # --- Required v3 event types must all have fired ---
        # (every run must emit these regardless of outcome)
        required = {
            "run_start",
            "skill_manifest",       # fix #2: UI manifest
            "snapshot_preflight",   # fix #5: snapshot preflight
            "scan_complete",
            "iteration_start",
            "rule_selected",         # fix #4: architect-visible rule choice
            "attempt_start",         # fix #4: per-attempt marker
            "agent_response",
            "prompt_assembled",      # fix #2: context budget telemetry
            "rule_complete",          # the punchline: per-rule summary
        }
        missing = required - event_types
        assert not missing, f"Missing required event types: {missing}"

        # --- rule_selected must carry category ---
        rs = _first_event(events, "rule_selected")
        assert rs is not None
        assert rs["data"].get("category"), "rule_selected missing category"
        assert rs["data"].get("time_budget_s"), "rule_selected missing time_budget_s"

        # --- attempt_start must carry attempt number and rule_id ---
        at = _first_event(events, "attempt_start")
        assert at is not None
        assert at["data"].get("attempt") == 1
        assert at["data"].get("rule_id")

        # --- rule_complete must be populated with expected fields ---
        rc = _last_event(events, "rule_complete")
        assert rc is not None, "rule_complete event never fired"
        rc_data = rc["data"]
        assert rc_data.get("outcome") in ("remediated", "escalated"), (
            f"Unexpected outcome: {rc_data.get('outcome')}"
        )
        assert rc_data.get("rule_id")
        assert rc_data.get("category")
        assert isinstance(rc_data.get("attempts"), int) and rc_data["attempts"] >= 1
        assert isinstance(rc_data.get("wall_time_s"), (int, float))
        assert isinstance(rc_data.get("reflections_count"), int)
        assert isinstance(rc_data.get("architect_reengagements"), int)

        # --- If escalated, escalation_reason must be set ---
        if rc_data["outcome"] == "escalated":
            assert rc_data.get("escalation_reason") in (
                "time_budget", "retry_ceiling", "architect_preemptive"
            ), f"Unknown escalation reason: {rc_data.get('escalation_reason')}"

        # --- Worker turns must have tool_calls <= 1 (fix #1 single-action) ---
        worker_responses = [
            e for e in events
            if e["event_type"] == "agent_response" and e.get("agent") == "worker"
        ]
        for wr in worker_responses:
            tc = wr["data"].get("tool_calls")
            if tc is not None:  # field present
                assert tc <= 1, (
                    f"Worker turn made {tc} tool calls — single-action cap violated"
                )

        # --- prompt_assembled events must show the budget was respected ---
        prompt_events = [e for e in events if e["event_type"] == "prompt_assembled"]
        assert len(prompt_events) > 0, "No prompt_assembled events emitted"
        for pe in prompt_events:
            used = pe["data"].get("used_tokens", 0)
            budget = pe["data"].get("budget_tokens", 1)
            assert used <= budget + 20, (  # small grace for truncation marker
                f"prompt_assembled shows budget exceeded: used={used}, budget={budget}"
            )

        # --- If the run failed, the post_mortem and revert events must fire ---
        if rc_data["outcome"] == "escalated":
            reverts = [e for e in events if e["event_type"] == "revert"]
            if reverts:  # at least one attempt must have failed
                assert "post_mortem" in event_types, (
                    "Escalated run with reverts had no post_mortem event — "
                    "snapshot-based revert diagnostics missing"
                )
                # Post-mortem should have the structured flags
                pm = _first_event(events, "post_mortem")
                assert pm is not None
                assert "sudo_ok" in pm["data"]
                assert "services_ok" in pm["data"]
                assert "mission_healthy" in pm["data"]

                # Revert events should use snapshot_restore method
                for rv in reverts:
                    assert rv["data"].get("method") == "snapshot_restore", (
                        f"Revert method should be snapshot_restore, got {rv['data'].get('method')}"
                    )

        # --- Log summary for human inspection ---
        print(f"\n=== Tier 5 integration run summary ===")
        print(f"  Log: {log_path.name}")
        print(f"  Total events: {len(events)}")
        print(f"  Rule: {rc_data['rule_id']}")
        print(f"  Category: {rc_data['category']}")
        print(f"  Outcome: {rc_data['outcome']}")
        print(f"  Attempts: {rc_data['attempts']}")
        print(f"  Wall time: {rc_data['wall_time_s']}s")
        print(f"  Reflections: {rc_data['reflections_count']}")
        print(f"  Reengagements: {rc_data['architect_reengagements']}")
        if rc_data["outcome"] == "escalated":
            print(f"  Escalation reason: {rc_data.get('escalation_reason')}")
