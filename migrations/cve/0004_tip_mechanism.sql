-- migrations/cve/0004_tip_mechanism.sql
--
-- V2 post-Run-5 refinement. The Run 5 post-mortem (entry 32) found
-- tip_type alone doesn't discriminate quality — every tip in Run 5
-- was labeled recovery, but the useful ones ("X failed BECAUSE Y")
-- and the harmful ones ("X failed") looked identical to the retrieval
-- layer. Both scored sim=1.30, both landed in prompts, the Worker
-- couldn't tell them apart.
--
-- This migration adds a mechanism column for new tips. The Reflector's
-- prompt now requires a one-sentence mechanism — for strategy tips,
-- why-effective; for recovery/warning, why-it-fails. The parser drops
-- tips missing it. Backfilled V1 lessons stay mechanism=NULL (we don't
-- have enough signal to reconstruct the causal explanation post-hoc).
--
-- Column is nullable rather than NOT NULL so existing 2,973 rows can
-- stay as-is. Parser-level enforcement handles new writes; the column
-- being NULL for older rows is a read-time signal ("tip predates the
-- mechanism requirement") rather than a write-time check.
--
-- Idempotent.

SET search_path TO cve;

ALTER TABLE tips
    ADD COLUMN IF NOT EXISTS mechanism TEXT;

COMMENT ON COLUMN tips.mechanism IS
  'Causal explanation. For strategy tips: why the approach works. '
  'For recovery/warning: why the failed approach fails. NULL for '
  'pre-Run-6 backfilled tips where mechanism was not captured.';

-- An index is not justified yet — mechanism is not used in retrieval
-- scoring (Phase G composite doesn't reference it). It's read back
-- with the tip in prompts and surfaced in the Memory tab. Add an
-- index only when a query needs it.
