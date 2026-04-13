---
id: journey-11-the-missing-reflector
type: journey
title: "Journey: The Missing Reflector — What Vibe Coding Misses"
date: 2026-04-10
tags: [L4-orchestration, reflexion-loop, discovery]
related:
  - journey/08-model-architecture-revision
  - journey/13-ralph-persistence-retry-budget
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "We built three agents, proved tool calling worked, ran successful overnight tests, celebrated — and then realized we were missing a foundational component of the reflexion architecture we set out to prove."
---

# Journey: The Missing Reflector — What Vibe Coding Misses

## The story in one sentence
We built three agents, proved tool calling worked, ran successful
overnight tests, celebrated — and then realized we were missing a
foundational component of the reflexion architecture we set out to
prove.

## How it got missed

The build started with a PRD that described four GPU roles:
Architect, Worker, Auditor, Sentry. As we worked through Phase 1-3,
the architecture evolved based on real hardware constraints:

1. The 31B didn't fit on one L4 → TP=2 → Architect and Worker share GPUs 0+1
2. Tool calling validation consumed attention → the loop worked! Ship it!
3. The Auditor was a rubber stamp → expanded to real audit tools
4. Ken asked "what about GPU 3?" → we dropped Sentry, moved to Nemotron PP=2
5. The cross-model eval was working, the throughput data was rich →
   we were excited about the TP vs PP story

At no point did anyone stop to ask: **"Are three agents actually the
right architecture for a reflexion loop?"**

The answer was no. A proper reflexion / Ralph loop has FOUR cognitive
functions:

| Function | Purpose | We had it? |
|---|---|---|
| Plan | Decide what to do | ✓ Architect |
| Execute | Do it | ✓ Worker |
| Evaluate | Did it work? | ✓ Auditor |
| **Reflect** | **WHY did it fail? What should change?** | **✗ Nobody** |

The Sentry was originally supposed to be a "watchdog" — monitoring
for collateral damage. That's a monitoring function, not a reflection
function. Even if Sentry had been wired in, it wouldn't have filled
the reflexion gap. The four-GPU-four-role mapping was cosmetic
(one thing per GPU) rather than architectural (what cognitive
functions does the loop need?).

## What the Reflector does

The Reflector runs ONLY after a revert — not every iteration. It
analyzes the pattern of failures across the run and generates
strategic guidance for the Architect.

Without Reflector (what we had):
```
Iteration 5: Worker uses sed to modify aide.conf → breaks syntax → reverted
Iteration 6: Architect sees "aide.conf sed failed" → picks a different rule
Iteration 9: Worker uses sed to modify sshd_config → breaks syntax → reverted
Iteration 10: Architect sees "sshd_config sed failed" → picks a different rule
```

The Architect never learns that SED IS THE PROBLEM. It just avoids
the specific rules that failed, not the approach that caused them to
fail.

With Reflector:
```
Iteration 5: sed breaks aide.conf → reverted
Reflector: "Failure pattern: sed commands on config files with non-standard
  syntax cause parsing errors. Strategic recommendation: use cat with
  heredoc to replace entire config blocks, or use the application's
  native config tools (aide --config-check, etc.)"
Iteration 6: Architect reads reflection → changes approach for ALL
  subsequent config file modifications
```

The Reflector produces meta-reasoning that changes the Architect's
STRATEGY, not just its target selection. That's the difference between
a retry loop and a learning loop.

## Where to put the Reflector

Ken asked the critical question: "Which model reasons better — Gemma
or Nemotron?" The benchmarks are clear:

| | Gemma 4 31B | Nemotron 30B |
|---|---|---|
| MMLU-Pro | 85.2% | 78.3% |
| AIME | 89.2% | 82.9% |

The Reflector does the HARDEST cognitive task — pattern analysis
across multiple failures, abstraction, strategic guidance. It needs
the strongest reasoner.

The Reflector runs on Gemma (GPUs 0+1), sharing the engine with
Architect and Worker. They're all sequential — no contention. The
Reflector only fires after reverts, adding zero latency to successful
iterations.

## The architectural split

```
GEMMA 4 31B (GPUs 0+1) — Internal reasoning:
  Architect → plans
  Worker → executes
  Reflector → reflects on failures (same model = coherent strategy)

NEMOTRON 30B (GPUs 2+3) — External evaluation:
  Auditor → independently checks the work (different model = catches
            blind spots the Gemma team would miss)
```

The cross-model boundary is between the DOERS and the CHECKER.
The Reflector is on the same side as the Architect because its
output feeds directly into the Architect's next turn — having them
on the same model family means the strategic guidance is in a
"language" the Architect naturally understands.

## The vibe coding lesson

This is a genuine gotcha of iterative, AI-assisted development:

1. Each individual step was correct and well-reasoned
2. We tested, measured, debugged, documented at every stage
3. The system WORKED — 36 rules fixed in one run
4. But we were solving implementation problems (VRAM, tool calling,
   context overflow) instead of stepping back to ask "is the
   ARCHITECTURE right?"

Ken caught it by asking: "By only having an agent team of 3, are we
properly implementing the right architecture for the ralph loop
construct we are trying to prove out?"

That's the kind of question that gets lost in the momentum of
building. It's also the kind of question that anyone reviewing this
architecture would ask in the first five minutes. Better to catch
it now — and document that we caught it — than to ship a three-agent
system and call it reflexion.

## Key artifacts

- `gemma_forge/harness/ralph.py` — updated with Reflector agent
- `skills/stig-rhel9/prompts/reflector.md` — reflection prompt
- `gemma_forge/harness/agents.py` — REFLECTOR_INSTRUCTION
