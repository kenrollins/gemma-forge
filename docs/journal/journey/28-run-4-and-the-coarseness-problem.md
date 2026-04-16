---
id: journey-28-run-4-and-the-coarseness-problem
type: journey
title: "Run 4: When the Dream Pass Passes the Wrong Test"
date: 2026-04-16
status: DRAFT — Run 4 still in flight at time of writing; numbers will be finalized when it completes
tags: [L4-orchestration, reflexion-loop, cross-run-learning, postmortem, discovery]
related:
  - journey/27-building-the-dream-pass
  - journey/26-dreaming-and-real-databases
  - journey/25-run-3-learning-plateaus
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Run 4 was the dream pass's first real test. The aggregate fix rate looked impressive mid-flight (70%), but the per-rule analysis told a different story: the dream pass produced six dramatic wins and twelve regressions, the regressions clustered in a specific audit subfamily, and the architectural lesson was sharper than the result — category-level credit assignment is too coarse to be safe."
---

# Run 4: When the Dream Pass Passes the Wrong Test

## The story in one sentence

The first dream-informed run came in at roughly the same aggregate fix rate as Run 3, but the per-rule outcome shifts revealed exactly how the V1 credit assignment goes wrong: lessons in a low-confidence category get penalized as a group even when individual lessons within that category were load-bearing for specific rules — and those rules then regress.

## Why this is its own entry

Entry 27 was the build. Entry 28 is the verdict. The dream pass works as a mechanism — confidence scores land in both stores, the composite ranking changes which lessons surface — but the V1 algorithm's coarseness has measurable, traceable consequences in the data. Documenting that pattern now, while the evidence is fresh, is more valuable than letting the result fade into a single number on a chart.

## What we expected

Going in, we had a clean hypothesis: the dream pass would deprioritize lessons from low-outcome categories (audit, banner) and promote lessons from high-outcome categories (service-config, authentication, kernel). The Worker's prompt would carry better-targeted context. First-try success would rise, escalations would shrink, fix rate would climb past 60%.

The hypothesis was about the *aggregate*. The actual mechanism turned out to be more subtle than the aggregate could capture.

## What actually happened

### The misleading mid-flight number

At the 9-hour mark, Run 4 read 70% fix rate (124 remediated, 50 escalated). Compared to Run 3's final 60%, this looked like the breakthrough we hoped for. It wasn't.

The 70% was a stage-of-run artifact. Run 4 had processed 188 of 270 rules — the *easy front* of the run, where high-success categories like service-config (100%), authentication (96%), and kernel (92%) get done early. The remaining 82 rules were almost entirely audit (74 of them), which historically converts at 33%. Once the audit tail rolled through, the fix rate would compress back toward Run 3 territory.

The lesson here is operational, not architectural: **comparing fix rates mid-run is meaningless** if the run hasn't reached the same point in the difficulty distribution. Final numbers only.

### The real signals

The honest per-rule comparison against Run 3 (179 rules processed by both runs at the time of mid-flight analysis):

| Metric | Run 3 | Run 4 (mid-flight) | Δ |
|---|---|---|---|
| First-try success rate | 86.0% | 87.6% | +1.6pp |
| Avg attempts on completed | 1.33 | 1.29 | -0.03 |
| Avg attempts on escalated | 4.73 | 4.42 | **-0.31** |
| Wall time per completed | 55.4s | 54.6s | -1% |
| Wall time per escalated | 426s | 398s | **-7%** |

Two of these are real: **escalation attempts down 0.31 (-7% wall time)**. The Worker is giving up on dead ends faster, which suggests the lessons it has are routing it away from doomed approaches more efficiently. That's the dream pass doing its job, narrowly.

First-try and per-completed metrics moved by less than the noise floor. Within the resolution of one comparison run, you cannot claim the dream pass improved them.

### The wins (6)

Six rules went from escalated in Run 3 to remediated in Run 4. Five of them dramatic:

- `aide_build_database`: **8 attempts and 564s in Run 3 → 1 attempt and 72s in Run 4**.
- `kernel_module_sctp_disabled`: 3 → 1, 242s → 32s.
- `sshd_enable_warning_banner`: 4 → 2, 418s → 103s.
- `networkmanager_dns_mode`: 7 → 4, 584s → 226s.
- `use_pam_wheel_for_su`: 4 → 3, 361s → 153s.
- `sudo_remove_nopasswd`: 5 → 8, 527s → 689s. (Won, but slower.)

Each of these is a rule whose category had positive confidence in the dream pass, where the Worker's prompt now carried promoted lessons that it didn't carry in Run 3. The mechanism worked exactly as designed.

### The regressions (12) and the actual mechanism

Twelve rules went from remediated in Run 3 to escalated in Run 4. The pattern is the alarming part: **three of them are `audit_rules_dac_modification_*` rules**, a family that previously succeeded at 1-4 attempts.

- `audit_rules_dac_modification_fchmod`: **1 attempt, 44s, completed** → 6 attempts, 567s, escalated.
- `audit_rules_dac_modification_fchmodat`: 4 attempts, 214s, completed → 7 attempts, 552s, escalated.
- `audit_rules_dac_modification_fchown`: 1 attempt, 41s, completed → 4 attempts, 348s, escalated.

Reading the JSONL trace for `audit_rules_dac_modification_fchmod` makes the mechanism plain. In Run 3, the Worker hit `/etc/audit/rules.d/audit.rules` with a heredoc on attempt 1 and the rule passed. In Run 4, the Worker spent attempts 1–2 hammering `/etc/audit/audit.rules` (which `augenrules` regenerates from `rules.d/`) and the *Reflector* — not the prompt — had to teach it about the `rules.d` mechanism on attempt 2. Same model, same VM baseline, same skill. The only thing that changed was the cross-run lesson context the Worker received.

The audit `audit_rules_immutable` cascade is *not* the cause: that rule had not yet been processed in Run 4 at the time of the dac_modification regressions. The VM was in the same state both runs.

What did change is the lessons table. Three compounding architectural failures, all traceable in the data:

1. **Category-level credit is too coarse.** Audit got -0.35 confidence because of the immutable cascade and other noisy audit subfamilies (privileged_commands, etc.). The proven `rules.d/heredoc` lesson from Run 3, despite being load-bearing for the dac_modification family, got demoted along with the bad audit lessons. Composite score for a weight-1.0 audit lesson dropped from 1.0 (pre-dream) to 0.325 (post-dream).

2. **NULL-confidence new lessons outrank dream-penalized old lessons.** New lessons saved during Run 4 itself carry NULL confidence, which the composite ranking treats as a neutral 0.5 multiplier. A NULL-confidence weight-0.55 lesson scores 0.275; a dream-penalized weight-1.0 audit lesson scores 0.325 — close enough that *fresh failure-derived lessons* from Run 4 itself can flood the per-category top-5 by sheer count. At the moment the dac_modification rules fired, **all top 15 audit lessons by composite score were from Run 4, all NULL-confidence, all describing Run 4's own current failures**: "augenrules failed, rules.d failed, auditctl failed."

3. **Within-run feedback loop has no damping.** The harness saves new lessons during a run from failed attempts. Those new lessons enter the prompt context for *subsequent* rules in the same run. When the dream pass demotes the prior run's success lessons, the within-run negative lessons drown them out — turning the prompt into a "here is what doesn't work" list with no surviving "here is what does." The Worker's first attempt is now actively misdirected by the run's own struggles.

This is the architectural finding of Run 4: **category-level credit, NULL-vs-penalized ranking interaction, and unconstrained within-run lesson creation form a feedback loop that can erase prior-run knowledge**. Each one of the three is a real bug. Together they produced the dac_modification regressions.

### The win:regression ratio

| Comparison | Wins | Regressions | Ratio |
|---|---|---|---|
| Run 1 → Run 2 | 59 | 1 | 59:1 |
| Run 2 → Run 3 | 14 | 10 | 1.4:1 |
| Run 3 → Run 4 (mid-flight) | 6 | 12 | **0.5:1** |

The trajectory is unambiguous and uncomfortable. Cross-run learning's win:regression ratio is collapsing run over run. Run 4 is the first run where regressions outpace wins, and the regressions track to a specific architectural choice we made between runs.

## The meta-lesson

The dream pass *works* — the plumbing is solid, the scores are persisted, the rankings change as predicted. The V1 *algorithm* is too coarse for this domain. What looked like the right granularity at design time (lessons load by category, so credit by category) turned out to lump together rules with very different difficulty profiles.

This is exactly the failure mode the [whitepaper section on memory](../../drafts/whitepaper/04-memory.md) describes for any agent system that persists learned behavior as text. Aggregating signal at the wrong granularity makes the persistence layer actively misdirect the agent. We built a working version of that failure mode and then ran it as our V1.

That is not a bug. That is the cycle the architecture is supposed to expose. The dream pass V1 produced no aggregate gain, and in producing no gain it taught us something specific about what V2 has to do.

## What V2 needs

The path forward is concrete now in a way it wasn't before Run 4:

1. **Per-rule lesson attribution (logging).** The harness must log which specific lesson IDs were assembled into each rule's Worker prompt. We were able to reason about the dac_modification regression because the JSONL trace preserved the Worker's actual fix scripts; we could not have proven the lesson-displacement mechanism without that. Per-prompt lesson logging would let the dream pass do real per-lesson credit assignment in V2. **Highest priority.**

2. **Fix the NULL-vs-penalty ranking flaw.** A NULL-confidence lesson is treated as neutral (multiplier 0.5), which makes new lessons rank above dream-penalized older ones. Two viable fixes: (a) treat NULL as "use the source category's average confidence" rather than 0.5, so new audit lessons inherit the audit penalty until proven otherwise; or (b) cap NULL at the multiplier of the lowest-scoring category (so new lessons can never outrank dream-curated ones). Either closes the displacement loophole.

3. **Damp within-run lesson creation OR exclude same-run lessons from prompt context.** A run's own failed-attempt lessons should not be promoted to outrank prior-run successful lessons. Two options: (a) exclude `source_run_id == current_run_id` from the prompt-time lesson load (only cross-run lessons are loaded); or (b) save new lessons with confidence=`-0.3` (slightly negative) by default, since they were derived from failed attempts. Option (a) is cleaner.

4. **Per-rule (or per-rule-family) credit, not category-level.** When a lesson from category C is loaded into rule R's prompt and R succeeds, the lesson should accrue confidence specifically — independent of whether other rules in C succeeded. Audit category contains both `dac_modification_*` (solvable) and `rules_immutable` (cascade trigger); penalizing them together is incorrect. Per-rule-family categorization (subdividing audit into `audit_dac`, `audit_immutable`, `audit_privileged_cmd`, etc.) would give the dream pass a meaningful unit to score against.

5. **Don't suppress; demote.** The current composite ranking makes a confidence-`-1` lesson score zero. Too aggressive. A floor of, say, 0.1 (instead of 0.0) keeps demonstrated-useful lessons in the candidate pool even when their category is troubled.

6. **Honest reflection in the journal.** When V1 of an architectural change produces a result like Run 4's, the move is not to defend the design — it is to say what it taught and what V2 needs. That is what this entry is for.

## What this means for the broader story

The fix-rate plateau at ~60% across Runs 3 and 4 is not the end of the cross-run-learning story. It is the empirical confirmation that **memory accumulation alone, even with a curation layer, hits its ceiling without per-instance attribution**. The whitepaper said this might happen and named it as the limitation that production systems would have to solve. Run 4 confirmed it experimentally, on the harness we built, with the dream pass we shipped.

The thesis that the agentic harness shapes outcomes still holds. The thesis that memory curation is necessary still holds. What Run 4 added is a sharper claim: **memory curation done at category granularity will produce no aggregate gain on workloads with mixed-difficulty subfamilies inside a category**. The granularity has to match the difficulty distribution, and on STIG that means at least at the rule-family level.

This is the story to take into the whitepaper update. Not "dream pass works → fix rate jumps." The actual story: **dream pass V1 works as plumbing, V1 algorithm is empirically too coarse, V2 is now empirically scoped**. The honesty is the strength.

---

## Related

- [`journey/27`](27-building-the-dream-pass.md) — the build that produced the V1 algorithm.
- [`journey/26`](26-dreaming-and-real-databases.md) — the architectural decision behind the dream pass.
- [`journey/25`](25-run-3-learning-plateaus.md) — the run that motivated the dream pass; ran out at 60%.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — the memory architecture decision.
