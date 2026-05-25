-- migrations/detection/0006_tip_followed.sql
--
-- Parity with stig.0006_tip_followed. See that file for the
-- architectural reasoning; this migration mirrors the schema
-- additions onto the Detection skill's tip_retrievals table so the
-- attribution signal lands in lockstep when Detection's next run runs.
--
-- Idempotent.

SET search_path TO detection;

ALTER TABLE tip_retrievals
    ADD COLUMN IF NOT EXISTS tip_followed_llm BOOLEAN,
    ADD COLUMN IF NOT EXISTS tip_followed_emb DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tip_followed_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN tip_retrievals.tip_followed_llm IS
  'LLM judge ruling: did the Worker''s fix in this attempt follow '
  'the advice in this retrieved tip? See stig.tip_retrievals.tip_followed_llm '
  'for full semantics and skills/detection-response/prompts/tip_follow_judge.md '
  'for the Detection-specific judge prompt.';

COMMENT ON COLUMN tip_retrievals.tip_followed_emb IS
  'Cosine similarity between tip text and the Worker''s applied '
  'fix (dnf transaction args + reasoning). See '
  'stig.tip_retrievals.tip_followed_emb for full semantics.';

COMMENT ON COLUMN tip_retrievals.tip_followed_computed_at IS
  'When the dream pass set the followed columns. See '
  'stig.tip_retrievals.tip_followed_computed_at.';

CREATE INDEX IF NOT EXISTS tip_retrievals_unscored_followed_idx
    ON tip_retrievals (run_id) WHERE tip_followed_computed_at IS NULL
                              AND outcome_value IS NOT NULL;

INSERT INTO migrations_applied (name) VALUES ('0006_tip_followed')
ON CONFLICT (name) DO NOTHING;
