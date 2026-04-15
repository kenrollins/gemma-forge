---
id: journey-15-test-as-architecture-discovery
type: journey
title: "Journey: When Testing Becomes Architecture Discovery"
date: 2026-04-11
tags: [L4-orchestration, reflexion-loop, refactor]
related:
  - journey/14-overnight-run-findings
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "After the overnight run revealed five architectural flaws and I fixed them in a half-day push, I caught myself about to write verification tests for the fixes — and realized the real product is not the fixes but the abstract harness properties they embody."
---

# Journey: When Testing Becomes Architecture Discovery

## The story in one sentence
After the overnight run revealed five distinct architectural flaws and I
fixed them all in a half-day push, I caught myself about to write
"verification tests" for the fixes — and realized that the real product of
this work is not the fixes but the **abstract harness properties** the
fixes embody, and that the tests should assert those properties, not the
fixes.

## How it started

Going into the test pass after the v3 fixes, the natural framing was:

> "Five things got fixed. Verify each fix works."

The natural test plan looked like:

> Test #1: Worker no longer retries internally on apply_fix
> Test #2: Context budget assembler caps prompt under 8K tokens
> Test #3: Plateau detection now flags repeated reflections
> Test #4: Architect re-engagement triggers after 3 attempts
> Test #5: Snapshot-based revert recovers from sudo breakage

This is the wrong test plan. It's correct in the narrow sense — those are
real things to verify — but it's the wrong *abstraction level*. Each of
those tests would have been bound to a specific bug from the overnight run
and would have stopped catching problems the moment a different bug took
its place.

## What changed

Two reframing observations landed at the same time:

> Don't code for a specific bug or trap. Step back and figure out how to
> deal with the core issue even if it means completely redoing something.

and:

> These issues point to something more broadly — the problems that agentic
> architectures need to address.

Together they reframed the entire test pass. The thing being built is not
"a STIG remediation loop." It is **a generic reflexive-agent harness**,
and STIG is the first witness that reveals what its abstractions need to
be. The tests, therefore, should not test specific bugs from the overnight
run — they should test the abstract properties the harness must hold
*regardless* of which skill is plugged in.

## The principle

Every test the harness has should be a **falsifiable claim about a
property of the harness itself**. Properties like:

- *Agent turns are bounded in tool calls.*
- *Prompts assembled by the harness do not exceed a configured token budget.*
- *Target state is restored to a known-good checkpoint after every failed attempt.*
- *Diagnostic forensics are captured before any revert.*
- *The strategy agent is re-engaged on a defined schedule, with full failure history.*
- *Plateau detection identifies semantically equivalent reflections regardless of phrasing.*
- *Wall-clock budgets bound work-item duration.*

Each of these is testable without referring to STIG or sudo or apply_fix
or any specific bug. Each one is a **load-bearing claim about the harness
architecture.** If a test for one of these properties fails, the right
question is not "what's the bug?" but "is the abstraction missing or
wrong?"

## The discipline

I adopted an explicit checkpoint after each test tier:

> *Did the failures we just observed point to specific bugs, or do they
> point to a missing abstraction?*

If the answer is "bugs," fix them and continue. If the answer is "missing
abstraction," **stop testing and refactor** before continuing. This avoids
the trap of fixing every red test until they go green and shipping a
"verified" version of an architecture that was wrong all along.

## What this discovered

Writing the abstract test for "agent turns are bounded in actions"
surfaced an issue: there is no place in the harness where a different
agent can be plugged in and have the cap apply automatically. The cap
is wired into a specific function (`_run_agent_turn`) and the convention
is "everyone calls that function." That works for the current three
agents but it doesn't *enforce* the property at the level of the
abstraction. It enforces it at the level of the implementation.

This is a smell. The right shape would be:
- A `BoundedAgent` wrapper that any agent gets put into
- A `Harness` class that knows about agents only through the bounded
  wrapper, so violations are syntactically impossible

I did not refactor to that shape today (would be premature; the rest of
the architecture is also still in flux). But the test about to be written
*names* the property explicitly, so when the refactor happens, the test
moves with it without modification. The test is the spec; the
implementation is the current best instance.

## What goes in the journal vs what goes in the standalone piece

The decision was made to write **two** documents instead of one:

- **`architecture/01-reflexive-agent-harness-failure-modes.md`** — the
  standalone artifact. Project-agnostic, suitable for citation outside
  GemmaForge, names the failure modes and prescribes the harness
  mechanisms. STIG appears only as a running example.

- **`journey/14-overnight-run-findings.md`** and the present
  `journey/15-the-test-as-architecture-discovery.md` — the autobiographical
  layer. Captures *how* each failure was discovered, what was tried
  first, and why positions shifted. This layer exists to make the
  standalone piece *credible*: each abstract claim has an empirical
  receipt in the journey.

The standalone piece is the contribution. The journey is the evidence.
The harness implementation is the proof. The tests are the executable
specification. All four are the same activity, written in different
genres.

## What I want to remember from this

1. **Test the abstractions, not the bugs.** Every test name should be a
   property statement, not an action description. If you can't write the
   test that way, the abstraction is missing.

2. **Checkpoints over completion.** After each test tier, stop and ask
   whether the failures are bugs or missing abstractions. Don't fix-and-
   continue if the right answer is refactor-then-continue.

3. **Two documents.** When you discover something that's interesting
   beyond your own project, separate the contribution from the journey.
   Both belong in the journal; only one belongs on someone else's reading
   list.

4. **The thing being built is the abstraction, not the demo.** STIG
   is the demo. The harness is the contribution. Every architectural
   decision should ask "would this make sense for a different skill?"

5. **Empirical work earns the right to be opinionated.** The failure
   modes document is opinionated. It is allowed to be opinionated because
   each opinion has 10 hours of run data behind it. A paper could not
   take these positions on day one. A document built from a single
   adversarial run can.

## Companion files (created during this conversation)

- `architecture/01-reflexive-agent-harness-failure-modes.md` — the
  standalone failure-modes piece (v0.1)
- `journey/14-overnight-run-findings.md` — the postmortem of the run
  that produced the empirical evidence
- `improvements/01-04-*.md` — the per-fix improvement docs (now redundant
  with the failure modes piece, but kept for chronological accuracy)
- `tests/PLAN.md` (TODO) — the detailed test plan derived from this
  conversation, with property-statement test naming
- `tests/test_harness_properties.py` (TODO) — the test file itself,
  organized by property

## What happens next

The test plan executes in tiers, with checkpoints. At each checkpoint I
either fix-and-continue or stop-and-redesign. The test outcomes feed back
into both the harness code (fixes and refactors) and the failure-modes
document (new evidence, new modes if any surface).

When the test suite stabilizes, the deliverables are:
- A harness whose properties are explicitly named and verified
- A test file that reads as a specification
- A failure-modes document with empirical receipts
- A journey narrative explaining how it all happened
- A demo (the original STIG remediation) that proves it works on a real
  workload

Then the frontend Journal + Architecture pages get built, a second
overnight run happens, and the evaluation of whether a v4 is needed
follows.
