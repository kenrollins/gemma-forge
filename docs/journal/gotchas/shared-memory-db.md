---
id: gotcha-shared-memory-db
type: gotcha
title: "Gotcha: Shared memory DB leaks lessons across skills"
date: 2026-04-13
tags: [L4-orchestration, memory, cross-run-learning]
related:
  - journey/22-context-graphs-and-the-memory-question
  - journey/23-first-complete-run
one_line: "The SQLite memory store defaulted to a single shared database. Lessons from one skill would leak into another skill's prompts — a kernel sysctl lesson from STIG appearing in a CVE triage Worker's context."
---

# Gotcha: Shared memory DB leaks lessons across skills

## Symptom

All cross-run lessons, banned patterns, and category stats are
shared across skills. A lesson learned from STIG remediation
("fix the RPM DB before retrying audit rules") would be injected
into the context of a completely unrelated skill where it's noise.

## Root cause

`SQLiteMemoryStore` defaulted to a single hardcoded path:
`memory/gemma_forge.db`. The `runs` table records which skill
produced each run, but `load_lessons`, `load_global_bans`, and
`query_prior_attempts` all query across skills with no filter.

## Fix

Per-skill database path: `memory/{skill_name}.db`.

```python
# ralph.py — before
mem_store = SQLiteMemoryStore()

# ralph.py — after
skill_db_name = (skill_name or "default").replace("/", "-")
mem_store = SQLiteMemoryStore(db_path=f"memory/{skill_db_name}.db")
```

Each skill accumulates its own cross-run knowledge in isolation.
No query-time filtering needed — the separation is at the storage
level.

## Migration

After any run completes on the old shared path, rename the DB
before the next run:

```bash
mv memory/gemma_forge.db memory/stig-rhel9.db
```

The new code picks it up at the per-skill path with full history.

## Why per-skill DB instead of query-time filtering

Query-time filtering (adding `WHERE skill = ?` to every query)
would work but adds complexity to every read path and risks
subtle bugs if a new query forgets the filter. Per-skill files
are simpler, match the folder-per-skill design pattern, and make
it obvious from the filesystem which skills have accumulated
knowledge.

## Environment

- gemma_forge/harness/memory_store.py — SQLiteMemoryStore
- gemma_forge/harness/ralph.py — mem_store construction
