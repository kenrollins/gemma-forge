---
id: gotcha-clutch-not-wired
type: gotcha
title: "Gotcha: Adaptive concurrency clutch is built and deliberately deferred"
date: 2026-04-13
tags: [L4-orchestration, concurrency, memory]
related:
  - journey/22-context-graphs-and-the-memory-question
  - journey/23-first-complete-run
  - journey/32-three-tips-a-dead-clutch-and-a-registry
one_line: "The clutch reads category difficulty from the memory store and recommends parallel worker counts, but the outer loop in ralph.py is still serial by design — concurrency wiring + a dashboard rewrite to visualize parallel lanes is DEF-01 in the deferred registry, not a bug."
---

# Gotcha: Adaptive concurrency clutch is built and deliberately deferred

## Status

**Deliberately deferred, DEF-01 in [`docs/deferred.md`](../../deferred.md).**
The clutch was never accidentally left unwired. We built it in V5
with the intent to wire it after cross-run memory had Run 1+Run 2
data to reason about. Every run since has kept it serial because the
wiring work is bundled with a dashboard rewrite (parallel lanes
instead of a single linear narrative), and stopping momentum on
memory, ordering, or the second skill to spend a weekend on
concurrency + UI never felt like the right trade. Entry 32 promoted
that recurring deferral to the debt registry where the full plan
lives.

This page remains as an orientation marker — if you're reading the
code, you'll see the clutch initialized and will want to know why
it doesn't seem to do anything.

## Symptom

The `clutch_initialized` event in the run log shows per-category
worker recommendations (authentication=3, kernel=2, audit=1), but
every rule is still processed one at a time. The clutch has no
effect on execution — by design, for now.

## What is actually running

`ralph.py`'s outer loop is a simple `for` loop that processes one
rule per iteration. The clutch is initialized, reads the difficulty
model from prior runs, and logs its recommendations — but
`clutch.recommend_workers()` is never called and `asyncio.gather`
is never used to run multiple rules concurrently. The methods are
covered by `tests/test_memory_and_clutch.py` and exercised by
`tools/smoke_memory_e2e.py`, so the infrastructure is production-
ready from a correctness standpoint. The gating item is the UI.

## What wiring would involve

1. Before selecting the next rule, ask the clutch how many workers
   the current category supports.
2. If >1, use `asyncio.gather` to process multiple rules from that
   category concurrently.
3. Respect resource conflict constraints from the TaskGraph
   (rules that touch the same files can't run in parallel).
4. Rewrite the dashboard's "now processing" ribbon to widen and
   narrow as the clutch's recommendation changes — the
   active-queue band design captured in DEF-01.

## Why it stayed deferred

The concurrency work is not trivial and the existing dashboard is
built around a single linear narrative. Delivering hidden throughput
without the UI to show it would destroy the "watch the edge AI work"
demo that is core to what this project is for. The bundle —
concurrency wiring + dashboard rewrite — is a weekend of focused
work that hasn't been the right trade against V2 memory, the
ordering constraint, CVE as a second skill, or the per-family reboot
architecture. DEF-01 captures the trigger: when we're ready to trade
a demo weekend for throughput, the plan is already written.

## Environment

- gemma_forge/harness/clutch.py — ClutchConfig, Clutch class
- gemma_forge/harness/ralph.py — outer loop (lines ~885-900)
- Single vLLM instance, TP=4 — concurrent requests are supported
  but not tested under parallel agent load
