---
id: futures-detection-tuning-preflight
type: futures
title: "Detection-Tuning Skill — Pre-Flight Checklist + Build Sequence"
date_first_surfaced: 2026-05-22
related:
  - futures/detection-tuning-skill
  - adr/0020-skill-provided-worker-context
status: SPECULATIVE — execution plan for if/when the weekend project starts
one_line: "The companion doc to futures/detection-tuning-skill.md — three pre-flight investigations that convert the proposal from 'maybe' into 'green light, here's what I'm building,' plus the hour-by-hour build sequence if pre-flight comes back clean."
---

# Detection-Tuning Skill — Pre-Flight Checklist + Build Sequence

This is the operational companion to
[`futures/detection-tuning-skill.md`](detection-tuning-skill.md). That
doc captures the proposal and architectural reasoning. This one
captures the *executable plan* — what to actually do before and
during a weekend build.

## Operating constraints

The build runs alongside an active gemma-forge installation that's
serving production workloads:

- **Do NOT touch the `gemma4-31b-vllm` systemd service** — it's shared
  inference infrastructure used by multiple projects. The vLLM
  endpoint at `http://localhost:8050` is fine to *use* (it's
  designed for concurrent clients); just don't restart it.
- **Do NOT touch the running Ralph process** if there's one in
  flight. Check `/tmp/forge-run.pid` and `forge status` before any
  changes that might affect it.
- **Do NOT modify the `stig` or `cve` schemas in Postgres**. Make a
  new `detection` schema if you need persistence.
- **Do NOT modify files in** `/data/triton/`, `/data/vm/`, or the
  active `runs/` directory.

Safe playground:

- New files under `skills/detection-tuning/`
- New scratch docs under `docs/drafts/` (gitignored)
- External corpora under `/tmp/dt-corpora/` or wherever you prefer
  outside the repo
- Repository git (commits/branches)

## Pre-flight — three investigations, ~2 hours total

Each has a clear go/no-go signal. If any comes back red, the
build assumptions in [`detection-tuning-skill.md`](detection-tuning-skill.md)
need revisiting before committing to a weekend.

### Pre-flight 1: corpus reality check (30 min)

**Goal**: Confirm at least one corpus has labels in a shape we can
compute precision/recall against.

```bash
mkdir -p /tmp/dt-corpora && cd /tmp/dt-corpora

# Start with EVTX-ATTACK-SAMPLES — smallest, fastest, Sigma-native
git clone --depth 1 https://github.com/sbousseaden/EVTX-ATTACK-SAMPLES.git evtx

# Look at the directory structure — is it organized by ATT&CK technique?
ls evtx | head -20
find evtx -name "*.evtx" | head -5

# Read the README — is there a label schema (malicious vs benign)
# or is everything in here "this happened during an attack"?
head -80 evtx/README.md
```

**Green** if:
- The repo is per-technique organized (e.g., `T1003 Credential Dumping/`)
- The README or directory naming makes it clear each EVTX is *malicious*
  (so the corpus IS the labeled-malicious set; benign comes from a
  separate clean baseline)

**Yellow** if:
- It's all labeled "malicious" with no benign corpus included.
  Workaround: pair with a clean Windows event baseline (e.g.,
  Splunk BOTS v3, or generate a clean Sysmon capture). Note this
  as a build step.

**Red** if:
- No clear label structure at all. Investigate BOTSv3 or DARPA OpTC
  before committing.

### Pre-flight 2: Sigma toolchain reality check (30 min)

**Goal**: Confirm `sigmac` (or `sigma-cli`) can convert real Sigma
rules to a query language we can execute against the corpus.

```bash
# sigma-cli is the modern replacement for sigmac
uv pip install sigma-cli --python /tmp/dt-venv/bin/python || true
# OR: pipx install sigma-cli

# Get a representative Sigma rule from the public repo
mkdir -p /tmp/dt-rules && cd /tmp/dt-rules
git clone --depth 1 https://github.com/SigmaHQ/sigma.git
ls sigma/rules/windows/process_creation/ | head -5

# Pick one rule, convert it to Splunk SPL (the lingua franca)
sigma convert -t splunk sigma/rules/windows/process_creation/proc_creation_win_powershell_encoded_command.yml
```

**Green** if:
- `sigma convert` outputs valid-looking SPL
- The conversion doesn't error out on common rule patterns

**Yellow** if:
- Conversion works but some Sigma `modifiers` (`|contains|all`,
  `|cidr`, etc.) fail. Note which patterns work / don't.

**Red** if:
- The Python tool is fundamentally broken or missing. Pivot to
  Sigma's CLI (Java-based, slower) or fall back to running rules
  directly against a Sigma engine like
  `sigma-cli` against Splunk's HTTP API.

### Pre-flight 3: end-to-end manual proof (1 hour)

**Goal**: Manually take one Sigma rule, run it against the corpus,
compute precision/recall — no harness, no skill, just a script.

```python
# /tmp/dt-poc.py
# Manually parse a few EVTX files for a known-malicious technique,
# add some known-benign Windows events from a clean capture, and
# run a converted Sigma query against them.
#
# Endpoint: precision = matched_malicious / matched_total
#           recall    = matched_malicious / all_malicious_in_corpus
#
# Output: a single number for one rule. That's what the harness
# Evaluator will compute on every iteration.
```

Sketch what "a single iteration of Ralph's loop" looks like
*manually*: load the rule, run it against the labeled events, print
precision/recall. Tune one parameter (e.g., add an exclusion), run
again, see precision improve.

**Green** if:
- You can compute precision/recall in under 5 seconds per rule
- The numbers move when you tune the rule (i.e., the signal is real)

**Yellow** if:
- The numbers are noisy or hard to compare (label imbalance,
  corpus too small). May need to combine multiple corpora.

**Red** if:
- You can't even build the labeled dataset manually. The skill is
  blocked on data engineering, not on Ralph. Defer or pivot domain.

### Decision after pre-flight

| Result | Action |
|---|---|
| All three green | Start the weekend build per the sequence below |
| Any yellow | Document the workaround in `docs/drafts/`, then proceed cautiously |
| Any red | Update [`detection-tuning-skill.md`](detection-tuning-skill.md) with the finding, don't build |

## Build sequence (if pre-flight is green)

The build mirrors the STIG/CVE skill structure. Read those for the
pattern before writing new code:

- [`skills/stig-rhel9/`](../../../skills/stig-rhel9/) — the most
  fleshed-out skill; canonical reference
- [`skills/cve-response/`](../../../skills/cve-response/) — the
  second skill; shows how reuse the harness pattern with
  domain-specific extensions
- [`gemma_forge/harness/interfaces.py`](../../../gemma_forge/harness/interfaces.py) —
  the SkillRuntime / Evaluator / WorkQueue / Executor / Checkpoint
  Protocol contracts

### Friday night (2-3h): minimal vertical slice

**Goal**: One Sigma rule goes through the Ralph loop end-to-end.

```
skills/detection-tuning/
├── skill.yaml                # name, version, FailureMode mapping
├── runtime.py                # bundles sub-runtimes
├── corpus_loader.py          # loads EVTX-ATTACK-SAMPLES
└── sigma_eval.py             # rule → SPL → corpus → P/R/F1
```

`runtime.py` exposes:
- `DetectionWorkQueue.next()` — yields one hardcoded `WorkItem` for
  Friday night ("tune rule X against corpus Y")
- `DetectionExecutor.apply(item, candidate)` — writes the candidate
  rule file to a scratch dir
- `DetectionEvaluator.evaluate(item)` — runs `sigma_eval.py`, returns
  `EvalResult(passed=..., failure_mode=...)`
- `DetectionEvaluator.signal_for(result, attempt_number=...)` —
  graded signal: precision/recall as outcome quality
- `DetectionCheckpoint` — git-stash-style save/restore on the rule
  file

Run it once through `forge run detection-tuning` (after registering
in the skill loader). If it loops once and produces output, Friday
night succeeded.

**Don't build**: WorkQueue scaling, prompts, multi-rule batching,
demo polish.

### Saturday morning (3-4h): real skill

**Goal**: All of `SkillRuntime` is implemented properly, including
the new patterns.

- `worker_context(item)` — returns `{sigma_schema_excerpt,
  sample_events_for_technique, prior_rule_version}`. This is the
  DEF-28 enrichment that gives the Worker the *spec* it needs.
- `prompts/architect.md` — "here are 50 rules below threshold, pick
  the one most worth tuning"
- `prompts/worker.md` — "tighten this rule against these labeled
  events"
- `prompts/tip_follow_judge.md` — DEF-27 follow judge for this
  skill (templated after `skills/stig-rhel9/prompts/tip_follow_judge.md`)
- Failure mode taxonomy: `RULE_TOO_NOISY`, `RULE_TOO_NARROW`,
  `RULE_PARSE_FAILURE`, `CORPUS_GAP`, `NEEDS_BASELINE`
- `EvaluatorMetadata`: `signal_type="graded"`, `eviction_threshold`
  tuned for this domain

By end of Saturday morning, `forge run detection-tuning` should
process 5-10 rules in a single run.

### Saturday afternoon (2-3h): the demo arc

**Goal**: Run twice against different corpora; verify cross-corpus
tip transfer works.

- Add a second corpus loader (BOTS v3 or MITRE ATT&CK Evaluation)
- Run 1: `forge run detection-tuning --corpus evtx-attack-samples`
- Audit memory state with `tools/audit_memory.py --skill detection`
- Run 2: `forge run detection-tuning --corpus bots-v3` (or whichever
  corpus you added)
- The headline metric: did Run 2's first-attempt success rate
  benefit from Run 1's tips?

If yes — the cross-corpus story is empirically supported and the
demo writes itself.

If no — the skill still works; the framing pivots to "Sigma tuning
automation" without the cross-corpus claim.

### Sunday (optional): polish

- ADR-0021 capturing the build (if it shipped well)
- Journey entry — promote
  [`futures/detection-tuning-skill.md`](detection-tuning-skill.md)
  to a journey entry per the futures-promotion convention; leave the
  futures doc as the historical record of the original proposal
- Demo screenshots / narrative
- Performance tuning if the eval loop is slower than expected

## Files to read first (in this order)

1. [`futures/detection-tuning-skill.md`](detection-tuning-skill.md) —
   the proposal
2. This file — the execution plan
3. [`adr/0020-skill-provided-worker-context.md`](../../adr/0020-skill-provided-worker-context.md) —
   the `worker_context` pattern this skill will use
4. [`adr/0019-context-graph-outcome-attribution.md`](../../adr/0019-context-graph-outcome-attribution.md) —
   the graded outcome + DEF-27 tip-follow scoring this skill will
   inherit automatically
5. [`skills/stig-rhel9/runtime.py`](../../../skills/stig-rhel9/runtime.py) —
   the most complete skill implementation; mirror its structure
6. [`gemma_forge/harness/interfaces.py`](../../../gemma_forge/harness/interfaces.py) —
   the Protocol contracts the skill needs to satisfy
7. [`journey/38.7-runs-8-and-9-what-the-data-said.md`](../journey/38.7-runs-8-and-9-what-the-data-said.md)
   and
   [`journey/38.8-how-we-missed-the-descriptions.md`](../journey/38.8-how-we-missed-the-descriptions.md) —
   the recent context that motivates this skill being structurally
   different from STIG/CVE

## Working notes go where?

- **Pre-flight findings**: append to this file as you complete each
  check (`Pre-flight 1: ✅ green, see notes`)
- **Build progress**: a new draft doc at
  `docs/drafts/detection-tuning-build-notes.md` (gitignored — for
  your own running notes)
- **Architectural decisions**: ADR-0021 if any new patterns emerge
- **Domain learnings**: journey entry promotion when the skill ships
