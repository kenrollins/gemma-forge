---
id: journey-44-loop-engineering-gets-a-name
type: journey
title: "The Month Loop Engineering Got a Name"
date: 2026-06-24
tags: [L4-orchestration, loop-engineering, cross-run-learning, memory, positioning, discovery]
related:
  - journey/00-origin
  - journey/13-ralph-persistence-retry-budget
  - journey/26-dreaming-and-real-databases
  - journey/38.6-context-graphs-and-the-tip-causal-signal
  - journey/42-12b-vs-31b-straight-up
  - journey/43-memory-isnt-reasoning
one_line: "In June 2026 the term 'loop engineering' went from a seed tweet to a 6.5-million-view movement, with Addy Osmani codifying it and Anthropic's Boris Cherny endorsing it ('My job is to write loops'). The lineage it draws — prompt → context → harness → loop, descended from Ghuntley's Ralph loop, ReAct, and Reflexion — is the exact lineage this project has been building on since April. We line our harness up against the movement's six building blocks: most match, two things they emphasize we're weak on, and two things we ship that the discourse hasn't started talking about yet."
---

# The Month Loop Engineering Got a Name

This month a phrase the project had been quietly living inside got a name, a press cycle, and — by one report — a single post that crossed 6.5 million views in days. The phrase is **loop engineering**. The rallying line, attributed to Anthropic Claude Code lead Boris Cherny, is the kind of sentence that travels:

!!! quote ""
    I don't prompt Claude anymore. I have loops running that prompt Claude and figuring out what to do. My job is to write loops.

The interesting thing — the thing worth a journal entry — is not that the industry arrived somewhere new. It's that it arrived, independently and loudly, at the lineage this project picked in April, cited the same ancestors we cited in [entry 00](00-origin.md), and then stopped one or two steps short of where our worst runs had already dragged us. This isn't a flag-planting entry. There's no "we were first" to claim; the ideas predate all of us. It's a positioning entry: where the field's vocabulary now sits relative to what's running on the XR7620, what they're saying that we should steal, and what we're running that they haven't named yet.

## What the term actually means

Strip the press cycle and loop engineering is a vocabulary crystallization, not a new technique. Addy Osmani (Google Chrome DevRel), credited with naming and codifying it, defines it as: *"replacing yourself as the person who prompts the agent. You design the system that does it instead."* Peter Steinberger seeded the phrasing weeks earlier; Cherny's quote gave it authority. The community's own framing is a four-stage lineage:

> **Prompt Engineering (2022–24) → Context Engineering (2025) → Harness Engineering (early 2026) → Loop Engineering (June 2026)**

Each layer subsumes the last. Prompt engineering optimizes what you say in one turn. Context engineering optimizes what you show the model. Harness engineering scaffolds a single session. Loop engineering orchestrates *who prompts whom, when, and how often* — the LLM demoted to one component inside a self-correcting state machine, with the unit of value moving from the response to the trajectory.

That progression is the one this project has been operating inside since April. We did not derive it from the June discourse; we derived it from the same primary sources the June discourse is built from, which is the part that matters.

## Same ancestors, named on the record

The movement's technical genealogy reads like our citations file. Ghuntley's 2025 **Ralph loop** — run the agent, let it work, write state to a file, start a fresh session with only that file as context, repeat — is named as the practitioner root. **ReAct (2022)** and **Reflexion (NeurIPS 2023)** are named as the academic roots. Those are the same three the project's origin entry and whitepaper stand on. When a field's marquee writers reach for your bibliography to explain a trend, that's not a coincidence to brag about; it's confirmation that the bet on those primitives was the consensus-correct bet, which is more useful than being early.

The timeline, for the record: this project's foundational ADRs are dated **2026-04-08**; the wall-clock persistence budget landed [April 10](13-ralph-persistence-retry-budget.md); Runs 1–3 completed April 13–14; the memory-architecture pivot landed [April 15](26-dreaming-and-real-databases.md). The term went viral in mid-June. We had an instrumented version running about two months before the vocabulary existed. The honest framing of that gap is not "ahead of the curve" — it's "the curve and we read the same papers, and we happened to start building sooner."

## The six building blocks, lined up against the harness

Osmani's canonical formulation gives loop engineering six building blocks. Lining them up against what's actually running is the most honest way to see where we stand.

| Loop-engineering block | What it means | GemmaForge equivalent | Verdict |
|---|---|---|---|
| **Automations / triggers** | Cron, PR-opened, Slack-mention heartbeats that self-bootstrap the loop | `WorkQueue.scan()` over a batch corpus — no event triggers | **Weak match** |
| **Worktrees** | Isolated git checkouts for collision-free parallel sub-agents | `Checkpoint.save/.restore` — libvirt VM snapshots, revert not isolate | **Different problem** |
| **Skills (SKILL.md)** | Externalized project knowledge in repo files, not prompts | Five Protocol interfaces; domain plugs in without touching the harness | **Exact match** |
| **Plugins / MCP** | Managed connectors that grant execution permission | `Executor.apply()` does the privileged action | **Match** |
| **Maker / checker** | Separate generation from verification; "too kind to grade its own homework" | Architect / Worker / Reflector (LLM) + a **deterministic** Evaluator | **We go further** |
| **Durable state** | Memory on disk (STATE.md, Postgres) instead of in the context window | Four-tier memory + Graphiti/Postgres + a dream pass | **We go much further** |

Three of those rows are worth pulling out, because they're where the comparison gets specific.

## Where the field validates choices we'd already measured

Three of loop engineering's load-bearing principles are things this project not only adopted but has the run logs to defend.

**Persistence is measured in time, not attempts.** The discourse warns that *"without explicit stopping conditions a loop runs until the money runs out,"* and prescribes hard time and cost ceilings. [Entry 13](13-ralph-persistence-retry-budget.md) — titled "The Retry Budget That Wasn't Ralph" — is the day we tore out the attempt counter and replaced it with a 20-minute wall-clock budget per item, on exactly that reasoning. The field is now writing down the lesson that entry recorded in April.

**The checker can't grade its own homework.** The maker/checker block's stated principle is that "the model is too kind to grade its own homework," and the gold-standard verification is "verifiable automated checks, not agent self-assessment." That is the Evaluator-is-never-an-LLM rule, load-bearing in this harness since the reflexion architecture went in. Our Evaluator runs OpenSCAP for STIG, `dnf updateinfo` for CVE, a meterpreter-session check for pentest — binary, deterministic, domain-defined. The catch the discourse mostly elides: you only *get* a deterministic checker if you pick a domain with ground truth. We did, on purpose, and [entry 39](39-detection-tuning-third-skill-ships.md) is the postmortem of the one skill where the ground truth was fuzzy and the whole thing got softer for it.

**The context window is not memory.** *"Long-running loops break the moment you try to use the context window as memory"* is a direct quote from the movement's own writeups. It is also the literal Ralph insight the four-tier memory was built around. No daylight here — just the same conclusion, reached twice.

## Where they're ahead of us, plainly

Positioning entries that only flatter the project aren't worth writing. Two of the six blocks are places the field is genuinely ahead.

**Triggers and the outer loop.** The movement's strongest emphasis — automations that make the loop self-bootstrapping off a schedule or an external event — is our weakest surface. The harness is a queue-driven batch job: it scans a corpus, grinds it, stops. It does not yet wake itself on a cron, react to a freshly published CVE advisory, or get poked by a PR. The "loop that runs while you sleep" framing is real, and our loop only runs while you sleep because you started it before bed.

**Worktree parallelism.** They isolate parallel sub-agents in git worktrees so several can edit a repo without colliding. We mutate a VM, not a repo, and we do it sequentially per item behind a snapshot. It's a different problem — but they have a concurrency story and we don't, and "patient grind, one item at a time" is a deliberate constraint we should be able to defend rather than a capability we have.

## Where we've taken it farther than the conversation has reached

Two things this project ships are, as best I can find across the June writeups, simply not in the discourse yet.

**Memory that learns, not memory that persists.** Every loop-engineering treatment treats "durable state" as a place to *reload* facts so the context window doesn't have to hold them — STATE.md, a JSON blob, a Postgres row. That's storage. The dream pass between runs does *outcome-driven credit assignment*: it grades each retrieved lesson against the final state of the rule it was used on, tracks `confidence` (did the advice work) separately from `weight` (how often the advice appears), writes `SUPERSEDED_BY` edges when the Reflector identifies a prior approach as wrong, and penalizes lessons learned under a different VM baseline. [Entry 38.6](38.6-context-graphs-and-the-tip-causal-signal.md) pushed it further into per-retrieval causal attribution — distinguishing "the tip was retrieved and followed and the rule passed" from "the tip was retrieved, ignored, and the rule passed anyway," which look identical in a naive outcome log and were silently poisoning the corpus. The field is talking about giving the loop a memory. We're three or four iterations into the harder question of whether that memory is telling the truth.

**A measured ceiling on the optimism.** The movement is bullish, and the bullish case is mostly right: loops let a smaller, cheaper, edge-deployed model accomplish work that one-shot prompting can't. This project spent April through June proving that — and then ran straight into the boundary the hype hasn't hit yet. [Entry 42](42-12b-vs-31b-straight-up.md) and [entry 43](43-memory-isnt-reasoning.md) put a number on it: hand a 12B model the exact accumulated memory the 31B used, on the same VM, same rules, and it still goes **1/21 on the sysctl network-hardening family the 31B sweeps 21/21.** Memory is not reasoning. The loop lifts the floor on the declarative 70% of the work; it does not buy the parameter count the hard tail requires. [Entry 25](25-run-3-learning-plateaus.md) found the matching plateau on the memory side — accumulation helps until stale memory starts costing more than it saves. The discourse will hit both walls; it just hasn't run enough times to see them.

## What this is good for

The reusable read here, the one worth carrying into a presentation, is a clean three-part shape. **One:** the field independently converged on the prompt → context → harness → loop lineage and named the same ancestors, which means the architecture bet was the consensus-correct bet. **Two:** the harness already satisfies the movement's hard constraints — time-budgeted persistence, deterministic verification, state off the context window — with run logs to prove each one, plus two honest gaps (triggers, parallelism) worth naming out loud. **Three:** the two pieces that aren't in the conversation yet — a memory that grades its own advice, and a measured ceiling showing where the loop stops substituting for model size — are exactly the pieces a technical audience hasn't heard the hype address.

The movement's central caution, from Osmani, belongs at the end of the talk too: *"loops shouldn't go faster than you can understand them."* The journal you're reading is the project's answer to that — forty-odd entries of the loop being made to explain itself, run by run, including the runs where it got dumber. That's the part no view count buys.

## Related

- [Entry 00 — The Origin of gemma-forge](00-origin.md) — why Ralph loops, named here as the practitioner root the June discourse cites too.
- [Entry 13 — The Retry Budget That Wasn't Ralph](13-ralph-persistence-retry-budget.md) — wall-clock persistence, the field's "time not attempts" lesson, in April.
- [Entry 26 — Real Databases (and a Nap)](26-dreaming-and-real-databases.md) — the dream pass and why outcome-driven credit assignment is the distinctive contribution.
- [Entry 38.6 — Context Graphs and the Tip Causal Signal](38.6-context-graphs-and-the-tip-causal-signal.md) — the per-retrieval causal attribution that takes "durable state" past storage.
- [Entry 42 — 12B vs 31B, Straight Up](42-12b-vs-31b-straight-up.md) and [Entry 43 — Memory Isn't Reasoning](43-memory-isnt-reasoning.md) — the measured ceiling the optimism hasn't reached.
