# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not lazy strings.

"""Ralph loop — skill-agnostic reflexion harness.

Architecture (based on Shinn et al. NeurIPS 2023 + Anthropic harness patterns):

  OUTER LOOP: Architect picks a work item from remaining list
  INNER LOOP (time-budgeted per item):
    1. Architect plans approach (informed by episodic memory)
    2. Worker generates fix/change
    3. HARNESS validates against banned approaches
    4. HARNESS executes via skill's Executor
    5. HARNESS evaluates via skill's Evaluator → EvalResult with FailureMode
    6. If PASS → checkpoint progress, break inner loop
    7. If FAIL → triage failure mode, revert via Checkpoint, reflect, retry
  Escalation: time_budget | retry_ceiling | architect_preemptive

The harness operates on five abstract interfaces (WorkQueue, Executor,
Evaluator, Checkpoint, WorkItem) — see interfaces.py. Skills implement
these for their domain. The harness never imports skill-specific modules.

Memory tiers:
  - Working: per-attempt conversation (cleared each try via fresh ADK session)
  - Episodic: per-item attempts + reflections (cleared when item resolved)
  - Semantic: cross-task banned approaches + preferred tools (persists entire run)

Model decisions: which item, which approach, what fix, why it failed
Harness decisions: retry policy, validation, revert, evaluation triage, termination
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
from gemma_forge.harness.interfaces import (
    EvalResult,
    FailureMode,
    SkillRuntime,
    WorkItem,
)
from gemma_forge.harness.task_graph import TaskGraph, NodeState
from gemma_forge.models.vllm_llm import VllmLlm
from gemma_forge.skills.base import Skill
from gemma_forge.skills.loader import load_skill

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# -- Context budget helpers ---------------------------------------------------
#
# See docs/whitepaper/improvements/03-context-budget-assembly.md for design.
# Rough 4-chars-per-token estimation is within ~20% for English/code and
# good enough for budget decisions. We don't need exact tokenization.

def est_tokens(text: str) -> int:
    """Rough token count estimate (4 chars/token). Within ~20% for English."""
    if not text:
        return 0
    return len(text) // 4 + 1


def assemble_prompt(
    sections: list[tuple[int, str, str]],
    budget_tokens: int,
) -> tuple[str, dict]:
    """Assemble a prompt from prioritized sections within a token budget.

    Args:
        sections: list of (priority, label, content). Lower priority = more essential;
                  included first. Higher priority numbers get dropped/truncated when tight.
        budget_tokens: max estimated tokens for the resulting prompt body.

    Returns:
        (assembled_text, metadata) where metadata has:
          - used_tokens: estimated tokens in the assembled output
          - budget_tokens: the budget
          - sections_included: labels of sections that fit
          - sections_dropped: labels of sections that did not fit
          - sections_truncated: labels of sections that were truncated to fit
    """
    sorted_sections = sorted(sections, key=lambda s: s[0])
    included: list[tuple[int, str, str]] = []
    dropped: list[str] = []
    truncated: list[str] = []
    used = 0

    for prio, label, content in sorted_sections:
        est = est_tokens(content)
        if used + est <= budget_tokens:
            included.append((prio, label, content))
            used += est
            continue
        # Doesn't fit whole. Try to truncate if there's enough room for a useful chunk.
        remaining = budget_tokens - used
        if remaining > 50:
            # remaining tokens × 4 chars/token, minus space for a truncation marker
            take_chars = max(200, remaining * 4 - 40)
            if take_chars < len(content):
                truncated_content = content[:take_chars] + "\n[...truncated for context budget...]"
            else:
                truncated_content = content
            included.append((prio, label, truncated_content))
            used += est_tokens(truncated_content)
            truncated.append(label)
            # After truncation we've filled the remainder; drop everything else
            remaining_idx = sorted_sections.index((prio, label, content)) + 1
            dropped.extend(s[1] for s in sorted_sections[remaining_idx:])
            break
        else:
            dropped.append(label)

    # Preserve priority order in output
    included.sort(key=lambda s: s[0])
    body = "\n\n".join(content for _, _, content in included)
    meta = {
        "used_tokens": used,
        "budget_tokens": budget_tokens,
        "sections_included": [label for _, label, _ in included if label not in truncated],
        "sections_truncated": truncated,
        "sections_dropped": dropped,
    }
    return body, meta


# -- Memory tiers -------------------------------------------------------------

@dataclass
class EpisodicMemory:
    """Per-rule memory: what approaches were tried and what the Reflector distilled.

    Each attempt entry is:
      { approach: str (200 chars), result: str (80 chars),
        reflection: str (raw text, for event log), lesson: str (distilled, 120 chars) }

    For prompt assembly we prefer `lesson` (compact, LLM-distilled) over raw
    approach/result/reflection. The raw data stays in the log for post-run
    analysis but is not injected into subsequent prompts.
    """
    rule_id: str
    attempts: list = field(default_factory=list)

    def summary(self, max_attempts: int = 5, max_chars: int = 1200) -> str:
        """Compact summary using distilled lessons, capped at last N attempts.

        Also hard-caps total output to max_chars to prevent context overflow
        on high-attempt items where even 5 distilled lessons can be long.
        """
        if not self.attempts:
            return "No prior attempts."
        recent = self.attempts[-max_attempts:]
        lines = [f"Prior attempts on {self.rule_id} ({len(self.attempts)} total, showing last {len(recent)}):"]
        base_i = len(self.attempts) - len(recent) + 1
        for offset, a in enumerate(recent):
            i = base_i + offset
            # Prefer distilled lesson; fall back to approach/result/reflection clipped hard
            lesson = a.get("lesson", "").strip()
            if lesson:
                lines.append(f"  Attempt {i}: {lesson[:200]}")
            else:
                lines.append(f"  Attempt {i}: {a.get('approach','?')[:80]}")
                lines.append(f"    Result: {a.get('result','?')[:80]}")
                ref = a.get("reflection", "")
                if ref:
                    lines.append(f"    Reflection: {ref[:120]}")
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n[...episodic memory truncated for context budget...]"
        return result

    def full_summary(self) -> str:
        """Uncapped summary — used only for the Reflector's context when analyzing failures."""
        if not self.attempts:
            return "No prior attempts."
        lines = [f"Prior attempts on {self.rule_id} ({len(self.attempts)} total):"]
        for i, a in enumerate(self.attempts, 1):
            lesson = a.get("lesson", "").strip()
            if lesson:
                lines.append(f"  Attempt {i}: {lesson[:240]}")
            else:
                lines.append(f"  Attempt {i}: {a.get('approach','?')[:100]}")
                lines.append(f"    Result: {a.get('result','?')[:100]}")
                ref = a.get("reflection", "")
                if ref:
                    lines.append(f"    Reflection: {ref[:160]}")
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
            lines.append("STRATEGIC LESSONS (from prior runs and this run):")
            # Show up to 8 lessons. Prior-run lessons (tagged [prior run]) sort
            # first since they carry cross-run weight; within-run lessons follow.
            prior = [l for l in self.lessons if l.startswith("[prior run]")]
            current = [l for l in self.lessons if not l.startswith("[prior run]")]
            display = (prior + current)[-8:]
            for l in display:
                lines.append(f"  • {l}")
        return "\n".join(lines) if lines else ""


def categorize_rule(rule_id: str) -> str:
    """Classify a STIG rule into a coarse family for dashboards / analysis.

    DEPRECATED: Kept for backward compatibility with existing test imports.
    New code should use the skill runtime's categorization.
    """
    rid = rule_id.lower()
    if "aide" in rid: return "integrity-monitoring"
    if any(k in rid for k in ("fips", "crypto", "hash", "cipher", "ssl", "tls")):
        return "cryptography"
    if "audit" in rid: return "audit"
    if "sudo" in rid or "nopasswd" in rid: return "privileged-access"
    if "partition" in rid or "mount" in rid: return "filesystem"
    if "selinux" in rid: return "mac"
    if any(k in rid for k in ("kernel", "sysctl", "grub", "boot")): return "kernel"
    if any(k in rid for k in ("firewall", "firewalld", "iptables")): return "network-firewall"
    if "ssh" in rid: return "ssh"
    if any(k in rid for k in ("password", "pam", "faillock")): return "authentication"
    if any(k in rid for k in ("banner", "motd", "issue")): return "banner"
    if any(k in rid for k in ("package", "rpm", "dnf", "gpg")): return "package-management"
    if any(k in rid for k in ("log", "rsyslog", "journald")): return "logging"
    if "service" in rid or "systemd" in rid: return "service-config"
    if any(k in rid for k in ("user", "account", "umask")): return "user-account"
    return "other"


def reflection_first_sentence(text: str) -> str:
    """Extract the first meaningful sentence for plateau detection."""
    if not text:
        return ""
    # Strip markdown / code fences
    cleaned = re.sub(r'```[a-z]*', '', text).strip()
    # Look for "Pattern identified:" marker specifically
    m = re.search(r'pattern identified:\s*([^\n\.]*\.?)', cleaned, re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()[:160]
    # Fall back to first 160 chars of non-empty content
    for line in cleaned.split('\n'):
        line = line.strip()
        if len(line) > 15:
            return line.lower()[:160]
    return ""


_PLATEAU_STOPWORDS = frozenset("""
    the a an is to of on in for at by as it this that these those or and but with from
    not be been can may will would should should must was were are have has had do does did
    am been being im been all any any each every some such no nor most more less than
    then so if because while when where what which who whom whose how whether
""".split())


def _keyword_set(text: str) -> frozenset[str]:
    """Extract a content-keyword frozen set from reflection text.

    Used as the atom for semantic similarity comparison. Normalization:
    - Lowercase
    - Strip code fences and punctuation
    - Prefer the "Pattern identified: ..." clause if present
    - Drop stopwords and 1-2 char tokens
    - Strip trailing 's' plurals
    """
    if not text:
        return frozenset()
    s = text.lower()
    s = re.sub(r'```[a-z]*', '', s)
    m = re.search(r'pattern identified:\s*([^\n\.]*\.?)', s)
    if m:
        s = m.group(1)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = [w for w in s.split() if w not in _PLATEAU_STOPWORDS and len(w) > 2]
    toks = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in toks]
    return frozenset(toks)


def is_similar(a: str, b: str, min_shared: int = 3) -> bool:
    """True if two strings share at least `min_shared` content keywords.

    This is what the plateau detector uses internally. Simple, robust to
    length differences, and language-agnostic enough to handle the Reflector's
    tendency to re-word the same insight with varying elaboration.
    """
    ka, kb = _keyword_set(a), _keyword_set(b)
    return len(ka & kb) >= min_shared


def parse_architect_verdict(text: str) -> dict:
    """Extract VERDICT and NEW_PLAN from an architect re-engagement response.

    The architect, in re-engagement mode, is asked to return:
        VERDICT: <CONTINUE|PIVOT|ESCALATE>
        REASONING: ...
        NEW_PLAN: ...

    But LLMs produce many variations: markdown wrapping, different cases,
    extra whitespace, prefixed prose, reordered fields. This parser tries
    to handle the realistic variations and falls back to CONTINUE (the
    safe default — keep grinding) if it cannot find a parseable verdict.

    Returns a dict with keys:
        - verdict: "CONTINUE" | "PIVOT" | "ESCALATE"
        - new_plan: str (may be empty)
        - parsed_cleanly: bool (False if we fell back to default)

    See docs/whitepaper/architecture/01-reflexive-agent-harness-failure-modes.md
    failure mode 5 (authority hierarchy gap) for the architectural context.
    """
    if not text:
        return {"verdict": "CONTINUE", "new_plan": "", "parsed_cleanly": False}

    verdict = None
    new_plan = ""

    # Find the FIRST line that asserts a verdict. Tolerate inline prefixes,
    # case variations, extra whitespace, and surrounding markdown.
    lines = text.split("\n")
    for raw in lines:
        line = raw.strip().lstrip("`*#- ").rstrip("`*").strip()
        upper = line.upper()
        # Look for "VERDICT:" anywhere in the line, not just at start —
        # tolerates "FINAL VERDICT:" or "After analysis, VERDICT:"
        if "VERDICT:" in upper and verdict is None:
            after = upper.split("VERDICT:", 1)[1].strip()
            if after.startswith("ESCALATE") or "ESCALATE" in after.split()[:2]:
                verdict = "ESCALATE"
            elif after.startswith("PIVOT") or "PIVOT" in after.split()[:2]:
                verdict = "PIVOT"
            elif after.startswith("CONTINUE") or "CONTINUE" in after.split()[:2]:
                verdict = "CONTINUE"
        if "NEW_PLAN:" in upper and not new_plan:
            after_idx = raw.upper().index("NEW_PLAN:") + len("NEW_PLAN:")
            new_plan = raw[after_idx:].strip().strip("`*").strip()

    if verdict is None:
        return {"verdict": "CONTINUE", "new_plan": new_plan, "parsed_cleanly": False}

    return {"verdict": verdict, "new_plan": new_plan[:1000], "parsed_cleanly": True}


def detect_plateau(recent_reflections: list, window: int = 3, min_shared: int = 3) -> bool:
    """True if the last `window` reflections share at least `min_shared`
    content keywords across ALL of them (set intersection).

    Replaces the naive first-sentence exact-match detector with a keyword-
    intersection approach that's robust to cosmetic rewrites. See
    docs/whitepaper/journey/14-overnight-run-findings.md Finding 2 for why
    the old detector saw 0% plateau when the real rate was ~76% (empirically
    measured against the overnight run).

    Not a stopping rule by itself — used as a metric and as a signal for
    architect re-engagement.
    """
    if len(recent_reflections) < window:
        return False
    keyword_sets = [_keyword_set(r) for r in recent_reflections[-window:]]
    if any(len(k) < min_shared for k in keyword_sets):
        return False
    intersection = set(keyword_sets[0])
    for k in keyword_sets[1:]:
        intersection &= k
    return len(intersection) >= min_shared


@dataclass
class RunState:
    """Persistent state across the entire run."""
    failing_rules: list = field(default_factory=list)
    remediated: list = field(default_factory=list)
    escalated: list = field(default_factory=list)  # failed after max retries OR time budget
    skipped: list = field(default_factory=list)
    current_rule: Optional[dict] = None
    current_iteration: int = 0
    semantic: SemanticMemory = field(default_factory=SemanticMemory)
    episodic: dict = field(default_factory=dict)  # rule_id -> EpisodicMemory

    def get_episodic(self, rule_id: str) -> EpisodicMemory:
        if rule_id not in self.episodic:
            self.episodic[rule_id] = EpisodicMemory(rule_id=rule_id)
        return self.episodic[rule_id]

    def summary_for_architect(self, budget_tokens: int = 3000) -> tuple[str, dict]:
        """Budgeted run-state summary for the Architect's prompt.

        Returns (text, assembly_meta). Sections are prioritized so that even
        under tight budget, the architect always sees the high-level counts,
        the top remaining rules, and the semantic memory. Dropped first when
        budget is tight: older remediated / escalated entries.
        """
        sections: list[tuple[int, str, str]] = []

        # 0: Run counts — always kept
        header = (
            f"RUN STATE (iteration {self.current_iteration}):\n"
            f"  Fixed: {len(self.remediated)} | Escalated: {len(self.escalated)} | "
            f"Skipped: {len(self.skipped)} | Remaining: {len(self.failing_rules)}"
        )
        sections.append((0, "header", header))

        # 1: Top of the failing-rules list (what architect will pick from)
        if self.failing_rules:
            n_show = min(15, len(self.failing_rules))
            lines = [f"Remaining rules (top {n_show} of {len(self.failing_rules)}):"]
            for r in self.failing_rules[:n_show]:
                lines.append(f"  - {r['rule_id']}: {r['title']}")
            sections.append((1, "failing_rules", "\n".join(lines)))

        # 2: Semantic memory (banned patterns, lessons) — compact already
        sem = self.semantic.summary()
        if sem:
            sections.append((2, "semantic_memory", sem))

        # 3: Recent escalations — shows what to avoid picking again (bounded)
        if self.escalated:
            n_show = min(10, len(self.escalated))
            lines = [f"Recently escalated ({n_show} of {len(self.escalated)}) — do not reselect:"]
            for r in self.escalated[-n_show:]:
                reason = r.get("reason", "?")
                lines.append(f"  ✗ {r['rule_id']} [{reason}]")
            sections.append((3, "escalated_recent", "\n".join(lines)))

        # 4: Recent remediations — shows working patterns for reference
        if self.remediated:
            n_show = min(8, len(self.remediated))
            lines = [f"Recently remediated ({n_show} of {len(self.remediated)}):"]
            for r in self.remediated[-n_show:]:
                lines.append(f"  ✓ {r['rule_id']}: {r['title']}")
            sections.append((4, "remediated_recent", "\n".join(lines)))

        # 5: Older escalations — lowest priority, drop first
        if len(self.escalated) > 10:
            older = self.escalated[:-10]
            lines = [f"Older escalations ({len(older)}):"]
            for r in older[-20:]:  # hard cap
                lines.append(f"  ✗ {r['rule_id']}")
            sections.append((5, "escalated_older", "\n".join(lines)))

        return assemble_prompt(sections, budget_tokens=budget_tokens)


# -- Single-agent turn --------------------------------------------------------

async def _run_agent_turn(
    agent: Agent,
    session_service: InMemorySessionService,
    message: str,
    run_log=None,
    max_tool_calls: int = 1,
) -> str:
    """Run ONE agent turn with a FRESH session (working memory cleared).

    Enforces `max_tool_calls` tool invocations per turn. If the LLM attempts
    to call a tool after the limit is reached, the turn is cut short and an
    explicit tool_call_capped event is logged.

    This is the Layer 2 defense for the Worker internal retry bug — see
    docs/whitepaper/improvements/02-worker-single-action-enforcement.md and
    docs/whitepaper/journey/14-overnight-run-findings.md Finding 4.
    The outer reflexion loop is responsible for retries with reflection;
    agents should not retry tool calls on their own.
    """
    turn_start = time.time()

    runner = Runner(app_name="gemma-forge", agent=agent, session_service=session_service)
    session = session_service.create_session(app_name="gemma-forge", user_id="operator")

    response_parts: list = []
    first_token_time = None
    total_tokens = {"prompt": 0, "completion": 0}
    tool_calls_seen = 0
    capped = False

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
                    tool_calls_seen += 1
                    if tool_calls_seen > max_tool_calls:
                        # Layer 2 defense: agent tried to call a tool beyond the cap.
                        # End the turn immediately — the outer loop will handle retry with reflection.
                        logger.warning(
                            "[%s] Tool call #%d exceeds cap of %d — ending turn early (bypassed retry loop)",
                            event.author, tool_calls_seen, max_tool_calls,
                        )
                        if run_log:
                            run_log.log("tool_call_capped", agent.name, {
                                "attempted_count": tool_calls_seen,
                                "max_allowed": max_tool_calls,
                                "tool": part.function_call.name,
                            })
                        capped = True
                        break  # exit parts loop
                    if first_token_time is None:
                        first_token_time = time.time()
                    logger.info("[%s] \u2192 TOOL: %s(%s)", event.author,
                                part.function_call.name, str(part.function_call.args)[:200])
                    if run_log:
                        run_log.log_tool_call(event.author, part.function_call.name,
                            dict(part.function_call.args) if part.function_call.args else {})
                if part.function_response:
                    # Cap tool response logging. The actual response seen by the model
                    # within the ADK turn can be larger, but we truncate what we log.
                    resp = str(part.function_response.response)[:1500]
                    logger.info("[%s] \u2190 RESULT: %s", event.author, resp[:200])
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

        if capped:
            # Synthesize a closing text response so downstream logic has something sensible.
            response_parts.append(
                f"[harness] tool cap reached ({max_tool_calls}); ending turn to avoid "
                f"bypassing the reflection loop. The outer harness will retry if needed."
            )
            break  # exit the event loop cleanly

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
            "tool_calls": tool_calls_seen,
            "capped": capped,
        }, include_gpu=True)

    return "\n".join(response_parts).strip()


# -- Evaluation triage --------------------------------------------------------
#
# The harness classifies failure modes and routes responses:
#   HEALTH_FAILURE → immediate revert (target is broken)
#   EVALUATOR_GAP  → revert, count toward scanner-gap early escalation
#   FALSE_NEGATIVE → accept the change (evaluator passed but noise triggered)
#   CLEAN_FAILURE  → normal revert + reflect cycle

@dataclass
class TriageState:
    """Tracks per-item evaluation patterns for triage decisions."""
    evaluator_gap_count: int = 0  # consecutive health-ok but evaluator-fail
    distinct_approaches_in_gap: list = field(default_factory=list)

    def record_gap(self, approach_summary: str) -> None:
        self.evaluator_gap_count += 1
        # Track distinct approaches (by first 80 chars)
        key = approach_summary[:80]
        if key not in self.distinct_approaches_in_gap:
            self.distinct_approaches_in_gap.append(key)

    def is_scanner_gap(self, threshold: int = 3) -> bool:
        """True if we've seen enough distinct approaches fail the evaluator
        while the target stays healthy — indicates a knowledge gap, not a logic gap."""
        return (self.evaluator_gap_count >= threshold
                and len(self.distinct_approaches_in_gap) >= threshold)


# -- Main loop ----------------------------------------------------------------

def _build_skill_runtime(skill: Skill, harness_cfg: dict) -> SkillRuntime:
    """Instantiate the skill's runtime from its plugin or built-in runtimes."""
    vm_cfg = harness_cfg.get("vm", {})
    stig_cfg = harness_cfg.get("stig", {})

    if skill.manifest.stig:
        stig_cfg = {
            "profile": skill.manifest.stig.profile,
            "datastream": skill.manifest.stig.datastream,
        }

    # Import skill runtime dynamically
    from gemma_forge.harness.tools.ssh import SSHConfig
    ssh_config = SSHConfig(
        host=vm_cfg.get("ip", "192.168.122.43"),
        user=vm_cfg.get("user", "adm-forge"),
        key_path=vm_cfg.get("ssh_key", "/data/vm/gemma-forge/keys/adm-forge"),
    )

    # For now, detect runtime by skill name. In the future, skills will
    # declare their runtime class in skill.yaml.
    if skill.name == "stig-rhel9" or skill.manifest.stig:
        # Dynamic import of the skill's runtime module
        import importlib.util
        runtime_path = Path("skills/stig-rhel9/runtime.py")
        spec = importlib.util.spec_from_file_location("stig_runtime", runtime_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        StigSkillRuntime = mod.StigSkillRuntime
        profile = stig_cfg.get("profile", "xccdf_org.ssgproject.content_profile_stig")
        datastream = stig_cfg.get("datastream", "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml")
        return StigSkillRuntime(ssh_config, profile, datastream)

    raise ValueError(f"No runtime implementation for skill '{skill.name}'")


async def run_ralph(
    config_path: str = "config/harness.yaml",
    skill_name: Optional[str] = None,
) -> None:
    """Run the Ralph loop — skill-agnostic reflexion harness."""

    harness_cfg = {}
    if Path(config_path).exists():
        with open(config_path) as f:
            harness_cfg = yaml.safe_load(f) or {}

    models_cfg = {}
    if Path("config/models.yaml").exists():
        with open("config/models.yaml") as f:
            models_cfg = yaml.safe_load(f) or {}

    loop_cfg = harness_cfg.get("loop", {})

    # Load skill and its runtime
    skill = None
    runtime: Optional[SkillRuntime] = None
    if skill_name:
        skill = load_skill(skill_name)
        logger.info("Loaded skill: %s", skill.name)
        runtime = _build_skill_runtime(skill, harness_cfg)

    if runtime is None:
        raise RuntimeError("No skill specified — the harness requires a skill to run.")

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

    # Wire agents to skill-provided tools via the interfaces
    scan_tool = runtime.get_scan_tool()
    agent_tools = runtime.executor.get_agent_tools()

    architect = Agent(name="architect", model=_make_llm(),
                      instruction=arch_prompt, tools=[scan_tool])
    worker = Agent(name="worker", model=_make_llm(),
                   instruction=work_prompt, tools=agent_tools)
    reflector = Agent(name="reflector", model=_make_llm(),
                      instruction=ref_prompt, tools=[])

    session_service = InMemorySessionService()
    max_outer = loop_cfg.get("max_iterations", 50)
    max_retries = loop_cfg.get("max_retries_per_rule", 3)
    max_rules = loop_cfg.get("max_rules_per_run", 50)
    max_wall_time_per_rule_s = loop_cfg.get("max_wall_time_per_rule_s", 1200)
    arch_reengage_every_n = loop_cfg.get("architect_reengage_every_n_attempts", 3)
    arch_reengage_on_plateau = loop_cfg.get("architect_reengage_on_plateau", True)
    scanner_gap_threshold = loop_cfg.get("scanner_gap_threshold", 3)
    run_start_wall = time.time()

    try:
        from gemma_forge.observability.otel import init_telemetry
        init_telemetry()
    except Exception:
        pass

    from gemma_forge.harness.run_logger import RunLogger
    from gemma_forge.harness.memory_store import PostgresMemoryStore
    run_log = RunLogger()
    # Per-skill memory schema inside the shared `gemma_forge` Postgres DB
    # (ADR-0016). One Postgres role per skill (forge_<skill>) with the
    # search_path pinned at bootstrap. The skill family is the first
    # segment of the skill name (e.g., "stig" from "stig-rhel9"), and
    # maps to the schema name (stig) and role (forge_stig) created by
    # tools/bootstrap_skill.sh.
    skill_schema = (skill_name or "stig").split("-")[0].split("/")[0]
    mem_store = PostgresMemoryStore(skill=skill_schema)
    mem_store.initialize()
    mem_run_id = mem_store.start_run(skill_name or "unknown", harness_cfg)

    logger.info("=" * 60)
    logger.info("RALPH LOOP — Skill-agnostic reflexion harness (v5)")
    logger.info("=" * 60)
    logger.info("Model: Gemma 4 31B bf16 full precision, TP=4, all 4 GPUs")
    logger.info("Max outer iterations: %d | Retry ceiling/rule: %d | Time budget/rule: %ds",
                max_outer, max_retries, max_wall_time_per_rule_s)
    logger.info("Escalation trigger: WALL-CLOCK TIME (not attempt count)")
    logger.info("Scanner-gap early escalation threshold: %d distinct approaches", scanner_gap_threshold)
    logger.info("Run log: %s", run_log.log_path)
    logger.info("")

    state = RunState()

    # Emit skill manifest for the UI
    if skill:
        ui = skill.manifest.ui
        run_log.log("skill_manifest", "system", {
            "skill_name": skill.name,
            "skill_description": skill.description,
            "ui": {
                "title": ui.title,
                "work_item": ui.work_item,
                "work_item_plural": ui.work_item_plural,
                "id_prefix_strip": ui.id_prefix_strip,
                "fixed_label": ui.fixed_label,
                "outcomes": [
                    {"type": o.type, "label": o.label, "color": o.color}
                    for o in ui.outcomes
                ],
            },
        })

    # -- Checkpoint preflight: verify baseline exists, clear stale progress --
    if not await runtime.checkpoint.exists("baseline"):
        raise RuntimeError(
            "Baseline checkpoint does not exist. The Ralph loop relies on "
            "baseline/progress checkpoints for authoritative revert. Create a "
            "baseline checkpoint before starting a run."
        )
    logger.info("Baseline checkpoint OK")
    await runtime.checkpoint.delete("progress")
    run_log.log("snapshot_preflight", "system", {
        "baseline_ok": True,
        "progress_cleared": True,
    })

    # -- Initial scan via skill's WorkQueue --
    logger.info("Running initial scan...")
    work_items = await runtime.work_queue.scan()
    graph = TaskGraph()
    graph.add_items(work_items)
    for item in work_items:
        state.failing_rules.append({"rule_id": item.id, "title": item.title,
                                     "category": item.category, "_item": item})
    logger.info("Found %d work items", len(state.failing_rules))
    run_log.log("scan_complete", "system", {"failing_count": len(state.failing_rules)}, include_gpu=True)
    # Emit initial graph state for dashboard
    run_log.log("graph_state", "system", graph.snapshot())

    # -- Cross-run retrieval: hydrate from prior runs --------------------------
    prior_run_count = mem_store.get_run_count() - 1  # exclude current run
    if prior_run_count > 0:
        logger.info("Cross-run memory: %d prior runs found", prior_run_count)

        # Load globally banned patterns from prior runs
        prior_bans = mem_store.load_global_bans()
        for ban in prior_bans:
            if ban not in state.semantic.banned_patterns:
                state.semantic.banned_patterns.append(ban)
        if prior_bans:
            logger.info("  Loaded %d banned patterns from prior runs", len(prior_bans))

        # Load top strategic lessons — category-diverse, weight-ranked.
        # First load top global lessons, then ensure each category is represented.
        prior_lessons = mem_store.load_all_lessons(min_weight=0.2, limit=40)
        seen_texts: set = set()
        deduped: list = []
        for pl in prior_lessons:
            # Deduplicate near-identical lessons (same first 80 chars)
            key = pl.lesson[:80].lower()
            if key not in seen_texts:
                seen_texts.add(key)
                deduped.append(pl)
        # Ensure category diversity: at most 3 lessons per category in the global set
        cat_counts: dict = {}
        diverse: list = []
        for pl in deduped:
            cat_counts.setdefault(pl.category, 0)
            if cat_counts[pl.category] < 3:
                diverse.append(pl)
                cat_counts[pl.category] += 1
        # Cap the global set at 30
        for pl in diverse[:30]:
            state.semantic.lessons.append(f"[prior run] {pl.lesson}")
        if diverse:
            logger.info("  Loaded %d strategic lessons (%d raw, %d after dedup, weight >= 0.2)",
                        len(diverse[:30]), len(prior_lessons), len(deduped))

        # Log the category difficulty model for the clutch
        cat_stats = mem_store.get_category_stats()
        if cat_stats:
            logger.info("  Category difficulty model:")
            for cs in cat_stats:
                logger.info("    %s: %.0f%% success, %.1f avg attempts, %.0fs avg time",
                            cs.category, cs.success_rate * 100, cs.avg_attempts, cs.avg_wall_time_s)

        run_log.log("cross_run_hydration", "system", {
            "prior_runs": prior_run_count,
            "loaded_bans": len(prior_bans),
            "loaded_lessons": len(prior_lessons),
            "category_stats": [
                {"category": cs.category, "success_rate": round(cs.success_rate, 2),
                 "avg_attempts": round(cs.avg_attempts, 1), "total_items": cs.total_items}
                for cs in cat_stats
            ] if cat_stats else [],
        })
    else:
        logger.info("Cross-run memory: first run — no prior knowledge")
        run_log.log("cross_run_hydration", "system", {"prior_runs": 0})

    # -- Adaptive concurrency (the clutch) ------------------------------------
    from gemma_forge.harness.clutch import Clutch, ClutchConfig
    clutch_cfg = ClutchConfig(
        max_workers=loop_cfg.get("max_parallel_workers", 3),
    )
    clutch = Clutch(config=clutch_cfg, mem_store=mem_store)
    clutch.initialize()
    logger.info("Clutch: %s", clutch.state.reason)
    run_log.log("clutch_initialized", "system", clutch.snapshot())

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

        elapsed_run = time.time() - run_start_wall
        run_log.log("iteration_start", "system", {
            "iteration": outer_iter,
            "failing": len(state.failing_rules),
            "remediated": len(state.remediated),
            "escalated": len(state.escalated),
            "skipped": len(state.skipped),
            "reverted": len(state.escalated),  # for frontend compat
            # Heartbeat fields: memory + learning trajectory
            "run_elapsed_s": round(elapsed_run, 1),
            "episodic_rules_tracked": len(state.episodic),
            "episodic_total_attempts": sum(len(m.attempts) for m in state.episodic.values()),
            "banned_patterns": len(state.semantic.banned_patterns),
            "preferred_approaches": len(state.semantic.preferred_approaches),
            "lessons_learned": len(state.semantic.lessons),
            "rules_per_hour": round(rules_processed * 3600 / max(elapsed_run, 1), 2),
        }, include_gpu=True)

        arch_body, arch_meta = state.summary_for_architect(budget_tokens=3000)
        arch_msg = f"{arch_body}\n\nSelect ONE rule to remediate. Explain your approach."
        run_log.log("prompt_assembled", "architect", {
            "phase": "rule_selection",
            **arch_meta,
        })
        arch_resp = await _run_agent_turn(architect, session_service, arch_msg, run_log)

        # Check for SKIP
        if "SKIP:" in arch_resp.upper():
            for rule in state.failing_rules:
                if rule["rule_id"] in arch_resp:
                    logger.info(">>> SKIPPED: %s <<<", rule["rule_id"])
                    state.skipped.append({**rule, "reason": arch_resp[:150], "iteration": outer_iter})
                    state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != rule["rule_id"]]
                    graph.mark_skipped(rule["rule_id"])
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
        rule_category = selected.get("category", categorize_rule(selected["rule_id"]))
        work_item: WorkItem = selected.get("_item", WorkItem(id=selected["rule_id"], title=selected["title"], category=rule_category))
        episodic = state.get_episodic(selected["rule_id"])
        triage = TriageState()
        graph.mark_active(selected["rule_id"])
        rule_start_wall = time.time()
        rule_total_tokens = 0
        approaches_tried: list = []
        logger.info("Selected: %s (%s) [%s]", selected["rule_id"], selected["title"], rule_category)
        run_log.log("rule_selected", "architect", {
            "rule_id": selected["rule_id"],
            "title": selected["title"],
            "category": rule_category,
            "iteration": outer_iter,
            "time_budget_s": max_wall_time_per_rule_s,
        })

        # -- Inner loop: Ralph-style. Keep grinding until time budget runs out. --
        rule_succeeded = False
        escalation_reason: Optional[str] = None
        attempt = 0
        # Track the last attempt at which the architect re-engaged, so we
        # trigger re-engagement every N attempts since the last architect touch.
        last_architect_touch = 0  # attempt #0 = initial rule selection
        reengagements_count = 0
        while True:
            attempt += 1

            # --- Escalation checks BEFORE starting a new attempt ---
            rule_elapsed = time.time() - rule_start_wall
            if rule_elapsed >= max_wall_time_per_rule_s:
                escalation_reason = "time_budget"
                logger.warning("  >>> TIME BUDGET EXHAUSTED (%ds / %ds) after %d attempts <<<",
                               int(rule_elapsed), max_wall_time_per_rule_s, attempt - 1)
                break
            if attempt > max_retries:
                escalation_reason = "retry_ceiling"
                logger.warning("  >>> RETRY CEILING HIT (%d attempts) — this is a safety cap, not Ralph intent <<<",
                               max_retries)
                break

            logger.info("\n  --- Attempt %d for %s (%ds / %ds used) ---",
                        attempt, selected["rule_id"], int(rule_elapsed), max_wall_time_per_rule_s)
            run_log.log("attempt_start", "harness", {
                "rule_id": selected["rule_id"],
                "category": rule_category,
                "attempt": attempt,
                "max_attempts": max_retries,  # safety ceiling
                "time_budget_s": max_wall_time_per_rule_s,
                "rule_elapsed_s": round(rule_elapsed, 1),
            })

            attempt_phase_timing: dict = {}
            attempt_start_wall = time.time()

            # Worker prompt — assembled under token budget.
            # Full context budget: 16K max_model_len
            #   - System instruction: ~1500 tokens
            #   - Tool schema (apply_fix): ~300 tokens
            #   - Tool call (SSH command): ~200 tokens
            #   - Tool response (SSH output): up to ~1500 tokens
            #   - Model response: up to 2048 tokens
            #   - Safety margin: ~450 tokens
            # That leaves ~10K for the user message. But on high-attempt items,
            # episodic + semantic memory can grow large. We cap at 5500 to ensure
            # the full round-trip never exceeds 16K even with large tool responses.
            # This is the fix for the 8 context overflow errors from the v3 run.
            WORKER_USER_BUDGET = 5500

            work_sections: list[tuple[int, str, str]] = []

            # 0: Rule identity and directive — always kept
            work_sections.append((0, "rule_identity",
                f"Fix this STIG rule:\n  Rule: {selected['rule_id']}\n  Title: {selected['title']}"))

            # 1: Architect's plan (bounded) — critical context
            work_sections.append((1, "architect_plan",
                f"Architect's plan:\n{arch_resp[:500]}"))

            # 2: Current attempt marker — tells the Worker where it is in the loop
            work_sections.append((2, "attempt_marker",
                f"This is attempt {attempt} for this rule (time used: {int(rule_elapsed)}s of {max_wall_time_per_rule_s}s)."))

            # 3: Distilled episodic memory (last 5 attempts, summarized via `lesson`)
            if episodic.attempts:
                work_sections.append((3, "episodic_memory", episodic.summary(max_attempts=5)))

            # 3.5: Cross-run item history — what happened to THIS rule in prior runs
            if attempt == 1:  # only on first attempt to avoid prompt bloat
                prior_attempts = mem_store.query_prior_attempts(selected["rule_id"], limit=5)
                if prior_attempts:
                    pa_lines = [f"CROSS-RUN HISTORY for this rule ({len(prior_attempts)} prior attempts):"]
                    for pa in prior_attempts:
                        status = "PASSED" if pa.eval_passed else "FAILED"
                        pa_lines.append(f"  [{status}] {pa.approach[:120]}")
                        if pa.lesson:
                            pa_lines.append(f"    Lesson: {pa.lesson[:150]}")
                        if pa.failure_mode:
                            pa_lines.append(f"    Failure: {pa.failure_mode[:100]}")
                    work_sections.append((3, "cross_run_item_history", "\n".join(pa_lines)))

            # 4: Semantic memory (bans, preferred approaches, lessons) — already capped
            sem = state.semantic.summary()
            if sem:
                work_sections.append((4, "semantic_memory", sem))

            # 4.5: Category-specific lessons from prior runs
            cat_lessons = mem_store.load_lessons(rule_category, min_weight=0.2, limit=5)
            if cat_lessons:
                cl_lines = [f"LESSONS FROM PRIOR RUNS for [{rule_category}] rules:"]
                for cl in cat_lessons:
                    cl_lines.append(f"  • {cl.lesson[:150]}")
                work_sections.append((5, "category_lessons", "\n".join(cl_lines)))

            # 6: Final directive
            work_sections.append((6, "directive", "Call apply_fix EXACTLY ONCE now, then return a brief text summary."))

            work_context, work_meta = assemble_prompt(work_sections, budget_tokens=WORKER_USER_BUDGET)
            run_log.log("prompt_assembled", "worker", {
                "phase": "apply_fix",
                "rule_id": selected["rule_id"],
                "attempt": attempt,
                **work_meta,
            })

            t0 = time.time()
            work_resp = await _run_agent_turn(worker, session_service, work_context, run_log)
            attempt_phase_timing["worker_llm_s"] = round(time.time() - t0, 2)

            # HARNESS: deterministic evaluation via skill's Evaluator interface
            logger.info("  EVALUATING (deterministic)...")
            t0 = time.time()
            try:
                eval_result_obj: EvalResult = await runtime.evaluator.evaluate(work_item)
            except Exception as exc:  # noqa: BLE001
                logger.exception("  EVAL tool error: %s", exc)
                run_log.log("tool_error", "harness", {
                    "rule_id": selected["rule_id"],
                    "phase": "evaluate",
                    "error": str(exc)[:400],
                    "attempt": attempt,
                })
                eval_result_obj = EvalResult(
                    passed=False,
                    failure_mode=FailureMode.HEALTH_FAILURE,
                    summary=f"eval tool error: {exc}",
                )
            attempt_phase_timing["eval_s"] = round(time.time() - t0, 2)

            # Convert EvalResult to dict for logging compatibility
            eval_result = {
                "passed": eval_result_obj.passed,
                "failure_mode": eval_result_obj.failure_mode.value,
                "summary": eval_result_obj.summary,
                **eval_result_obj.signals,
            }
            logger.info("  EVAL: %s (mode=%s)", eval_result_obj.summary, eval_result_obj.failure_mode.value)
            run_log.log("evaluation", "harness", eval_result)

            # --- Evaluation triage ---
            if eval_result_obj.failure_mode == FailureMode.EVALUATOR_GAP:
                triage.record_gap(work_resp[:80])
                if triage.is_scanner_gap(threshold=scanner_gap_threshold):
                    logger.warning("  >>> SCANNER-GAP DETECTED: %d distinct approaches failed evaluator with healthy target <<<",
                                   len(triage.distinct_approaches_in_gap))
                    run_log.log("scanner_gap_detected", "harness", {
                        "rule_id": selected["rule_id"],
                        "gap_count": triage.evaluator_gap_count,
                        "distinct_approaches": len(triage.distinct_approaches_in_gap),
                        "threshold": scanner_gap_threshold,
                    })

            if eval_result_obj.passed:
                # SUCCESS
                logger.info("  >>> RULE REMEDIATED: %s <<<", selected["rule_id"])
                approaches_tried.append(work_resp[:200])
                state.remediated.append({
                    "rule_id": selected["rule_id"],
                    "title": selected["title"],
                    "approach": work_resp[:100],
                    "attempt": attempt,
                    "iteration": outer_iter,
                    "category": rule_category,
                })
                state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != selected["rule_id"]]
                rule_succeeded = True
                rules_processed += 1
                graph.mark_completed(selected["rule_id"], attempts=attempt,
                                     wall_time_s=time.time() - rule_start_wall)

                # Advance the progress checkpoint so future failures revert to a state
                # that includes this fix. Non-fatal if it fails — we log and continue.
                t0 = time.time()
                try:
                    snap_ok, snap_detail = await runtime.checkpoint.save("progress")
                except Exception as exc:  # noqa: BLE001
                    snap_ok, snap_detail = False, f"checkpoint save exception: {exc}"
                snap_save_s = round(time.time() - t0, 2)
                if snap_ok:
                    logger.info("  Progress checkpoint advanced (%.1fs)", snap_save_s)
                else:
                    logger.warning("  Progress checkpoint save FAILED: %s", snap_detail[:200])
                    run_log.log("tool_error", "harness", {
                        "rule_id": selected["rule_id"],
                        "phase": "checkpoint_save",
                        "error": snap_detail[:400],
                        "attempt": attempt,
                    })

                run_log.log("remediated", "harness", {
                    "rule_id": selected["rule_id"],
                    "category": rule_category,
                    "attempt": attempt,
                    "wall_time_s": round(time.time() - rule_start_wall, 1),
                    "phase_timing": attempt_phase_timing,
                    "snapshot_saved": snap_ok,
                    "snapshot_save_s": snap_save_s,
                })

                # Persist to cross-run memory
                mem_store.save_item_outcome(
                    mem_run_id, selected["rule_id"], selected["title"],
                    rule_category, "completed", attempt,
                    round(time.time() - rule_start_wall, 1))
                # Save the successful approach as a lesson
                if episodic.attempts:
                    last = episodic.attempts[-1]
                    lesson_text = last.get("lesson", "") or f"succeeded with: {work_resp[:100]}"
                    if lesson_text:
                        mem_store.save_lesson(rule_category, lesson_text,
                                              mem_run_id, selected["rule_id"])

                # Reinforce cross-run lessons: boost lessons from this category
                # that the agent had available when it succeeded.
                for sl in mem_store.load_lessons(rule_category, min_weight=0.0, limit=50):
                    mem_store.update_lesson_weight(sl.id, success=True)

                break

            # FAIL — diagnose, then checkpoint-restore.
            #
            # Capture forensics (for learning), then restore to the last-known-good
            # checkpoint. The checkpoint mechanism is authoritative — it cannot be
            # defeated by a target-level state change.
            logger.warning("  >>> EVAL FAILED (mode=%s) — gathering diagnostics before revert <<<",
                           eval_result_obj.failure_mode.value)

            # Step 1: Gather environment forensics before we touch anything.
            t0 = time.time()
            try:
                diagnostics = await runtime.gather_diagnostics()
            except Exception as exc:  # noqa: BLE001
                logger.warning("  Diagnostic gather failed: %s", exc)
                diagnostics = {"_error": str(exc)[:300], "sudo_ok": False,
                               "services_ok": False, "mission_healthy": False}
            attempt_phase_timing["diagnostics_s"] = round(time.time() - t0, 2)
            logger.info("  Diagnostics: sudo_ok=%s services_ok=%s mission_healthy=%s",
                        diagnostics.get("sudo_ok"), diagnostics.get("services_ok"),
                        diagnostics.get("mission_healthy"))
            run_log.log("post_mortem", "harness", {
                "rule_id": selected["rule_id"],
                "category": rule_category,
                "attempt": attempt,
                "failure_mode": eval_result_obj.failure_mode.value,
                "eval_summary": eval_result.get("summary", ""),
                "sudo_ok": diagnostics.get("sudo_ok", False),
                "services_ok": diagnostics.get("services_ok", False),
                "mission_healthy": diagnostics.get("mission_healthy", False),
                "sudo_probe": diagnostics.get("sudo_probe", "")[:400],
                "service_status": diagnostics.get("service_status", "")[:400],
                "mission_healthcheck": diagnostics.get("mission_healthcheck", "")[:500],
                "recent_auth_failures": diagnostics.get("recent_auth_failures", "")[:500],
                "sudoers_state": diagnostics.get("sudoers_state", "")[:300],
                "pam_state": diagnostics.get("pam_state", "")[:300],
                "fs_state": diagnostics.get("fs_state", "")[:300],
                "recent_journal_errors": diagnostics.get("recent_journal_errors", "")[:500],
            })

            # Step 2: Checkpoint-restore to the last known-good state.
            t0 = time.time()
            try:
                ok, restore_detail = await runtime.checkpoint.restore("progress")
            except Exception as exc:  # noqa: BLE001
                logger.exception("  Checkpoint restore error: %s", exc)
                run_log.log("tool_error", "harness", {
                    "rule_id": selected["rule_id"],
                    "phase": "checkpoint_restore",
                    "error": str(exc)[:400],
                    "attempt": attempt,
                })
                ok, restore_detail = False, f"checkpoint exception: {exc}"
            attempt_phase_timing["checkpoint_restore_s"] = round(time.time() - t0, 2)
            if ok:
                logger.info("  Checkpoint restore OK (%s)", restore_detail[:120])
            else:
                logger.error("  Checkpoint restore FAILED: %s", restore_detail[:200])
                run_log.log("tool_error", "harness", {
                    "rule_id": selected["rule_id"],
                    "phase": "checkpoint_restore",
                    "error": restore_detail[:400],
                    "attempt": attempt,
                })

            # Step 3: Verify the target is actually reachable post-restore.
            post_restore_ok, post_sudo_detail = await runtime.check_sudo_healthy()
            if not post_restore_ok:
                logger.error("  Post-restore sudo probe FAILED: %s", post_sudo_detail)
                run_log.log("environment_unrecoverable", "harness", {
                    "rule_id": selected["rule_id"],
                    "attempt": attempt,
                    "detail": post_sudo_detail[:400],
                    "restore_detail": restore_detail[:400],
                })

            approaches_tried.append(work_resp[:200])
            run_log.log("revert", "harness", {
                "rule_id": selected["rule_id"],
                "category": rule_category,
                "reason": eval_result["summary"],
                "failure_mode": eval_result_obj.failure_mode.value,
                "method": "checkpoint_restore",
                "restore_ok": ok,
                "restore_detail": restore_detail[:200],
                "post_restore_healthy": post_restore_ok,
                "scanner_gap": triage.is_scanner_gap(threshold=scanner_gap_threshold),
                "evaluator_gap_count": triage.evaluator_gap_count,
                "attempt": attempt,
                "phase_timing": attempt_phase_timing,
            }, include_gpu=True)

            # REFLECTOR analyzes — always, if there's still time for at least one more attempt
            time_left = max_wall_time_per_rule_s - (time.time() - rule_start_wall)
            if time_left > 60:  # need at least ~1 min for a next attempt to be worthwhile
                # Assemble the reflector prompt under a budget.
                REFLECTOR_USER_BUDGET = 4500

                ref_sections: list[tuple[int, str, str]] = []
                ref_sections.append((0, "rule_identity",
                    f"Rule: {selected['rule_id']} ({selected['title']})\n"
                    f"Attempt {attempt} FAILED. The loop will keep grinding until the time budget runs out ({int(time_left)}s remaining)."))
                ref_sections.append((1, "worker_approach",
                    f"Worker's approach:\n{work_resp[:500]}"))
                ref_sections.append((2, "eval_result",
                    f"Evaluation result:\n{json.dumps(eval_result, indent=2)[:500]}"))
                # Only the reflector gets the full episodic summary (capped later by budget)
                if episodic.attempts:
                    ref_sections.append((3, "episodic_full", episodic.full_summary()))
                ref_sections.append((4, "instructions",
                    "Analyze: WHY did this approach fail? What should the Worker try FUNDAMENTALLY "
                    "DIFFERENTLY on the next attempt? After multiple failures, consider radically "
                    "different strategies — not just tweaks to the same approach.\n\n"
                    "Output structured guidance — include ALL four fields:\n"
                    "BANNED: <regex pattern to reject in future scripts>\n"
                    "PREFERRED: <alternative approach to try>\n"
                    "LESSON: <one-sentence strategic insight>\n"
                    "DISTILLED: <one-sentence summary of this attempt and what was learned, <200 chars, for compact memory>"))

                ref_msg, ref_meta = assemble_prompt(ref_sections, budget_tokens=REFLECTOR_USER_BUDGET)
                run_log.log("prompt_assembled", "reflector", {
                    "phase": "reflection",
                    "rule_id": selected["rule_id"],
                    "attempt": attempt,
                    **ref_meta,
                })

                logger.info("  REFLECTOR analyzing attempt %d failure (%ds left)...", attempt, int(time_left))
                t0 = time.time()
                ref_resp = await _run_agent_turn(reflector, session_service, ref_msg, run_log)
                attempt_phase_timing["reflector_llm_s"] = round(time.time() - t0, 2)

                # Parse structured reflection — look for BANNED / PREFERRED / LESSON / DISTILLED
                reflection_text = ref_resp[:500]
                distilled_lesson = ""
                new_bans_this_reflection: list = []
                for line in ref_resp.split("\n"):
                    line = line.strip()
                    if line.upper().startswith("BANNED:"):
                        ban = line[7:].strip()
                        if ban and len(ban) > 3:
                            state.semantic.banned_patterns.append(ban)
                            new_bans_this_reflection.append(ban)
                            logger.info("  + Banned: %s", ban)
                            run_log.log("ban_added", "reflector", {
                                "rule_id": selected["rule_id"],
                                "pattern": ban[:200],
                                "attempt": attempt,
                                "banned_patterns_total": len(state.semantic.banned_patterns),
                            })
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
                    elif line.upper().startswith("DISTILLED:"):
                        distilled_lesson = line[10:].strip()

                # Fallback: if the Reflector didn't produce a DISTILLED line, synthesize
                # a compact one from the reflection text so episodic memory stays small.
                if not distilled_lesson:
                    distilled_lesson = reflection_first_sentence(reflection_text)[:200]

                episodic.attempts.append({
                    "approach": work_resp[:200],
                    "result": eval_result["summary"],
                    "reflection": reflection_text,
                    "lesson": distilled_lesson,
                })

                # Plateau detection — is the reflector giving the same answer repeatedly?
                prior_reflections = [a.get("reflection", "") for a in episodic.attempts]
                plateau = detect_plateau(prior_reflections, window=3)

                run_log.log("reflection", "reflector", {
                    "rule_id": selected["rule_id"],
                    "text": reflection_text,
                    "attempt": attempt,
                    "banned_count": len(state.semantic.banned_patterns),
                    "new_bans_this_reflection": len(new_bans_this_reflection),
                    "plateaued": plateau,
                    "phase_timing": attempt_phase_timing,
                })

                # --- ARCHITECT RE-ENGAGEMENT ---
                # After N failed attempts, or when the Reflector plateaus, re-invoke the
                # architect to decide CONTINUE / PIVOT / ESCALATE. This is the fix for
                # the overnight-run issue where the Reflector said "stop trying" 20 times
                # and the loop kept grinding because no one had authority to act on it.
                # See docs/whitepaper/improvements/01-architect-reengagement.md
                attempts_since_touch = attempt - last_architect_touch
                scanner_gap_flag = triage.is_scanner_gap(threshold=scanner_gap_threshold)
                should_reengage = (
                    (attempts_since_touch >= arch_reengage_every_n
                     or (arch_reengage_on_plateau and plateau)
                     or scanner_gap_flag)
                    and (max_wall_time_per_rule_s - (time.time() - rule_start_wall)) > 120
                )

                if should_reengage:
                    reengagements_count += 1
                    if scanner_gap_flag:
                        trigger = "scanner_gap"
                    elif plateau and attempts_since_touch < arch_reengage_every_n:
                        trigger = "plateau"
                    else:
                        trigger = "attempt_threshold"
                    logger.info("  \u26A1 ARCHITECT RE-ENGAGEMENT #%d (trigger=%s, attempts since touch=%d, plateau=%s)",
                                reengagements_count, trigger, attempts_since_touch, plateau)

                    # Build re-engagement prompt — architect sees the full rule journey
                    reeng_sections: list[tuple[int, str, str]] = []
                    scanner_gap_note = ""
                    if scanner_gap_flag:
                        scanner_gap_note = (
                            f"\n⚠ SCANNER GAP DETECTED: {triage.evaluator_gap_count} attempts passed "
                            f"health checks but failed the evaluator with {len(triage.distinct_approaches_in_gap)} "
                            f"distinct approaches. This suggests a knowledge gap — the model may not know what "
                            f"the evaluator expects. Consider ESCALATE."
                        )
                    reeng_sections.append((0, "header",
                        "=== ARCHITECT RE-ENGAGEMENT ===\n"
                        f"Rule: {selected['rule_id']} ({selected['title']})\n"
                        f"Category: {rule_category}\n"
                        f"Attempts so far: {attempt}\n"
                        f"Reflector plateau: {plateau}\n"
                        f"Re-engagement trigger: {trigger}\n"
                        f"Wall time used on this rule: {int(time.time() - rule_start_wall)}s of {max_wall_time_per_rule_s}s"
                        f"{scanner_gap_note}"))
                    reeng_sections.append((1, "episodic_full", episodic.full_summary()))
                    # Show the reflector's latest guidance verbatim so the architect
                    # can't miss a clear "stop trying" signal.
                    reeng_sections.append((2, "latest_reflection",
                        f"LATEST REFLECTION:\n{reflection_text}"))
                    reeng_sections.append((3, "directive",
                        "Decide CONTINUE / PIVOT / ESCALATE for this rule. Output format:\n"
                        "VERDICT: <CONTINUE|PIVOT|ESCALATE>\n"
                        "REASONING: <one paragraph>\n"
                        "NEW_PLAN: <if CONTINUE or PIVOT, a clear plan for the Worker. If ESCALATE, omit.>"))

                    reeng_msg, reeng_meta = assemble_prompt(reeng_sections, budget_tokens=5000)
                    run_log.log("prompt_assembled", "architect", {
                        "phase": "reengagement",
                        "rule_id": selected["rule_id"],
                        "attempt": attempt,
                        "trigger": trigger,
                        **reeng_meta,
                    })

                    t0 = time.time()
                    reeng_resp = await _run_agent_turn(architect, session_service, reeng_msg, run_log)
                    attempt_phase_timing["architect_reengage_llm_s"] = round(time.time() - t0, 2)
                    last_architect_touch = attempt

                    # Parse verdict via the extracted parser (testable in isolation)
                    parsed = parse_architect_verdict(reeng_resp)
                    verdict = parsed["verdict"]
                    new_plan = parsed["new_plan"]

                    run_log.log("architect_reengaged", "architect", {
                        "rule_id": selected["rule_id"],
                        "attempt": attempt,
                        "trigger": trigger,
                        "plateau": plateau,
                        "verdict": verdict,
                        "parsed_cleanly": parsed["parsed_cleanly"],
                        "reengagement_count": reengagements_count,
                        "full_response": reeng_resp[:1000],
                    })

                    logger.info("  \u26A1 ARCHITECT VERDICT: %s", verdict)

                    if verdict == "ESCALATE":
                        # Preemptive escalation — architect decided this rule is not solvable
                        escalation_reason = "architect_preemptive"
                        logger.warning("  >>> ARCHITECT ESCALATED %s preemptively (attempts=%d, wall_time=%ds) <<<",
                                       selected["rule_id"], attempt, int(time.time() - rule_start_wall))
                        break  # exit inner retry loop
                    else:
                        # CONTINUE or PIVOT — update arch_resp with the architect's new plan
                        # so the Worker's next turn sees the refreshed direction.
                        if new_plan:
                            arch_resp = new_plan
                        else:
                            # If the architect didn't include NEW_PLAN, use the full reeng response
                            arch_resp = reeng_resp
                        logger.info("  \u26A1 Architect updated plan for next attempt (verdict=%s)", verdict)
            else:
                logger.info("  Time budget too low for another attempt (%ds left) — skipping reflection.", int(time_left))

        # --- Rule complete (success or escalation) ---
        rule_wall_time = time.time() - rule_start_wall

        if not rule_succeeded:
            # ESCALATED — time budget or retry ceiling exhausted
            reason = escalation_reason or "unknown"
            logger.warning("  >>> ESCALATED: %s (reason=%s, attempts=%d, wall_time=%ds) <<<",
                           selected["rule_id"], reason, attempt - 1, int(rule_wall_time))
            state.escalated.append({
                "rule_id": selected["rule_id"],
                "title": selected["title"],
                "attempts": attempt - 1,
                "iteration": outer_iter,
                "category": rule_category,
                "reason": reason,
            })
            state.failing_rules = [r for r in state.failing_rules if r["rule_id"] != selected["rule_id"]]
            rules_processed += 1
            graph.mark_escalated(selected["rule_id"], reason=reason,
                                 attempts=attempt - 1,
                                 wall_time_s=rule_wall_time)
            run_log.log("escalated", "harness", {
                "rule_id": selected["rule_id"],
                "category": rule_category,
                "attempts": attempt - 1,
                "wall_time_s": round(rule_wall_time, 1),
                "reason": reason,
            })

            # Persist to cross-run memory
            mem_store.save_item_outcome(
                mem_run_id, selected["rule_id"], selected["title"],
                rule_category, "escalated", attempt - 1,
                round(rule_wall_time, 1))

            # Reinforce cross-run lessons: decay lessons from this category
            # that were available but didn't prevent escalation.
            for sl in mem_store.load_lessons(rule_category, min_weight=0.0, limit=50):
                mem_store.update_lesson_weight(sl.id, success=False)

        # Persist attempt traces to cross-run memory (all attempts, success or failure)
        for i, att in enumerate(episodic.attempts):
            mem_store.save_attempt(
                mem_run_id, selected["rule_id"], i + 1,
                att.get("approach", "")[:500],
                False,  # individual attempts within a rule are always pre-resolution
                att.get("result", ""),
                att.get("reflection", "")[:500],
                att.get("lesson", "")[:200],
                "",  # banned patterns are in semantic memory, not per-attempt
                0.0,
            )
        # Save distilled lessons from failed approaches
        for att in episodic.attempts:
            lesson = att.get("lesson", "").strip()
            if lesson and not rule_succeeded:
                mem_store.save_lesson(rule_category, lesson,
                                     mem_run_id, selected["rule_id"])

        # Emit graph state for dashboard DAG visualization
        run_log.log("graph_state", "system", graph.snapshot())

        # Emit rich rule_complete summary — the key event for per-rule timeline views
        reflections_for_rule = [a.get("reflection", "") for a in episodic.attempts]
        final_plateau = detect_plateau(reflections_for_rule, window=3)
        run_log.log("rule_complete", "harness", {
            "rule_id": selected["rule_id"],
            "title": selected["title"],
            "category": rule_category,
            "outcome": "remediated" if rule_succeeded else "escalated",
            "escalation_reason": None if rule_succeeded else escalation_reason,
            "attempts": attempt if rule_succeeded else (attempt - 1),
            "wall_time_s": round(rule_wall_time, 1),
            "approaches_tried": [a[:160] for a in approaches_tried],
            "reflections_count": len(episodic.attempts),
            "reflector_plateaued": final_plateau,
            "bans_at_completion": len(state.semantic.banned_patterns),
            "architect_reengagements": reengagements_count,
            "iteration": outer_iter,
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

    summary_data = {
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
    }
    run_log.log_summary(summary_data)

    # Persist run summary and close memory store
    mem_store.end_run(mem_run_id, summary_data)
    # Persist all banned patterns discovered in this run
    for ban in state.semantic.banned_patterns:
        # Save as attempt-level bans so they're queryable across runs
        mem_store.save_attempt(
            mem_run_id, "_global_ban", 0, "", False, "", "", "", ban, 0.0)
    logger.info("Memory store: %s", mem_store.summary())
    mem_store.close()

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
