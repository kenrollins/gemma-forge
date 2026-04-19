-- migrations/cve/0003_tips_schema.sql
--
-- Phase E2 of the V2 memory architecture (see docs/drafts/v2-architecture-plan.md
-- §2.1 and §2.5). Adds the structured-tip storage that replaces the
-- free-text-lesson + scalar-confidence framing of V1.
--
-- Two tables:
--   tips             — structured memory units (text + trigger conditions
--                      + application context + embedding + provenance)
--   tip_retrievals   — per-(prompt, tip) record so per-(tip, rule) hit
--                      tracking and history-based eviction (Xu et al.
--                      arxiv 2505.16067) can run against real data.
--
-- The legacy lessons_current table stays for one transition run as a
-- fallback retrieval path; it is removed after Run 5 validates V2.
--
-- Idempotent — every CREATE uses IF NOT EXISTS.

SET search_path TO cve;

-- ---------------------------------------------------------------------------
-- tips: structured memory unit. Replaces the free-text-only lessons_current
-- as the prompt-assembly source once the similarity retrieval (Phase G)
-- ships. Until then this table is written by the new Reflector path
-- (Phase F) but not yet read by the harness.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tips (
    id                            BIGSERIAL PRIMARY KEY,
    reflective_uuid               UUID,                   -- Neo4j Tip node ID

    -- Core content
    text                          TEXT NOT NULL,
    tip_type                      TEXT NOT NULL DEFAULT 'recovery',
                                                          -- strategy | recovery | optimization | warning
    trigger_conditions            TEXT[],                 -- e.g. {'audit rule modification','augenrules present'}
    application_context           TEXT[],                 -- e.g. {'audit','audit_rules_*'}
    embedding                     public.vector(768),     -- pgvector cosine similarity (type lives in public)

    -- Provenance: links back to the attempt that produced this tip
    source_attempt_id             BIGINT REFERENCES attempts(id) ON DELETE SET NULL,
    source_run_id                 TEXT REFERENCES runs(id),
    source_rule_id                TEXT,
    outcome_at_source_value       DOUBLE PRECISION,       -- OutcomeSignal.value at source attempt
    outcome_at_source_confidence  DOUBLE PRECISION,       -- OutcomeSignal.confidence at source attempt

    -- Bi-temporal supersession (Phase H eviction marks tips here rather than DELETE)
    retired_at                    TIMESTAMPTZ,
    retired_reason                TEXT,
    superseded_by_id              BIGINT REFERENCES tips(id),

    -- Bookkeeping
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment_tag               TEXT
);

CREATE INDEX IF NOT EXISTS tips_application_context_gin
    ON tips USING gin (application_context);
CREATE INDEX IF NOT EXISTS tips_trigger_conditions_gin
    ON tips USING gin (trigger_conditions);
CREATE INDEX IF NOT EXISTS tips_active
    ON tips (retired_at) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS tips_source_rule
    ON tips (source_rule_id);
CREATE INDEX IF NOT EXISTS tips_source_run
    ON tips (source_run_id);

-- ivfflat vector index. Built lazily — pgvector recommends populating the
-- table first then running CREATE INDEX (or the index uses lists=1 default
-- which degrades on growth). For the empty-table case below this is a no-op
-- index that will be efficient at our 270-rule × ~5 tips/rule scale either
-- way; if the table grows materially we can REINDEX with a tuned lists value.
CREATE INDEX IF NOT EXISTS tips_embedding_ivfflat
    ON tips USING ivfflat (embedding public.vector_cosine_ops) WITH (lists = 32);

-- ---------------------------------------------------------------------------
-- tip_retrievals: every time a tip lands in a Worker prompt, record it
-- with rank/similarity/outcome. This is the data structure that makes
-- per-(tip, rule) hit-rate computation a single SQL query and that
-- closes the auditability gap Diagnostic 1 had to reconstruct by hand
-- (see docs/drafts/run4-architectural-analysis.md "Update 2026-04-16").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tip_retrievals (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    attempt_id          BIGINT REFERENCES attempts(id) ON DELETE SET NULL,
    tip_id              BIGINT NOT NULL REFERENCES tips(id) ON DELETE CASCADE,
    rule_id             TEXT,                          -- denormalized for fast group-by
    rank                INTEGER,                       -- rank within the assembled prompt
    similarity_score    DOUBLE PRECISION,              -- whatever ranking signal drove inclusion
    outcome_value       DOUBLE PRECISION,              -- OutcomeSignal.value, populated when attempt completes
    outcome_confidence  DOUBLE PRECISION,              -- OutcomeSignal.confidence
    retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tip_retrievals_tip_idx
    ON tip_retrievals (tip_id);
CREATE INDEX IF NOT EXISTS tip_retrievals_rule_idx
    ON tip_retrievals (rule_id);
CREATE INDEX IF NOT EXISTS tip_retrievals_run_attempt_idx
    ON tip_retrievals (run_id, attempt_id);
-- Pending-outcome scan: rows whose outcome columns are still NULL because
-- the attempt hasn't been evaluated yet. The eviction sweep (Phase H)
-- ignores these.
CREATE INDEX IF NOT EXISTS tip_retrievals_pending_idx
    ON tip_retrievals (attempt_id) WHERE outcome_value IS NULL;

INSERT INTO migrations_applied (name) VALUES ('0003_tips_schema')
ON CONFLICT (name) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tips TO forge_cve;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tip_retrievals TO forge_cve;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE tips_id_seq TO forge_cve;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE tip_retrievals_id_seq TO forge_cve;
