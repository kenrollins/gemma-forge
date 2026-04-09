"""Ralph loop — the core Fail → Revert → Retry orchestrator.

This module implements the GemmaForge Ralph loop: an autonomous STIG
remediation cycle where agents fail, reason through failures, revert
broken fixes, and retry until the mission succeeds.

The loop is deliberately NOT built as a single ADK LoopAgent because
the revert-on-failure branch requires conditional logic that's more
naturally expressed as explicit Python control flow. ADK's LoopAgent
is used for the inner remediation cycle; the outer orchestration
(scan → pick rule → fix → audit → revert-if-broken → next) is
explicit Python.

Usage:
    python -m gemma_forge.harness.loop --config config/harness.yaml
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI

from .tools.healthcheck import mission_healthcheck
from .tools.openscap import stig_check_rule, stig_scan
from .tools.ssh import SSHConfig, ssh_apply, ssh_revert

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


async def _run_ssh_local(cmd: str) -> tuple[str, str, int]:
    """Run a command on the LOCAL host (not the VM). Used for virsh snapshots."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode() if stdout else "",
        stderr.decode() if stderr else "",
        proc.returncode or 0,
    )


@dataclass
class LoopConfig:
    """Configuration for the Ralph loop."""

    max_iterations: int = 20
    max_rules_per_run: int = 5
    vm: SSHConfig = field(default_factory=lambda: SSHConfig(
        host="192.168.122.43",
        user="adm-forge",
        key_path="/data/vm/gemma-forge/keys/adm-forge",
    ))
    stig_profile: str = "xccdf_org.ssgproject.content_profile_stig"
    stig_datastream: str = "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml"

    # Model endpoints
    architect_url: str = "http://localhost:8050/v1"
    architect_model: str = "/weights/Gemma-4-31B-IT-NVFP4"
    worker_url: str = "http://localhost:8050/v1"
    worker_model: str = "/weights/Gemma-4-31B-IT-NVFP4"
    auditor_url: str = "http://localhost:8060/v1"
    auditor_model: str = "/weights/gemma-4-E4B-it"


@dataclass
class RemediationRecord:
    """Record of a single remediation attempt."""

    rule_id: str
    rule_title: str
    iteration: int
    fix_script: str
    revert_script: str
    apply_result: str
    healthcheck_result: str
    reverted: bool
    success: bool


async def _chat(
    client: AsyncOpenAI,
    model: str,
    system: str,
    user_msg: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    """Simple chat completion helper."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def _parse_scripts(worker_response: str) -> tuple[str, str]:
    """Extract FIX_SCRIPT and REVERT_SCRIPT from the Worker's response.

    Looks for ```bash ... ``` blocks. The first block is the fix,
    the second is the revert.
    """
    blocks: list[str] = []
    in_block = False
    current: list[str] = []

    for line in worker_response.split("\n"):
        if line.strip().startswith("```"):
            if in_block:
                blocks.append("\n".join(current))
                current = []
                in_block = False
            else:
                in_block = True
                current = []
        elif in_block:
            current.append(line)

    if current and in_block:
        blocks.append("\n".join(current))

    if len(blocks) >= 2:
        return blocks[0], blocks[1]
    elif len(blocks) == 1:
        return blocks[0], "echo 'NO REVERT SCRIPT PROVIDED'"
    else:
        return worker_response, "echo 'COULD NOT PARSE SCRIPTS'"


def _parse_scan_results(scan_output: str) -> list[tuple[str, str]]:
    """Parse STIG scan output into (rule_id, title) pairs for failing rules."""
    rules: list[tuple[str, str]] = []
    # Normalize \r\t to \t (oscap outputs carriage returns on some systems)
    lines = [line.replace("\r", "") for line in scan_output.split("\n")]
    i = 0
    while i < len(lines):
        if lines[i].startswith("Title\t"):
            title = lines[i].replace("Title\t", "").strip()
            if i + 1 < len(lines) and lines[i + 1].startswith("Rule\t"):
                rule_id = lines[i + 1].replace("Rule\t", "").strip()
                if i + 2 < len(lines) and "fail" in lines[i + 2].lower():
                    rules.append((rule_id, title))
                i += 3
                continue
        i += 1
    return rules


async def run_ralph_loop(config: LoopConfig) -> list[RemediationRecord]:
    """Execute the Ralph loop: Scan → Fix → Audit → (Revert if broken) → Repeat.

    Returns a list of RemediationRecord documenting every attempt.
    """
    from .agents import (
        ARCHITECT_INSTRUCTION,
        AUDITOR_INSTRUCTION,
        WORKER_INSTRUCTION,
    )

    architect_client = AsyncOpenAI(base_url=config.architect_url, api_key="n/a")
    worker_client = AsyncOpenAI(base_url=config.worker_url, api_key="n/a")
    auditor_client = AsyncOpenAI(base_url=config.auditor_url, api_key="n/a")

    records: list[RemediationRecord] = []
    failed_approaches: dict[str, list[str]] = {}  # rule_id → list of failed fix descriptions

    # Step 1: Initial STIG scan
    logger.info("=" * 60)
    logger.info("RALPH LOOP — Starting STIG remediation")
    logger.info("=" * 60)
    logger.info("Running initial STIG scan...")
    scan_results = await stig_scan(
        config.vm, config.stig_profile, config.stig_datastream
    )
    failing_rules = _parse_scan_results(scan_results)
    logger.info("Found %d failing STIG rules", len(failing_rules))

    if not failing_rules:
        logger.info("No failing rules — system is compliant!")
        return records

    # Step 2: Pre-flight healthcheck
    logger.info("Pre-flight healthcheck...")
    health = await mission_healthcheck(config.vm)
    if "HEALTHY" not in health:
        logger.error("Mission app is UNHEALTHY before we even started: %s", health)
        return records
    logger.info("Mission app is HEALTHY — proceeding")

    # Step 3: Remediation loop
    rules_attempted = 0
    for iteration in range(1, config.max_iterations + 1):
        if rules_attempted >= config.max_rules_per_run:
            logger.info("Reached max_rules_per_run (%d) — stopping", config.max_rules_per_run)
            break

        if not failing_rules:
            logger.info("No more failing rules — remediation complete!")
            break

        logger.info("")
        logger.info("-" * 60)
        logger.info("ITERATION %d (rules remaining: %d)", iteration, len(failing_rules))
        logger.info("-" * 60)

        # --- ARCHITECT: pick a rule and plan the fix ---
        rule_context = "\n".join(
            f"  - {rid}: {title}" for rid, title in failing_rules[:10]
        )
        failed_ctx = ""
        if failed_approaches:
            failed_ctx = "\n\nPreviously failed approaches (DO NOT repeat these):\n"
            for rid, approaches in failed_approaches.items():
                for a in approaches:
                    failed_ctx += f"  - {rid}: {a}\n"

        architect_prompt = (
            f"Here are the current failing STIG rules:\n{rule_context}\n"
            f"{failed_ctx}\n"
            f"Select ONE rule to remediate and explain your plan."
        )

        logger.info("ARCHITECT: analyzing rules...")
        architect_response = await _chat(
            architect_client,
            config.architect_model,
            ARCHITECT_INSTRUCTION,
            architect_prompt,
            max_tokens=512,
        )
        logger.info("ARCHITECT response:\n%s", architect_response[:300])

        # Try to identify which rule the Architect picked
        selected_rule = None
        selected_title = ""
        for rid, title in failing_rules:
            if rid in architect_response or title.lower() in architect_response.lower():
                selected_rule = rid
                selected_title = title
                break

        if not selected_rule:
            # Default to first rule if we can't parse the Architect's choice
            selected_rule, selected_title = failing_rules[0]
            logger.warning("Could not parse Architect's rule selection, defaulting to: %s", selected_rule)

        logger.info("Selected rule: %s (%s)", selected_rule, selected_title)

        # --- WORKER: generate fix and revert scripts ---
        worker_prompt = (
            f"The Architect has selected this STIG rule to remediate:\n"
            f"  Rule: {selected_rule}\n"
            f"  Title: {selected_title}\n\n"
            f"Architect's plan:\n{architect_response}\n\n"
            f"Generate the FIX_SCRIPT and REVERT_SCRIPT."
        )

        logger.info("WORKER: generating fix scripts...")
        worker_response = await _chat(
            worker_client,
            config.worker_model,
            WORKER_INSTRUCTION,
            worker_prompt,
            max_tokens=1024,
        )
        logger.info("WORKER response:\n%s", worker_response[:300])

        fix_script, revert_script = _parse_scripts(worker_response)
        logger.info("Parsed fix script (%d chars) and revert script (%d chars)",
                     len(fix_script), len(revert_script))

        # --- Pre-fix snapshot (safety net for cascading damage) ---
        snapshot_name = f"pre-iter-{iteration}"
        logger.info("Taking pre-fix snapshot '%s'...", snapshot_name)
        snap_stdout, snap_stderr, snap_rc = await _run_ssh_local(
            f"sudo virsh snapshot-create-as gemma-forge-mission-app {snapshot_name} "
            f"--description 'Ralph loop iteration {iteration} safety net' --atomic"
        )
        has_snapshot = snap_rc == 0
        if has_snapshot:
            logger.info("Snapshot '%s' created", snapshot_name)
        else:
            logger.warning("Snapshot failed (continuing without safety net): %s", snap_stderr)

        # --- APPLY the fix ---
        logger.info("APPLYING fix to VM...")
        apply_result = await ssh_apply(
            config.vm, fix_script, revert_script, selected_title
        )
        logger.info("Apply result: %s", apply_result[:200])

        # --- AUDITOR: healthcheck ---
        logger.info("AUDITOR: running healthcheck...")
        health = await mission_healthcheck(config.vm)
        logger.info("Healthcheck: %s", health)

        auditor_prompt = (
            f"A STIG fix was just applied to the target system.\n"
            f"Rule: {selected_rule} ({selected_title})\n\n"
            f"Healthcheck result: {health}\n\n"
            f"Fix applied: {apply_result[:200]}\n\n"
            f"Is the mission app healthy? Should we keep this fix or revert?"
        )

        auditor_response = await _chat(
            auditor_client,
            config.auditor_model,
            AUDITOR_INSTRUCTION,
            auditor_prompt,
            max_tokens=256,
        )
        logger.info("AUDITOR response: %s", auditor_response[:200])

        # --- Decision: keep or revert ---
        reverted = False
        success = False

        # A fix should be reverted if ANY of these are true:
        #   1. The apply itself failed (APPLY_FAILED) — even if health is OK,
        #      the fix didn't work and may have left partial damage
        #   2. The healthcheck says UNHEALTHY — the fix broke the mission app
        #   3. The Auditor says AUDIT_FAIL
        apply_failed = "APPLY_FAILED" in apply_result
        health_failed = "UNHEALTHY" in health
        auditor_failed = "AUDIT_FAIL" in auditor_response.upper()
        should_revert = apply_failed or health_failed or auditor_failed

        if should_revert:
            # === THE RALPH MOMENT: Fail → Revert → Retry ===
            reason = []
            if apply_failed:
                reason.append("fix script failed")
            if health_failed:
                reason.append("mission app broken")
            if auditor_failed:
                reason.append("auditor rejected")

            logger.warning(
                ">>> REVERTING FIX — %s <<<", " + ".join(reason)
            )

            # Try script-based revert first (preferred — agent-driven)
            revert_result = await ssh_revert(config.vm)
            logger.info("Script revert result: %s", revert_result)
            reverted = True

            # Verify revert restored health
            health_after = await mission_healthcheck(config.vm)
            logger.info("Health after script revert: %s", health_after)

            if "HEALTHY" not in health_after and has_snapshot:
                # Script-based revert didn't fully restore — fall back to snapshot
                logger.warning(
                    "Script revert insufficient — falling back to snapshot '%s'",
                    snapshot_name,
                )
                await _run_ssh_local(
                    f"sudo virsh snapshot-revert gemma-forge-mission-app {snapshot_name}"
                )
                # Wait for VM to come back from snapshot revert
                await asyncio.sleep(10)
                health_after = await mission_healthcheck(config.vm)
                logger.info("Health after snapshot revert: %s", health_after)

            if "HEALTHY" not in health_after:
                logger.error("!!! MISSION APP STILL BROKEN AFTER REVERT !!!")
                logger.error("Manual intervention required.")
                break

            # Record this failed approach so Architect doesn't repeat it
            if selected_rule not in failed_approaches:
                failed_approaches[selected_rule] = []
            failed_approaches[selected_rule].append(
                f"{selected_title}: {fix_script[:100]}..."
            )

        else:
            # Fix succeeded — remove this rule from the failing list
            logger.info(">>> FIX ACCEPTED — rule %s remediated <<<", selected_rule)
            success = True
            failing_rules = [
                (rid, t) for rid, t in failing_rules if rid != selected_rule
            ]
            rules_attempted += 1

        # Clean up the pre-iteration snapshot (don't accumulate)
        if has_snapshot:
            await _run_ssh_local(
                f"sudo virsh snapshot-delete gemma-forge-mission-app {snapshot_name}"
            )
            logger.info("Cleaned up snapshot '%s'", snapshot_name)

        records.append(RemediationRecord(
            rule_id=selected_rule,
            rule_title=selected_title,
            iteration=iteration,
            fix_script=fix_script,
            revert_script=revert_script,
            apply_result=apply_result,
            healthcheck_result=health,
            reverted=reverted,
            success=success,
        ))

    # --- Summary ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("RALPH LOOP — COMPLETE")
    logger.info("=" * 60)
    successes = sum(1 for r in records if r.success)
    reverts = sum(1 for r in records if r.reverted)
    logger.info("Total iterations: %d", len(records))
    logger.info("Successful fixes: %d", successes)
    logger.info("Reverted fixes:   %d", reverts)
    logger.info("Rules remaining:  %d", len(failing_rules))
    logger.info("")

    for r in records:
        status = "✓ PASS" if r.success else "✗ REVERTED"
        logger.info("  [%s] %s: %s", status, r.rule_id, r.rule_title)

    return records


def main() -> int:
    """CLI entry point for running the Ralph loop."""
    import argparse

    parser = argparse.ArgumentParser(description="GemmaForge Ralph Loop")
    parser.add_argument(
        "--config",
        default="config/harness.yaml",
        help="Path to harness config YAML",
    )
    parser.add_argument(
        "--max-rules",
        type=int,
        default=None,
        help="Override max_rules_per_run from config",
    )
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    else:
        raw = {}

    loop_cfg = raw.get("loop", {})
    vm_cfg = raw.get("vm", {})
    stig_cfg = raw.get("stig", {})

    # Load model endpoints
    models_path = Path("config/models.yaml")
    if models_path.exists():
        with open(models_path) as f:
            models = yaml.safe_load(f)
    else:
        models = {}

    config = LoopConfig(
        max_iterations=loop_cfg.get("max_iterations", 20),
        max_rules_per_run=args.max_rules or loop_cfg.get("max_rules_per_run", 5),
        vm=SSHConfig(
            host=vm_cfg.get("ip", "192.168.122.43"),
            user=vm_cfg.get("user", "adm-forge"),
            key_path=vm_cfg.get("ssh_key", "/data/vm/gemma-forge/keys/adm-forge"),
        ),
        stig_profile=stig_cfg.get("profile", "xccdf_org.ssgproject.content_profile_stig"),
        stig_datastream=stig_cfg.get("datastream", "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml"),
        architect_url=models.get("architect", {}).get("endpoint", "http://localhost:8050/v1"),
        architect_model=models.get("architect", {}).get("model", "/weights/Gemma-4-31B-IT-NVFP4"),
        worker_url=models.get("worker", {}).get("endpoint", "http://localhost:8050/v1"),
        worker_model=models.get("worker", {}).get("model", "/weights/Gemma-4-31B-IT-NVFP4"),
        auditor_url=models.get("auditor", {}).get("endpoint", "http://localhost:8060/v1"),
        auditor_model=models.get("auditor", {}).get("model", "/weights/gemma-4-E4B-it"),
    )

    records = asyncio.run(run_ralph_loop(config))
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
