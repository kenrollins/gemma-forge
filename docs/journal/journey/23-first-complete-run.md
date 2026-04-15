---
id: journey-23-first-complete-run
type: journey
title: "The First Complete Run: 270 Rules, 13.5 Hours, and a Cascade We Didn't See Coming"
date: 2026-04-13
tags: [L4-orchestration, reflexion-loop, cross-run-learning, memory]
related:
  - journey/22-context-graphs-and-the-memory-question
  - journey/14-overnight-run-findings
  - journey/18-second-overnight-run
  - improvements/01-architect-reengagement
one_line: "Run 1 processed all 270 STIG rules — 85 remediated, 157 escalated — and the forensics revealed both a catastrophic cascade (RPM DB corruption) and a deeper problem: the cross-run memory system I'd carefully designed wasn't actually wired up to learn."
---

# The First Complete Run

## The story in one sentence

The v5 harness ran overnight, processed every single STIG rule without
crashing, and the result was simultaneously a triumph (it finished!)
and a wake-up call (the learning system I built isn't actually
learning).

## The overnight result

Run 1 kicked off at 5:38 PM on Saturday. By the Sunday morning check at
7:07 AM — 13.5 hours later — it had finished. Every rule processed.
No crashes, no hangs, no unrecoverable states. After weeks of runs
that died mid-way through from context overflows, broken checkpoints,
and infinite retry loops, just *finishing* felt like a milestone.

The numbers:

| Metric | Count |
|--------|-------|
| Remediated | 85 (31.5%) |
| Escalated | 157 (58.1%) |
| Skipped | 28 (10.4%) |

11,518 events. 26.7 MB of structured JSONL. The biggest log file
this project has produced by an order of magnitude.

## The RPM cascade

The category breakdown told the real story:

| Category | Fix Rate | Notes |
|----------|----------|-------|
| authentication | **100%** (23/23) | Clean sweep |
| kernel | **68%** (28/41) | Strongest non-trivial category |
| audit | **6%** (4/65) | Something is very wrong |
| ssh | **6%** (1/18) | Same problem |

Authentication rules? Perfect. Kernel sysctl rules? Mostly good.
But audit and SSH — categories where the fixes were probably
*correct* — cratered at 6%.

I dug into the lessons table. The SQLite memory store had captured
644 lessons during the run. And 397 of them — sixty-two percent —
mentioned the RPM database.

The pattern was obvious once it surfaced: somewhere early in the run,
a remediation broke the RPM database on the target VM. After that,
`oscap` (the STIG evaluator) couldn't verify *anything* that
required package metadata. The fixes were going in correctly — the
health checks passed, the config files were right — but the
evaluator kept returning FAIL because it couldn't open the RPM DB
to cross-reference package state.

The Reflector saw it. Over and over:

> "The remediation is technically successful, but the evaluation
> tool (oscap) is failing due to a corrupted or inaccessible RPM
> database."

> "Stop modifying sysctl and prioritize repairing the RPM database
> to enable verification."

The Reflector knew. It said "stop trying, fix the RPM DB" in 406
separate reflections. But the harness kept grinding because the
knowledge wasn't *actionable* — it lived in the lessons list but
never reached the Worker's hands in a way that could change
behavior.

## The real problem: the learning loop was open

This is where the morning turned from "analyze the run results"
to "audit the entire cross-run architecture." I'd built a
beautiful memory system — SQLite with WAL mode, four tables,
lesson weights, category stats, the whole context graph from
[entry 22](22-context-graphs-and-the-memory-question.md). But
tracing the actual data flow from storage through hydration to
prompt injection surfaced five gaps:

**1. Lesson weights never changed.** Every single lesson in the
database had weight 0.5 — the default. The `update_lesson_weight()`
method existed, had clean code, had proper up/down logic. Nobody
called it. Success didn't boost lessons. Failure didn't decay them.
The ranking system was a no-op.

**2. Only 3 lessons reached the prompt.** The semantic memory
summary — the text that actually gets injected into the Architect's
and Worker's prompts — showed the last 3 lessons. Not the best 3.
Not the most relevant 3. The *last* 3, by insertion order. With 644
lessons stored and 20 loaded at hydration, the agents saw... 3.

**3. No per-item cross-run memory.** I built
`query_prior_attempts(item_id)` specifically so Run 2 could ask
"what happened to this exact rule last time?" It was never called.
When the Worker started on a rule that had been tried 3 times in
Run 1 and escalated due to RPM corruption, it had no idea.

**4. No category-specific lessons.** I built
`load_lessons(category)` so a Worker tackling a `kernel` rule
would see kernel-specific insights. Never called. The Worker saw
the same 3 generic lessons regardless of what it was working on.

**5. No diversity in lesson selection.** With all weights at 0.5,
`load_all_lessons(min_weight=0.3, limit=20)` returned 20 arbitrary
lessons. In practice, this meant 20 AIDE lessons about integrity
monitoring — because they happened to be first in the table. The
RPM DB lessons, which were arguably the most important finding of
the entire run, might not even appear.

Five gaps. Every one was a case where the *storage* side was
implemented correctly and the *retrieval-and-injection* side was
either missing or truncated. The database was learning. The agents
weren't reading.

## The meta-question

There was a moment of pause: is this cheating?

The RPM DB problem was visible. I could hardcode a
`rpm --rebuilddb` recovery step. I could add a pre-flight check.
But that would be *me* fixing a problem the system discovered —
human intervention masquerading as machine learning.

The better question: does the system have the *mechanism* to act
on what it knows? If Run 2's Architect, armed with 397 lessons
about RPM DB corruption, independently decides to run
`rpm --rebuilddb` before attempting audit rules — that's the demo
money shot. That's cross-run learning working as designed.

But it can only do that if the lessons actually reach the prompt.

## The five fixes

All five changes are to `ralph.py` and touch the general harness,
not the STIG skill. Any future skill benefits from the same
improvements.

**1. Lesson weight reinforcement.** On success: boost all lessons
in that category (they were available when the agent succeeded, so
they're probably helpful). On escalation: decay them (they were
available and didn't prevent failure). Over runs, good lessons
float up, bad ones sink.

**2. Show 8 lessons, not 3.** Prioritize prior-run lessons
(tagged `[prior run]`) over within-run lessons, since cross-run
knowledge is the whole point of the memory system.

**3. Per-item cross-run history.** On first attempt at any rule,
query the memory store for prior attempts against that exact rule.
Inject the approach, outcome, and lesson from each prior attempt.
The Worker sees "this was tried 3 times before, all failed because
of RPM DB errors" before it writes a single line of bash.

**4. Category-specific lessons.** Load the top 5 lessons for the
current rule's category and inject them at priority 5 (below
semantic memory, above the final directive). When working on an
`audit` rule, the Worker sees audit-specific lessons.

**5. Diverse hydration.** Load 40 lessons instead of 20, deduplicate
by first 80 characters, cap at 3 per category to ensure diversity,
then take the top 30. The RPM DB lessons now compete fairly with
AIDE lessons instead of being crowded out.

## What happens next

Run 2. The same 270 rules, the same target VM (reset to baseline),
the same model. The only difference is what's in the memory store
and the five fixes that let the harness actually *use* it.

If the fix rate improves — especially in audit and SSH categories
where RPM corruption was the bottleneck — that's the cross-run
learning thesis validated. The harness gets smarter by running, not
by being reprogrammed.

If it doesn't improve, that's an equally important finding: maybe
644 text lessons aren't the right representation, maybe the lessons
are too specific to generalize, maybe the model can't act on
injected historical context at this scale. Either way, that's a
real learning.

Honest truth: I'm nervous. Not about whether the harness will
crash — it proved it can finish. About whether the learning is
real. There's a difference between a system that *stores* what it
learned and a system that *uses* what it learned. Run 2 is the
test.

---

## Related

- [`journey/22`](22-context-graphs-and-the-memory-question.md) —
  the architectural decision that created the memory system we just
  audited.
- [`journey/14`](14-overnight-run-findings.md) — the first overnight
  run that proved the inner loop works but revealed the missing
  Architect re-engagement.
- [`journey/18`](18-second-overnight-run.md) — the second overnight
  run that proved re-engagement and checkpoint-restore work.
