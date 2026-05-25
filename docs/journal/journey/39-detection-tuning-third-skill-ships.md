---
title: "39. The Third Skill Ships, and the Harness Isn't STIG-Shaped"
date: 2026-05-25
related:
  - 33-second-skill-cve-pivot
  - 38.13-run-14-the-arc-closes
  - ../futures/detection-tuning-skill
  - ../futures/detection-tuning-preflight
  - ../../adr/0022-detection-tuning-skill-architecture
hook: >
  Run 14 closed the STIG arc at 90.3% and named the architectural
  ceiling. Entry 38.13's closing sentence said the next move was
  shipping a second-skill that exercised a *different problem shape*
  on the same harness. This entry is that ship: detection-tuning —
  Sigma rule authoring against labeled threat-telemetry corpora —
  built across one weekend, with the loop running end-to-end against
  EVTX-ATTACK-SAMPLES and primed for the cross-corpus tip-transfer
  experiment.
---

# 39. The Third Skill Ships, and the Harness Isn't STIG-Shaped

Run 14 ([38.13](38.13-run-14-the-arc-closes.md)) closed the four-run STIG arc at 90.3% and named the architectural ceiling. The closing sentence asked the obvious next question: does the harness generalize? CVE was a useful sibling skill but structurally a STIG-shaped twin — binary pass/fail, fix-and-rescan, the same reflexion loop with different tooling underneath. The harness's "skills are pluggable" claim needed to be tested against a problem shape that wasn't pass/fail.

**Detection engineering** is the obvious candidate. Sigma rules are tuned against labeled threat-telemetry corpora; "did it pass" is statistics (precision, recall, F1) instead of a boolean; the "fix" is a spec change instead of a system-state change; there is no mission app to protect because the corpus is static text. If the same five-interface harness handles that, the architecture isn't STIG-shaped. If it doesn't, we learn where.

This entry is the build narrative — proposal to production-running loop, across one weekend, with the live-run debugging saga that smoke tests didn't catch.

## Why this is its own entry

Three things converge here that haven't converged before:

1. **A skill with a genuinely different problem shape.** Graded outcomes (F1 in [0,1]) instead of binary; corpus-as-input instead of system-as-target; statistical thresholds (`P >= 0.95 AND R >= 0.80`) instead of scanner pass/fail.

2. **The first DEF-28 worker_context application outside STIG.** STIG returns XCCDF rule descriptions. Detection-tuning returns the *current Sigma rule YAML plus three labeled positive sample events*. Validates ADR-0020's claim that `worker_context` generalizes — or shows where the abstraction leaks.

3. **The first skill to be wired entirely via the new manifest-driven dispatch** (commit `9ff8688`). ralph.py's `_build_skill_runtime` has zero references to "detection-tuning". The skill declares its builder in `skill.yaml`'s `runtime:` block; the harness finds and calls it. The "drop a skill dir and the harness picks it up" claim is now empirically defensible.

## The proposal, the pre-flight, the green light

[`futures/detection-tuning-skill.md`](../futures/detection-tuning-skill.md) captured the proposal at the start of the weekend. The core architectural bet: Sigma authoring patterns accumulate across runs the same way STIG remediation patterns do, and the *authoring patterns transfer across corpora* even though the underlying attack scenarios don't. If true, Run 1 against EVTX-ATTACK-SAMPLES produces a tip pool that materially helps Run 2 against OTRF Security-Datasets — a corpus the Worker has never seen.

[`futures/detection-tuning-preflight.md`](../futures/detection-tuning-preflight.md) was the pragmatic check that came next: three 30-minute investigations, green/yellow/red signals, do not power through ambiguity. The findings:

- **Pre-flight 1** (corpus reality check): EVTX-ATTACK-SAMPLES ships `evtx_data.csv` — every event from every EVTX file, parsed into a flat schema. The column names *exactly match* Sigma rule field references (`CommandLine`, `Image`, `GrantedAccess`, `CallTrace`, `TargetImage`, `EventID`, `Channel`). No EVTX parsing required; pandas reads the CSV in ~100ms. Cross-tactic noise inside the corpus provides the false-positive signal — no separate clean baseline needed. **Green.**

- **Pre-flight 2** (Sigma toolchain): `sigma-cli 3.0.2` + `pysigma 1.3.3` convert real SigmaHQ rules into clean SPL output (`Field="value"` and `Field IN (...)` patterns) — easy to interpret in Python without standing up a Splunk instance. **Green.**

- **Pre-flight 3** (end-to-end manual proof): manually ran the `proc_access_win_lsass_memdump` Sigma rule against the corpus in 0.6 seconds. Tuned `GrantedAccess` (added the corpus-observed access masks the upstream rule excluded as "too noisy") and watched F1 climb from 0.821 → 0.936. Numbers moved in the right direction with the right tuning. **Green** — with two findings that shaped the build:
  - *Logsource-aware scoping is required.* A Sigma rule with `logsource: process_access` should only be evaluated against Sysmon EID 10 events, not the whole corpus.
  - *Hex value normalization is required.* The corpus stores `0x00001038`; the rule says `0x1038`. Substring matchers fail on leading zeros. Normalize the data at load time, not the matcher.

All three green. The build started.

## Friday night → Sunday: four commits, six lanes

The Friday-night vertical slice (commit `beb1c9b`) implemented the five sub-runtimes (`WorkQueue`, `Executor`, `Evaluator`, `Checkpoint`, `SkillRuntime`) end-to-end. One hardcoded work item (LSASS memdump), one corpus (EVTX-ATTACK-SAMPLES), no harness changes. The smoke test (`/tmp/dt-smoke.py`) drove a Ralph-shaped iteration manually: scan → evaluate baseline → write tuned candidate → re-evaluate → revert via checkpoint. F1 went 0.604 → 0.721 → 0.604 exact. All five interfaces behaved correctly.

Path A (commit `11d1349`) expanded to five curated work items across two logsources and three failure modes (`rule_too_narrow` ×3, `rule_too_noisy_and_narrow` ×2), wrote the four agent prompts (Architect, Worker, Auditor, plus a DEF-27 tip-follow judge with examples calibrated to Sigma authoring vocabulary instead of bash commands), and proved cross-item isolation in the smoke test — tuning one rule does not bleed into another rule's eval.

Path C (commit `b9ed562`) added OTRF Security-Datasets as a second corpus. The `SdsCorpus` class — sibling to `CorpusLoader`, same interface, reads NDJSON instead of CSV, synthesizes the `EVTX_FileName` column from source filenames so the shared `label_positives` static method works on either corpus unchanged. Same five Sigma rules, two different corpora, meaningfully different P/R/F1 scores. The corpus-as-input architecture proved out empirically.

[ADR-0022](../../adr/0022-detection-tuning-skill-architecture.md) (commit `a8dfb93`) recorded the six architectural decisions: graded outcomes via `signal_type="graded"`, FailureMode enum extension, hex normalization at corpus load, sibling loader classes (no shared ABC — duck typing on the three-method protocol), skill-side helpers in `gemma_forge/harness/tools/sigma/`, and DEF-28 `worker_context` for the graded-skill spec.

Path B (commit `a3642d3`, then `cf02a4c` post-rebase) wired everything into the harness: four new `FailureMode` enum members, `pandas` as an optional `[detection]` dep group, a `--corpus`-style selector via `DT_CORPUS` env / `harness.yaml` / manifest default, per-corpus positive-event keyword maps (because the EVTX corpus uses `mimikatz/hashdump/lsass` filename prefixes while SDS uses `empire_*/covenant_*/cmd_*/psh_*` — labeling has to be per-corpus or Run 2 silently scores R=0 on everything).

Cross-corpus smoke results before the first live run, with the SDS `defense_evasion` subdir extracted (74 captures / 972MB):

| Rule | EVTX (P/R/F1) | SDS (P/R/F1) |
|---|---|---|
| lsass_memdump | 1.00 / 0.43 / 0.60 | 1.00 / 0.00 / 0.00 |
| lsass_dump_keyword_image | 1.00 / 0.19 / 0.32 | 1.00 / 0.00 / 0.00 |
| mshta_lethalhta_technique | 1.00 / 0.14 / 0.25 | 0.00 / 0.00 / 0.00 |
| mshta_inline_vbscript | 0.50 / 0.29 / 0.36 | 1.00 / 0.07 / 0.12 |
| wscript_cscript_mshta_dropper | 0.50 / 0.14 / 0.22 | 1.00 / 0.07 / 0.12 |

The LSASS-on-SDS R=0.00 is the cross-corpus question made concrete: the rule's strict `GrantedAccess` values miss the SDS access masks entirely. Whether tuning that helps EVTX *transfers* to SDS is what Run 2 measures.

The branch landed as [PR #1](https://github.com/kenrollins/gemma-forge/pull/1) — the first PR in the repo's history.

## The first end-to-end Ralph run, and the four bugs the smoke didn't catch

`forge run` couldn't be used because its preflight script does VM snapshot restores and SSH probes — no-ops for a corpus-based skill but they would fail and block the run. Going around it by invoking `python -m gemma_forge.harness.ralph --skill detection-tuning` directly. (Generalizing `forge run` is its own ticket; for the first run, bypass.)

The manifest-driven dispatch loaded the runtime cleanly. The Postgres schema (`detection`) was bootstrapped via `tools/bootstrap_skill.sh --skill detection` (after generating `migrations/detection/*.sql` by sed-substituting `s/cve/detection/g` over the CVE migrations — they're functionally identical at the per-skill level). Pandas got installed via `uv pip install -e ".[detection]"` — the first new project dep since the original setup.

The harness loaded, scanned, found 5 work items, picked LSASS memdump, fired the Worker. Then surfaced four integration bugs the smoke tests had no way to catch:

**Bug 1 — `worker_context` keys beyond `description` get dropped.** The smoke test called `SkillRuntime.worker_context(item)` and inspected the dict directly. The harness, however, only consumes `description` (and optionally STIG's `oval_criteria`) when assembling the Worker's prompt — every other key gets silently dropped. So the Worker received the rule's natural-language description with *zero rule YAML*, and dutifully wrote a candidate from scratch with the wrong UUID, wrong logsource (missing `category`), wrong author (it invented `author: STIG`, which is funny in a sad way). The fix was to pack the current rule YAML + sample events INTO the description blob with explicit section markers ("=== CURRENT RULE YAML ===", "=== 3 POSITIVE SAMPLE EVENTS ==="). Inelegant but immediately effective; when the harness is generalized to plumb arbitrary keys, this collapses back to a clean dict per ADR-0020.

**Bug 2 — `apply_rule_change` had no item context.** I'd built the tool to read `_skill_config["_current_work_file"]`, a module-level variable that `Executor.apply()` sets. But the Worker LLM calls `apply_rule_change` *directly* via its ADK FunctionTool wrapper — `Executor.apply()` is bypassed entirely. First Worker call hit `NoneType` on the work file path. The fix was to add `rule_id` as the first tool argument so the LLM passes it explicitly (the prompt's `work_item_context` already showed the ID). More correct than hidden module state — it makes the Worker explicit about what it's tuning, which is how an actual Sigma editor would scope writes anyway.

**Bug 3 — `Checkpoint.restore("progress")` failed on first attempt.** First attempt of a rule has no `progress` snapshot because `save("progress")` runs *after* a successful apply, and the first apply hadn't completed yet. STIG's libvirt-based restore succeeds in this case because the VM snapshot is global state. Detection-tuning's per-item file state has no such property. The fix was explicit no-op cases for `progress` (return success when no snapshot yet) and `baseline` (delete the work file so the Evaluator falls back to upstream via `_resolve_rule_path`).

**Bug 4 — `gather_diagnostics` / `check_sudo_healthy` were undeclared.** The harness calls both during revert. STIG/CVE implement them to SSH to the VM and probe sudo/services. I'd never written stubs. AttributeError. Two five-line no-op methods returning healthy + empty dict.

None of these are *architectural* problems — they're integration-point gaps between the smoke test (which calls `SkillRuntime` methods directly) and the harness (which calls them via ADK + the full Ralph loop). They're exactly the kind of thing that surfaces on first contact with the real loop and not before. Captured in [commit `7540fe5`](https://github.com/kenrollins/gemma-forge/commit/7540fe5).

## The first Ralph iteration end-to-end

With those four bugs patched, the loop ran:

```
OUTER ITERATION 1 | fixed:0 escalated:0 remaining:5
Selected: proc_access_win_lsass_memdump

--- Attempt 1 for proc_access_win_lsass_memdump (0s / 1200s used) ---
[worker] → TOOL: apply_rule_change(rule_id=proc_access_win_lsass_memdump,
                                    candidate_rule_yaml="...",
                                    description="Added '0x1010' to GrantedAccess
                                    to match positive sample events.")
EVAL: P=1.000 R=0.514 F1=0.679 (mode=rule_too_narrow)
REFLECTOR: Pattern identified: Worker is attempting to increase recall
           by adding a single specific value (0x1010), but the rule
           remains too narrow.
           Preferred: Use a list of the top 3-5 most frequent
           GrantedAccess values observed in the positive sample events.

--- Attempt 2 ---
EVAL: P=1.000 R=0.568 F1=0.724 (mode=rule_too_narrow)

--- Attempt 3 ---
EVAL: P=0.889 R=0.649 F1=0.750 (mode=rule_too_noisy)
ARCHITECT VERDICT: PIVOT

--- Attempt 4 ---
EVAL: P=0.846 R=0.595 F1=0.698 (mode=rule_too_noisy)
ARCHITECT VERDICT: PIVOT
```

Three things this trace shows:

1. **The Worker is reading the packed worker_context correctly.** Every candidate preserves the upstream rule's UUID (`5ef9853e-4d0e-4a70-846f-a9ca37d876da`), `status: test`, the multi-line description block, and the logsource `category`. The "rewrote-from-scratch" problem from Bug 1 is solved.

2. **The Reflector's tips are domain-specific and load-bearing.** Attempt 2's bump (+0.045 F1) is the Worker following the Reflector's "use multiple access masks, not just one" advice from attempt 1. Attempt 3 overshoots (Worker added too many values, precision dropped). Attempt 4 also overshoots. The trajectory is bouncy, which is the *expected* shape for a graded-outcome skill where the right answer is a non-obvious point on a precision-recall tradeoff curve.

3. **Architect re-engagement fires correctly when the loop bounces.** Two PIVOT verdicts in a row after F1 went 0.724 → 0.750 → 0.698. This is exactly the behavior the harness was designed for — detect plateau, re-route to a different strategy.

At time of writing this entry, the loop is still grinding on LSASS memdump (5 attempts in, ~200s of 1200s budget used). The final outcome — PASS or escalation — is appended below.

## What this proves about the harness

The harness handled the problem shape change without complaint. Specifically:

- Manifest-driven dispatch picked up the new skill via its `runtime:` block; ralph.py has zero references to "detection-tuning". This is the post-`9ff8688` claim being empirically validated for the first time with a *new* skill.
- The graded-outcome path through `EvaluatorMetadata.signal_type="graded"` + F1 as `OutcomeSignal.value` worked. Tips are accumulating in `detection.tips` (Postgres, isolated schema, no leak from `stig.tips`). The dream pass will read these when the run finishes.
- DEF-28 `worker_context` generalized successfully. The pattern from ADR-0020 ("the Worker needs to see the authoritative spec") applied unchanged to a graded skill — the only adaptation was packing more keys into the description blob, which is a harness-side plumbing limitation, not an ADR-0020 limitation.
- All four new `FailureMode` enum members are routing correctly. The Reflector and Architect prompts branch on `detection_failure_mode` in the signals dict; the harness routes via the enum. No collisions with STIG's `HEALTH_FAILURE` / `EVALUATOR_GAP` / etc.

The four live-run bugs were all in the *skill side*, not the harness. The harness's contracts are clean enough that a new skill could be wired without modifying it — three commits' worth of skill code, zero commits of harness code (after the rebase onto the already-landed `9ff8688` refactor).

## What actually happened (post-runs update)

Both runs completed. Reported here honestly, not as the entry first
imagined them.

**Run 1 (cold, EVTX corpus)**: 5/5 rules **escalated**. ~65 minutes
wall-clock. All escalations were Architect-preemptive (the Architect
correctly identified that each rule had plateaued and called ESCALATE
rather than waste budget). 90 active tips persisted to
`detection.tips`. Dream pass and eviction sweep ran cleanly.

**Run 2 (warm, SDS corpus)**: 5/5 rules **escalated**. ~36 minutes
wall-clock — **1.8× faster** than Run 1 because the Architect leveraged
prior-run history to escalate dead-end rules sooner (LSASS-memdump:
321s in Run 2 vs 814s in Run 1, same un-tunable verdict reached
2.5× faster).

**Cross-run signal — measurable on the rule that completed cleanly:**

| Stage | Rule | P | R | F1 |
|---|---|---|---|---|
| Cold SDS baseline | `wscript_cscript_mshta_dropper` | 1.00 | 0.07 | 0.12 |
| Run 2 attempt 1 (warm) | same | 0.667 | 0.133 | **0.222** |
| Run 2 peak (attempt 2) | same | 0.750 | 0.200 | **0.316** |

Attempt 1 was 1.85× the cold baseline F1; peak was 2.6×. The Worker
applied Run 1's broaden-selectors tips immediately on attempt 1 of
a rule it had never seen against a corpus it had never seen.

That's the **cross-corpus tip-transfer claim from
`futures/detection-tuning-skill.md` empirically supported** — on its
first measurable opportunity.

## The honest verdict — and why the skill was set aside

The architecture worked. Every load-bearing claim from ADR-0022 held:

- Harness handles graded outcomes (`signal_type="graded"`) ✓
- Manifest-driven dispatch picked up a new skill with zero ralph.py
  edits ✓
- DEF-28 `worker_context` validated in a graded domain ✓
- Cross-run learning transferred tips across corpora measurably ✓
- 90 tips persisted, dream pass ran, eviction worked ✓

But the demo headline was **0 fixed / 5 escalated** in both runs.
"We escalated less catastrophically the second time" is not a
customer pitch.

Two compounding root causes:

1. **The PASS bar I picked was wrong for the corpus.** The proposal
   suggested `P >= 0.95 AND R >= 0.80`, with `F1 >= 0.85` as a fallback.
   Both are generic-ML evaluation thresholds, not what real detection
   engineers tune to. Real engineers tune for "false positives per N
   hosts per day" against production telemetry, not P/R/F1 against
   labeled corpora. The detection-tuning rules' achievable F1 ceilings
   on these corpora sit around 0.7-0.75 due to fuzzy event-level labels;
   no Worker prompt was going to break that ceiling.

2. **The labeling was noisy by construction.** EVTX-ATTACK-SAMPLES and
   SDS both label at the FILE level ("this capture demonstrates
   mimikatz"), but each capture contains hundreds of routine system
   events alongside the attack-specific ones. My loader marked every
   event in an attack-named file as a positive. That labeling fuzz
   imposed a hard ceiling that no rule tuning could overcome.

Both problems are downstream of "I picked detection-tuning to exercise
the harness on a new problem shape" without enough rigor about whether
the *customer-relevant version* of that shape was actually demoable on
publicly available datasets. Detection engineering at production scale
IS solved daily; doing it autonomously against fuzzy-labeled academic
corpora with a strict generic-ML PASS bar is not the same problem.

**Lesson for the project**: a skill that proves out the architecture
is not automatically a skill that lands as a customer demo. Both
properties matter.

## Final disposition

Detection-tuning shipped as a working third skill on the harness
(PR #1, branch `feat/detection-tuning-skill`, branch retained).
The architecture is captured in ADR-0022. The runs and their
honest verdict are captured here.

The skill is **set aside as a customer demo**. It remains valuable
as internal validation of the harness's handling of graded outcomes
and corpus-as-input shapes — both unproven before this skill, both
empirically supported after it. The cross-corpus tip-transfer
claim landed.

The architectural pattern (graded skill + dynamic per-run input +
multi-corpus support + cross-run measurement) carries forward. The
NEXT skill, captured in [40](40-fourth-skill-pentest-pivot.md), pivots
to a domain where the construct's properties — patient grinding,
cross-run memory, sovereignty, token-as-input — converge on a real
customer pull instead of an architectural exercise.
