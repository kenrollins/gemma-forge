---
id: improvement-02-worker-single-action-enforcement
type: improvement
title: "Improvement: Enforce One Action Per Agent Turn"
date: 2026-04-11
tags: [L4-orchestration, tool-calling, reflexion-loop, refactor]
related:
  - journey/14-overnight-run-findings
  - gotchas/context-window-edge
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Per-turn action budget capping tool calls to one per turn, preventing worker agents from bypassing the outer reflexion loop with hidden internal retries."
---

# Improvement: Enforce One Action Per Agent Turn

**Status:** Implemented in v3 (2026-04-13). See [journey/17 — The v3 Fix Pass](../journey/17-v3-fix-pass.md). The per-turn action budget lives in the Ralph loop and defaults to 1 tool call per Worker turn.
**Surfaced:** 2026-04-11, analyzing overnight run findings
**Priority:** CRITICAL — must fix before next run
**Related:** `journey/14-overnight-run-findings.md` Finding 4

## The problem

A single call to `_run_agent_turn()` is supposed to represent one discrete
action by an agent — one LLM call, one tool invocation, one result, one text
response, turn ends. The outer reflexion harness then decides whether to
retry, reflect, or move on.

In practice, the Worker LLM treats a tool failure as a signal to retry with
different arguments. On `APPLY_FAILED`, the LLM issues another `apply_fix`
call — and then another, and another. In the overnight run's first context
overflow, we observed **15 consecutive `apply_fix` calls in a single agent
turn over ~6 minutes of wall time**, with zero `agent_response` events
between them. The turn was trapped in an LLM-driven retry loop the harness
never saw.

This defeats the entire reflexion architecture:

- The Reflector never runs between the hidden internal retries
- The harness's revert logic never fires between them either
- The "fresh context per turn" principle is violated inside a turn (each
  internal retry sees the growing tool-call history of prior failed retries)
- Our instrumentation undercount is ~5–10×: a "single attempt" in the log
  represents 5–15 actual tool invocations

It also causes the context overflow: 15 tool_call + tool_result pairs add
~6000 tokens to the in-turn conversation, pushing the prompt past 16K tokens
and crashing the LLM call.

## Why this is the #1 fix

Until this is solved, **no other improvement to the reflexion loop matters**,
because the loop isn't actually running reflexion — it's running an LLM
internal retry loop with reflexion wrapped around the outside. Fixing the
Architect re-engagement, fixing plateau detection, fixing the context budget
— none of these produce correct behavior if the Worker is doing 14 hidden
retries inside every one of its "attempts."

## The fix — two layers

### Layer 1: Prompt-level instruction

Update `skills/stig-rhel9/prompts/worker.md` (and any future skill's worker
prompt) to explicitly cap to one action:

```
YOUR JOB:
1. Read the Architect's plan from the conversation history.
2. Call apply_fix ONCE with the fix_script, revert_script, and description.
3. Return a brief text summary of what you did and what the tool returned.

RULES:
- Call apply_fix EXACTLY ONCE per turn. Do not call it a second time.
- If apply_fix returns APPLY_FAILED, that is EXPECTED — the outer harness
  will revert and schedule a new attempt with Reflector guidance. Your job
  is done once you have made ONE tool call.
- Retrying apply_fix yourself bypasses the reflection step and defeats the
  whole architecture. Do not do it.
```

This is a zero-code, zero-risk change that should dramatically reduce
internal retries. It relies on the LLM following instructions, which is a
90%-solution not a 100%-solution.

### Layer 2: Harness enforcement

In `gemma_forge/harness/ralph.py`, modify `_run_agent_turn()` to hard-cap
tool calls to one per turn. The approach depends on ADK internals, but the
general shape is:

```python
async def _run_agent_turn(agent, session_service, message, run_log=None):
    turn_start = time.time()
    runner = Runner(app_name="gemma-forge", agent=agent, session_service=session_service)
    session = session_service.create_session(app_name="gemma-forge", user_id="operator")

    response_parts = []
    first_token_time = None
    total_tokens = {"prompt": 0, "completion": 0}
    tool_calls_seen = 0
    MAX_TOOL_CALLS_PER_TURN = 1

    async for event in runner.run_async(
        user_id="operator", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        # ... existing event handling ...

        # Count tool calls; after the first one's result is delivered,
        # stop yielding to the agent so it cannot make a second call
        if event has function_call:
            tool_calls_seen += 1
            if tool_calls_seen > MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Worker attempted tool call #%d in single turn — cutting off", tool_calls_seen)
                run_log.log("tool_call_capped", agent.name, {
                    "attempted_count": tool_calls_seen,
                    "allowed": MAX_TOOL_CALLS_PER_TURN,
                })
                break  # end the turn, let outer harness handle
```

ADK may support a `max_tool_calls` knob on Runner or Agent that does this
cleanly — worth checking before hand-rolling the interception. If ADK
exposes it: use that. If not: manual interception per above.

The cap should probably be configurable per-agent. The Architect's tools
include `run_stig_scan` which may legitimately need one call per turn (same
as Worker). The Reflector has no tools so the cap doesn't apply. If future
skills add agents with multi-tool reasoning workflows, make it
skill-configurable.

## Instrumentation additions

When Layer 2 fires (i.e., we cut off a turn because of the cap), emit a
`tool_call_capped` event so we can measure how often the cap is needed in
practice. A future run should show this event firing rarely if Layer 1 is
effective, or frequently if the LLM insists on retrying regardless.

Also add a counter to `rule_complete`: `internal_retries_capped` = total
number of times the harness had to cut off the Worker within this rule's
attempts. High values indicate prompt tuning may be needed.

## Testing the fix

1. **Dry run on one AIDE rule.** Launch ralph against a VM restored to
   baseline, target the `aide_check_audit_tools` rule specifically, and
   verify that each attempt shows exactly one `tool_call` + `tool_result`
   followed by an `agent_response` before the next `attempt_start`.
2. **Check prompt token count.** Assemble the Worker prompt for an attempt
   deep into a rule's history (attempt 8+) and verify it stays under 10K
   tokens with margin for the turn's internal tool round-trip.
3. **Compare reflection counts.** A run with this fix should emit more
   reflection events per rule than the previous run (because every
   harness-level attempt now ends with an honest `agent_response` that
   triggers the Reflector), not fewer.

## Open question: is this a Ralph violation?

One could argue that the LLM's internal retry loop is *more* Ralph-like —
the Worker just keeps grinding until it succeeds, isn't that the whole point?

No. The Ralph doctrine is "grind until physics says stop, AND distill every
failed attempt into a learning that compounds." The internal retry loop
grinds without distilling — each retry is just a tweaked bash script, not a
reasoned response to a reflection. The outer harness-level loop is where
distillation happens (via the Reflector and semantic memory). Hiding retries
inside a single turn means those retries skip the distillation step, which
is un-Ralph in spirit if not in letter.

## Estimated effort

- Layer 1 (prompt): 5 minutes
- Layer 2 (harness): 30–60 minutes (depends on whether ADK has a native knob)
- Instrumentation + tests: 30 minutes
- Dry-run validation: 30 minutes

**Total: ~2 hours.** This should be the first thing built after the
overnight-run postmortem.
