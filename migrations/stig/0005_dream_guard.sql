-- migrations/stig/0005_dream_guard.sql
--
-- Dream pass idempotency guard. V1's dream pass applies
-- `new_confidence = old + signal × 0.3` to every lesson in each
-- category. That formula is NOT idempotent — run it twice on the
-- same run's data and confidences drift 2×. Ten times and they
-- saturate.
--
-- Until now the dream pass was only triggered manually (infrequently
-- enough that nobody hit the bug). Run 6 auto-triggers at run-end
-- via ralph.py's finally-block, so the guard becomes load-bearing.
--
-- This column lets run_dream_pass check "has this run already been
-- dreamed?" and no-op if so, unless explicitly forced (--force flag
-- on the CLI for policy-change backfills).
--
-- Idempotent.

SET search_path TO stig;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS dreamed_at TIMESTAMPTZ;

COMMENT ON COLUMN runs.dreamed_at IS
  'Timestamp when the dream pass last ran against this run. NULL = '
  'not yet dreamed. Dream pass sets this after successful completion '
  'so repeat calls no-op unless --force is passed. See '
  'gemma_forge/dream/pass_.py.';
