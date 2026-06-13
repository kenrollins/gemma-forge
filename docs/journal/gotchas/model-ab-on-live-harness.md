---
id: gotcha-model-ab-on-live-harness
type: gotcha
title: "Gotcha: running a clean model A/B against production memory without scarring it"
date: 2026-06-13
tags: [harness, measurement, memory, methodology, discovery]
related:
  - journey/42-12b-vs-31b-straight-up
  - journey/43-memory-isnt-reasoning
  - adr/0016-graphiti-neo4j-postgres-memory-stack
one_line: "Five traps that will silently corrupt a model A/B run on the live harness: the hard-coded model banner, contaminated cross-condition memory, schema-drop killing role grants, the run-file race, and a babysat shared-service teardown. Each has a cheap, safe fix."
---

# Gotcha: running a clean model A/B against production memory without scarring it

Comparing two models (12B vs 31B, [journey/42](../journey/42-12b-vs-31b-straight-up.md),
[journey/43](../journey/43-memory-isnt-reasoning.md)) on the production harness
looks like "swap the config and run twice." It isn't. Five ways it silently
goes wrong, and the fixes:

## 1. The model banner lies — verify the actual backend

`ralph.py` logs `Model: Gemma 4 31B bf16 ... TP=4` as a **hard-coded string**.
It does not reflect `config/models.yaml`. Run the 12B and the banner still says
31B. You could analyse an entire run convinced it was a different model than it
was. **Always confirm from the data, not the log:**

```bash
jq -r 'select(.event_type=="agent_response")|.data.model' runs/run-*.jsonl | sort -u
# -> /weights/gemma-4-12B-it
```

## 2. The first condition contaminates the second through shared memory

The harness is *warm* — it hydrates tips/lessons/bans from prior runs, and at
the end it runs a dream-pass that **writes new ones plus reshuffles existing tip
weights.** So condition A deposits rule-specific learnings (e.g. 210 bans about
the exact rules) that condition B then inherits. If both work the same rules,
B gets A's homework and the comparison is junk. **Rewind to the exact
pre-experiment state between conditions** (and before the matched run), verified
against a backup:

```
runs=52  lessons=6126  tips=8136   # must match the backup, row-for-row
```

## 3. Rewind surgically — `DROP SCHEMA` strips the role's grants

Tempting fix: `DROP SCHEMA stig CASCADE` + restore from `pg_dump`. Don't. A
plain `--no-owner --no-privileges` dump doesn't carry the `forge_stig` role's
`USAGE`/table grants, so the live harness loses access to its own memory and
you're now debugging permissions at midnight. Every memory table carries
`run_id` / `source_run_id` / `created_at`, so **scope deletes by run-id and a
timestamp cutoff inside a transaction** — structure and grants untouched:

```sql
BEGIN;
DELETE FROM stig.bans WHERE created_at > :cut;
DELETE FROM stig.tips WHERE source_run_id IN (SELECT id FROM exp_runs) OR created_at > :cut;
UPDATE stig.tips SET retired_at = NULL WHERE retired_at > :cut;  -- undo the dream-pass eviction
-- ...run_events, lessons, attempts, runs...
COMMIT;
```

The JSONL run log is **append-only and per-run** — a new run never touches a
prior file. That's the leverage: *all* analysis comes from the immutable JSONL,
so the Postgres rewind is completely decoupled from your data. Rewind whenever;
the evidence is safe on disk.

## 4. The run-file race — read it from the log, not `ls -t`

A detached run doesn't create its JSONL instantly (skill load + SSH + XCCDF
prefetch first, ~15s). `RUNF=$(ls -1t runs/*.jsonl | head -1)` grabbed 8s after
launch returns the *previous* run's file. A watcher pointed at it then reports
the wrong run. **Take the path from the harness's own line:**

```bash
RUNF=$(grep -oE 'runs/run-[0-9-]+\.jsonl' run.log | head -1)
```

## 5. A long run on a shared service needs an auto-restore, not a babysitter

The 12B needs the GPUs the shared 31B (`:8050`, also serving
quantum-ai-orchestrator) holds, so the full run meant an 8h+ teardown. Don't
rely on being awake when it ends. A tiny watcher tied to the run's PID tears
down the experiment engine and restarts the shared service the instant the run
exits — the outage is the run length, not the run length plus however long
until a human notices. (The matched 31B run avoided this entirely: it *is* the
production service, so it ran against the live `:8050` with no teardown.)

## The principle

You can run a real experiment against production infrastructure without
scarring it — but only if you **back up first, verify the backend from data,
rewind on purpose, and let immutable per-run logs carry the analysis.** The
reassuring artifact at the end isn't a benchmark; it's `runs=52 lessons=6126
tips=8136` matching the backup after two model swaps and a shared-service
teardown.
