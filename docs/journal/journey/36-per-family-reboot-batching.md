---
id: journey-36-per-family-reboot-batching
type: journey
title: "Per-Family Reboot Batching: The Architectural Decision Before We Built It"
date: 2026-04-20
tags: [architecture, cve, reboot, predictions, design]
related:
  - journey/35-building-cve-in-a-day
  - journey/33-second-skill-cve-pivot
one_line: "CVE Run 1 closed 29/29 non-reboot advisories first-try but left the 6 reboot-required kernel RLSAs escalated because the reboot-verify loop was infrastructure-only. The reboot-verify smoke proved the architecture fires end-to-end (1 item deferred → resolve → reboot → re-eval passed) but exposed two issues: a bug where deferred items also get logged as escalated, and an Architect SKIP behavior that sends 8 of 9 reboot advisories straight to skipped rather than letting them batch. Fixing those forced a bigger question: how should we batch reboots? The answer landed on per-package-family with snapshot rollback per family — safer than batch-all (attribution when a family fails), faster than per-advisory (one reboot per family, not per CVE), and implementable now. This entry captures the decision before the engineering happens, because the production story for Federal — where a failed reboot can't leave a system in an unknown state — deserves the right architecture from the start, not a future refactor."
---

# Per-Family Reboot Batching: The Architectural Decision Before We Built It

The reboot-verify smoke on Run 2's morning landed clean on the happy path: one glibc RLSA marked `deferred_verification`, `resolve_deferred` executed the reboot, mission app came back healthy, re-evaluation returned APPLIED. The autonomous scan → apply → reboot → verify loop that entry 33 claimed as novel had just closed for the first time on a real advisory.

Then the logs showed the same RLSA also got logged as escalated with `reason=unknown`. And the Architect had SKIPped the other 8 reboot-required advisories in the pool rather than letting them go through the same path. The architecture worked; the behaviors around it didn't.

Fixing the bug (a stale `rule_succeeded` flag) and the Architect prompt (SKIP is wrong when items are deferrable) was straightforward. The harder question — the one that deserves its own entry before the code lands — is what reboot batching strategy the harness should actually use.

## The three strategies, honestly compared

Entry 35's MVP shipped batch-all: every `NEEDS_REBOOT` advisory gets staged through `deferred_verification`, then one reboot at end-of-run verifies all of them. It works on the happy path. It's also a bad production story because a reboot-time failure loses every batched advisory with zero attribution — the post-mortem literally says "something in the batch of {9 items} broke it."

| Strategy | Reboot count (9 kernel CVEs) | Fail blast radius | Attribution | Complexity |
|---|---|---|---|---|
| Batch-all | 1 | All 9 lost | "something in the batch" | Low |
| Per-advisory | 9 (~13 min reboot overhead) | 1 lost per failure | Perfect | Low |
| Per-package-family | ~3 (kernel + glibc + userland) | Whole family | Family-level | Medium |
| Per-family + bisection-on-failure | log₂(N) + retries | Isolated | Perfect | High |

The per-advisory approach looks attractive for attribution but the reboot overhead is disqualifying. A production host with 30 reboot-required advisories would spend ~45 minutes just in boot cycles before the reflexion loop got to do any reasoning. Nobody does that in real ops.

The per-family approach is what production actually does — RH bundles 3-5 kernel RLSAs together for a reason, `glibc` upgrades land as their own coherent batch, userland libraries (openssl, gnutls, nss) share service-restart semantics. One reboot per coherent bundle. If kernel breaks, glibc and userland already shipped. If glibc breaks, the kernel upgrade still succeeds as its own family.

## Why batch-all can't evolve into per-family

The instinct to ship batch-all first and refactor later doesn't survive contact with the implementation. Batch-all applies every advisory to disk during the main loop via `dnf upgrade --advisory=X`. By the time the post-loop fires, all N advisories are already committed to disk. A snapshot taken after the main loop captures the post-all-applied state. Reverting from there loses everything, including the non-reboot advisories the main loop successfully remediated.

For per-family rollback to work, the apply has to be deferred into the post-loop, grouped by family, applied + rebooted + verified as a unit. That's a contract change in three places: the `Executor.apply` skips the dnf call for `requires_reboot=True` items (returning a `deferred_apply` marker), the `Evaluator.evaluate` fast-paths `NEEDS_REBOOT` for items still listed in dnf's pending set (no Vuls rescan needed), and `resolve_deferred` gains per-item outcomes via a new `DeferredItemOutcome` dataclass so the harness consumes attribution directly instead of re-evaluating.

It's not a small change, which is why entry 35 deferred it. But every hour we run on batch-all is an hour of "don't stress-test the reboot path, we know it doesn't isolate failures" caveats we'd have to write into the whitepaper. Better to ship the architecture the whitepaper defends.

## The design landing

**Main loop, modified:** reboot-required advisories fast-path through the Worker without running `dnf`. The Evaluator returns `NEEDS_REBOOT` for any item whose advisory is still in `dnf updateinfo list`'s output. The item lands in `state.pending_verification`. This keeps main-loop iteration speed identical to the current CVE Run 1 rate.

**Post-loop `resolve_deferred` rewrite:**

1. Group pending items by primary package family (`kernel`, `glibc`, `systemd`, `openssl`, `gnutls`, `httpd`, etc.). Family classification uses the advisory's primary affected package name — the same heuristic `is_reboot_required_advisory` uses, just grouping instead of flagging.
2. Order families safest-first: userland services → cryptography libs → core-userland (glibc, systemd) → kernel. This way a userland failure doesn't prevent kernel from getting its chance.
3. Per family, in order:
   - Save snapshot `pre-family-<name>`.
   - Apply all advisories in the family via one `dnf upgrade --advisory=A --advisory=B --advisory=C` call. (`dnf` handles cross-advisory dependency resolution correctly — verified on the VM before writing this entry.)
   - Reboot via SSH, wait for SSH to come back (poll, 120s timeout).
   - Run mission healthcheck.
   - Re-scan via `dnf updateinfo list` for the family's advisories.
   - Per item: if still listed → `family_still_listed`; if cleared + healthy → `family_verified`.
   - On any failure (reboot timeout, health check, apply error): revert to the family's snapshot, mark every item in the family as failed with the specific reason.
4. Delete family snapshots in a final cleanup pass.

**Contract change:** `SkillRuntime.resolve_deferred` returns `(bool, str, list[DeferredItemOutcome])` instead of `(bool, str)`. The per-item outcomes carry the attribution the harness needs — `family_verified` on success, `family_reboot_failed` / `family_health_failed` / `family_still_listed` on failure. The harness post-loop reads these directly instead of re-evaluating each item, which also fixes a subtle bug where re-evaluation after a family rollback would re-hit the `NEEDS_REBOOT` fast-path and produce confusing "deferred loop" escalations.

## The one thing we're explicitly not building today

Bisection within a family. If the kernel family fails, we mark all three kernel RLSAs escalated with `family_reboot_failed` and move on to the next family. We do *not* bisect down to find the specific bad advisory.

The reasoning: bisection within a family doubles the engineering complexity and, in practice, kernel advisories mostly fail for cross-cutting reasons (boot regression on a specific hardware quirk, a systemd interaction) rather than any single RHSA being individually bad. Family-level attribution is enough signal for the operator to triage — they know the whole kernel bundle didn't come back clean, they can look at that bundle in detail offline.

Captured as DEF-21: revisit if we see families fail often enough that bisection would isolate a specific advisory that the family-level report can't. Adding bisection later is a clean layer on top of per-family — the snapshot infrastructure is already there, we'd just add a retry loop with halved batches.

## Predictions for when this lands

Three things the post-implementation smoke and chained demo should show:

1. **The harness handles family rollback cleanly.** A deliberate test: stage a bad kernel advisory in a smoke run, watch the kernel family fail, watch userland + cryptography succeed on their own. If rollback leaks state across families (e.g., userland apply fails because kernel family's revert changed something unexpected), the architecture has a hole we didn't anticipate.

2. **The Architect stops SKIP-gaming the deferred pool.** With the prompt tuned to "pick reboot-required items normally when they appear — the harness batches them," all N reboot-required items in a run should land in `pending_verification`, not in `skipped`. Any skip of a reboot-required item post-prompt-tune is a sign the Architect prompt still has a blind spot.

3. **Chained demo (STIG-hardened → CVE) is the real test.** CVE Run 1 on a fresh Rocky 9 baseline was the easy case — all upgrades clean, no FIPS interaction, no auditd immutable mode, no hardened SSH config fighting the upgrade. The stig-run6-final snapshot carries the full STIG hardening posture. Running CVE remediation against that state is where the interesting failure modes live: does the kernel reboot survive FIPS crypto re-initialization? Does the glibc upgrade interact with auditd's immutable mode? Does the SSH hardening block the reboot-wait loop from re-authenticating? None of those are hypothetical for a Federal edge deployment, and none got exercised in Run 1.

The honest framing for this entry: we're about to build a meaningfully harder architecture than the MVP we shipped yesterday, because the MVP's failure mode (lose-all-on-batch-failure) isn't one we'd want to write into a whitepaper. Per-family is the architecture the demo deserves. Bisection-within-family is the architecture a paying customer deserves. We're shipping the first; flagging the second for later.

## Related

- [`journey/35`](35-building-cve-in-a-day.md) — the MVP build entry that shipped
  batch-all and flagged the attribution limitation. This entry is the evolution
  that closes that limitation.
- [`journey/33`](33-second-skill-cve-pivot.md) — the pivot entry that claimed
  autonomous reboot-verify as novel. Until today's smoke, that was
  infrastructure-only; per-family batching is the architecture that makes the
  claim defensible beyond the happy path.
- [`deferred.md`](../../deferred.md) — DEF-21 (bisection within a failed family)
  captured as future work.
- [`skills/cve-response/DESIGN.md`](https://github.com/kenrollins/gemma-forge/blob/main/skills/cve-response/DESIGN.md) —
  the skill's own design notes updated with the batching section.
