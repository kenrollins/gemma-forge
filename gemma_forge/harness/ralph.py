# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser inspects parameter annotations as type objects
# (e.g., `str`, `int`). The __future__ import makes them lazy strings
# (e.g., `'str'`), which ADK cannot parse.

"""Ralph loop — stateful agent-driven STIG remediation.

Architecture:
  - Python loop owns iteration control and STATE management
  - ADK Agent + Runner owns each individual agent turn (tool calling)
  - Each iteration starts with a FRESH conversation + compact state summary
  - State persists across iterations as a structured dict, NOT as conversation

This is the correct Ralph loop design: working memory (conversation) is
per-iteration; persistent state (what's been tried, what worked) survives
across iterations in a compact form.
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from google.adk.agents.llm_agent import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from gemma_forge.harness.agents import (
    ARCHITECT_INSTRUCTION,
    AUDITOR_INSTRUCTION,
    WORKER_INSTRUCTION,
)
from gemma_forge.harness.tools.healthcheck import mission_healthcheck
from gemma_forge.harness.tools.openscap import stig_scan
from gemma_forge.harness.tools.ssh import SSHConfig, ssh_apply, ssh_revert
from gemma_forge.models.vllm_llm import VllmLlm
from gemma_forge.skills.base import Skill
from gemma_forge.skills.loader import load_skill

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# -- Module-level SSH config (avoids closures that break ADK parsing) ---------

_ssh_config: Optional[SSHConfig] = None
_stig_profile: str = ""
_stig_datastream: str = ""


async def run_stig_scan() -> str:
    """Scan the target VM for STIG compliance violations.
    Returns a list of failing rules with their IDs and titles."""
    assert _ssh_config is not None
    return await stig_scan(_ssh_config, _stig_profile, _stig_datastream)


async def apply_fix(fix_script: str, revert_script: str, description: str) -> str:
    """Apply a STIG fix to the target VM via SSH.

    Args:
        fix_script: The bash commands to apply the fix.
        revert_script: The bash commands to undo the fix.
        description: One-line description of what this fix does.
    """
    assert _ssh_config is not None
    return await ssh_apply(_ssh_config, fix_script, revert_script, description)


async def check_health() -> str:
    """Check if the mission app (nginx + postgres + sshd) is still healthy.
    Returns HEALTHY or UNHEALTHY with details."""
    assert _ssh_config is not None
    return await mission_healthcheck(_ssh_config)


async def revert_last_fix() -> str:
    """Revert the most recently applied fix."""
    assert _ssh_config is not None
    return await ssh_revert(_ssh_config)


TOOL_REGISTRY = {
    "run_stig_scan": run_stig_scan,
    "apply_fix": apply_fix,
    "check_health": check_health,
    "revert_last_fix": revert_last_fix,
}


# -- Persistent state ---------------------------------------------------------

@dataclass
class RunState:
    """Persistent state that survives across iterations.

    This is the 'checklist' — NOT conversation history. Each iteration
    starts with a fresh conversation but reads from this state.
    """
    failing_rules: list = field(default_factory=list)
    remediated: list = field(default_factory=list)
    reverted: list = field(default_factory=list)
    current_iteration: int = 0

    def summary_for_architect(self) -> str:
        """Compact state summary injected into the Architect's fresh context."""
        lines = [f"ITERATION {self.current_iteration} STATE:"]

        if self.remediated:
            lines.append(f"\nFixed ({len(self.remediated)}):")
            for r in self.remediated:
                lines.append(f"  ✓ {r['rule_id']}: {r['title']}")

        if self.reverted:
            lines.append(f"\nFailed — DO NOT retry these approaches:")
            for r in self.reverted:
                lines.append(f"  ✗ {r['rule_id']}: {r['title']} — {r['reason']}")

        if self.failing_rules:
            lines.append(f"\nRemaining ({len(self.failing_rules)}, first 15):")
            for r in self.failing_rules[:15]:
                lines.append(f"  - {r['rule_id']}: {r['title']}")
            if len(self.failing_rules) > 15:
                lines.append(f"  ... and {len(self.failing_rules) - 15} more")

        return "\n".join(lines)


# -- Single-agent turn --------------------------------------------------------

async def _run_agent_turn(
    agent: Agent,
    session_service: InMemorySessionService,
    message: str,
    run_log=None,
) -> str:
    """Run ONE agent turn with a FRESH session. Returns text response."""
    runner = Runner(
        app_name="gemma-forge",
        agent=agent,
        session_service=session_service,
    )
    session = session_service.create_session(
        app_name="gemma-forge",
        user_id="operator",
    )

    response_parts = []

    async for event in runner.run_async(
        user_id="operator",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=message)],
        ),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    logger.info("[%s] %s", event.author, part.text[:300])
                    response_parts.append(part.text)
                    if run_log:
                        run_log.log_agent_response(event.author, part.text)

                if part.function_call:
                    logger.info("[%s] → TOOL: %s(%s)", event.author,
                                part.function_call.name,
                                str(part.function_call.args)[:200])
                    if run_log:
                        run_log.log_tool_call(
                            event.author, part.function_call.name,
                            dict(part.function_call.args) if part.function_call.args else {},
                        )

                if part.function_response:
                    resp = str(part.function_response.response)[:500]
                    logger.info("[%s] ← RESULT: %s", event.author, resp[:200])
                    if run_log:
                        run_log.log_tool_result(
                            event.author,
                            part.function_response.name or "unknown",
                            resp,
                        )

        if event.error_message:
            logger.error("[%s] ERROR: %s", event.author, event.error_message)
            if run_log:
                run_log.log_error(event.author, event.error_message)

    return "\n".join(response_parts).strip()


# -- Main loop ----------------------------------------------------------------

async def run_ralph(
    config_path: str = "config/harness.yaml",
    skill_name: Optional[str] = None,
) -> None:
    """Run the Ralph loop with proper state management.

    Each iteration:
      1. Architect gets FRESH session + compact state summary → picks a rule
      2. Worker gets FRESH session + Architect's plan → calls apply_fix
      3. Auditor gets FRESH session + apply result → calls check_health
      4. State updated; conversation discarded
    """
    global _ssh_config, _stig_profile, _stig_datastream

    harness_cfg = {}
    if Path(config_path).exists():
        with open(config_path) as f:
            harness_cfg = yaml.safe_load(f) or {}

    models_cfg = {}
    if Path("config/models.yaml").exists():
        with open("config/models.yaml") as f:
            models_cfg = yaml.safe_load(f) or {}

    vm_cfg = harness_cfg.get("vm", {})
    loop_cfg = harness_cfg.get("loop", {})
    stig_cfg = harness_cfg.get("stig", {})

    _ssh_config = SSHConfig(
        host=vm_cfg.get("ip", "192.168.122.43"),
        user=vm_cfg.get("user", "adm-forge"),
        key_path=vm_cfg.get("ssh_key", "/data/vm/gemma-forge/keys/adm-forge"),
    )

    skill = None
    if skill_name:
        skill = load_skill(skill_name)
        logger.info("Loaded skill: %s", skill.name)
        if skill.manifest.stig:
            stig_cfg = {
                "profile": skill.manifest.stig.profile,
                "datastream": skill.manifest.stig.datastream,
            }

    _stig_profile = stig_cfg.get("profile", "xccdf_org.ssgproject.content_profile_stig")
    _stig_datastream = stig_cfg.get("datastream", "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml")

    def _make_llm(role: str) -> VllmLlm:
        cfg = models_cfg.get(role, {})
        return VllmLlm(
            model=role,
            base_url=cfg.get("endpoint", "http://localhost:8050/v1"),
            served_model_name=cfg.get("model", ""),
            max_tokens=1024,
        )

    arch_prompt = skill.get_prompt("architect") if skill else ARCHITECT_INSTRUCTION
    work_prompt = skill.get_prompt("worker") if skill else WORKER_INSTRUCTION
    aud_prompt = skill.get_prompt("auditor") if skill else AUDITOR_INSTRUCTION

    architect = Agent(name="architect", model=_make_llm("architect"),
                      instruction=arch_prompt, tools=[run_stig_scan])
    worker = Agent(name="worker", model=_make_llm("worker"),
                   instruction=work_prompt, tools=[apply_fix])
    auditor = Agent(name="auditor", model=_make_llm("auditor"),
                    instruction=aud_prompt, tools=[check_health, revert_last_fix])

    session_service = InMemorySessionService()
    max_iters = loop_cfg.get("max_iterations", 10)
    max_rules = loop_cfg.get("max_rules_per_run", 5)

    try:
        from gemma_forge.observability.otel import init_telemetry
        init_telemetry()
    except Exception:
        pass

    from gemma_forge.harness.run_logger import RunLogger
    run_log = RunLogger()

    logger.info("=" * 60)
    logger.info("RALPH LOOP — Stateful remediation")
    logger.info("=" * 60)
    logger.info("Skill: %s", skill.name if skill else "hardcoded")
    logger.info("Max iterations: %d | Max rules: %d", max_iters, max_rules)
    logger.info("Run log: %s", run_log.log_path)

    state = RunState()

    # -- Initial scan --
    logger.info("\nRunning initial STIG scan...")
    raw_scan = await run_stig_scan()
    for line in raw_scan.split("\n"):
        if line.startswith("- "):
            parts = line[2:].split(": ", 1)
            if len(parts) == 2:
                state.failing_rules.append({
                    "rule_id": parts[0].strip(),
                    "title": parts[1].strip(),
                })
    logger.info("Found %d failing rules", len(state.failing_rules))
    run_log.log("scan_complete", "system", {
        "failing_count": len(state.failing_rules),
    }, include_gpu=True)

    # -- Remediation loop --
    for iteration in range(1, max_iters + 1):
        if len(state.remediated) >= max_rules:
            logger.info("Reached max_rules (%d)", max_rules)
            break
        if not state.failing_rules:
            logger.info("All rules remediated!")
            break

        state.current_iteration = iteration
        run_log.set_iteration(iteration)
        run_log.log("iteration_start", "system", {
            "iteration": iteration,
            "failing": len(state.failing_rules),
            "remediated": len(state.remediated),
            "reverted": len(state.reverted),
        }, include_gpu=True)

        logger.info("\n" + "-" * 60)
        logger.info("ITERATION %d | fixed:%d reverted:%d remaining:%d",
                     iteration, len(state.remediated), len(state.reverted),
                     len(state.failing_rules))
        logger.info("-" * 60)

        # -- ARCHITECT (fresh session) --
        arch_msg = (
            f"{state.summary_for_architect()}\n\n"
            f"Select ONE rule to remediate. Explain your plan briefly."
        )
        arch_resp = await _run_agent_turn(architect, session_service, arch_msg, run_log)

        selected = None
        for rule in state.failing_rules:
            if rule["rule_id"] in arch_resp or rule["title"].lower() in arch_resp.lower():
                selected = rule
                break
        if not selected:
            selected = state.failing_rules[0]
        logger.info("Selected: %s", selected["rule_id"])

        # -- WORKER (fresh session) --
        work_msg = (
            f"Fix this STIG rule:\n"
            f"  Rule: {selected['rule_id']}\n"
            f"  Title: {selected['title']}\n\n"
            f"Architect's plan:\n{arch_resp[:400]}\n\n"
            f"Call apply_fix now."
        )
        work_resp = await _run_agent_turn(worker, session_service, work_msg, run_log)

        # -- AUDITOR (fresh session) --
        aud_msg = (
            f"A fix was applied for: {selected['rule_id']} ({selected['title']})\n\n"
            f"Worker's report:\n{work_resp[:300]}\n\n"
            f"Call check_health now. If unhealthy or fix failed, call revert_last_fix."
        )
        aud_resp = await _run_agent_turn(auditor, session_service, aud_msg, run_log)

        # -- Update state --
        if "AUDIT_PASS" in aud_resp:
            logger.info(">>> FIX ACCEPTED <<<")
            state.remediated.append({
                "rule_id": selected["rule_id"],
                "title": selected["title"],
                "approach": work_resp[:100],
                "iteration": iteration,
            })
            state.failing_rules = [
                r for r in state.failing_rules if r["rule_id"] != selected["rule_id"]
            ]
        else:
            logger.warning(">>> FIX REJECTED / REVERTED <<<")
            state.reverted.append({
                "rule_id": selected["rule_id"],
                "title": selected["title"],
                "approach": work_resp[:100],
                "reason": aud_resp[:150],
                "iteration": iteration,
            })
            run_log.log_revert("auditor", aud_resp[:150], "state updated")

    # -- Summary --
    logger.info("\n" + "=" * 60)
    logger.info("RALPH LOOP — COMPLETE")
    logger.info("=" * 60)
    logger.info("Iterations: %d", state.current_iteration)
    logger.info("Remediated: %d", len(state.remediated))
    logger.info("Reverted:   %d", len(state.reverted))
    logger.info("Remaining:  %d", len(state.failing_rules))

    for r in state.remediated:
        logger.info("  ✓ %s: %s", r["rule_id"], r["title"])
    for r in state.reverted:
        logger.info("  ✗ %s: %s", r["rule_id"], r["title"])

    run_log.log_summary({
        "iterations": state.current_iteration,
        "remediated": len(state.remediated),
        "reverted": len(state.reverted),
        "remaining": len(state.failing_rules),
        "elapsed_s": round(time.time() - run_log.start_time, 2),
        "remediated_rules": state.remediated,
        "reverted_rules": state.reverted,
    })
    logger.info("Run log: %s", run_log.log_path)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="GemmaForge Ralph Loop")
    parser.add_argument("--config", default="config/harness.yaml")
    parser.add_argument("--skill", default="stig-rhel9")
    args = parser.parse_args()
    asyncio.run(run_ralph(args.config, skill_name=args.skill))
    return 0


if __name__ == "__main__":
    sys.exit(main())
