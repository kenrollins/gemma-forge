"""
Tests for harness property: AGENT TURNS ARE BOUNDED IN ACTIONS

Why: Failure mode 1 (tool-call explosion) — LLMs default to retry-on-
failure for tool calls. The harness must enforce a per-turn action
budget that competes with and overrides the LLM's local retry instinct.
This property must hold REGARDLESS of which tool, which agent, or which
skill is in use.

These tests use real LLM calls (vLLM on :8050) but synthetic agents and
synthetic dummy tools so the property is verified in isolation from any
specific skill. They do not require the VM.

This file is Tier 4 of the test plan in tests/PLAN.md.

Also tests one related property: REFLECTOR PROMPTS PRODUCE PARSEABLE
DISTILLED OUTPUTS. The Reflector is the sole producer of distilled
episodic memory entries, and the harness's distillation property
depends on the Reflector reliably producing the `DISTILLED:` field in
its response.
"""

# Note: do NOT add `from __future__ import annotations` to this file.
# ADK's FunctionTool parser inspects runtime type annotations on tool
# functions, and future-annotations makes them strings at runtime, which
# the parser cannot handle. See docs/whitepaper/gotchas/ — this has tripped
# us before.
import logging

import pytest
from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService

from gemma_forge.harness.ralph import _run_agent_turn
from gemma_forge.models.vllm_llm import VllmLlm
from gemma_forge.skills.loader import load_skill

# Silence noisy loggers during tests
logging.getLogger("asyncssh").setLevel(logging.WARNING)
logging.getLogger("google.adk").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)


@pytest.fixture
def llm() -> VllmLlm:
    return VllmLlm(
        model="gemma-4-31B-it",
        base_url="http://localhost:8050/v1",
        served_model_name="/weights/gemma-4-31B-it",
        max_tokens=512,
    )


@pytest.fixture
def session_service() -> InMemorySessionService:
    return InMemorySessionService()


# =============================================================================
# Synthetic dummy tools — used so property tests don't depend on apply_fix,
# stig_scan, or any specific skill's tool functions.
# =============================================================================

_fake_tool_calls: list = []


async def always_fail_tool(message: str) -> str:
    """Always return a failure string. Used to provoke retry behavior.

    Args:
        message: arbitrary text to echo back in the failure reason.
    """
    _fake_tool_calls.append(("always_fail_tool", message))
    return f"FAILED: deliberate failure: {message}"


async def always_succeed_tool(message: str) -> str:
    """Always return a success string. Used to verify happy path.

    Args:
        message: arbitrary text to echo back in the success result.
    """
    _fake_tool_calls.append(("always_succeed_tool", message))
    return f"OK: {message}"


# =============================================================================
# Property: Agent turns cap at max_tool_calls regardless of tool function
# =============================================================================


class TestToolCallCapIsGeneric:
    """The cap must work for ANY tool, not just apply_fix."""

    @pytest.fixture(autouse=True)
    def _reset_fake_calls(self):
        _fake_tool_calls.clear()
        yield
        _fake_tool_calls.clear()

    async def test_property_cap_fires_on_always_fail_tool_with_loose_prompt(self, llm, session_service):
        """The cap must catch retry behavior on any failing tool."""
        agent = Agent(
            name="cap_test_fail",
            model=llm,
            instruction=(
                "You are a test agent. Call always_fail_tool with any message. "
                "If it fails, try again with a different message. Keep trying until it succeeds."
            ),
            tools=[always_fail_tool],
        )
        response = await _run_agent_turn(
            agent, session_service,
            "Call always_fail_tool with message='first try'. If it fails, retry.",
            run_log=None, max_tool_calls=1,
        )
        # The cap should have fired — synthetic response signals it
        assert "tool cap reached" in response, (
            f"Expected cap-reached message, got: {response[:300]}"
        )
        # And the tool was only called once (maybe 2 if the LLM was extra persistent,
        # but never 15 like the overnight run)
        assert len(_fake_tool_calls) <= 2, (
            f"Expected tool cap to prevent runaway retries, got {len(_fake_tool_calls)} calls"
        )

    async def test_property_cap_allows_single_successful_tool_call(self, llm, session_service):
        """The cap doesn't interfere with normal one-call behavior."""
        agent = Agent(
            name="cap_test_ok",
            model=llm,
            instruction=(
                "You are a test agent. Call always_succeed_tool with message='hello', "
                "then return a brief text response describing what you did."
            ),
            tools=[always_succeed_tool],
        )
        response = await _run_agent_turn(
            agent, session_service,
            "Call always_succeed_tool with message='hello' and summarize the result.",
            run_log=None, max_tool_calls=1,
        )
        # Should have exactly one tool call, no cap firing
        assert "tool cap reached" not in response, (
            f"Cap should not fire on single successful call. Response: {response[:300]}"
        )
        assert len(_fake_tool_calls) == 1, (
            f"Expected exactly 1 tool call, got {len(_fake_tool_calls)}"
        )
        assert _fake_tool_calls[0][0] == "always_succeed_tool"

    async def test_property_cap_allows_text_only_response(self, llm, session_service):
        """An agent with tools is allowed to return text without calling any tool."""
        agent = Agent(
            name="cap_test_text",
            model=llm,
            instruction=(
                "You are a test agent. Do NOT call any tools. "
                "Respond only with a short greeting."
            ),
            tools=[always_succeed_tool],
        )
        response = await _run_agent_turn(
            agent, session_service,
            "Please say hello without calling any tools.",
            run_log=None, max_tool_calls=1,
        )
        # Should not fire the cap
        assert "tool cap reached" not in response
        # And no tool calls happened
        assert len(_fake_tool_calls) == 0


# =============================================================================
# Property: max_tool_calls=0 blocks all tool invocations
# =============================================================================


class TestToolCallCapZero:
    @pytest.fixture(autouse=True)
    def _reset_fake_calls(self):
        _fake_tool_calls.clear()
        yield
        _fake_tool_calls.clear()

    async def test_property_max_zero_blocks_first_tool_call(self, llm, session_service):
        """An agent configured with zero tool calls allowed must be prevented
        from invoking any tool, even if the LLM tries."""
        agent = Agent(
            name="cap_test_zero",
            model=llm,
            instruction="Call always_succeed_tool with message='test'.",
            tools=[always_succeed_tool],
        )
        response = await _run_agent_turn(
            agent, session_service,
            "Call always_succeed_tool with message='test' now.",
            run_log=None, max_tool_calls=0,
        )
        # Cap should fire on the first attempt
        assert "tool cap reached" in response, (
            f"max_tool_calls=0 should block all tool calls. Response: {response[:300]}"
        )


# =============================================================================
# Property: Strict prompts avoid the need for the cap — voluntary stop works
# =============================================================================


class TestVoluntaryStop:
    @pytest.fixture(autouse=True)
    def _reset_fake_calls(self):
        _fake_tool_calls.clear()
        yield
        _fake_tool_calls.clear()

    async def test_property_strict_prompt_stops_voluntarily_after_failure(self, llm, session_service):
        """When the prompt explicitly says 'exactly once, do not retry',
        the LLM should voluntarily stop after one tool call even if it failed.
        The harness cap is defense in depth, not the primary mechanism."""
        agent = Agent(
            name="strict_test",
            model=llm,
            instruction=(
                "You are a test agent. Call always_fail_tool EXACTLY ONCE with any message. "
                "After the tool returns, produce a brief text response describing the outcome. "
                "Do NOT call always_fail_tool a second time even if it fails — that is expected."
            ),
            tools=[always_fail_tool],
        )
        response = await _run_agent_turn(
            agent, session_service,
            "Call always_fail_tool with message='strict test'. Expect it to fail.",
            run_log=None, max_tool_calls=1,
        )
        # The cap should NOT have fired — the LLM voluntarily stopped
        assert "tool cap reached" not in response, (
            f"Strict prompt should produce voluntary stop, cap should not fire. "
            f"Response: {response[:300]}"
        )
        assert len(_fake_tool_calls) == 1


# =============================================================================
# Property: Reflector produces a DISTILLED field with the new prompt
#
# The episodic memory distillation (failure mode 4 mitigation) depends on the
# Reflector reliably emitting DISTILLED: as part of its response. This test
# verifies the prompt contract holds against the real model.
# =============================================================================


class TestReflectorDistilledOutput:
    async def test_property_reflector_outputs_distilled_field(self, llm, session_service):
        """Given a synthetic failure history, the Reflector should emit a
        DISTILLED: line that our parser can extract."""
        skill = load_skill("stig-rhel9")
        reflector_prompt = skill.get_prompt("reflector")

        reflector = Agent(
            name="reflector_test",
            model=llm,
            instruction=reflector_prompt,
            tools=[],  # no tools
        )

        # Synthetic failure history — this is exactly what the harness feeds
        # the Reflector after a failed attempt.
        message = """
Rule: xccdf_org.ssgproject.content_rule_aide_check_audit_tools (AIDE must verify audit tools)
Attempt 3 FAILED. This is attempt 3 — the loop will keep grinding until the time budget runs out.

Worker's approach:
Added 'sha256' to /etc/aide.conf for audit tool paths and ran `aide --init`.

Evaluation result:
{"passed": false, "health_ok": true, "rule_ok": false, "journal_clean": true,
 "summary": "rule check still failing"}

Prior attempts on aide_check_audit_tools (2 tries, showing last 2):
  Attempt 1: tried adding attrs=sha256 to aide.conf, rule check still failing
  Attempt 2: tried rebuilding database after config change, rule check still failing

Analyze: WHY did this approach fail? What should the Worker try FUNDAMENTALLY
DIFFERENTLY on the next attempt? After multiple failures, consider radically
different strategies — not just tweaks to the same approach.

Output structured guidance — include ALL four fields:
BANNED: <regex pattern to reject in future scripts>
PREFERRED: <alternative approach to try>
LESSON: <one-sentence strategic insight>
DISTILLED: <one-sentence summary of this attempt and what was learned, <200 chars, for compact memory>
"""

        response = await _run_agent_turn(
            reflector, session_service, message, run_log=None, max_tool_calls=1,
        )

        # The Reflector should produce the DISTILLED field
        assert "DISTILLED:" in response, (
            f"Reflector did not produce DISTILLED field. Response: {response[:500]}"
        )

        # And our extraction logic should find a non-empty distilled lesson
        distilled = ""
        for line in response.split("\n"):
            if line.strip().upper().startswith("DISTILLED:"):
                distilled = line.strip()[10:].strip()
                break
        assert len(distilled) > 10, (
            f"DISTILLED field was empty or very short: {distilled!r}"
        )

    async def test_property_reflector_also_produces_banned_and_preferred(self, llm, session_service):
        """The other structured fields should also appear — distilled isn't the
        only one. If the Reflector consistently omits these, our learning
        propagation is weaker than designed."""
        skill = load_skill("stig-rhel9")
        reflector = Agent(
            name="reflector_test2",
            model=llm,
            instruction=skill.get_prompt("reflector"),
            tools=[],
        )
        message = """
Rule: xccdf_org.ssgproject.content_rule_sudo_remove_nopasswd (Remove NOPASSWD from sudoers)
Attempt 2 FAILED.
Worker's approach: sed -i 's/NOPASSWD://' /etc/sudoers
Evaluation: {"passed": false, "health_ok": false, "rule_ok": true, "summary": "mission app healthcheck failed"}
Prior attempts: 1 prior, similar sed approach, health broke after applying.

Analyze and output:
BANNED: <pattern>
PREFERRED: <approach>
LESSON: <insight>
DISTILLED: <summary>
"""
        response = await _run_agent_turn(
            reflector, session_service, message, run_log=None, max_tool_calls=1,
        )
        # At least two of the four should be present — the Reflector isn't
        # perfectly reliable but should produce most of them
        present = sum([
            "BANNED:" in response,
            "PREFERRED:" in response,
            "LESSON:" in response,
            "DISTILLED:" in response,
        ])
        assert present >= 3, (
            f"Reflector only produced {present} of 4 structured fields. "
            f"Response: {response[:500]}"
        )
