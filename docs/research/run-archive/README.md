# Run archive

Runs whose JSONL is archived under version control because their
outcomes are referenced in the journal or research documents. The
default `runs/` directory is gitignored (per `.gitignore`), so
archive copies live here when a run's data is load-bearing for a
cited analysis.

## Inventory

| File | Run context | Size | Cited in |
|---|---|---|---|
| `run-6-20260418-173522.jsonl` | STIG Run 6 — first ordering-constraint run, ~19h, 153/247 remediated (61.9%), 781 tips with 100% mechanism acceptance | ~30MB | [journey/34](../../journal/journey/34-run-6-ordering-works-runtime-doesnt.md), [cve-agent-landscape-2026-04.md](../cve-agent-landscape-2026-04.md) |

## Policy

Add a run here when:
- A journal entry post-mortems it with specific numbers that would
  be re-derivable only by re-reading the JSONL.
- It's a baseline for a cross-run comparison (Runs N-1, N, N+1).
- The host it was produced on is at risk of being reset / rebuilt.

Do NOT add:
- Every run. Most runs are superseded by the next one.
- Smoke runs. Those are by-construction incomplete.
- Runs whose data duplicates a tracked summary (e.g., if the journal
  entry captures all the numbers, the raw JSONL may not add value).

## Compression

30MB is tolerable in-tree. If this directory grows past ~200MB, gzip
the older runs (`jq -c < in.jsonl | gzip > out.jsonl.gz`) and update
this README. Do not use git LFS — binary blobs are a pattern to avoid
per the project convention.
