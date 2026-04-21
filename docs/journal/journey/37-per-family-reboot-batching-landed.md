---
id: journey-37-per-family-reboot-batching-landed
type: journey
title: "Per-Family Reboot Batching Lands: 44 Advisories, Zero Escalations, One Clean Sweep"
date: 2026-04-20
tags: [architecture, cve, reboot, results, refactor]
related:
  - journey/36-per-family-reboot-batching
  - journey/35-building-cve-in-a-day
  - journey/33-second-skill-cve-pivot
one_line: "Entry 36 was the design doc. This is the landing. The per-package-family reboot-verify architecture shipped in a single sprint: one new dataclass on the Protocol boundary, one post-loop rewrite in the harness, one 180-line rewrite of the skill's resolve_deferred method, one Architect prompt tune. The smoke against pre-cve-run-1 closed 44/44 advisories — 29 non-reboot remediated normally, 15 reboot-required batched into two families (core-userland + kernel), every item verified post-reboot. The escalation-ghost bug was real (a stale `rule_succeeded` flag escalating deferred items after the inner loop). The Architect SKIP bug was real (8 of 9 reboot items SKIPped on the first attempt because the prompt told them to). Both fixed. The `resolve_deferred` phase took 190s for two reboots and per-item verification across 15 items. The production story the whitepaper deserves is now the production story the code actually executes."
---

# Per-Family Reboot Batching Lands: 44 Advisories, Zero Escalations, One Clean Sweep

Entry 36 closed with a table comparing three reboot-batching strategies and a decision: per-package-family with snapshot rollback per family. Safer than batch-all (attribution when a family fails), faster than per-advisory (one reboot per family, not per CVE). The entry was deliberately pre-implementation — the design written down before the engineering so the decision would stand on its own merits rather than getting retrofitted to whatever happened to ship.

This entry is the counterpart. The engineering landed. The smoke ran. The architecture worked.

## What shipped

Four files moved. That's it.

**`gemma_forge/harness/interfaces.py`** gained a `DeferredItemOutcome` dataclass and the `SkillRuntime.resolve_deferred` Protocol signature changed from returning `tuple[bool, str]` to returning `tuple[bool, str, list[DeferredItemOutcome]]`. The per-item outcomes are now the load-bearing return; the bool+str are a rollup for logging. Each outcome carries `rule_id`, `passed`, `reason` (free-form but skill-stable: `family_verified`, `family_reboot_failed`, `family_still_listed`, `family_health_failed`, `family_apply_failed`, `family_exception_<type>`), and a `metadata` dict for observability (family name, wall time, exception strings). The harness consumes them directly without re-evaluating — the skill already knows what happened per item, the harness doesn't need to go ask the Evaluator again.

**`gemma_forge/harness/ralph.py`** had two edits. The post-loop deferred-resolution phase was rewritten to call `resolve_deferred` once per reason, unpack the per-item outcomes, and route each directly to remediated or escalated based on `outcome.passed`. No re-evaluation. The second edit fixed a bug that entry 36 predicted but the smoke was the place that proved: the inner loop's `break` for deferred items fell through to the `if not rule_succeeded:` escalation block, so every deferred item was *also* getting appended to `state.escalated` with `reason=unknown`. The fix was a second flag — `rule_deferred` — alongside `rule_succeeded`, and guarding the escalation and `rule_complete` logging with both. The logs now emit three possible outcomes: `remediated`, `deferred`, `escalated`. Previously the enum was two, and `deferred` collapsed into `escalated`, which is why entry 36 opened with "the same RLSA also got logged as escalated with `reason=unknown`."

**`skills/cve-response/runtime.py`** got the rewrite. Module-level `_reboot_required_advisories: set[str]` populated by `CveWorkQueue.scan` from Vuls's `requires_reboot` flag. The fast-paths in `apply_cve_fix` (the ADK tool the Worker calls) and `CveExecutor.apply` (the Protocol method the harness's default path would call) both check this set and, for matching advisories, return `deferred_apply: requires_reboot` without running dnf — the VM's disk state stays unchanged for reboot-required items so per-family rollback remains possible. `CveEvaluator.evaluate` gained a Step 0 fast-path: if the item has `requires_reboot=True` in its metadata *and* the advisory is still listed in `dnf updateinfo`, return `NEEDS_REBOOT` immediately. No health check, no ssh round-trips beyond the dnf listing call. The harness sees `NEEDS_REBOOT`, sees it in the skill's `deferrable_failure_modes`, and defers. `CveSkillRuntime.resolve_deferred` was rewritten in full. Groups items by family via `_family_for_work_item` (which delegates to the same `_categorize_advisory` helper the STIG-style category tip retrieval uses — one classification source of truth per skill). Orders families safest-first via `_order_families` against the canonical `_FAMILY_ORDER` list (web-service → language-runtime → database → network-firewall → audit-logging → ssh → cryptography → core-userland → kernel → other). Per family: save snapshot `pre-family-<name>`, apply all advisories in one dnf transaction with `--advisory=A --advisory=B ...`, reboot, wait for SSH (24 × 5s polls), mission healthcheck, per-item verify via `list_pending_advisories`. On any exception: revert the family snapshot, tag all family items with `family_exception_<typename>`. Always delete the family snapshot after — success or failure — so the next family starts from clean libvirt state. Per-item verification uses `dnf updateinfo` (cheap) rather than Vuls (slow) because the question after reboot is narrow: is this specific advisory still listed or not?

**`skills/cve-response/prompts/architect.md`** changed two sentences. The old prompt said reboot-required advisories would never appear in the candidate list — a claim that was true when `deferrable_reboot` filtered them out entirely, and became false the moment we made them pickable so the harness could catch them at apply-time. The new prompt is explicit: pick reboot-required advisories normally, the harness handles them, `deferred_apply` in the Worker output is a success signal not a failure signal, and `SKIP:` is never a valid response for a reboot-required item.

That is the entire change set. The Reflector, the Worker, the memory layers, the graph — untouched. The harness grew a new contract at one boundary and the existing post-loop machinery wrapped around it. The CVE skill grew a real implementation of that contract in about 180 lines, replacing a batch-all sketch that was never going to hold up to a failed reboot.

## The smoke run

Ran against the `pre-cve-run-1` snapshot with `config/harness-smoke-reboot.yaml` — a stock Rocky 9 mission-app VM with 44 pending advisories (29 non-reboot + 15 reboot-required). Wall time: **2132 seconds / 35.5 minutes**. Remediated: **44/44**. Escalated: 0. Skipped: 0. Remaining: 0.

The 29 non-reboot advisories cleared in the main loop at 37.6s average per rule — that's dnf apply + Vuls re-scan + mission healthcheck, single attempt for most, a second attempt for a few where the Architect wanted to rewrite the approach before declaring victory. Nothing surprising here; this is the CVE equivalent of what STIG's remediated path looks like.

The 15 reboot-required advisories are the entry's whole point. Main loop: 17.7s average per rule. The fast-path in the Evaluator caught them at Step 0 (advisory still in `dnf updateinfo`, `requires_reboot=True` in metadata) and returned `NEEDS_REBOOT` immediately. The harness intercepted `NEEDS_REBOOT` against the skill's `deferrable_failure_modes=["needs_reboot"]`, appended the item to `state.pending_verification`, removed it from the failing pool, logged a `deferred_verification` event, saved the progress checkpoint, and moved on. No reflector cycle, no re-engagement, no attempt-counter increment. 15 items drained out of the loop in about 5 minutes of wall time — which is exactly what entry 36 predicted: reboot-required items should be cheap in the main loop because the real work happens in the post-loop phase.

Post-loop phase: **190 seconds**, two families, 15 items. The grouping landed as:

| Family | Items | Ordering position |
|---|---|---|
| `core-userland` | 1 (glibc: RLSA-2026:2786) | first (safest) |
| `kernel` | 14 (every kernel RLSA in the pool) | last (riskiest) |

The safest-first ordering held. The glibc RLSA rebooted first. If the glibc transaction had wrecked the VM, the snapshot would have reverted it, the 14 kernel RLSAs would have proceeded from a known-clean state, and the diagnostic log would have said "family=core-userland reverted due to exception_runtimeerror" — one family lost, not all 15. Nothing like that happened in the smoke because nothing went wrong, but the *capability* is what matters: entry 36's table had a column labeled "Fail blast radius" and this architecture ships with "whole family" instead of "all 9" as the value in that column.

The 14-kernel batch is the one I was most nervous about. 14 kernel RLSAs in a single `dnf upgrade --advisory=<A> --advisory=<B> ...` transaction compressing to a single reboot. Dnf's transaction resolver handles the package-set deduplication — the 14 advisories collapse into a much smaller set of kernel packages updated to the most recent applicable version. The reboot picks up the new kernel. `dnf updateinfo` post-reboot confirms every one of the 14 advisories is cleared. Per-item verification returns `family_verified` for each. 14 RLSAs remediated in what would have been 14 reboots under per-advisory batching — roughly 19 minutes of pure reboot overhead saved on this single family.

Per-item attribution held. Every one of the 15 items emitted a `post_deferred_evaluation` event with `rule_id`, `passed=true`, `outcome_reason=family_verified`, and a `metadata.family` tag. The harness routed every item to `state.remediated` with `deferred_verified=True` and `outcome_reason=family_verified` — that field lands in the structured run log and the cross-run memory persistence, which means a future run's memory retrieval can distinguish "this glibc RLSA was verified via family-verified" from "this one was escalated via family_reboot_failed" rather than collapsing both into "failed" or "succeeded."

## The bugs entry 36 predicted

Both of the behaviors entry 36 flagged as "needs fixing" showed up in the pre-fix smoke (run against the original batch-all code at 00:47 UTC) and then stayed dead in the post-fix smoke.

**Ghost escalations.** Entry 36 quoted the first reboot-verify smoke log: "the same RLSA also got logged as escalated with `reason=unknown`." That was the inner loop's `break` for deferred items falling through to `if not rule_succeeded:`. The post-fix smoke emitted zero escalated events. 15 `rule_complete` events with `outcome=deferred`, 29 with `outcome=remediated`, zero with `outcome=escalated`. The `rule_deferred` flag did its job.

**Architect SKIP on reboot items.** Entry 36 said: "The Architect had SKIPped the other 8 reboot-required advisories in the pool rather than letting them go through the same path." The post-fix smoke: zero skipped rule events across 44 candidates. The old prompt had instructed the Architect "You will not see reboot-required advisories in your candidate list until near end-of-run" — which became a lie the moment we removed the `deferrable_reboot` filtering. Models take prompts literally; when the prompt told them reboot-required shouldn't appear, they treated the appearance as an error state and SKIPped rather than picking. The new prompt is explicit about what `deferred_apply` means in the Worker output and calls out that `SKIP:` for a reboot-required item is wrong. The Architect picked every reboot-required advisory it saw.

## What this validates

Two architectural claims that entry 33 and entry 35 asserted but couldn't prove yet, and entry 36 set up as falsifiable:

**The harness is skill-agnostic.** The `DeferredItemOutcome` contract is the third extension point (after `FailureMode` additions and the ordering predicate) that CVE added to the harness. All three are mechanism-free: they describe shapes of outcomes and batch-processing needs without baking in anything specific to CVE. The CVE `resolve_deferred` owns the reboot-specific mechanics (dnf multi-advisory syntax, SSH polling, mission healthcheck) entirely inside the skill. If a future skill needs a different deferred-resolution pattern — say, a crypto-rotation skill that defers "wait for cert propagation" — it implements `resolve_deferred` differently and the harness's post-loop phase doesn't change. The STIG skill still declares `deferrable_failure_modes=[]` and never hits any of this code. The abstraction survived its first second-skill test.

**The production story the whitepaper claims.** Entry 33's case-for-CVE said no commercial vendor ships autonomous host-level CVE execution. That claim was only true if the execution was actually safe — and "batch-all with no attribution on failure" is not safe by the standards a federal customer would audit against. Per-family with snapshot rollback per family and per-item attribution on the way out passes that audit. A failed kernel reboot doesn't take down the glibc family's successful apply. A family that partially succeeds (some advisories cleared, others still listed) emits the specific `family_still_listed` reason per item rather than collapsing into a batch-level failure. The run log carries enough attribution that a post-incident review can trace exactly which dnf transaction succeeded, which family's snapshot was restored, and which individual advisory was the one that caused the rollback.

## What I still don't know

**Bisection-within-family on failure.** Entry 36 listed this as Medium+ complexity and noted `docs/deferred.md`'s DEF-21 captures it as future work. The smoke didn't exercise any failure path because nothing failed. The per-family revert-on-exception code path is exercised only in the `try/except` wrapping — the happy-path smoke can't prove the revert restores cleanly, can't prove the per-item tagging on exception is correct, and can't prove the bisection logic we *didn't* ship is the right shape. The chained demo (running now against the STIG-hardened snapshot) adds another data point but still assumes the happy path. A future run will need to be engineered to fail — deliberately injecting a broken advisory or a reboot-hostile config — before any of the failure-mode claims above are tested empirically rather than just structurally.

**Larger families.** The kernel family here was 14 advisories that dnf's resolver compressed to a small package set. A pathological case with two incompatible advisories in the same family — a half-baked RHSA + a subsequent errata — would exercise the `family_apply_failed` path. The smoke didn't hit this. Real-world CVE repositories do sometimes have such pairs, and the per-family approach's answer is "the whole family reverts and the items all tag with `family_apply_failed`" — acceptable as a ceiling, but the question of whether we should automatically retry with a smaller sub-family on that signal is an open design question that should wait for real data.

**The memory system on deferred outcomes.** The V2 tip curation runs `utility_contribution = value * confidence`. Deferred items that pass via `family_verified` currently persist to cross-run memory as remediated with `deferred_verified=True`. That's correct for the outcome but flattens the attribution: a tip that retrieved because of a kernel RLSA and contributed to a family-verified pass gets the same utility credit as a tip that contributed to a direct-remediation pass. A strict reading of the utility math says these should be weighted differently — the direct path is a stronger positive signal than the "package upgraded plus the whole family reboot happened to work" path. I did not try to fix this in this sprint because the memory system isn't what this architecture is about and the weighting choice deserves its own entry. DEF-22 should be filed.

## The numbers, for the record

| Metric | Value |
|---|---|
| Total advisories | 44 |
| Remediated | 44 (100%) |
| Escalated | 0 |
| Skipped | 0 |
| Wall time | 2132s (35.5 min) |
| Non-reboot path, average per rule | 37.6s |
| Reboot path main loop, average per rule | 17.7s |
| Post-loop phase (resolve_deferred) | 190s |
| Families batched | 2 (core-userland, kernel) |
| Reboots issued | 2 |
| Ghost escalations | 0 (was 1 in pre-fix smoke) |
| Architect SKIPs on reboot items | 0 (was 8 of 9 in pre-fix smoke) |

## The chained demo

The smoke ran against `pre-cve-run-1` — a stock Rocky 9 mission-app VM. That's a meaningful validation but it isn't the whitepaper story. The whitepaper story is **skill composition**: the harness drives STIG hardening end-to-end, then the same harness drives CVE remediation from the hardened state. One Gemma 4 deployment, two security workflows, zero glue code changes between them.

So I reran the same reboot-smoke config against `stig-run6-final` — the VM snapshot from Run 6 of the STIG skill, where 62% of a 270-rule DISA STIG profile had already been applied. Same skill (`cve-response`), same config (`harness-smoke-reboot.yaml`), different starting state.

| Metric | Smoke (Rocky 9 baseline) | Chained (STIG-hardened) |
|---|---|---|
| Total advisories | 44 | 44 |
| Remediated | 44 | 44 |
| Escalated | 0 | 0 |
| Wall time | 35.5 min | 35.1 min |
| Families batched | 2 (core-userland, kernel) | 2 (core-userland, kernel) |
| Remediated on attempt 1 | 29/29 | 29/29 |
| Architect re-engagements | 0 | 0 |
| Reflector plateaus | 0 | 0 |

The STIG-hardened starting state changed nothing about the CVE run. Same advisory set (STIG hardens config; it doesn't upgrade packages). Same family composition. Same per-rule wall time. Same zero reflection cycles.

What this tells us is different from what entry 36 was trying to prove. Entry 36 was about the reboot-batching architecture. The chained demo was about skill composition, and what it exposed is that **CVE is almost deterministic work**:

- 29 of 29 remediated rules passed on **attempt 1**. Not one required a second attempt.
- Zero Architect re-engagements across the full 44-rule run.
- Zero Reflector plateaus.
- Average remediated-rule wall time: 41 seconds — basically `Vuls scan` + `dnf upgrade --advisory=<ID>` + `Vuls re-scan`.

Compare to STIG Run 6: 61.9% fix rate in 19.1 hours, multi-attempt fixes on many rules, routine Architect re-engagement, Reflector plateaus as a real signal. Same harness, same Gemma 4, same four-agent reflexion pattern. The CVE task is just structurally easier: dnf is deterministic, Vuls is deterministic, there's no ambiguity about what a fix looks like.

The interesting read here is that **the harness's reflection machinery was unused in the CVE run and that's the right outcome**. The loop is there for the hard cases; it stays quiet when the task is straightforward. A harness that forced reflection on every rule would add cost for no benefit on workloads like CVE. A harness that couldn't reflect would collapse on workloads like STIG. The abstraction has the right shape — same code path, different load based on difficulty.

The whitepaper story gets sharper: "STIG took 19 hours of reflection cycles to fix 62% of a 270-rule policy. CVE took 35 minutes to fix 100% of 44 advisories with zero retries. Both ran on the same Gemma 4 harness."

## What the chained demo exposed about observability

Entry 36 predicted the functional bugs (ghost escalation, Architect SKIP). The chained demo exposed a third issue entry 36 didn't see coming because the dashboard wasn't involved: the **resolve_deferred phase emits almost nothing while it runs**. The event log jumps from `deferred_resolve_start` straight to `deferred_resolve_complete` ~190 seconds later, and all 15 `post_deferred_evaluation` events land with identical timestamps. Watching the live dashboard, the focus square froze on the last deferred rule for three minutes, then every verification flashed through in a single frame.

For correctness this is fine — the outcomes are right. For the demo video and for humans watching the run, three minutes of silence is three minutes of nothing. The reboot-verify phase is the architectural showcase of this entry; it deserves a narrative, not a void.

The fix landed alongside this entry: `SkillRuntime.resolve_deferred` gained an optional `emit` callback, the harness wires it to `run_log.log(...)`, and the CVE skill now emits roughly a dozen event types across each family batch:

```
family_batch_start         → position, total_families, item_ids
family_apply_start/complete → dnf transaction bracket
family_reboot_issued
family_ssh_wait_tick        → every 5 s during the ~60 s wait
family_ssh_up               → wait_s
family_healthcheck_start/ok
family_verify_start
family_item_verified        → per item, in sequence (not batched)
family_verify_complete
family_batch_complete
family_exception            → on any raised failure
```

Every `logger.info()` in the old code that marked a phase boundary now has a corresponding structured event. The 190-second silence becomes a steady heartbeat of ~20 events across two families. Per-item verification flips animate in sequence instead of a single batched frame. The replay UI has something to paint.

This is the kind of issue that only surfaces once you watch the run. I didn't anticipate it in entry 36's design because entry 36 was thinking about correctness and blast radius. The dashboard was the teacher: the architecture was right, the event log was too coarse to show it.

The `emit` callback is the fourth harness extension point CVE has added alongside `FailureMode`, the ordering predicate, and `DeferredItemOutcome`. Like the others, it's mechanism-free: the harness doesn't know what CVE's events mean, only that it should forward them to the run log. A future skill's `resolve_deferred` picks its own vocabulary. The harness stays skill-agnostic.

---

This is what a working architecture looks like from the outside. Entry 36 argued for the shape before it existed; this entry is the evidence — plus a third thing neither of us predicted: the difficulty gradient between STIG and CVE. The same loop ran both workloads without modification and the harder one surfaced the reflection loop exactly where it was supposed to.
