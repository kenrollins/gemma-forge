-- migrations/stig/0006_tip_followed.sql
--
-- Per-retrieval causal attribution signal — the missing column that
-- distinguishes "tip was present during success" from "tip's advice
-- was actually followed by the Worker." See journey/38.5 for the
-- empirical witness (cryptography regression) and architecture/02
-- for the project-agnostic pattern.
--
-- Outcome (value, confidence) records WHAT happened. These three
-- columns record WHY — specifically, whether the action that
-- followed the retrieval reflected the tip's content.
--
-- Both attribution signals are populated by the dream pass at
-- end-of-run, NOT at retrieval time (the LLM judge would otherwise
-- add per-call latency to every Ralph turn — moved to the cold
-- path). They remain NULL on pre-fix retrievals; the retrieval
-- ranker and dream-pass credit-assignment must handle that gracefully.
--
-- Idempotent.

SET search_path TO stig;

ALTER TABLE tip_retrievals
    ADD COLUMN IF NOT EXISTS tip_followed_llm BOOLEAN,
    ADD COLUMN IF NOT EXISTS tip_followed_emb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tip_followed_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN tip_retrievals.tip_followed_llm IS
  'LLM judge ruling: did the Worker''s fix_script in this attempt '
  'follow the advice in this retrieved tip? True/False/NULL. Populated '
  'by the dream pass via a low-temperature judge call. See '
  'gemma_forge/dream/pass_.py and skills/<skill>/prompts/tip_follow_judge.md.';

COMMENT ON COLUMN tip_retrievals.tip_followed_emb IS
  'Cosine similarity between tip text and the Worker''s fix_script, '
  'encoded with sentence-transformers/all-MiniLM-L6-v2. Float in [-1, 1]; '
  '>0.5 typically indicates semantic overlap. Populated by the dream pass '
  'as the deterministic complement to tip_followed_llm.';

COMMENT ON COLUMN tip_retrievals.tip_followed_computed_at IS
  'Timestamp when the dream pass set tip_followed_llm/emb. NULL '
  'until the run containing this retrieval has been dreamed.';

-- Allow the dream pass to find unscored retrievals quickly.
CREATE INDEX IF NOT EXISTS tip_retrievals_unscored_followed_idx
    ON tip_retrievals (run_id) WHERE tip_followed_computed_at IS NULL
                              AND outcome_value IS NOT NULL;

INSERT INTO migrations_applied (name) VALUES ('0006_tip_followed')
ON CONFLICT (name) DO NOTHING;
