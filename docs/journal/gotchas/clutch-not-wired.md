---
id: gotcha-clutch-not-wired
type: gotcha
title: "Gotcha: Adaptive concurrency clutch is built but not wired into execution"
date: 2026-04-13
tags: [L4-orchestration, concurrency, memory]
related:
  - journey/22-context-graphs-and-the-memory-question
  - journey/23-first-complete-run
one_line: "The clutch reads category difficulty from the memory store and recommends parallel worker counts, but the outer loop in ralph.py is still serial. recommend_workers() is never called. Built infrastructure, zero utilization."
---

# Gotcha: Adaptive concurrency clutch is built but not wired

## Symptom

The `clutch_initialized` event in the run log shows per-category
worker recommendations (authentication=3, kernel=2, audit=1), but
every rule is still processed one at a time. The clutch has no
effect on execution.

## Root cause

`ralph.py`'s outer loop is a simple `for` loop that processes one
rule per iteration. The clutch is initialized, reads the difficulty
model from prior runs, and logs its recommendations — but
`clutch.recommend_workers()` is never called and `asyncio.gather`
is never used to run multiple rules concurrently.

The clutch was built alongside the memory store
([entry 22](../journey/22-context-graphs-and-the-memory-question.md))
as part of the v5 architecture. The memory store and cross-run
learning were wired in first because they were prerequisite — the
clutch needs difficulty data from prior runs to make decisions.
With Run 1 and Run 2 providing that data, the clutch can now be
wired in for Run 3.

## Fix (planned for Run 3)

Wire `recommend_workers(category)` into the outer loop:

1. Before selecting the next rule, ask the clutch how many workers
   the current category supports
2. If >1, use `asyncio.gather` to process multiple rules from that
   category concurrently
3. Respect resource conflict constraints from the TaskGraph
   (rules that touch the same files can't run in parallel)
4. Dashboard will need parallel conversation lanes when this lands

## Why it wasn't wired in yet

The clutch needs cross-run data to make informed decisions. On a
first run with no prior data, it defaults to serial (1 worker).
We chose to validate the memory system first (Runs 1 and 2) before
adding concurrency, because debugging parallel agent execution on
top of a broken memory system would have been a nightmare.

## Environment

- gemma_forge/harness/clutch.py — ClutchConfig, Clutch class
- gemma_forge/harness/ralph.py — outer loop (lines ~885-900)
- Single vLLM instance, TP=4 — concurrent requests are supported
  but not tested under parallel agent load
