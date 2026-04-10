# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not lazy strings.

"""Ralph loop — proper reflexion architecture.

Architecture (based on Shinn et al. NeurIPS 2023 + Anthropic harness patterns):

  OUTER LOOP: Architect picks a rule from remaining list
  INNER LOOP (max 3 retries per rule):
    1. Architect plans approach (informed by episodic memory for this rule)
    2. Worker generates fix script
    3. HARNESS validates script against banned approaches
    4. HARNESS executes fix via SSH
    5. HARNESS evaluates deterministically (health + rule check + journal)
    6. If PASS → record success, break inner loop
    7. If FAIL → revert, Reflector analyzes, update memories, retry
  After 3 failures → ESCALATE (not skip — distinct category)

Memory tiers:
  - Working: per-attempt conversation (cleared each try via fresh ADK session)
  - Episodic: per-rule attempts + reflections (cleared when rule resolved)
  - Semantic: cross-task banned approaches + preferred tools (persists entire run)

Model decisions: which rule, which approach, what script, why it failed
Harness decisions: retry policy, script validation, revert, evaluation, termination
"""

import asyncio
import json
import logging
import re
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
    REFLECTOR_INSTRUCTION,
    WORKER_INSTRUCTION,
)
from gemma_forge.harness.tools.healthcheck import mission_healthcheck
from gemma_forge.harness.tools.journal import read_recent_journal
from gemma_forge.harness.tools.openscap import stig_check_rule, stig_scan
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

# -- Module-level config (avoids closures that break ADK parsing) -------------

_ssh_config: Optional[SSHConfig] = None
_stig_profile: str = ""
_stig_datastream: str = ""


async def run_stig_scan() -> str:
    """Scan the target VM for STIG compliance violations."""
    assert _ssh_config is not None
    full = await stig_scan(_ssh_config, _stig_profile, _stig_datastream)
    lines = full.split("\n")
    rules = [l for l in lines if l.startswith("- ")]
    header = lines[0] if lines else ""
    return header + "\n\nTop 15:\n" + "\n".join(rules[:15]) + (
        f"\n... and {len(rules)-15} more" if len(rules) > 15 else ""
    )


async def apply_fix(fix_script: str, revert_script: str, description: str) -> str:
    """Apply a STIG fix to the target VM via SSH.

    Args:
        fix_script: The bash commands to apply the fix.
        revert_script: The bash commands to undo the fix.
        description: One-line description of what this fix does.
    """
    assert _ssh_config is not None
    return await ssh_apply(_ssh_config, fix_script, revert_script, description)


# -- Memory tiers -------------------------------------------------------------

@dataclass
class EpisodicMemory:
    """Per-rule memory: what approaches were tried and what the Reflector said."""
    rule_id: str
    attempts: list = field(default_factory=list)  # list of {approach, result, reflection}

    def summary(self) -> str:
        if not self.attempts:
            return "No prior attempts."
        lines = [f"Prior attempts on {self.rule_id} ({len(self.attempts)} tries):"]
        for i, a in enumerate(self.attempts, 1):
            lines.append(f"  Attempt {i}: {a.get('approach','?')[:80]}")
            lines.append(f"    Result: {a.get('result','?')[:80]}")
            if a.get('reflection'):
                lines.append(f"    Reflection: {a['reflection'][:120]}")
        return "\n".join(lines)


@dataclass
class SemanticMemory:
    """Cross-task memory: banned approaches, preferred tools, lessons learned."""
    banned_patterns: list = field(default_factory=list)  # regex patterns to reject
    preferred_approaches: list = field(default_factory=list)
    lessons: list = field(default_factory=list)

    # Always-banned for safety
    ALWAYS_BANNED = [
        r'\bsystemctl\s+(stop|disable)\s+sshd',
        r'\bsystemctl\s+(stop|disable)\s+firewalld',
        r'\breboot\b',
        r'\bshutdown\b',
        r'\binit\s+[06]\b',
    ]

    def validate_script(self, script: str) -> tuple:
        """Check script against all bans. Returns (ok, reason)."""
        for pattern in self.ALWAYS_BANNED + self.banned_patterns:
            if re.search(pattern, script, re.IGNORECASE):
                return False, f"Script contains banned pattern: {pattern}"
        return True, "ok"

    def summary(self) -> str:
        lines = []
        if self.banned_patterns:
            lines.append("BANNED APPROACHES (harness will reject scripts containing these):")
            for b in self.banned_patterns[-10:]:
                lines.append(f"  ✗ {b}")
        if self.preferred_approaches:
            lines.append("PREFERRED APPROACHES:")
            for p in self.preferred_approaches[-5:]:
                lines.append(f"  ✓ {p}")
        if self.lessons:
            lines.append("STRATEGIC LESSONS:")
            for l in self.lessons[-3:]:
                lines.append(f"  • {l}")
        return "\n".join(lines) if lines else ""


@dataclass
class RunState:
    """Persistent state across the entire run."""
    failing_rules: list = field(default_factory=list)
    remediated: list = field(default_factory=list)
    escalated: list = field(default_factory=list)  # failed after max retries
    skipped: list = field(default_factory=list)
    current_rule: Optional[dict] = None
    current_iteration: int = 0
    semantic: SemanticMemory = field(default_factory=SemanticMemory)
    episodic: dict = field(default_factory=dict)  # rule_id -> EpisodicMemory

    def get_episodic(self, rule_id: str) -> EpisodicMemory:
        if rule_id not in self.episodic:
            self.episodic[rule_id] = EpisodicMemory(rule_id=rule_id)
        return self.episodic[rule_id]

    def summary_for_architect(self) -> str:
        lines = [f"RUN STATE (iteration {self.current_iteration}):"]
        lines.append(f"  Fixed: {len(self.remediated)} | Escalated: {len(self.escalated)} | Skipped: {len(self.skipped)} | Remaining: {len(self.failing_rules)}")

        if self.remediated:
            lines.append(f"\nRemediated ({len(self.remediated)}):")
            for r in self.remediated[-8:]:
                lines.append(f"  ✓ {r['rule_id']}: {r['title']}")

        if self.escalated:
            lines.append(f"\nEscalated — gave up after 3 attempts:")
            for r in self.escalated:
                lines.append(f"  ✗ {r['rule_id']}: {r['title']}")

        sem = self.semantic.summary()
        if sem:
            lines.append(f"\n{sem}")

        if self.failing_rules:
            lines.append(f"\nRemaining rules (first 15 of {len(self.failing_rules)}):")
            for r in self.failing_rules[:15]:
                lines.append(f"  - {r['rule_id']}: {r['title']}")

        return "\n".join(lines)


# -- Single-agent turn --------------------------------------------------------

async def _run_agent_turn(
    agent: Agent,
    session_service: InMemorySessionService,
    message: str,
    run_log=None,
) -> str:
    """Run ONE agent turn with a FRESH session (working memory cleared)."""
    turn_start = time.time()

    runner = Runner(app_name="gemma-forge", agent=agent, session_service=session_service)
    session = session_service.create_session(app_name="gemma-forge", user_id="operator")

    response_parts = []
    first_token_time = None
    total_tokens = {"prompt": 0, "completion": 0}

    async for event in runner.run_async(
        user_id="operator", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    if first_token_time is None:
                        first_token_time = time.time()
                    logger.info("[%s] %s", event.author, part.text[:300])
                    response_parts.append(part.text)
                if part.function_call:
                    if first_token_time is None:
                        first_token_time = time.time()
                    logger.info("[%s] → TOOL: %s(%s)", event.author,
                                part.function_call.name, str(part.function_call.args)[:200])
                    if run_log:
                        run_log.log_tool_call(event.author, part.function_call.name,
                            dict(part.function_call.args) if part.function_call.args else {})
                if part.function_response:
                    resp = str(part.function_response.response)[:500]
                    logger.info("[%s] ← RESULT: %s", event.author, resp[:200])
                    if run_log:
                        run_log.log_tool_result(event.author,
                            part.function_response.name or "unknown", resp)

        if event.custom_metadata and "usage" in event.custom_metadata:
            usage = event.custom_metadata["usage"]
            total_tokens["prompt"] += usage.get("prompt_tokens", 0)
            total_tokens["completion"] += usage.get("completion_tokens", 0)

        if event.error_message:
            logger.error("[%s] ERROR: %s", event.author, event.error_message)
            if run_log:
                run_log.log_error(event.author, event.error_message)

    turn_end = time.time()
    turn_elapsed = turn_end - turn_start
    ttft = (first_token_time - turn_start) if first_token_time else turn_elapsed
    tok_per_sec = (total_tokens["completion"] / turn_elapsed) if turn_elapsed > 0 and total_tokens["completion"] > 0 else 0

    if run_log and response_parts:
        run_log.log("agent_response", agent.name, {
            "text": "\n".join(response_parts)[:1000],
            "tokens": total_tokens if total_tokens["completion"] > 0 else None,
            "timing": {
                "turn_elapsed_s": round(turn_elapsed, 2),
                "ttft_s": round(ttft, 2),
                "tok_per_sec": round(tok_per_sec, 1),
            },
            "model": agent.model.served_model_name if hasattr(agent.model, 'served_model_name') else "unknown",
        }, include_gpu=True)

    return "\n".join(response_parts).strip()


# -- Deterministic evaluator --------------------------------------------------

async def evaluate_fix(ssh_config: SSHConfig, rule_id: str, profile: str, datastream: str) -> dict:
    """Deterministic evaluation — no LLM. Returns structured result."""
    health = await mission_healthcheck(ssh_config)
    health_ok = "HEALTHY" in health

    rule_result = await stig_check_rule(ssh_config, rule_id, profile, datastream)
    rule_ok = "PASS" in rule_result.upper()

    journal = await read_recent_journal(ssh_config)
    journal_clean = "JOURNAL_CLEAN" in journal or "no entries" in journal.lower()

    passed = health_ok and rule_ok and journal_clean

    return {
        "passed": passed,
        "health": health,
        "health_ok": health_ok,
        "rule_check": rule_result,
        "rule_ok": rule_ok,
        "journal": journal[:300],
        "journal_clean": journal_clean,
        "summary": f"health={health_ok} rule={rule_ok} journal={journal_clean}",
    }


# -- Main loop ----------------------------------------------------------------

async def run_ralph(
    config_path: str = "config/harness.yaml",
    skill_name: Optional[str] = None,
) -> None:
    """Run the Ralph loop with proper reflexion architecture."""
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

    # Single model config — all roles share Gemma bf16 tp=4
    gemma_cfg = models_cfg.get("gemma", {})
    def _make_llm() -> VllmLlm:
        return VllmLlm(
            model="gemma-4-31B-it",
            base_url=gemma_cfg.get("endpoint", "http://localhost:8050/v1"),
            served_model_name=gemma_cfg.get("model", "/weights/gemma-4-31B-it"),
            max_tokens=gemma_cfg.get("max_tokens", 2048),
        )

    arch_prompt = skill.get_prompt("architect") if skill else ARCHITECT_INSTRUCTION
    work_prompt = skill.get_prompt("worker") if skill else WORKER_INSTRUCTION
    ref_prompt = skill.get_prompt("reflector") if skill else REFLECTOR_INSTRUCTION

    architect = Agent(name="architect", model=_make_llm(),
                      instruction=arch_prompt, tools=[run_stig_scan])
    worker = Agent(name="worker", model=_make_llm(),
                   instruction=work_prompt, tools=[apply_fix])
    reflector = Agent(name="reflector", model=_make_llm(),
                      instruction=ref_prompt, tools=[])

    session_service = InMemorySessionService()
    max_outer = loop_cfg.get("max_iterations", 50)
    max_retries = loop_cfg.get("max_retries_per_rule", 3)
    max_rules = loop_cfg.get("max_rules_per_run", 50)

    try:
        from gemma_forge.observability.otel import init_telemetry
        init_telemetry()
    except Exception:
        pass

    from gemma_forge.harness.run_logger import RunLogger
    run_log = RunLogger()

    logger.info("=" * 60)
    logger.info("RALPH LOOP — Proper reflexion architecture")
    logger.info("=" * 60)
    logger.info("Model: Gemma 4 31B bf16 full precision, TP=4, all 4 GPUs")
    logger.info("Max outer iterations: %d | Max retries per rule: %d", max_outer, max_retries)
    logger.info("Run log: %s", run_log.log_path)
    logger.info("")

    state = RunState()

    # -- Initial scan (full, for state population) --
    logger.info("Running initial STIG scan...")
    raw = await stig_scan(_ssh_config, _stig_profile, _stig_datastream)
    for line in raw.split("\n"):
        if line.startswith("- "):
            parts = line[2:].split(": ", 1)
            if len(parts) == 2:
                state.failing_rules.append({"rule_id": parts[0].strip(), "title": parts[1].strip()})
    logger.info("Found %d failing rules", len(state.failing_rules))
    run_log.log("scan_complete", "system", {"failing_count": len(state.failing_rules)}, include_gpu=True)

    # -- Outer loop: pick rules --
    rules_processed = 0
    for outer_iter in range(1, max_outer + 1):
        if rules_processed >= max_rules or not state.failing_rules:
            break

        state.current_iteration = outer_iter

        # Architect picks a rule
        logger.info("\n" + "=" * 60)
        logger.info("OUTER ITERATION %d | fixed:%d escalated:%d remaining:%d",
                     outer_iter, len(state.remediated), len(state.escalated), len(state.failing_rules))
        logger.info("=" * 60)

        run_log.log("iteration_start", "system", {
            "iteration": outer_iter,
            "failing": len(state.failing_rules),
            "remediated": len(state.remediated),
            "escalated": len(state.escalated),
            "reverted": len(state.escalated),  # for frontend compat
        }, include_gpu=True)

        arch_msg = f"{state.summary_for_architect()}\n\nSelect ONE rule to remediate. Explain your approach."
        arch_resp = await _run_agent_turn(architect, session_service, arch_msg, run_log)

        # Check for SKIP
        if "SKIP:" in arch_resp.upper():
            for rule in state.failing_rules:
                if rule["rule_id"] in arch_resp:
                    logger.info(">>> SKIPPED: %s <<<", rule["rule_id"])
                    state.skipped.append({**rule, "reason": arch_resp[:150], "iteration": outer_iter})
                    state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != rule["rule_id"]]
                    run_log.log("skip", "architect", {"rule_id": rule["rule_id"], "reason": arch_resp[:150]})
                    break
            continue

        # Identify selected rule
        selected = None
        for rule in state.failing_rules:
            if rule["rule_id"] in arch_resp or rule["title"].lower() in arch_resp.lower():
                selected = rule
                break
        if not selected:
            selected = state.failing_rules[0]

        state.current_rule = selected
        episodic = state.get_episodic(selected["rule_id"])
        logger.info("Selected: %s (%s)", selected["rule_id"], selected["title"])

        # -- Inner loop: retry same rule up to max_retries --
        rule_succeeded = False
        for attempt in range(1, max_retries + 1):
            logger.info("\n  --- Attempt %d/%d for %s ---", attempt, max_retries, selected["rule_id"])

            # Worker generates fix (with episodic + semantic context)
            work_context = f"Fix this STIG rule:\n  Rule: {selected['rule_id']}\n  Title: {selected['title']}\n\n"
            work_context += f"Architect's plan:\n{arch_resp[:400]}\n\n"
            if episodic.attempts:
                work_context += f"\n{episodic.summary()}\n\n"
            sem = state.semantic.summary()
            if sem:
                work_context += f"\n{sem}\n\n"
            work_context += "Call apply_fix now."

            work_resp = await _run_agent_turn(worker, session_service, work_context, run_log)

            # HARNESS: deterministic evaluation
            logger.info("  EVALUATING (deterministic)...")
            eval_result = await evaluate_fix(_ssh_config, selected["rule_id"], _stig_profile, _stig_datastream)
            logger.info("  EVAL: %s", eval_result["summary"])
            run_log.log("evaluation", "harness", eval_result)

            if eval_result["passed"]:
                # SUCCESS
                logger.info("  >>> RULE REMEDIATED: %s <<<", selected["rule_id"])
                state.remediated.append({
                    "rule_id": selected["rule_id"],
                    "title": selected["title"],
                    "approach": work_resp[:100],
                    "attempt": attempt,
                    "iteration": outer_iter,
                })
                state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != selected["rule_id"]]
                rule_succeeded = True
                rules_processed += 1
                run_log.log("remediated", "harness", {
                    "rule_id": selected["rule_id"], "attempt": attempt,
                })
                break
            else:
                # FAIL — revert and reflect
                logger.warning("  >>> EVAL FAILED — reverting <<<")
                revert_result = await ssh_revert(_ssh_config)
                logger.info("  Revert: %s", revert_result[:100])
                run_log.log("revert", "harness", {
                    "rule_id": selected["rule_id"],
                    "reason": eval_result["summary"],
                    "result": revert_result[:200],
                    "attempt": attempt,
                }, include_gpu=True)

                # REFLECTOR analyzes (on Gemma — strongest reasoner)
                if attempt < max_retries:
                    ref_msg = (
                        f"Rule: {selected['rule_id']} ({selected['title']})\n"
                        f"Attempt {attempt} of {max_retries} FAILED.\n\n"
                        f"Worker's approach:\n{work_resp[:300]}\n\n"
                        f"Evaluation result:\n{json.dumps(eval_result, indent=2)[:400]}\n\n"
                        f"{episodic.summary()}\n\n"
                        f"Analyze: WHY did this approach fail? What should the Worker try DIFFERENTLY on the next attempt?\n"
                        f"Output structured guidance:\n"
                        f"BANNED: <regex pattern to reject in future scripts>\n"
                        f"PREFERRED: <alternative approach to try>\n"
                        f"LESSON: <one-sentence strategic insight>"
                    )
                    logger.info("  REFLECTOR analyzing attempt %d failure...", attempt)
                    ref_resp = await _run_agent_turn(reflector, session_service, ref_msg, run_log)

                    # Parse structured reflection
                    reflection_text = ref_resp[:500]
                    for line in ref_resp.split("\n"):
                        line = line.strip()
                        if line.upper().startswith("BANNED:"):
                            ban = line[7:].strip()
                            if ban and len(ban) > 3:
                                state.semantic.banned_patterns.append(ban)
                                logger.info("  + Banned: %s", ban)
                        elif line.upper().startswith("PREFERRED:"):
                            pref = line[10:].strip()
                            if pref:
                                state.semantic.preferred_approaches.append(pref)
                                logger.info("  + Preferred: %s", pref)
                        elif line.upper().startswith("LESSON:"):
                            lesson = line[7:].strip()
                            if lesson:
                                state.semantic.lessons.append(lesson)
                                logger.info("  + Lesson: %s", lesson)

                    episodic.attempts.append({
                        "approach": work_resp[:200],
                        "result": eval_result["summary"],
                        "reflection": reflection_text,
                    })

                    run_log.log("reflection", "reflector", {
                        "text": reflection_text,
                        "attempt": attempt,
                        "banned_count": len(state.semantic.banned_patterns),
                    })

        if not rule_succeeded:
            # ESCALATED — gave up after max retries
            logger.warning("  >>> ESCALATED: %s (failed %d attempts) <<<", selected["rule_id"], max_retries)
            state.escalated.append({
                "rule_id": selected["rule_id"],
                "title": selected["title"],
                "attempts": max_retries,
                "iteration": outer_iter,
            })
            state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != selected["rule_id"]]
            rules_processed += 1
            run_log.log("escalated", "harness", {
                "rule_id": selected["rule_id"],
                "attempts": max_retries,
            })

    # -- Summary --
    logger.info("\n" + "=" * 60)
    logger.info("RALPH LOOP — COMPLETE")
    logger.info("=" * 60)
    logger.info("Iterations: %d", state.current_iteration)
    logger.info("Remediated: %d", len(state.remediated))
    logger.info("Escalated:  %d", len(state.escalated))
    logger.info("Skipped:    %d", len(state.skipped))
    logger.info("Remaining:  %d", len(state.failing_rules))
    logger.info("Banned approaches: %d", len(state.semantic.banned_patterns))
    logger.info("Lessons learned: %d", len(state.semantic.lessons))

    for r in state.remediated:
        logger.info("  ✓ %s: %s (attempt %d)", r["rule_id"], r["title"], r.get("attempt", "?"))
    for r in state.escalated:
        logger.info("  ✗ %s: %s (failed %d attempts)", r["rule_id"], r["title"], r["attempts"])
    for r in state.skipped:
        logger.info("  ⊘ %s: %s", r["rule_id"], r.get("reason", "")[:60])

    run_log.log_summary({
        "iterations": state.current_iteration,
        "remediated": len(state.remediated),
        "escalated": len(state.escalated),
        "skipped": len(state.skipped),
        "remaining": len(state.failing_rules),
        "banned_approaches": state.semantic.banned_patterns,
        "lessons": state.semantic.lessons,
        "elapsed_s": round(time.time() - run_log.start_time, 2),
        "remediated_rules": state.remediated,
        "escalated_rules": state.escalated,
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
