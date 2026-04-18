---
id: journey-25-run-3-learning-plateaus
type: journey
title: "Run 3: When the Learning Curve Bends"
date: 2026-04-14
tags: [L4-orchestration, reflexion-loop, cross-run-learning, memory]
related:
  - journey/23-first-complete-run
  - journey/24-run-2-cross-run-learning
  - gotchas/shared-memory-db
one_line: "Run 3 moved the fix rate from 58% to 60%. Two percentage points after six more fixes were applied. The trajectory is still positive but the curve is bending, and the reason is exactly what the memory architecture chapter predicted: lesson accumulation has diminishing returns when you cannot tell the still-true from the stale."
---

# Run 3: When the Learning Curve Bends

Run 3 delivered a modest improvement over Run 2 (58% to 60%), but with ten regressions against fourteen wins, longer runtime, and higher token cost. It is the clearest signal yet that naive lesson accumulation has a ceiling, and that the environment fidelity problem we described hypothetically in the last entry is now showing up in the data.

## The headline numbers

| | Run 1 | Run 2 | Run 3 | Delta |
|---|---|---|---|---|
| Fix rate | 35% | 58% | **60%** | +2pp |
| Remediated | 85 | 145 | 150 | +5 |
| Escalated | 157 | 104 | 102 | -2 |
| Duration | 13.5h | 13.0h | 16.2h | +3.2h |
| Tokens | 4.97M | 5.14M | 6.49M | +1.35M |
| First-try success | 85% | 82% | **86%** | +4pp |
| Rules/hour | 20.0 | 20.8 | 16.7 | -4.1 |

The fix rate inched up. The first-try success rate jumped. But the run took longer and cost more tokens. Something got better, and something else got worse.

## What improved and what regressed

The flip analysis tells the story more clearly than the aggregate numbers:

- **Run 1 → Run 2:** 59 wins, 1 regression. A 59:1 ratio. Dramatic improvement from cross-run memory finally working.
- **Run 2 → Run 3:** 14 wins, 10 regressions. A 1.4:1 ratio. Still net-positive, but the signal-to-noise has collapsed.

The 14 wins include rules we targeted directly with skill refinements between runs: several `audit_rules_*` rules that had been blocked by the immutable cascade in Run 2 succeeded in Run 3 because the Architect now processes `audit_rules_immutable` later in the category, and `sudo_remove_nopasswd` finally succeeded after we updated the Worker prompt to run `whoami` before touching sudoers.

The 10 regressions are the more interesting finding. These are rules that succeeded in Run 2 but escalated in Run 3. The pattern:

- **3 audit rules** that happened to land after `audit_rules_immutable` even with the ordering guidance (the prompt guidance is advisory, not enforced)
- **2 kernel rules** about specific module loading patterns
- **1 authentication rule** (`use_pam_wheel_for_su`)
- **1 integrity monitoring rule** (`aide_build_database`)
- **3 miscellaneous** (`mount_option_nodev_nonroot_local_partitions`, `networkmanager_dns_mode`, `auditd_data_retention_space_left_action`)

None of the regressions have an obvious single cause. They are scattered across categories, which is the signature of the environment fidelity problem: lessons accumulated from prior environmental states are misdirecting the Worker on problems it would otherwise handle cleanly.

## The token cost

Run 3 used 1.35M more tokens than Run 2, a 26% increase, while only producing five more remediations. Tokens per rule went from 19,025 to 24,037. The Architect re-engagement loop is spending more time pivoting on rules where the Reflector is plateauing, and the accumulated lesson corpus (now over 1,500 lessons) is generating longer prompts with more context to process per turn.

This is consistent with the "lessons need curation, not just accumulation" framing from [entry 24](24-run-2-cross-run-learning.md) and the whitepaper's Section 4.3. Memory that only grows has a cost, and Run 3 is where that cost became measurable.

## Why the first-try rate still went up

Here is the interesting contradiction: first-try success went from 82% to 86%, which suggests the system *did* get better at the rules it could solve. The gains from cross-run learning on well-understood categories are real. The problem is not that the memory is useless. The problem is that the memory is fine for solving familiar problems and actively counterproductive for novel ones in changed conditions.

This matches the architecture's predicted failure mode. Weight tracks how often a lesson appears when a category succeeds, so lessons that were load-bearing in Runs 1 and 2 stay load-bearing in Run 3. When those lessons are genuinely applicable, the Worker nails the fix on attempt one. When they are stale, pointing at an RPM problem the target no longer has, or a configuration pattern from a different environment state, the Worker wastes attempts diagnosing phantom problems.

## What this means for the thesis

The thesis of the project is that the agentic harness shapes outcomes as much as the model, and that cross-run memory lets the same model improve across runs. Three runs in, the claim still holds:

- Run 1 (no memory): 35%
- Run 2 (memory pipeline working): 58%
- Run 3 (more memory): 60%

But the curve is bending. The 23-point gain from Run 1 to Run 2 was driven by unblocking the RPM cascade. The 2-point gain from Run 2 to Run 3 was driven by targeted skill refinements (the immutable-audit ordering, the Worker prompt additions). Very little of the Run 3 gain came from the memory system alone doing more of what it did in Run 2.

This is not a failure of the thesis. It is the thesis meeting its boundary.

!!! quote ""
    Memory accumulation produces improvement up to the point where stale memory starts costing more than it saves.

Production systems will need mechanisms to curate what is stored: environment tagging, weight decay on rebuild, confidence dimensions, or explicit expiration. None of those are implemented yet. Run 4 would need them to avoid further regression.

## What we learned about the ordering constraint fix

The immutable-audit ordering guidance in the Architect prompt worked, but imperfectly. It did delay the selection of the immutable rule. It was processed at the 12-hour mark in Run 3 versus the 8-hour mark in Run 2. Some audit rules that had been blocked in Run 2 (like several of the `audit_rules_dac_modification_*` family) succeeded in Run 3 because they were processed before the flag was set.

But three audit rules that Run 2 happened to process before immutable were processed after it in Run 3. Prompt guidance is not enforcement. The Architect respected the guidance most of the time, not always.

The architectural takeaway: skills need a declarative way to express ordering constraints that the harness enforces, rather than prompt hints that the Architect may or may not honor. This is on the improvement list.

## Looking forward

Three runs in, the picture is:

1. **The harness pattern works.** 35% → 60% across three runs on the same hardware and same model is real improvement.
2. **Cross-run memory is load-bearing.** Most of the improvement came from the memory architecture, not from skill changes.
3. **Memory quality matters as much as memory quantity.** Run 3's returns are diminishing because the memory system cannot tell stale from still-true.
4. **Skill refinement has a ceiling.** Five targeted improvements between Runs 2 and 3 produced measurable but modest gains. The lesson: skills codify known knowledge; they do not substitute for better infrastructure.

The next improvements that would move the needle are not more runs of the same architecture. They are architectural: memory curation, declarative ordering constraints, and the adaptive concurrency controller we have built but not yet wired in. Run 4 waits on that work.

---

## Related

- [`journey/23`](23-first-complete-run.md) — Run 1 analysis and the five memory pipeline fixes.
- [`journey/24`](24-run-2-cross-run-learning.md) — Run 2 results and the limitations we anticipated but had not yet observed.
- [`gotchas/shared-memory-db`](../gotchas/shared-memory-db.md) — per-skill DB separation that enables Run 4 onward.
