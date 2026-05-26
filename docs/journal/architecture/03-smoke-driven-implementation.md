---
id: architecture-03-smoke-driven-implementation
type: architecture
title: "Smoke-Driven Implementation for Agentic Systems"
date: 2026-05-26
tags: [L4-orchestration, L5-execution, testing, methodology, discovery]
related:
  - journey/40.7-phase-3-skill-stands-up
  - gotchas/pentest-phase-3-architectural-surprises
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "For agentic systems that interact with real adversarial environments, every sub-phase should end with a smoke that runs the new code against a live target. Unit tests can't see what real environments expose."
---

# Smoke-Driven Implementation for Agentic Systems

**Status:** Observed empirically across three skills (STIG, CVE
remediation, network pen-test). Distilled from network-pentest
Phase 3 build, where the pattern produced six sub-phases of green-lit
smokes that each exposed a real bug not visible to unit tests.

**Audience:** People building autonomous agent systems against real
environments — pen-testing harnesses, compliance remediation
harnesses, ops automation, anything where the agent's "world" is a
real container / VM / network / API.

## The pattern

Each sub-phase of an implementation ends with a smoke script that:

  1. Runs the new code against a real (not mocked) environment
  2. Goes end-to-end through whatever layer the sub-phase touches
  3. Reports binary success/failure at the end

You do NOT proceed to the next sub-phase until the smoke passes. When
a smoke fails (and it usually does), the failure is almost always an
architectural surprise — not a bug in the code as designed, but a
bug in the design's contact assumption with reality.

The pattern works regardless of whether unit tests exist. Unit tests
verify code-against-contract. Smokes verify
contract-against-environment. Both matter; smokes catch what unit
tests can't see.

## Why this matters specifically for agentic systems

Agentic systems differ from conventional software in one structural
way: **the environment is part of the system.** The agent doesn't just
run code; it reads observations from a real thing (a target, a
corpus, an API surface) and acts based on what it sees.

Three properties of that environment that unit tests can't capture:

  1. **Multi-layer occlusion.** A vulnerable PHP-CGI service shows up
     in nmap as Apache, because Apache fronts it. The "obvious"
     identification path (banner = component) is wrong. No unit test
     would catch this; the wrapper isn't in the test fixtures.

  2. **Tooling implicit contracts.** `msfconsole` lives in the
     `metasploitframework/metasploit-framework` image, but it isn't
     on `$PATH` for non-interactive `docker exec` — the image's
     entrypoint sets `$PATH` and that only fires for interactive
     shells. A unit test that mocks subprocess success would not
     reveal this.

  3. **Corpus shape vs corpus contents.** A corpus may be the right
     SHAPE but have content gaps you only see when you query it
     (nginx has zero CVEs with recipes; vendor-fallback over Apache
     pulls in too many products). Test fixtures lie because they're
     small; real corpora reveal scale-dependent failure modes.

For all three, the only path to discovery is running the actual code
against the actual environment.

## What a Phase 3 smoke looks like

Each sub-phase of network-pentest Phase 3 had a smoke. The shape
recurred:

```python
# Phase 3.3 engine smoke
def smoke():
    # 1. Bring up the real thing
    spin_up_target(vulhub_php_cve_2012_1823, lab_network)
    ensure_driver_container(lab_network)

    # 2. Build the structures the new code is supposed to produce
    attempt = ExploitAttempt(cve_id="CVE-2012-1823", recipe_id=27482,
                             tool="metasploit", target_host=...)

    # 3. Run the new code end-to-end
    outcome = MsfEngine().run(attempt, recipe_meta)

    # 4. Verify the result through the same pipeline production uses
    result = verify("session_opened", config, outcome.observation)

    # 5. Report binary PASS/FAIL
    print(f"[{'PASS' if result.passed else 'FAIL'}] ...")

    # 6. Tear down
    tear_down(target)
```

The smoke is not "show me a unit-tested function returning the right
type." It's "show me that this new code, against a real target,
produces a real verified outcome."

When the smoke fails, you find out something the design didn't
predict. When it passes, you move on with empirical confidence that
the layer is real-world correct.

## The five smokes that built network-pentest Phase 3

| Sub-phase | Smoke target                          | Caught                                                  |
|-----------|---------------------------------------|---------------------------------------------------------|
| 3.1       | 5 random vetted targets               | Fingerprint-only lookup misses 27k-entry corpus         |
| 3.1       | (rerun)                               | Generic-alias fallback bleeds Apache CVEs to nginx      |
| 3.3       | vulhub PHP-CGI + MsfEngine            | `msfconsole` not on `$PATH` in non-interactive exec     |
| 3.4       | vulhub PHP-CGI + full runtime         | Banner-based recon hides actual vuln component          |
| 3.6       | harness load → propose_exploit        | Dynamically-loaded module orphaned from `sys.modules`   |

Five architectural surprises. None visible to unit tests. All caught
by running new code against the real environment before declaring
the sub-phase done.

## Why not write the smokes after implementation?

The temptation is to "build the layer, write unit tests, then write a
smoke at the end." This is roughly what conventional test-first
methodology recommends, and for non-agentic code it's fine.

For agentic code it's wrong. Here's why:

If the smoke is the last thing you write, you commit to a design
shape BEFORE checking that the shape contacts reality. By the time
the smoke fails (and it will), you've already built three layers on
top of the broken assumption. The refactor is expensive.

If the smoke is concurrent with implementation — written and run at
the END of each sub-phase, before moving to the next — you find the
architectural surprises while they cost a single sub-phase's worth
of changes. That's typically <100 lines of code per fix.

For network-pentest Phase 3:

  - 3.1's lookup-layer surprise → ~50 lines of `lookup_by_banner` +
    `_KNOWN_SERVICE_ALIASES`
  - 3.3's `$PATH` surprise → 1 line (absolute path)
  - 3.4's banner-occlusion surprise → ~30 lines of `pool_intel`
    block in `worker_context()`
  - 3.6's `sys.modules` surprise → 5 lines for `set_current_item()`

Compare to "discovered after Phase 4 LLM tests fail" — at that point
the same fixes ripple through prompts, telemetry, dream-pass
attribution, the whole stack.

## Properties of a good smoke

1. **Real environment.** No mocks for what's-on-the-other-side. Spin
   up the real container, hit the real corpus, run the real binary.
   Mocks for components you've already validated are fine; mocks for
   the environment under test defeat the point.

2. **End-to-end.** A smoke that tests one function in isolation is a
   unit test wearing the wrong hat. A smoke runs the full path through
   the layer — input from a real upstream, output consumed by a real
   downstream.

3. **Binary verdict.** Not "the output looked reasonable" — a specific
   condition that's either met or not. "Session opened against host
   X via msf module Y in <30s" — yes or no.

4. **Self-contained teardown.** Smoke runs, smoke cleans up. Don't
   leave containers, sessions, or test data around. Each run starts
   fresh.

5. **Cheap to rerun.** If a smoke takes 5 minutes you'll run it once;
   if it takes 30 seconds you'll run it after every fix. Optimize for
   the second case. The pentest engine smoke takes ~60s (including
   target spin-up, msf attack, tear-down); the lab smoke takes ~3
   minutes for 5 targets. Both rerunnable mid-fix.

6. **Smoke FOR a sub-phase, not for the whole thing.** A single
   sub-phase per smoke means each surprise lands in code you wrote
   in the last few hours, not last week.

## How this relates to detection-tuning and STIG

Detection-tuning (skill #3) shipped functionally but failed as a
customer demo. With hindsight: the corpus + evaluator smokes would
have surfaced the F1 ceiling earlier. The architecture worked but the
score-against-noise-labeled-data property was invisible until the
agent loop ran. A smoke that ran the evaluator against ground-truth
labels (without the agent) would have shown "no rule can score
P≥0.95 ∧ R≥0.80 on this corpus" before five days of LLM tuning.

STIG (skill #1) was built before this pattern was named. Many of the
journey-entry findings (entries 14, 15, 25, 28, 33) describe what
were essentially smoke-fail discoveries: properties of the SCAP
datastream, behavior of `auditctl -e 2`, FIPS-mode SSH keyalgo
incompatibilities. The pattern shipped value retrospectively as
gotchas; we'd have shipped faster doing it prospectively.

## When the pattern doesn't apply

  - **Pure compute.** A SAT solver, a sort, a serialization format —
    these have static contracts. Unit tests dominate.

  - **No external surface.** A module that only touches in-process
    data structures. No smoke needed.

  - **Already-validated subsystems.** Don't smoke the database driver
    every sub-phase. Smoke layers that you wrote and that touch the
    environment.

If your code's behavior depends on what's running on the other end
of a socket / inside a container / in a 27k-row table, smoke it.

## Why this stays underused

Two reasons:

  - **Smokes are perceived as expensive.** Spinning up real
    containers, real msf instances, real corpora — it feels slow.
    But the cost of NOT smoking is paid in late-stage architectural
    refactors, which are always worse.

  - **Test-first culture doesn't have language for it.** Smokes
    aren't unit tests; they aren't integration tests in the
    conventional sense; they aren't acceptance tests either. They're
    a fourth thing: continuous architectural-contact validation.
    Without a name, the practice stays informal.

This document is the language. Use it.

## TL;DR

  - For each sub-phase of an agentic-system build, write a smoke
    that runs the new code against the real environment.
  - The smoke must produce a binary PASS / FAIL on a specific
    condition.
  - Do not proceed to the next sub-phase until the smoke passes.
  - When the smoke fails (and it will), expect architectural
    surprises, not code bugs. Fix the architecture; rerun the smoke.
  - The cost is one tear-up + spin-up cycle per sub-phase. The
    benefit is finding architectural surprises while they're still
    cheap to fix.
