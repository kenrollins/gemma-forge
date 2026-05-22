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

---

## Pre-flight findings — 2026-05-22 19:53 PT

Run alongside an active Run 10 (stig-rhel9, PID 655932). VM untouched.
Total time: ~45 min (vs 2 h budget).

### Pre-flight 1: ✅ green (after one yellow downgrade)

Cloned `sbousseaden/EVTX-ATTACK-SAMPLES` to `/tmp/dt-corpora/evtx`.
**278 EVTX files** organized by ATT&CK *tactic* (Credential Access,
Defense Evasion, …), not technique. Filenames encode the
technique/scenario (`CA_Mimikatz_Memssp_…evtx`).

Per README, every EVTX is an attack-associated capture — no paired
benign corpus included. That matched the doc's predicted **yellow**.

The win that flipped it back to green: the repo ships
`evtx_data.csv` (9886 rows, parsed by tactic), one row per event with:

- `EVTX_FileName` — source EVTX
- `EVTX_Tactic` — MITRE tactic
- All the Windows/Sysmon event fields (`CommandLine`, `Image`,
  `OriginalFileName`, `GrantedAccess`, `CallTrace`, `TargetImage`,
  `ParentImage`, `EventID`, `Channel`, …) — the *exact* field names
  Sigma rules reference

Implication: no EVTX parsing needed in the skill — read the CSV with
pandas. Cross-tactic noise (rule targeting Credential Access matches
an event labeled Defense Evasion) provides the false-positive
signal *without* needing a separate clean baseline.

### Pre-flight 2: ✅ green

Installed `sigma-cli 3.0.2` + `pysigma 1.3.3` + `pysigma-backend-splunk 2.1.0`
into `/tmp/dt-venv`. Cloned `SigmaHQ/sigma` (3132 rules).

Conversion works cleanly on representative rules with the
`splunk_windows` pipeline:

```
$ sigma convert -t splunk -p splunk_windows proc_creation_win_hktl_mimikatz_command_line.yml
CommandLine IN ("*DumpCreds*", "*mimikatz*")
OR CommandLine IN ("*::aadcookie*", ...)
OR CommandLine IN ("*rpc::*", "*token::*", "*crypto::*", ...)
```

Output is structured `Field="value"` / `Field IN (...)` SPL — easy
to interpret in Python against the CSV columns. No Splunk install
required.

### Pre-flight 3: ✅ green (with build-design discovery)

POC at `/tmp/dt-poc2.py`. Test rule:
`proc_access_win_lsass_memdump.yml`. Scoped to Sysmon EID 10 events
targeting `lsass.exe` (26 events: 23 cred-dumping positives, 3 negatives).

Four iterations, all complete in <0.6 s wall-clock total:

| Iteration | Rule change | P | R | F1 |
|---|---|---|---|---|
| 1 | rule as-written | 1.000 | 0.696 | 0.821 |
| 2 | add `0x1010`, `0x1410`, `0x1000` to GrantedAccess | 0.917 | 0.957 | **0.936** |
| 3 | (2) + exclude SourceImage=powershell.exe | 0.905 | 0.826 | 0.864 |
| 4 | (2) + exclude common system source images | 0.895 | 0.739 | 0.810 |

The signal is exactly what the harness needs: precision falls as
recall rises, F1 peaks at a non-obvious tuning point, and an
over-cautious "exclude everything that looks system-y" strategy
*loses* F1 by dropping real Invoke-Mimikatz powershell.exe events.

**Two non-obvious findings that shape the build:**

1. **Logsource-aware scoping is required.** A Sigma rule declares
   `logsource: category: process_access, product: windows`. The
   evaluator must filter the corpus to *just* the matching events
   (Sysmon EID 10 for process_access) before computing P/R. Naive
   "any event in a malicious EVTX = positive" gives garbage numbers
   because (a) most events in any given malicious capture are routine
   system events, and (b) a rule that watches process_creation
   shouldn't be penalized for not matching process_access events.
   The Sigma `logsource:` taxonomy maps to specific `(Channel,
   EventID)` filters — this mapping lives in the SigmaHQ pipelines
   and the skill will need a copy.

2. **Hex value normalization is required.** `GrantedAccess=0x001fffff`
   in the corpus needs to match the rule's `0x1fffff` — substring
   matching fails on leading zeros. The Sigma spec defines value
   equality semantics; the evaluator must implement them rather
   than naïvely substring-matching the SPL output. Easiest path:
   walk the parsed pySigma AST directly instead of round-tripping
   through SPL.

Neither finding blocks the build, but both will shape Friday
night's `sigma_eval.py` module. Documented here so the next session
inherits them.

### Decision: GREEN. Proceeding with build.

All three pre-flight checks satisfy the green criteria from the
table above. The build-design findings (logsource scoping, hex
normalization) are *clarifications* of the evaluator's contract,
not blockers — they're known-unknowns going into Friday night
instead of mid-build surprises.

POCs preserved at `/tmp/dt-poc.py` (initial mimikatz attempt that
exposed the logsource-scoping issue) and `/tmp/dt-poc2.py` (the
working LSASS demonstration). Corpus at `/tmp/dt-corpora/evtx/`,
rules at `/tmp/dt-rules/sigma/`, venv at `/tmp/dt-venv/`.

