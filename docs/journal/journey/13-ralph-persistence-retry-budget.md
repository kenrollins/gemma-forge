---
id: journey-13-ralph-persistence-retry-budget
type: journey
title: "The Retry Budget That Wasn't Ralph"
date: 2026-04-10
tags: [L4-orchestration, reflexion-loop, decision]
related:
  - journey/11-the-missing-reflector
  - journey/14-overnight-run-findings
  - improvements/01-architect-reengagement
one_line: "I built a reflexion loop with a 3-retry cap because that is what the academic paper does — and then realized that capping retries is the opposite of what makes Ralph interesting, so I replaced the counter with a wall-clock budget and let the loop actually grind."
---

# The Retry Budget That Wasn't Ralph

I built a reflexion loop with a 3-retry cap because that's what the academic
paper does — and then realized that capping retries is the *opposite* of what
makes Ralph interesting, so I replaced the counter with a wall-clock budget
and let the loop actually grind.

## The trap

The Reflexion paper (Shinn et al., NeurIPS 2023) uses a fixed retry cap —
typically 3 to 5 — for each task in its benchmark. That convention slid into
ralph.py as `max_retries_per_rule: 3` and stayed there through four major
architecture revisions.

For a while the cap felt reasonable. The first Reflexion runs were about
*validating that the loop worked at all*: can the Reflector produce structured
output? Does episodic memory propagate? Does plateau detection fire? 3 retries
is plenty to exercise all of that.

Then a reframing question landed: is 10 failed attempts in a row Ralphie
enough? Given enough room, could the logic actually figure some of these
out?

## What the previous run's reflections actually said

I went back and looked at the AIDE-rule failures from
`run-20260410-203508.jsonl`. The reflections told a clear story:

- **Attempt 1** on `aide_check_audit_tools`:
  > "Pattern: Incomplete remediation of AIDE configuration. Root cause: The
  > Worker updated the configuration file and the database, but likely failed
  > to ensure the AIDE database was initialized/updated in a way that the
  > scanning tool recognizes as current."

- **Attempt 2**:
  > "Pattern: Superficial configuration updates that fail to trigger a Pass in
  > the SCAP scanner. Root cause: The Architect is modifying
  > `/etc/aide.conf` but neglecting the operational requirement of AIDE: the
  > configuration changes are not active until the AIDE database is initialized
  > or updated."

- **Attempt 3**:
  > "Pattern: Attempting to remediate a specific configuration rule while
  > ignoring critical underlying system failures (broken `/etc/aide.conf`
  > preventing `aide --init`). Root cause: The Worker treated the STIG rule as
  > a standalone text-editing task rather than a functional system requirement."

Then the loop gave up.

Look at the arc: reflection #1 says "something about db init probably", #2 says
"you're ignoring db activation", #3 says "the config is broken AND the db isn't
being initialized". Each reflection is *sharper* than the last. The Reflector
is converging on the real fix ("you need to run `aide --init` AND your config
has to actually be valid before that works").

**At attempt 4 or 5, the Reflector would almost certainly have produced the
exact remediation strategy.** And the cap cut it off.

## The insight

The whole point of the Ralph loop — the thing that differentiates it from
"just call a good model" — is *persistence*. The thesis is:

> With enough grinding and a good reflection mechanism, a small edge-deployed
> model can solve problems that would otherwise require a frontier model,
> because each failure compounds into sharper reasoning on the next attempt.

A fixed attempt cap directly undercuts that thesis. It says "the model failed
3 times in a row, therefore it cannot solve this problem" — which is the exact
opposite of what persistence-first means.

!!! quote ""
    Persistence-first says the model keeps working until *physics* says stop, and physics means wall clock or provably diminishing returns — not an arbitrary counter.

The academic cap of 3 makes sense in a paper where the experiment is measuring
a benchmark of 200 tasks and you need bounded runtime per task. This experiment
isn't benchmarking — it's demonstrating that persistence pays off. Different
experiment, different stopping rule.

## The fix

Replace the attempt counter with a wall-clock budget:

```yaml
loop:
  max_iterations: 1000
  max_rules_per_run: 1000
  max_retries_per_rule: 100          # safety ceiling, not a real limit
  max_wall_time_per_rule_s: 1200     # 20 minutes — the REAL escalation trigger
```

The inner loop structure changes from `for attempt in range(1, max_retries+1)`
to `while True` with the first thing inside being a time check:

```python
while True:
    attempt += 1
    rule_elapsed = time.time() - rule_start_wall
    if rule_elapsed >= max_wall_time_per_rule_s:
        escalation_reason = "time_budget"
        break
    if attempt > max_retries:  # safety only
        escalation_reason = "retry_ceiling"
        break
    # ... attempt logic
```

And the escalation event now records *why* the loop gave up, so "the model
plateaued and hit time" is distinguishable from "something broke and the
safety ceiling tripped":

```python
run_log.log("escalated", "harness", {
    "rule_id": ...,
    "attempts": attempt - 1,
    "wall_time_s": round(rule_wall_time, 1),
    "reason": escalation_reason,  # "time_budget" | "retry_ceiling"
})
```

I also added a plateau-detection *metric* (not a stopping rule) — if the last
three reflections for a rule share the same first-sentence pattern, the
`rule_complete` event is flagged with `reflector_plateaued: true`. That gives
a way to analyze after the run: did unlimited retries actually pay off, or
did the reflector just repeat itself once it plateaued?

## What this run is about to reveal

The plan is a ~12 hour run against the same 270 STIG rules. The questions
this run answers:

1. **Does compounding reflection actually pay off past attempt 3?** If so,
   rules should be remediated at attempts 4, 5, 6+. If not, `reflector_plateaued`
   should become true around attempt 3–4 for most rules.

2. **Which rules converge fast and which grind?** The time budget forces a
   natural distribution — easy rules take 30-60 seconds, medium rules take a
   few minutes, hard rules hit 20 minutes. The `rule_complete` events with
   `wall_time_s` give that distribution directly.

3. **Does the architect's rule selection strategy hold up?** The previous run
   saw the architect picking AIDE rules repeatedly because they were listed
   first. With the architect only being consulted at rule selection (not
   during retries), a time-budget-per-rule means the architect's initial
   rule-picking judgment matters more. If it keeps picking hard rules first,
   time burns before the easy wins arrive.

4. **What's the real rules/hour at steady state?** Previous run was ~13 rules/hour
   with 3-retry cap. With a 20-min budget, most rules will be *faster* on
   average (because easy rules still take 30s), but some will be *slower*
   (because hard rules now grind for the full 20 minutes). The net should be
   lower throughput but higher remediation rate.

## What this tells me about the architecture

The bigger lesson is that **the architect only engages at rule selection time.**
After the rule is picked, the inner loop is just Worker + Eval + Reflector.

This means if the Reflector suggests a fundamentally different strategy that
would need a different Worker prompt or a different tool, nobody can act on
that. The Worker is constrained to the tools and prompt it was given at rule
selection time.

A future refactor would be to *re-engage the architect* after N failures. The
architect would see the full reflection history and could either:
- Approve continued grinding with the current Worker approach
- Tell the Worker to take a radically different angle
- Decide the rule is genuinely stuck and preemptively escalate

That's a phase-4 improvement. For now, this overnight run establishes whether
the simpler "Reflector-only iteration" is sufficient, or whether we actually
need the architect's strategic re-engagement to unblock stuck rules.

## Files changed

- `config/harness.yaml` — new keys, bumped caps
- `gemma_forge/harness/ralph.py` — inner loop rewritten, new instrumentation,
  helper functions added (`categorize_rule`, `reflection_first_sentence`,
  `detect_plateau`)

## Related

- ADR for this decision: TBD (add after overnight run validates the approach)
- Reflexion paper: Shinn et al., NeurIPS 2023
- `journey/11-the-missing-reflector.md` — why the Reflector was added in the first place
- `journey/12-bf16-tp4-full-precision.md` — the hardware substrate that makes extended grinding affordable
