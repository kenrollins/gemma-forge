"""Ralph loop — ADK LoopAgent implementation with real tool calling.

This is the agent-driven Ralph loop where Gemma 4 models make the
decisions: which rule to fix, how to fix it, whether to revert. The
conversation history carries between iterations, so the Architect
can learn from the Auditor's revert explanations.

The loop uses ADK's LoopAgent with three sub-agents:
  - Architect (31B NVFP4): calls stig_scan, picks a rule, plans the fix
  - Worker (31B NVFP4): calls ssh_apply with fix + revert scripts
  - Auditor (E4B): calls healthcheck, decides whether to revert

Usage:
    python -m gemma_forge.harness.ralph
"""

# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser inspects parameter annotations as type objects
# (e.g., `str`, `int`). The __future__ import makes them lazy strings
# (e.g., `'str'`), which ADK cannot parse. This was the root cause of the
# "Failed to parse the parameter fix_script: 'str'" error during Phase 3.

import asyncio
import logging
import sys
import time
from pathlib import Path

import yaml
from google.adk.agents import LoopAgent
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


# Module-level SSH config — set by build_ralph_loop() before tools are used.
# This avoids closures which confuse ADK's function parameter parser.
from typing import Optional
_ssh_config: Optional[SSHConfig] = None
_stig_profile: str = ""
_stig_datastream: str = ""


async def run_stig_scan() -> str:
    """Scan the target VM for STIG compliance violations.
    Returns a list of failing rules with their IDs and titles.
    Run this at the start and after fixes to see what remains."""
    assert _ssh_config is not None
    return await stig_scan(_ssh_config, _stig_profile, _stig_datastream)


async def apply_fix(fix_script: str, revert_script: str, description: str) -> str:
    """Apply a STIG fix to the target VM via SSH.

    Args:
        fix_script: The bash commands to apply the fix. Always back up files first.
        revert_script: The bash commands to undo the fix. Must restore exact original state.
        description: One-line description of what this fix does.
    """
    assert _ssh_config is not None
    return await ssh_apply(_ssh_config, fix_script, revert_script, description)


async def check_health() -> str:
    """Check if the mission app (nginx + postgres + sshd) is still healthy.
    Returns HEALTHY or UNHEALTHY with details. Call this after every fix."""
    assert _ssh_config is not None
    return await mission_healthcheck(_ssh_config)


async def revert_last_fix() -> str:
    """Revert the most recently applied fix. Call this if the mission app
    is UNHEALTHY after a fix was applied. The revert script from the
    last apply_fix call will be executed."""
    assert _ssh_config is not None
    return await ssh_revert(_ssh_config)


# Map from tool names (in skill.yaml) to actual tool functions.
# Skills reference tools by name; this registry resolves them.
TOOL_REGISTRY = {
    "run_stig_scan": run_stig_scan,
    "apply_fix": apply_fix,
    "check_health": check_health,
    "revert_last_fix": revert_last_fix,
}


def build_ralph_loop(
    ssh_config: SSHConfig,
    stig_profile: str,
    stig_datastream: str,
    architect_llm: VllmLlm,
    worker_llm: VllmLlm,
    auditor_llm: VllmLlm,
    max_iterations: int = 10,
    skill: Optional[Skill] = None,
) -> LoopAgent:
    """Build the Ralph loop as an ADK LoopAgent with three sub-agents.

    If a skill is provided, prompts and tool assignments come from the
    skill manifest. Otherwise falls back to the hardcoded defaults in
    agents.py (for backwards compatibility during development).
    """

    # Set module-level config so the tool functions can access it
    global _ssh_config, _stig_profile, _stig_datastream
    _ssh_config = ssh_config
    _stig_profile = stig_profile
    _stig_datastream = stig_datastream

    # Resolve prompts — from skill or hardcoded fallback
    if skill:
        arch_prompt = skill.get_prompt("architect")
        work_prompt = skill.get_prompt("worker")
        aud_prompt = skill.get_prompt("auditor")
        arch_tools = [TOOL_REGISTRY[t] for t in skill.get_tools("architect")]
        work_tools = [TOOL_REGISTRY[t] for t in skill.get_tools("worker")]
        aud_tools = [TOOL_REGISTRY[t] for t in skill.get_tools("auditor")]
        logger.info("Using skill: %s", skill.name)
    else:
        arch_prompt = ARCHITECT_INSTRUCTION
        work_prompt = WORKER_INSTRUCTION
        aud_prompt = AUDITOR_INSTRUCTION
        arch_tools = [run_stig_scan]
        work_tools = [apply_fix]
        aud_tools = [check_health, revert_last_fix]
        logger.info("Using hardcoded prompts (no skill loaded)")

    architect = Agent(
        name="architect",
        model=architect_llm,
        instruction=arch_prompt,
        tools=arch_tools,
    )

    worker = Agent(
        name="worker",
        model=worker_llm,
        instruction=work_prompt,
        tools=work_tools,
    )

    auditor = Agent(
        name="auditor",
        model=auditor_llm,
        instruction=aud_prompt,
        tools=aud_tools,
    )

    loop = LoopAgent(
        name="ralph_loop",
        sub_agents=[architect, worker, auditor],
        max_iterations=max_iterations,
    )

    return loop


async def run_ralph(
    config_path: str = "config/harness.yaml",
    skill_name: Optional[str] = None,
) -> None:
    """Run the Ralph loop end-to-end.

    Args:
        config_path: Path to harness config YAML.
        skill_name: Name of the skill directory under skills/
                    (e.g., "stig-rhel9"). If None, uses hardcoded defaults.
    """

    # Load configs
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

    ssh_config = SSHConfig(
        host=vm_cfg.get("ip", "192.168.122.43"),
        user=vm_cfg.get("user", "adm-forge"),
        key_path=vm_cfg.get("ssh_key", "/data/vm/gemma-forge/keys/adm-forge"),
    )

    # Load skill if specified
    skill = None
    if skill_name:
        skill = load_skill(skill_name)
        logger.info("Loaded skill: %s — %s", skill.name, skill.description)
        # Override STIG config from skill manifest if available
        if skill.manifest.stig:
            stig_cfg = {
                "profile": skill.manifest.stig.profile,
                "datastream": skill.manifest.stig.datastream,
            }

    # Create LLM instances for each role
    def _make_llm(role: str) -> VllmLlm:
        cfg = models_cfg.get(role, {})
        return VllmLlm(
            model=role,
            base_url=cfg.get("endpoint", "http://localhost:8050/v1"),
            served_model_name=cfg.get("model", ""),
            max_tokens=1024,
        )

    architect_llm = _make_llm("architect")
    worker_llm = _make_llm("worker")
    auditor_llm = _make_llm("auditor")

    max_iters = loop_cfg.get("max_iterations", 10)

    ralph_loop = build_ralph_loop(
        ssh_config=ssh_config,
        stig_profile=stig_cfg.get("profile", "xccdf_org.ssgproject.content_profile_stig"),
        stig_datastream=stig_cfg.get("datastream", "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml"),
        architect_llm=architect_llm,
        worker_llm=worker_llm,
        auditor_llm=auditor_llm,
        max_iterations=max_iters,
        skill=skill,
    )

    # Initialize OpenTelemetry if the collector is reachable
    try:
        from gemma_forge.observability.otel import init_telemetry
        init_telemetry()
    except Exception as e:
        logger.warning("OTel initialization failed (traces disabled): %s", e)

    # Set up ADK runner
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="gemma-forge",
        agent=ralph_loop,
        session_service=session_service,
    )

    session = session_service.create_session(
        app_name="gemma-forge",
        user_id="operator",
    )

    logger.info("=" * 60)
    logger.info("RALPH LOOP — Agent-driven STIG remediation")
    logger.info("=" * 60)
    logger.info("Architect: %s", architect_llm.base_url)
    logger.info("Worker:    %s", worker_llm.base_url)
    logger.info("Auditor:   %s", auditor_llm.base_url)
    logger.info("Target VM: %s@%s", ssh_config.user, ssh_config.host)
    logger.info("Max iterations: %d", max_iters)
    logger.info("")

    # The initial message kicks off the loop. The Architect will call
    # stig_scan on its first turn to discover failing rules.
    initial_message = types.Content(
        role="user",
        parts=[types.Part(text=(
            "Begin STIG remediation of the target Rocky Linux 9 system. "
            "Start by running a STIG scan to identify failing rules. "
            "Then fix them one at a time, verifying the mission app health "
            "after each fix. If a fix breaks the mission app, revert it "
            "immediately and try a different approach. "
            "Focus on safe, low-risk rules first (package installations, "
            "configuration changes). Avoid FIPS mode and kernel changes."
        ))],
    )

    # Structured run logger for post-analysis and frontend history replay
    from gemma_forge.harness.run_logger import RunLogger
    run_log = RunLogger()
    logger.info("Run log: %s", run_log.log_path)

    iteration = 0
    successes = 0
    reverts = 0

    async for event in runner.run_async(
        user_id="operator",
        session_id=session.id,
        new_message=initial_message,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    logger.info("[%s] %s", event.author, part.text[:300])
                    run_log.log_agent_response(
                        event.author, part.text,
                        tokens=event.custom_metadata.get("usage") if event.custom_metadata else None,
                    )

                    # Track outcomes
                    if "AUDIT_PASS" in part.text:
                        successes += 1
                    if "AUDIT_FAIL" in part.text or "REVERT" in part.text.upper():
                        reverts += 1

                if part.function_call:
                    logger.info(
                        "[%s] → TOOL: %s(%s)",
                        event.author,
                        part.function_call.name,
                        str(part.function_call.args)[:200],
                    )
                    run_log.log_tool_call(
                        event.author,
                        part.function_call.name,
                        dict(part.function_call.args) if part.function_call.args else {},
                    )

                if part.function_response:
                    resp = str(part.function_response.response)[:500]
                    logger.info("[%s] ← RESULT: %s", event.author, resp[:200])
                    run_log.log_tool_result(
                        event.author,
                        part.function_response.name or "unknown",
                        resp,
                    )

        # Track iterations by watching for the architect's turn
        if event.author == "architect" and event.content:
            iteration += 1
            run_log.set_iteration(iteration)
            # Snapshot GPU state at each iteration boundary
            run_log.log("iteration_start", "system", {
                "iteration": iteration,
            }, include_gpu=True)
            logger.info("--- iteration %d ---", iteration)

        # Log errors
        if event.error_message:
            run_log.log_error(event.author, event.error_message)

    run_log.log_summary({
        "total_iterations": iteration,
        "successes": successes,
        "reverts": reverts,
        "elapsed_s": round(time.time() - run_log.start_time, 2),
        "skill": skill_name or "hardcoded",
    })

    logger.info("")
    logger.info("=" * 60)
    logger.info("RALPH LOOP — COMPLETE (%d iterations)", iteration)
    logger.info("Successes: %d | Reverts: %d", successes, reverts)
    logger.info("Run log: %s", run_log.log_path)
    logger.info("=" * 60)


def main() -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GemmaForge Ralph Loop (ADK)")
    parser.add_argument("--config", default="config/harness.yaml")
    parser.add_argument(
        "--skill",
        default="stig-rhel9",
        help="Skill directory name under skills/ (default: stig-rhel9)",
    )
    args = parser.parse_args()

    asyncio.run(run_ralph(args.config, skill_name=args.skill))
    return 0


if __name__ == "__main__":
    sys.exit(main())
