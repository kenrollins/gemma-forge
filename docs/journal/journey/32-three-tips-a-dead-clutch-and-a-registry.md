---
id: journey-32-three-tips-a-dead-clutch-and-a-registry
type: journey
title: "Three Tips, a Dead Clutch, and a Registry"
date: 2026-04-18
tags: [L4-orchestration, memory, discovery, decision, process]
related:
  - journey/31-run-5-grading-the-bets
  - journey/30-building-v2
  - journey/28-run-4-and-the-coarseness-problem
one_line: "Run 5's post-mortem didn't just grade predictions — it exposed three separate architectural problems that the aggregate fix rate had been hiding. Tips aren't all created equal (a tip that says 'X failed' steers the Worker away from the case where X would have worked). The clutch adaptive concurrency controller is fully implemented, tested, logged on every run since V5 shipped, and never actually called by the loop. And neither of the consolidation passes (dream, eviction) auto-run between runs — every run has been drinking from a tip pool that only grew. Run 6 closes the first and third; the clutch gets a proper deferred-work registry entry until the UI can represent parallel work."
---

# Three Tips, a Dead Clutch, and a Registry

Three tips loaded at sim=1.30 told Run 5's Worker that `install /bin/true` fails on cramfs, that `blacklist` fails on cramfs, and that both fail together. All three tips were literally accurate. All three were also the correct fix in Run 3. The Worker, now in Run 5, dutifully avoided the approaches that would have worked, and `kernel_module_cramfs_disabled` escalated at attempt 5 — regressed from a first-try win in Run 4. That's the small story. The bigger story is what the trace found when we asked "what else is like this?"

## cramfs: tips as pure negative examples

Five V2 tips landed in the Worker's prompt on attempt 1. All `tip_type=recovery`. All from prior-run failure reflections:

| Tip | Source rule | What it said |
|---|---|---|
| 7220 | cramfs itself | "Attempt 4 used `install /bin/true` and `modprobe -r`, but failed" |
| 7200–7203 | ATM sibling | "Attempted `blacklist atm`, failed" → "must use `install atm /bin/true` instead" → "used `install /bin/true` but failed" |

Every tip is a *negative result*. Each tells the Worker what didn't work in some prior run without saying why. A Worker reading this prompt learns one thing: `install /bin/true` is a trap; `blacklist` is a trap; both together are a trap. So it tried neither. It went looking for a different approach, didn't find one, and oscillated for four more attempts until `scanner_gap_detected` tripped and the Architect preemptively escalated.

The actual fix for cramfs is `install /bin/true` + `blacklist` + runtime unload. Two of the three things the tips flagged as "failed." Run 5's own Reflector figured this out at attempt 2 ("blacklist only prevents automatic loading; needs runtime unload too"), but by then `scanner_gap` was already counting down and it was too late to use the insight.

The DB says what the system measured: **tip 7220's average utility across 6 retrievals is 0.0**. The system noticed it's useless. Nothing happened.

## rhosts: tips as misleading frame

The `sshd_disable_rhosts` regression has a different flavor:

| Tip | Content |
|---|---|
| 7384 | "`UseRhosts no` caused health_failure" |
| 7385 | "`UseRhosts` is deprecated; verify via `sshd -T`" |
| 7386 | Same as 7385 |
| 7387 | "After 3 failed UseRhosts attempts, stop forcing deprecated config" |

The tips convinced the Worker that `UseRhosts` is a broken directive. The Reflector picked up the frame and confirmed it ("Attempting to configure a parameter that is deprecated or removed"). The Worker started deleting `.rhosts` files from disk — which is not what the rule asks for — and escalated at attempt 4.

The actual fix is one line in `sshd_config`. Whether that line is `UseRhosts no` or `IgnoreRhosts yes` or both depends on the OpenSSH version, but none of them are deprecated in any version this VM ships. The tips were wrong about deprecation. The Worker believed them because they were all pointing the same direction with sim=1.30.

## dns_mode: tips that actually composed

`networkmanager_dns_mode` went the other way — Run 4 escalated at 7 attempts, Run 5 won first try. Same five tips, same `recovery` type, same sim=1.30:

| Tip | Content |
|---|---|
| 7206 | "Use `nmcli general modify` for active and persistent settings" |
| 7207 | "`nmcli general modify dns=dns` failed; use conf.d files for persistence" |
| 7208 | "Config without section header was ignored" |
| 7209 | "conf.d with headers failed; use primary NetworkManager.conf" |
| 7210 | "Global settings ignored; requires profile-level nmcli" |

These tips don't just say "X failed." Each one carries a *root cause* — "ignored because no section header," "global vs profile level." A Worker can read this as a diagnostic arc: four approaches tried, here's specifically why each failed, pick approach five. Which is what the Worker did, in 53 seconds, first try.

## What the three traces teach

Tip quality is not correlated with `tip_type`. All fifteen tips across the three cases were labeled `recovery`. The ones that helped carried causal mechanism (*because Y*). The ones that hurt carried only outcome (*X failed*). Retrieval doesn't distinguish them — it just loads whatever scored highest by similarity. A Worker seeing five "X failed" tips will dutifully avoid X. A Worker seeing five "X failed because Y" tips will construct a mental model and try approach six.

!!! quote ""
    Recovery tips without a mechanism are negative examples. A Worker dutifully avoids negative examples, including the ones where the negative example was actually the correct answer.

That maps to a concrete Run-6 change: the Reflector's prompt now requires a `mechanism` line on every tip — "explain the causal why, not just the outcome." For strategy tips, mechanism is *why-effective*. For warning and recovery tips, it's *why-it-fails*. Tips without a mechanism become filterable noise.

## The scan that found the clutch

Writing up the three traces, a question landed: *what else in the codebase has this same shape — built, claimed, not actually doing what we think it's doing?* Three minutes of grep against the harness found it.

[`gemma_forge/harness/clutch.py`](../../../gemma_forge/harness/clutch.py) implements adaptive concurrency. `Clutch.recommend_workers()` computes how many parallel workers to spawn per category based on prior-run success rate. `Clutch.select_batch()` pulls runnable items from a task graph respecting the recommendation. Both methods are covered in `tests/test_memory_and_clutch.py`. Both are exercised in `tools/smoke_memory_e2e.py`. Both are imported in `ralph.py`. The import path in the harness loop:

```python
clutch = Clutch(config=clutch_cfg, mem_store=mem_store)
clutch.initialize()
logger.info("Clutch: %s", clutch.state.reason)
run_log.log("clutch_initialized", "system", clutch.snapshot())
```

And that's it. `recommend_workers()` is never called. `select_batch()` is never called. Every run since V5 shipped has been fully serial, despite a working adaptive-concurrency controller sitting right there and every JSONL carrying a `clutch_initialized` event that claims otherwise.

The honest read: the clutch works. The UI doesn't. There's no way today to show an operator three concurrent rules being processed in parallel — the dashboard assumes one linear narrative. Wiring the clutch into the loop without upgrading the UI first would deliver throughput silently and destroy the "watch the edge AI work" demo that's core to what this project is.

So the clutch doesn't get wired for Run 6. It gets a proper entry in [`deferred.md`](../../deferred.md) as DEF-01, with the UI gate captured: an **active-queue band** — a single "now processing" region that expands from 1 card into N cards as clutch recommendations change, with a clutch meter above the band. The UI narrows and widens as difficulty changes. Adaptive concurrency becomes the visible subject rather than a hidden optimization. That design waits for its own weekend.

## Eviction has never run

Same scan, different shape. `gemma_forge/memory/eviction.py` retires tips whose average utility falls below threshold with enough evidence. `tools/evict_tips.py` exposes a CLI. A comment in ralph.py states: *"Eviction (Phase H) runs between runs via tools/evict_tips.py or the dream pass."* Nothing actually schedules either. 2,973 active tips in the database. 0 retired. The cramfs tip with util=0.0 across 6 retrievals that just cost us a rule in Run 5 has been retrievable this whole time.

Dream pass has the same shape (manual CLI, never scheduled) with an additional hazard: it's not idempotent. The formula is `new_confidence = old + signal × 0.3`. Run it twice, confidences drift 2×. No `dreamed_at` guard exists.

Both problems close together. Run 6 ships three changes:
1. A `dreamed_at TIMESTAMPTZ` column on `stig.runs`; dream pass no-ops if already set
2. A finally-block at run-end that invokes dream + eviction with their stats logged as a `consolidation_complete` event
3. A manual sweep against Run 5's data *before* Run 6 starts, so Run 6 retrieves from a cleaned tip pool rather than the uncleaned one Run 5 did

## The registry

Three distinct problems surfaced from this post-mortem — tip quality, clutch wiring, consolidation scheduling — and a fourth from entry 31 (prompt-level ordering guidance). None of them would have been caught by a better JSONL query. They're architectural drift, and architectural drift doesn't show up in data, it shows up when someone asks "what else is like this?" and has the time to look.

The registry fixes the social problem that enables the drift: things get mentioned in passing, nobody writes them down, three weeks later one of them shows up as a symptom and an hour gets spent rediscovering it. [`docs/deferred.md`](../../deferred.md) now tracks seven architectural items — three debt, four opportunity — each with a *pain signal*: the specific symptom that tells us "this one is ready to be promoted out of this file." The pain signal is the thing that stops this from being a graveyard.

And `docs/journal/STYLE.md` does the same work for the journal itself. Thirty entries of earned voice had drifted into conversation-only guidance. Now it lives on disk. Entry 31 and this entry are the first two written against the explicit style guide — Luu observational restraint as the spine, Weir predicament-first openings, Levine permission to name the absurd when an entire adaptive-concurrency subsystem is fully tested and never called.

## Three changes for Run 6

Shipping today, in this order:
1. **Skill-declared ordering constraint mechanism.** STIG's manifest declares `audit_rules_immutable` must defer until ≤1 audit rule remains. Harness filters deferred rules out of the Architect's candidate pool and logs `rule_deferred` events. Other skills add their own declarations; the mechanism is skill-agnostic. Closes the `audit_rules_immutable` cascade entry 31 diagnosed.
2. **Reflector `mechanism` field.** New required field in tip emissions, schema-enforced by a migration on `stig.tips`. Parser warns when a Reflector output is missing it. Changes what gets written, not what's retrieved — so Run 6 primarily measures eviction's effect on existing tips. Run 7+ measures the mechanism field's effect on retrieval quality.
3. **Auto-consolidation at run-end.** Crash-safe finally block. Dream pass (with idempotency guard). Eviction (with the `forge_stig-rhel9` → `forge_stig` role-mapping bug fixed). Both callable manually with `--force` for backfill. Manual sweep against Run 5 happens before Run 6 launches.

Run 6 is the one that asks whether closing the cascade and cleaning the tip pool produces the aggregate lift V2 promised. Ordering constraint (change 1) tests *wrong problem*: if the cascade was costing 18 rules, closing it should move fix rate from 56% toward 62%. Eviction sweep (part of change 3) tests *right problem, wrong priors*: if the cramfs/rhosts-type tips were actively hurting, retiring them should reduce regressions. Run 6 separates these two hypotheses because they act on different rules.

The plumbing worked in Run 5. The policies around the plumbing — what gets into the tip pool, when it gets cleaned, which rule gets picked next — were the story Run 5 actually told. Run 6 tests whether fixing the policies is enough, or whether the architecture itself needs a V3 rewrite.

## Related

- [`journey/31`](31-run-5-grading-the-bets.md) — the seven bets, graded.
- [`journey/30`](30-building-v2.md) — what V2 is and what Run 5 was
  supposed to prove.
- [`journey/28`](28-run-4-and-the-coarseness-problem.md) — the V1
  coarseness problem that motivated V2.
- [`deferred.md`](../../deferred.md) — the registry DEF-01 (clutch),
  DEF-02 (prompt-vs-enforcement), DEF-03 (dream pass granularity) all
  land from the discoveries in this entry.
- [`journal/STYLE.md`](../STYLE.md) — the voice guide, first applied
  to entries 31 and 32.
