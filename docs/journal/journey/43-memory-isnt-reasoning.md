---
id: journey-43-memory-isnt-reasoning
type: journey
title: "Memory Isn't Reasoning: Where the 12B Hits Its Ceiling"
date: 2026-06-13
tags: [L3-model, capability, edge, measurement, gemma4-12b, model-choice]
related:
  - journey/42-12b-vs-31b-straight-up
  - adr/0024-gemma4-12b-data-parallel-fleet-evaluation
  - futures/gemma4-12b-fleet-preflight
one_line: "Ran the 12B and the 31B across the entire 263-rule DISA STIG corpus from the identical 52-run memory. The 12B got 76%, the 31B 91% — but the whole gap is one cliff: sysctl_net network rules, where the 12B went 1/21 and the 31B went 21/21. The wall is the model, not the environment, and the bigger model was cheaper getting there."
---

# Memory Isn't Reasoning: Where the 12B Hits Its Ceiling

[Journey 42](42-12b-vs-31b-straight-up.md) was five rules and a hunch: the
12B kept pace on count but escalated the one hard rule the 31B reasoned
through. Five rules can't tell you whether that's a fluke or a ceiling. So
we ran the whole thing — **all 263 failing DISA STIG rules, both models,
from the identical pristine 52-run production memory, same baseline VM.**
The 12B went overnight (~8.25h). Then I rewound its deposits so the 31B
started from the exact same memory, and ran the 31B against the live :8050
(no teardown — it's the production service; quantum-ai-orchestrator just
shared it). ~5.5h later it was done.

## The numbers

| | 12B | 31B |
|---|---|---|
| Remediated | 198/260 (**76%**) | 235/257 (**91%**) |
| Escalated | 58 | 18 |
| Total attempts | 663 | 360 |
| Completion tokens | 644k | 335k |
| Rule wall-clock | 7.05h | 4.56h |
| Attempts / remediated rule | 1.33 | 1.17 |

The 31B remediated 15 points more of the corpus, with **half the attempts,
half the tokens, and a shorter wall-clock.** Sit with that: the *bigger*
model was *cheaper and faster* over the corpus. Capability is convergence —
the 31B solved-and-moved-on where the 12B ground.

## The whole gap is one cliff

Per-category, the two models are nearly indistinguishable on the easy,
declarative majority:

| | 12B | 31B |
|---|---|---|
| authentication | 23/23 | 23/23 |
| package-management | 14/14 | 14/14 |
| service-config | 6/6 | 6/6 |
| audit | 79/86 | 83/86 |
| ssh | 16/18 | 17/18 |
| logging | 9/11 | 10/11 |
| **kernel** | **18/44** | **44/45** |
| filesystem | 5/11 | 6/7 |
| user-account | 2/8 | 4/8 |

For the bulk of a real compliance corpus — install a package, set an sshd
flag, write an audit rule — **the 12B is as good as the 31B.** The entire
15-point gap concentrates in **kernel**, and inside kernel, in one family:

> **`sysctl_net_*` network hardening rules — 12B: 1/21. 31B: 21/21.**

Every IPv4/IPv6 sysctl rule (`tcp_syncookies`, `rp_filter`,
`accept_redirects`, `log_martians`…) defeated the 12B and none defeated the
31B. These were the 12B's worst grinds, too — it burned the full 20-minute
budget across 35–37 attempts on single rules and still failed. The 31B
walked through all 21.

## Why this is the result that matters

The honest worry after journey 42 was that the hard rules might be an
*environmental trap* — something about this VM (the clock skew, the
evaluator) that no model could beat, so the 12B's failure said nothing about
the 12B. The matched 31B run kills that worry. **Same VM, same memory, same
rules: 1/21 vs 21/21.** The wall is the model's reasoning, not the
environment.

And it's not a vague "bigger is better." It's specific. `sysctl_net` rules
demand a fix that's simultaneously runtime-applied, persistent across
reboots, correct across all interfaces, and exactly matching a strict
checker — i.e., precise, multi-constraint code generation. That is exactly
what the published benchmarks separate these models on:

| | 31B | 12B | gap |
|---|---|---|---|
| MMLU-Pro | 85.2 | 77.2 | +8 |
| GPQA-Diamond | 84.3 | 78.8 | +5.5 |
| **LiveCodeBench v6** | **80.0** | **72** | **+8** |

The 8-point coding-benchmark gap looks modest on a leaderboard. **On our
corpus it wasn't smeared — it was a cliff:** 90%+ on the easy rules,
collapsing to 5% on the class that needed the reasoning the benchmark
measures. An abstract score difference became a concrete, nameable
operational wall.

The deepest cut: **the 12B had the knowledge and still failed.** The 52-run
memory it rode contains the exact lessons for sysctl persistence
("use `/etc/sysctl.d/` and `sysctl -w`; `sysctl.conf` + `sysctl -p` fails
because it doesn't override all interfaces"). It hydrated them and still
went 1/21. So the project's own thesis — that the *system* around the model
can substitute for model size — **holds on the easy 70% and breaks on the
hard tail.** Memory is not reasoning. You can hand a model the right
lesson; it still has to execute it under a strict checker, and that's where
parameter count bought the 31B something the system could not.

## What this means for AI at the edge

1. **A single-GPU 12B can own ~76% of real compliance work autonomously.**
   That's a legitimate, cheap edge capability, not a toy.
2. **It has a hard, predictable ceiling.** The failures aren't random — they
   name themselves (sysctl, partitions, mounts, umask). Predictable means
   routable.
3. **The architecture is tiered, not single-model.** The 12B preemptively
   escalated 55 of its 58 failures — *it knows when it's stuck*. That's a
   ready-made routing signal: 12B runs the corpus, escalations go to a 31B
   or a human. ~91%-class outcomes at mostly-12B cost.
4. **The cost intuition inverts at the wall.** "Smaller is cheaper at the
   edge" is true until the small model hits a class it can't solve, then
   burns more tokens and more wall-clock grinding than the big model spends
   succeeding. Cap the grind; escalate fast.
5. **Measure the wall on your own distribution.** An 8-point leaderboard gap
   was a 95-point cliff (1/21 vs 21/21) on the rules that mattered here. The
   leaderboard tells you a model exists; only your corpus tells you where it
   breaks.

## Housekeeping

The 31B run is a normal production run — kept as run 53; the memory grows
as designed (no rewind; the 12B was rewound only because it's a non-prod
model and the 31B needed a matched clean start). The 52-run baseline is
preserved in a backup for any future controlled experiment. Both run logs
are immutable on disk (`run-20260612-013514.jsonl` = 12B,
`run-20260612-133313.jsonl` = 31B) — the full evidence trail for anyone who
wants to re-derive every number here.
