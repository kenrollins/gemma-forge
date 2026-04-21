---
id: improvement-01-architect-reengagement
type: improvement
title: "Improvement Idea: Architect Re-engagement After N Failures"
date: 2026-04-11
tags: [L4-orchestration, reflexion-loop, refactor]
related:
  - journey/14-overnight-run-findings
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Proposal for architect re-engagement: after N failed attempts or plateau detection, re-invoke the strategy agent with full failure history and let it decide CONTINUE / PIVOT / ESCALATE."
---

# Improvement Idea: Architect Re-engagement After N Failures

**Status:** Implemented in v3 (2026-04-13). See [journey/17 — The v3 Fix Pass](../journey/17-v3-fix-pass.md) for the implementation narrative. Still load-bearing in the current harness; entry 34 shows the re-engagement mechanism firing routinely in Run 6.
**Surfaced:** 2026-04-11, during overnight instrumentation work
**Priority:** High — likely the next structural improvement to the Ralph loop

## The observation

In the current architecture, the Architect is only consulted once per rule:
at rule-selection time. After that, the inner loop is a closed Worker + Eval
+ Reflector cycle until the rule succeeds, escalates, or times out.

This means if the Reflector determines that the *entire strategic direction*
is wrong (not just that the Worker made a small mistake), nobody has the
authority to pivot. The Worker keeps trying variations on the same original
plan, informed by progressively sharper reflections, but constrained to the
conceptual frame the Architect set at the start.

## Why this matters

The Reflector can identify a flawed strategy but cannot replace it. Consider
the AIDE rules from `run-20260410-203508.jsonl`:

- **Architect's initial plan (attempt 1)**: "Modify `/etc/aide.conf` to enable
  the check required by this rule"
- **Reflector after attempt 1**: "The config changes aren't active because
  AIDE's database hasn't been rebuilt"
- **Reflector after attempt 2**: "Config is being treated as authoritative but
  the tool validates against the database state"
- **Reflector after attempt 3**: "The AIDE binary can't execute because the
  config is syntactically invalid — solve THAT before anything else"

By attempt 3 the Reflector has essentially said "the Architect's plan assumed
the wrong abstraction." But the Architect isn't in the room to hear it. The
Worker, in the next attempt, is still implicitly working off the original
"modify config" frame, just with tighter constraints.

A human operator watching this loop would intervene at attempt 3 and say
"stop, back up — fix the config validity first, then everything else becomes
possible." The Architect agent is exactly the role that should do this, and
we're not giving it the chance.

## Proposed mechanism

After every N failed attempts for a rule (N ≈ 3), OR whenever the plateau
detector fires, call the Architect with:

1. The original rule and its title
2. The full episodic memory for this rule (every attempt, every reflection)
3. The prompt: *"The current approach is not working. Review the failure
   history. Either (a) approve continued retries with a new strategic
   direction for the Worker, or (b) declare the rule genuinely stuck and
   escalate preemptively."*

The Architect's response becomes the new "Architect's plan" passed to the
Worker for subsequent attempts. The Reflector's job stays the same — it still
reflects on individual failures — but the Architect now has the power to
*reframe the problem* rather than just pick which problem to work on.

## Why this is the right next step

1. **It's the natural completion of the 4-agent architecture.** Right now
   Architect and Reflector play disjoint roles: Architect picks, Reflector
   learns from micro-failures. Re-engagement closes the loop by letting the
   Reflector's insights flow back up to the Architect, who can act on them
   with strategic authority.

2. **It fixes the specific failure mode we observed.** The AIDE rules failed
   not because the Worker was bad but because the Architect's initial plan
   was the wrong abstraction. Re-engagement solves exactly this.

3. **It reinforces the "persistence is intelligence, not stubbornness"
   story.** Right now, "persistence" in our loop looks like "the same plan
   with tighter constraints." With re-engagement, persistence looks like "the
   Architect is willing to fundamentally rethink the approach when the data
   demands it." That's a much more compelling demo narrative.

## Risks and considerations

- **Extra LLM calls** — every N attempts costs an extra Architect turn. At
  N=3 and a 20-minute budget per rule, that's maybe 5–6 extra architect calls
  per stuck rule. Marginal cost.
- **Prompt stability** — the Architect is prompted for initial rule selection,
  not for re-planning. Needs either a new system prompt or a clearly-separated
  re-engagement mode in the existing prompt.
- **State handling** — when the Architect re-engages, which parts of history
  does it see? Too little and it repeats the original mistake. Too much and
  context explodes. Probably: full episodic memory for *this rule only* plus
  the last K reflections.
- **Termination** — if the Architect re-engages, fails, and re-engages again
  with the same verdict, we need a meta-plateau detector. Otherwise we could
  loop forever at the re-engagement level.

## Signal we'll collect tonight that validates this

The overnight run emits `rule_complete` events with `reflector_plateaued` and
`attempts`. After the run we can analyze:

- **If `reflector_plateaued == true`** for a significant fraction of escalated
  rules → strong evidence that re-engagement is needed (the Reflector ran out
  of fresh ideas but didn't have the authority to change the plan)
- **If `reflector_plateaued == false`** for most escalated rules → the
  Reflector kept generating new angles but still couldn't solve the rule,
  suggesting a different issue (maybe Worker execution, maybe rule is genuinely
  unsolvable without human help)

Either way, the data tells us whether re-engagement is the right next move or
whether we should be looking elsewhere.

## Implementation scope (when we do it)

- Add `architect_reengage_every_n_attempts` to `config/harness.yaml`
- Extend `gemma_forge/skills/stig-rhel9/prompts/architect.md` with a
  "re-engagement mode" section that handles the new prompt shape
- In `ralph.py` inner loop, add a re-engagement call after every N attempts or
  when plateau is detected
- Emit a new `architect_reengaged` event with the architect's verdict
- Update Mission/MissionHeader frontend components to show re-engagement moments

Estimated effort: 2–3 hours of code, plus whatever iteration the prompt
engineering needs.
