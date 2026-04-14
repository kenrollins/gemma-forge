---
id: journey-24-run-2-cross-run-learning
type: journey
title: "Run 2: The Learning Worked, and Then We Found the Next Wall"
date: 2026-04-13
tags: [L4-orchestration, reflexion-loop, cross-run-learning, memory]
related:
  - journey/23-first-complete-run
  - journey/22-context-graphs-and-the-memory-question
  - gotchas/shared-memory-db
  - gotchas/clutch-not-wired
one_line: "Run 2 doubled the fix rate (35% → 58%), flipped 59 rules from escalated to remediated, and proved the cross-run memory works — then revealed a new cascade, a lesson quality problem, and the uncomfortable question of whether memory that was right yesterday can be wrong tomorrow."
---

# Run 2: The Learning Worked, and Then We Found the Next Wall

## The story in one sentence

The cross-run learning system we fixed that morning worked exactly
as designed — 59 rules flipped from failure to success, the fix
rate nearly doubled — and then the run hit a completely different
cascade that taught us something deeper about ordering, memory
fidelity, and the limits of what text-based lessons can carry.

## The numbers

| | Run 1 | Run 2 | Delta |
|---|---|---|---|
| **Fix rate** | 35% | **58%** | +23pp |
| **Remediated** | 85 | **145** | +60 |
| **Escalated** | 157 | **104** | -53 |
| **Duration** | 13.5h | 13.0h | -0.5h |
| **Total tokens** | 4.97M | 5.14M | similar |

59 rules flipped from escalated to remediated. 1 regressed. A
59:1 win-to-loss ratio from memory alone.

## What the learning actually did

We woke up that morning, analyzed Run 1, found five gaps in the
memory-to-prompt pipeline ([entry 23](23-first-complete-run.md)),
fixed them, and kicked off Run 2 before lunch. The harness loaded
644 lessons, 50 banned patterns, and a full category difficulty
model from Run 1.

The effects were visible immediately:

**SSH went from 6% to 78%.** In Run 1, SSH fixes were probably
correct but the evaluator couldn't verify them because the RPM
database was corrupted. Run 2's agents had lessons warning about
RPM DB issues and adjusted their approaches. 13 SSH rules that
escalated in Run 1 were remediated in Run 2.

**Kernel went from 68% to 97%.** Same story — the RPM cascade
blocked verification in Run 1. With the cascade lessons loaded,
Run 2 handled kernel rules cleanly. 37 of 38 remediated.

**Service config went from 33% to 100%.** Package management
went from 50% to 86%. Audit went from 6% to 30%.

**The Architect learned to sequence by difficulty.** Run 2
front-loaded easy categories (authentication, kernel, package
management) and deferred hard categories (audit, SSH) to the
end. This wasn't programmed — the category difficulty model from
Run 1 fed into the Architect's context, and it made the rational
choice to bank easy wins first.

**The lesson weight system started producing real signal.** After
Run 2, the lesson weights spread from the uniform 0.5 of Run 1
to a genuine distribution: 163 lessons above 0.9, 188 above 0.8,
with the rest decaying based on category outcomes. Good lessons
are floating up. Bad ones are sinking.

## The new cascade: audit immutable mode

Around hour 8 of the run, the harness successfully remediated
`audit_rules_immutable` — a STIG rule that *requires* setting the
audit system to immutable mode (`-e 2`). This is one of those
rules where doing what the STIG asks actively prevents you from
fixing other STIG rules. The moment `-e 2` was set, every
remaining audit rule that needed to load into the kernel was
blocked.

The Reflector caught it within 200 seconds. "The audit system is
configured with the `-e 2` flag (immutable mode), which locks the
configuration until the next reboot." It said this 135 times
across the remaining audit rules.

But the Architect kept selecting audit rules one by one, noting
the immutable flag each time but never changing its strategy.
There was no lesson that said "process audit_rules_immutable
LAST" — and the harness has no ordering mechanism. The task graph
has zero edges. No dependencies, no sequencing constraints.

**The impact:** ~44 syscall-based audit rules escalated that
might have succeeded if processed before the immutable flag was
set. Estimated: 20-35 additional remediations lost to ordering.

This is a different beast from Run 1's RPM cascade. That was
accidental — a fix broke something. This was *intentional* — the
STIG requires immutable audit, and the system correctly
implemented it. The problem is *when* it was implemented, not
*whether*.

The fix is a one-sentence addition to the skill's architect
prompt: "Process `audit_rules_immutable` last within the audit
category." But it also reveals a harness-level gap: skills should
be able to declare ordering constraints that the Architect
respects.

## The regression that taught us about lesson quality

One rule regressed: `sudo_remove_nopasswd`. Fixed in Run 1,
escalated in Run 2 after 8 attempts.

The forensics tell a story about what happens when lessons lose
critical detail during generalization. In Run 1, the Worker
succeeded by using `whoami` inside the fix script to identify the
agent's username before modifying sudoers. The lesson that was
saved abstracted this to "preserve the health agent's identity."

The abstraction was reasonable — it captured the *principle*. But
it lost the *implementation*: use `whoami`. Run 2's Worker knew
it needed to preserve "the agent" but didn't know the agent's
username. It guessed across 9 attempts. All wrong.

This is a lesson quality problem that the weight system can't
catch. The lesson kept firing (it's relevant to sudo rules), it
just didn't carry enough information to be actionable. Weight
reflects frequency of use, not implementation completeness.

## The uncomfortable memory question

Digging through the highest-weight lessons revealed something
we didn't expect: 50 SSH lessons and 16 service-config lessons
at weight 1.0, all saying "the RPM DB is broken." These were
true in Run 1. They were helpful in Run 2. But the VM gets
rebuilt between runs.

In Run 3, with a fresh VM and a healthy RPM DB, these lessons
will fire and tell the Worker to diagnose an RPM problem that
doesn't exist. The Worker will waste attempts checking something
that isn't broken, and the lessons will maintain their high
weights because the system has no way to know they're
environment-specific.

This is the question we hadn't considered when we built the
memory system: **a lesson that was right in one environment can
be wrong in another, and the weight system can't tell the
difference.** Weight measures "how often has this lesson been
present when things went well." It doesn't measure "is this
lesson still true."

We don't have a solution yet. The options range from simple
(decay all weights on VM rebuild) to complex (tag lessons with
environment hashes, add a "confidence" dimension separate from
weight). For now we're noting it as a real architectural finding
and moving on — but this is the kind of problem that separates a
demo from a production system.

## What we also found in the logs

**Two context overflow errors.** The `apply_fix` tool returned
unconstrained `dnf install` output that blew past the 16K token
limit. The harness doesn't truncate tool results before feeding
them back to the model. A max-bytes guard would prevent this.

**Token efficiency:** Escalations cost 3.1x more per rule than
remediations and consume 69% of total tokens despite being 38%
of rules. The tail is brutal — one rule burned 89K tokens before
escalating. The Reflector plateaued (said "stop trying"
repeatedly) but the Architect kept issuing CONTINUE verdicts.

**A rule categorization bug** in the skill runtime: `"sudo" in
rule_id` matches before `"audit" in rule_id`, so audit rules
about privileged sudo commands get miscategorized and receive
wrong lessons.

## Where this leaves us

Run 2 proved the thesis: cross-run learning works. The system
gets meaningfully smarter by running, not by being reprogrammed.
A 59:1 improvement-to-regression ratio is real signal, not noise.

But it also showed us three things the thesis didn't predict:

1. **Ordering matters.** Some fixes have system-wide side effects
   that block other fixes. The harness needs to either learn this
   (via lessons) or be told this (via skill-level ordering
   constraints). Today it has neither.

2. **Lesson quality matters as much as lesson weight.** A lesson
   that captures the principle but loses the implementation detail
   can cause regressions. Weight measures frequency, not fidelity.

3. **Memory fidelity across environments is an open problem.** A
   lesson that was true in one environment may be false in the
   next. The system can't distinguish between "always true" and
   "true last time."

These aren't bugs. They're the next layer of the architecture.
Run 1 showed us the memory pipeline was broken. Run 2 showed us
the memory pipeline works but the *memory content* needs more
structure than plain text lessons with a single weight scalar.

The forge keeps heating.

---

## Related

- [`journey/23`](23-first-complete-run.md) — Run 1 analysis and
  the five memory pipeline fixes that enabled Run 2.
- [`journey/22`](22-context-graphs-and-the-memory-question.md) —
  the architectural decision that created the memory system.
- [`gotchas/shared-memory-db`](../gotchas/shared-memory-db.md) —
  per-skill DB separation discovered during this analysis.
- [`gotchas/clutch-not-wired`](../gotchas/clutch-not-wired.md) —
  the adaptive concurrency gap.
