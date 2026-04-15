---
id: journey-21-task-graph-and-react-flow
type: journey
title: "The Task Graph: From Flat Queue to Live DAG"
date: 2026-04-12
tags: [L4-orchestration, L5-application, reflexion-loop, refactor]
related:
  - journey/20-the-interface-extraction
  - journey/12.5-structured-run-logger
  - improvements/07-rule-dependency-awareness
one_line: "I replaced the flat work-item queue with a DAG-based task graph that tracks dependencies, detects resource conflicts, cascades escalations, and emits state snapshots — then visualized it with React Flow so the audience sees the architecture working at a glance."
---

# The Task Graph: From Flat Queue to Live DAG

## The story in one sentence

The overnight run proved the loop works; the interface extraction
proved it generalizes; the task graph is what makes that
generalization *visible* — a live DAG where the audience watches
green wash across 120 nodes as the harness grinds through them.

## Why this is its own entry

This is where the frontend story and the harness story converge. The
structured run logger ([`journey/12.5`](12.5-structured-run-logger.md))
produced events. The interface extraction
([`journey/20`](20-the-interface-extraction.md)) produced skill-agnostic
work items. The task graph adds *structure* — and structure is
what you can visualize.

---

## What the flat queue couldn't do

The v3 harness processed work items from a flat list. The architect
picked one, the loop ground on it, and when it was done (remediated
or escalated), the architect picked another. Three problems:

1. **No dependency awareness.** Five AIDE rules all depend on
   `aide_build_database`. The flat queue treats them independently,
   so each one independently discovers the same prerequisite failure.
   83 minutes wasted.

2. **No parallelism potential.** Items that touch different resources
   could run simultaneously, but the flat queue serializes everything.
   Each 20-minute escalation blocks the pipeline.

3. **No visualization.** A flat list gives you a progress bar. A DAG
   gives you a map of the problem space. The audience can see *where*
   the hard problems are and *why* certain items are blocked.

## The graph

Each work item becomes a node with six possible states:

| State | Color | Meaning |
|-------|-------|---------|
| QUEUED | Gray | Ready, waiting for a worker slot |
| BLOCKED | Amber | Has unresolved dependencies |
| ACTIVE | Blue (pulsing) | Currently being processed |
| COMPLETED | Green | Successfully resolved |
| ESCALATED | Red | Failed after exhausting budget |
| SKIPPED | Dark gray | Architect decided to skip |

Edges represent dependencies — declared (from the skill manifest) or
discovered (from failure clustering at runtime). When a dependency
completes, its dependents automatically unblock. When a dependency is
escalated, its dependents cascade-escalate with a structured reason.

Resource conflict detection prevents parallel items from racing on
the same config files. If item A is actively modifying
`/etc/ssh/sshd_config`, item B (which also touches that file) won't
be scheduled until A completes.

## The visualization

Two views in the dashboard:

**Compact grid** (left sidebar): category-grouped cells, each colored
by state. A segmented progress bar shows the overall flow. This is
the at-a-glance view — the audience sees green spreading across
categories as the run progresses, with red clusters showing where
the scanner-gap or dependency failures live.

**Interactive graph** (full-screen, via "Expand"): a React Flow DAG
with Dagre layout. Zoom, pan, click any node to inspect its full
history. A minimap in the corner shows the full graph. Dependency
edges animate. Active nodes pulse. This is the "second screen" view
for the deep-dive portion of the demo.

Both views consume the same `graph_state` events from the harness.
The harness emits a snapshot on every state transition — rule
selected, completed, escalated, skipped. The dashboard renders
whatever it receives, agnostic to the skill.

## The test suite

18 property tests for the graph itself:
- Independent items are all schedulable
- Blocked items unblock when dependencies complete
- Escalation cascades through dependency chains
- Cycles are detected and rejected
- Resource conflicts prevent parallel scheduling
- Runtime-discovered dependencies update the graph
- Snapshots are always structurally consistent

14 property tests for the v4 interfaces:
- EvalResult and FailureMode classification
- TriageState scanner-gap detection (distinct approaches required)
- WorkItem contracts
- STIG runtime satisfies all five protocol interfaces

107 total tests, all passing.

---

## What it looks like in practice

The first time the live stream connected, the grid started all
gray — 270 queued items. Then a cell lit up cyan and pulsed. A few
seconds later, it turned green. Then another went cyan. Then green.
Then a third went cyan... and stayed cyan for a long time. Then
amber. Escalated.

Within a minute the pattern was visible without reading a single
number: authentication was a wall of green. Kernel was mixed.
Integrity-monitoring was almost entirely amber. The categories told
the story through color alone.

The expanded React Flow view lets you zoom into a cluster and click
individual nodes. Each one shows its attempt count, wall time, and
escalation reason. The dependency edges (when they exist) animate
to show which items are blocking which. The minimap in the corner
gives you the full picture while you're zoomed into a detail.

---

## Related

- [`journey/20`](20-the-interface-extraction.md) — the interface
  extraction that made this skill-agnostic.
- [`journey/12.5`](12.5-structured-run-logger.md) — the event stream
  this visualization consumes.
- [`improvements/07`](../improvements/07-rule-dependency-awareness.md) —
  the dependency awareness design doc.
