---
id: improvement-03-context-budget-assembly
type: improvement
title: "Improvement: Deterministic Context Budget per Prompt"
date: 2026-04-11
tags: [L4-orchestration, context-management, refactor]
related:
  - journey/14-overnight-run-findings
  - gotchas/context-window-edge
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Deterministic token-budget-aware prompt assembly with priority-ordered sections and distilled per-attempt lessons, replacing ad-hoc string concatenation that grew unboundedly."
---

# Improvement: Deterministic Context Budget per Prompt

**Status:** Implemented in v3 (2026-04-13). See [journey/17 — The v3 Fix Pass](../journey/17-v3-fix-pass.md). The deterministic prompt-budget assembler ships in `gemma_forge/harness/prompts.py` and is exercised on every agent turn.
**Surfaced:** 2026-04-11, analyzing overnight run findings
**Priority:** HIGH — deploy with the Worker single-action fix
**Related:** `journey/14-overnight-run-findings.md` Finding 4

## The problem

Right now the harness assembles agent prompts by concatenating whatever
state is relevant — system prompt, rule context, architect plan, episodic
memory, semantic memory — and ships it to the LLM without any token budget
enforcement. If the total prompt exceeds the model's context window (16K
for our current Gemma 4 deployment), the LLM call crashes with HTTP 400.

In the overnight run we hit this 9 times, all in the deeper attempts of
long-running rules. Each overflow was an entirely wasted LLM call.

With `max_wall_time_per_rule_s: 1200` and an unbounded Worker retry loop,
the overflows were inevitable. Even after fixing the retry loop
(`improvements/02`), we'll want a hard guarantee that no prompt will ever
exceed the budget, so we can add new state to prompts (architect
re-engagement, richer run context, more distilled lessons) without fear.

## The design

### Phase 1 — Rough token estimation

We don't need exact tokenization (which requires the model's tokenizer).
A rough estimate of "4 characters per token" is within 20% for English and
code, which is fine for budget decisions. The estimator is a 1-line function:

```python
def est_tokens(text: str) -> int:
    return len(text) // 4 + 1
```

### Phase 2 — Prompt assembler with priority order

Replace the current ad-hoc string concatenation in the inner loop with an
assembler function that takes a list of `(priority, label, content)` tuples
and builds the final prompt up to a token budget:

```python
def assemble_prompt(
    sections: list[tuple[int, str, str]],
    budget_tokens: int,
) -> str:
    """Assemble a prompt from prioritized sections within a token budget.

    Sections are given as (priority, label, content). Lower priority numbers
    are more essential — they are included first. If the budget is tight,
    higher-priority-number sections are dropped or truncated.

    Returns the assembled prompt as a single string.
    """
    sorted_sections = sorted(sections, key=lambda s: s[0])
    included = []
    used = 0
    for prio, label, content in sorted_sections:
        est = est_tokens(content)
        if used + est <= budget_tokens:
            included.append((prio, label, content))
            used += est
        else:
            # Try to truncate this section to fit
            remaining = budget_tokens - used
            if remaining > 100:
                truncated = content[: remaining * 4 - 50] + "\n[... truncated for budget ...]"
                included.append((prio, label, truncated))
                used += est_tokens(truncated)
            break

    included.sort(key=lambda s: s[0])
    return "\n\n".join(content for _, _, content in included)
```

### Phase 3 — Priority order for each agent

For the Worker's attempt turn (the current context bomb):

| Priority | Section | Typical size |
|---:|---|---|
| 0 | Current rule id + title | 100 chars |
| 1 | Directive: "call apply_fix once and return" | 200 chars |
| 2 | Architect's plan (first 400 chars) | 400 chars |
| 3 | Last 3 distilled lessons from episodic memory (this rule) | 600 chars |
| 4 | Top 5 banned patterns from semantic memory | 500 chars |
| 5 | Top 3 preferred approaches from semantic memory | 400 chars |
| 6 | Last 2 strategic lessons from semantic memory | 300 chars |

Total target: ~2500 chars ≈ 625 tokens for the user message. Plus system
prompt (~1500 tokens) plus tool schema (~500 tokens). Plus generous margin
for the in-turn tool round-trip (~2000 tokens). Target budget: **6–8K tokens
max**, well under the 16K limit.

For the Architect's rule-selection turn:

| Priority | Section |
|---:|---|
| 0 | Current run summary: fixed/escalated/skipped counts |
| 1 | Top 10 remaining failing rules |
| 2 | Top 5 banned patterns |
| 3 | Top 3 lessons |
| 4 | Last 5 remediated rules (for pattern reference) |
| 5 | Full escalated rules list |
| 6 | Full remaining rules list (beyond top 10) |

Target: ~3000 tokens for the user message. Architect system prompt is
bigger than Worker's (~2000 tokens). Total budget for architect turn:
**~7K tokens**.

For the Reflector's analysis turn:

| Priority | Section |
|---:|---|
| 0 | Current rule id + title |
| 1 | Latest attempt's approach + result (truncated) |
| 2 | Prior 3 attempts' distilled lessons |
| 3 | Directive: structured output format |
| 4 | Full episodic history (if space allows) |

Target: ~2000 tokens user message. Reflector system prompt ~1500. Budget:
**~5K tokens**.

### Phase 4 — Distillation for episodic memory

Instead of storing raw `approach` (200 chars) + `result` (80 chars) +
`reflection` (120 chars) per attempt in episodic memory, distill each failed
attempt into a **single one-line lesson** (~100 chars) produced by the
Reflector:

```
Attempt 3: Tried to edit /etc/aide.conf directly; failed because the AIDE
db needs --init after config changes. Reflector: use `aide --init --verbose`.
```

This is generated by adding a line to the Reflector's output requirement:

```
DISTILLED_LESSON: <one-sentence summary of this failure and the lesson learned>
```

The distilled lesson is what's stored in episodic memory going forward, not
the full raw text. The raw text is still in the event log for post-run
analysis, but it doesn't pollute subsequent prompts.

With 3 distilled lessons × 100 chars = 300 chars = ~75 tokens for the
full episodic context. Compare to the overnight run's episodic memory,
which was 15–20 attempts × ~400 chars = 6–8K chars = 1.5–2K tokens. A
20× reduction.

## Instrumentation

Every prompt assembly emits a structured event:

```python
run_log.log("prompt_assembled", agent.name, {
    "budget_tokens": budget,
    "used_tokens": used,
    "sections_included": [label for _, label, _ in included],
    "sections_dropped": [label for _, label, _ in sorted_sections[len(included):]],
    "rule_id": current_rule_id,
})
```

This lets us measure, after each run:

- How often the budget was approached
- Which sections got dropped when it was tight
- Whether the fix is working (no more HTTP 400s)

## Open question: shrink or split?

If a rule's episodic history is legitimately long and valuable, do we:

- **(A)** Truncate / summarize older entries (current proposal), or
- **(B)** Split the inner loop into two LLM calls: one to summarize history,
  one to propose the next action?

Option A is simpler but loses information. Option B preserves information
but doubles the LLM cost per attempt. For the current run where attempts
take 30–60s each and we're time-budgeted anyway, doubling LLM calls per
attempt is a big cost. Start with Option A, evaluate.

## Testing the fix

1. **Synthetic oversized input.** Construct a fake episodic memory with 30
   attempts worth of text, run assembly with budget 8K, verify truncation
   behaves deterministically and the output is under budget.
2. **Regression test against overnight run.** Replay the overnight run's
   state at each overflow point, verify the new assembler produces prompts
   under 10K tokens for each.
3. **Empirical run.** After Layer 1 + 2 of `improvements/02` and this fix
   are deployed, run for 2 hours and confirm zero HTTP 400 errors in the
   run log.

## Estimated effort

- `est_tokens` + `assemble_prompt` functions: 30 minutes
- Distilled lesson Reflector output + parsing: 30 minutes
- Rewiring Worker/Architect/Reflector prompt assembly to use the new
  assembler: 1 hour
- Instrumentation + validation: 30 minutes

**Total: ~2.5 hours.**

## Relationship to other improvements

This fix composes cleanly with `improvements/02-worker-single-action-enforcement.md`
and `improvements/01-architect-reengagement.md`. Both of those fixes
*reduce* the need for aggressive context budgeting (fewer internal retries =
less in-turn accumulation; architect re-engagement means fewer deep inner
loops). The budget enforcer is the belt to their suspenders: even if those
fixes partially regress or new agents get added, the budget will catch
overflows before they become HTTP 400s.
