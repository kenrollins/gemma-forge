---
id: journey-16-agentic-coding-as-a-method
type: journey
title: "Agentic Coding as a Method: How This Project Got Built at Speed"
date: 2026-04-12
tags: [L4-orchestration, decision]
related:
  - journey/00-origin
  - journey/14-overnight-run-findings
  - journey/15-the-test-as-architecture-discovery
  - journey/17-v3-fix-pass
one_line: "GemmaForge was built in ~72 hours using an agentic coding workflow: a human operator paired with an AI coding assistant, with the human making all architectural decisions and the AI contributing implementation velocity, test coverage, and documentation. The workflow is part of the story."
---

# Agentic Coding as a Method: How This Project Got Built at Speed

## The story in one sentence
GemmaForge was built in about 72 hours of wall-clock time from initial
scaffold to a fully instrumented reflexion loop with 99 passing property
tests, a failure-modes taxonomy, and a complete journal — using an
**agentic coding workflow** in which a human operator paired with an
AI coding assistant, each contributing what they are best at, with
enough discipline around the collaboration to make it productive rather
than noisy.

## Why this entry exists

A project like this, written up honestly, has to explain how it was
built. Federal presales engineers and technical partners reading these
notes will reasonably ask: *"How did this much get done in this little
time?"* The workflow is the answer, and hiding the workflow would be
dishonest.

This entry is *method-focused and vendor-neutral*. It describes the
pattern of the collaboration, not any specific AI coding tool. The
pattern transfers across tools — if you already use an agentic coding
assistant of any kind, the discipline described here is what makes
the difference between "AI wrote some boilerplate" and "AI-paired
engineering work at high velocity."

## What "agentic coding" means in this context

**Agentic coding** is a workflow in which a human engineer works
alongside an AI coding assistant that has *agency*: the assistant can
read files, edit files, run shell commands, execute tests, and take
multi-step actions against the codebase autonomously. Unlike a chat
assistant that produces suggestions to copy and paste, an agentic
assistant operates on the actual repository in real time.

The workflow is not *vibe coding* — the colloquial term for
low-discipline, trust-the-model prompting that produces unmaintainable
code. Agentic coding, done well, is structured, intentional,
tool-mediated engineering work. The human stays in the architect and
decision-maker role. The AI stays in the implementation and velocity
role. Both keep each other honest.

## The collaboration rhythm we settled into

After the first day, an implicit rhythm emerged:

### The human's job
- **All architectural decisions.** What to build, what pattern to use,
  what to reject, when to refactor. The AI never decided architecture;
  the human always did.
- **All positioning and narrative decisions.** Who the audience is,
  what tone the writing should take, which framings are honest versus
  which are oversold. The AI produced drafts; the human decided what
  shipped.
- **"Stop, we need to rethink this" moments.** Several times during
  the project, the human paused an in-progress implementation and
  said something like "this whole approach is wrong, we need to back
  up." Those moments were exclusively human calls. The AI would
  generally have happily continued grinding on the wrong thing.
- **Sanity checks against reality.** When the AI proposed a solution
  that looked plausible but contradicted something the human knew
  from experience ("that Dell hardware doesn't actually do that"),
  the human caught it.
- **Final review on any public artifact.** Nothing shipped without
  the human reading it.

### The AI's job
- **Implementation velocity.** Once the human made the architectural
  call, the AI produced working code fast. A reflexion loop harness
  with ADK integration, memory tiers, time budgeting, and tool
  calling — thousands of lines — got drafted in hours instead of days.
- **Test coverage.** The AI wrote the bulk of the unit and
  integration tests, including property-style tests that exercise
  abstract invariants. 99 tests across 7 tiers, most of them drafted
  by the AI and reviewed by the human.
- **Documentation drafting.** The journal entries, the failure-modes
  document, the improvement notes, this entry — all drafted by the
  AI with the human editing and deciding what stayed.
- **Pair debugging.** When tests failed, the AI was quick to
  hypothesize causes and propose fixes. Some of the hypotheses were
  wrong; the ones that were wrong were caught by the human or by the
  next test cycle.
- **Bulk mechanical work.** Adding frontmatter to 33 files. Running
  a retrofit pass. Grepping for patterns. Scanning git history. This
  kind of work is where the velocity multiplier is highest.

### Joint work
- **Diagnosis when something broke.** The human noticed the symptom;
  the AI proposed possible causes; the human picked which to
  investigate; the AI ran the investigation commands; the human
  interpreted the results. This ping-pong cycle is much faster than
  either party working alone.
- **Architectural reframes.** When the human said "step back, we
  need to think about this differently," the AI contributed to the
  rethinking by surfacing relevant context from the codebase and
  proposing alternative framings. Final decisions stayed with the
  human, but the rethinking itself was collaborative.

## What made it work

A few pieces of discipline made the difference between productive
collaboration and a pile of slop:

### 1. Journal as you go, not at the end

Every significant decision, every surprise, every "huh that's
interesting" moment got captured in a journey entry or a gotcha
*while it was happening*, not in a cleanup pass at the end of the
project. This sounds like overhead but it isn't — the AI drafted most
of the text, and the journal then became the long-term memory of the
project for both human and AI across sessions. When context
compressed or a new session started, the journal provided continuity.

### 2. Checkpoints over completion

At major milestones — end of each phase, end of each test tier, end of
each architectural discovery — the human explicitly paused and asked:
*"Is this the right thing to keep building, or do we need to reframe?"*
This discipline is counterintuitive because the AI is always ready to
keep going. The human's job is to be the one who says "stop, let's
check the map."

Two specific moments where this checkpoint discipline mattered:

- **The v3 fix pass** — after the overnight run revealed several
  architectural flaws, the human's first instinct was to write
  verification tests for each individual fix. The checkpoint revealed
  that the *real* product wasn't the fixes but the abstract harness
  properties the fixes embodied, so the tests should assert those
  properties, not the specific bugs. That reframe happened at a
  checkpoint and changed the whole test pass. See
  [`journey/15-the-test-as-architecture-discovery`](15-the-test-as-architecture-discovery.md).

- **The positioning reframe** — partway through the content work the
  human caught that the framing was drifting toward "GemmaForge as a
  product with a journey-as-differentiator." The checkpoint revealed
  that the real positioning is "GemmaForge as an exploration with
  learning as the product" and the whole voice and audience
  assumptions shifted. That reframe happened at a checkpoint and
  changed the README, the disclaimer, and the whole narrative tone.

Without checkpoints, both of those reframes would have shipped as
subtly-wrong versions that the human would have had to undo later.

### 3. Tests and data as ground truth

When the human and AI disagreed about how the system behaved, the
tiebreaker was always the actual behavior of the code. Running the
tests, reading the logs, executing the command and looking at the
output. Neither party's prior belief was allowed to win over observed
reality. This matters because AI coding assistants are confident in
ways that can fool a human into accepting plausible-sounding but
wrong claims, and humans are confident in ways that can override
correct AI suggestions. The data is the only party neither of them
can out-argue.

### 4. Write the abstraction, then the implementation

Whenever a fix was needed, the workflow was: **write the property
first** ("agent turns are bounded in tool calls"), then the test
that asserts the property, then the implementation. This kept the
code honest and made the test file read like a specification. It
also kept the AI from producing code that happened to pass tests
without implementing the actual intent.

### 5. The human has to understand what shipped

This one is non-negotiable. The human has to actually read and
understand every piece of code, every test, every document that
ships. The AI's job is to make that reading and understanding go
fast — by drafting, by explaining, by showing alternatives — not to
bypass it. If the human doesn't understand what the AI wrote, the
human doesn't ship it. That rule alone prevents most of the ways
agentic coding goes wrong.

## The speed data

Here is what 72 hours of agentic coding collaboration looked like in
rough strokes, from project start to the v3 test pass:

- **Hour 0**: empty repository, an idea, and a PRD sketch.
- **Hours 0–12**: inference layer brought up, Gemma 4 running on
  4× L4, vLLM configured, first-pass harness scaffolded.
- **Hours 12–24**: target VM provisioned via OpenTofu, SSH and
  snapshot infrastructure working, first end-to-end STIG rule
  attempted.
- **Hours 24–36**: reflexion loop written, agent roles defined,
  tool-calling contract established, first overnight run launched.
- **Hours 36–48**: the overnight run produced data that revealed
  four architectural flaws (see
  [`journey/14-overnight-run-findings`](14-overnight-run-findings.md)).
  The v3 fix pass began.
- **Hours 48–60**: five fixes implemented, each with its own
  improvement document, plus a failure-modes architecture essay
  covering the abstract taxonomy.
- **Hours 60–72**: 99 property-style tests across 7 tiers,
  all passing. Single-rule integration test verified. Content
  audit, frontmatter retrofit, architecture page, and this entry.

A project of that scope would typically take weeks with a single
engineer working in traditional mode. The compression isn't magic —
it comes from the AI handling most of the typing and some of the
thinking, while the human focuses on the decisions that actually
require judgment. The human hours are fewer and much higher-leverage;
the AI hours are many and do the grinding.

## What was hard

Not everything was smooth. Things that made the collaboration harder,
noted honestly:

- **AI overconfidence on things it had never actually tried.** The
  AI would sometimes produce authoritative-sounding explanations of
  behavior that turned out to be wrong when we ran the code. The
  defense is always the same: run the code and look at the result.
- **Context loss across long sessions.** Eventually the conversation
  history gets long enough that older context is compressed or
  dropped. The journal files are the only reliable way to preserve
  architectural decisions across that boundary. This is exactly why
  the "journal as you go" rule is non-negotiable.
- **"Why would you write it that way" moments.** Sometimes the AI
  would produce code in a style the human didn't want. The fix is
  to explicitly tell the AI the style preference up front and
  enforce it in review.
- **The temptation to ask the AI to do judgment work.** The AI is
  happy to propose "the best positioning" for a document or "the
  right audience framing" for a writeup. Its answers are usually
  plausible but not specific enough to be actually right. The human
  has to do those calls.

## What this means for presales engineers reading this

If you're a presales engineer or technical partner looking at this
project and wondering whether you could build something similar at
similar velocity — yes, you probably could. The prerequisites aren't
exotic:

1. An agentic coding assistant capable of reading, editing, and
   executing commands in your repository. Several exist; the workflow
   is similar across them.
2. A willingness to stay in the architect seat and not let the AI
   drive decisions.
3. The journal-as-you-go discipline.
4. The checkpoint-before-fix-and-continue discipline.
5. Tests and data as the tiebreaker when the human and AI disagree.

The tooling isn't the hard part. The discipline is. Teams that bring
the discipline to the workflow will find that agentic coding gives
them a real velocity multiplier without sacrificing the engineering
rigor Federal customers expect. Teams that treat the AI as a "just
do what I say" tool without the discipline will produce fast-looking
work that falls apart in review.

## Reading list

- [`journey/00-origin`](00-origin.md) — how the project started and
  what it's *not*
- [`journey/14-overnight-run-findings`](14-overnight-run-findings.md)
  — the moment the project's first major architectural flaws were
  discovered, which is a good example of checkpoint discipline in
  action
- [`journey/15-the-test-as-architecture-discovery`](15-the-test-as-architecture-discovery.md)
  — the reframe that came out of the checkpoint at the end of the
  v3 fix pass
- [`architecture/01-reflexive-agent-harness-failure-modes`](../architecture/01-reflexive-agent-harness-failure-modes.md)
  — the project-agnostic contribution that came out of the work
