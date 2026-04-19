-- migrations/cve/0001_base_schema.sql
--
-- Phase B baseline schema for the `cve` skill inside the gemma_forge
-- database. Applied by tools/apply_migrations.sh against forge_admin.
-- Idempotent — every CREATE uses IF NOT EXISTS so re-runs are safe.
--
-- Mapping to the retired SQLite memory_store.py schema:
--   runs            <- runs            (timestamps now TIMESTAMPTZ; config JSONB)
--   work_items      <- work_items      (renamed columns kept for clarity)
--   attempts        <- attempts        (BIGSERIAL pk, TIMESTAMPTZ created_at)
--   lessons_current <- lessons         (materialized projection of the
--                                       Reflective tier; rebuilt by the
--                                       dream pass between runs)
--   turns           [new]              (per-agent-turn records that the
--                                       SQLite store never captured —
--                                       backfilled from JSONL by the
--                                       migration tool)
--   run_events      [new]              (full JSONL event stream as JSONB
--                                       for Run Analyst SQL queries)
--   skill_settings  [new]              (per-skill tunables; loaded from
--                                       skill.yaml at boot)
--
-- See ADR-0016 (Phase A bring-up amendments) and
-- docs/drafts/memory-refactor-plan.md Phase B.

-- Pin the search_path for this session so unqualified CREATE TABLE
-- lands in the cve schema (forge_admin has no privilege on public).
SET search_path TO cve;

-- ---------------------------------------------------------------------------
-- runs: one row per harness run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,         -- e.g. '20260414-012052'
    skill         TEXT NOT NULL DEFAULT 'cve',
    started_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ,
    config        JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary       JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- work_items: per-rule outcome rollup within a run. One row per (run, rule).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS work_items (
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    item_id       TEXT NOT NULL,            -- xccdf_org.ssgproject.content_rule_*
    title         TEXT,
    category      TEXT,
    outcome       TEXT,                     -- remediated | escalated | skip | running
    attempts      INTEGER NOT NULL DEFAULT 0,
    wall_time_s   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    PRIMARY KEY (run_id, item_id)
);

CREATE INDEX IF NOT EXISTS work_items_category_idx ON work_items(category);
CREATE INDEX IF NOT EXISTS work_items_outcome_idx  ON work_items(outcome);

-- ---------------------------------------------------------------------------
-- attempts: per-attempt history within a (run, work_item).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attempts (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    attempt_num     INTEGER NOT NULL,
    approach        TEXT,
    eval_passed     BOOLEAN NOT NULL DEFAULT FALSE,
    failure_mode    TEXT,
    reflection      TEXT,
    lesson          TEXT,
    banned_pattern  TEXT,
    wall_time_s     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, item_id) REFERENCES work_items(run_id, item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS attempts_run_item_idx ON attempts(run_id, item_id);
CREATE INDEX IF NOT EXISTS attempts_eval_idx     ON attempts(eval_passed);

-- ---------------------------------------------------------------------------
-- turns: per-agent-turn records (Architect / Worker / Auditor / Reflector).
-- New table — the SQLite store never broke this out. Backfilled from the
-- JSONL agent_response and tool_call events by the migration tool.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS turns (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    item_id            TEXT,                -- nullable: some turns are pre-selection
    attempt_num        INTEGER,             -- nullable: same reason
    agent              TEXT NOT NULL,       -- architect | worker | auditor | reflector | system | harness
    iteration          INTEGER NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL,
    elapsed_s          DOUBLE PRECISION NOT NULL,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    content            TEXT,                -- agent text response
    structured         JSONB                -- parsed verdict, tool calls, etc.
);

CREATE INDEX IF NOT EXISTS turns_run_item_idx  ON turns(run_id, item_id);
CREATE INDEX IF NOT EXISTS turns_agent_idx     ON turns(agent);
CREATE INDEX IF NOT EXISTS turns_started_idx   ON turns(started_at);

-- ---------------------------------------------------------------------------
-- lessons_current: materialized projection of the Reflective tier.
-- The dream pass writes here after each run; the harness reads from here
-- at prompt-assembly time. NOT the source of truth — Neo4j is.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lessons_current (
    id                       BIGSERIAL PRIMARY KEY,
    -- Stable identifier matching the Lesson node ID in Neo4j (Graphiti
    -- assigns these). Lets us round-trip projections without dedup pain.
    reflective_uuid          UUID,
    category                 TEXT NOT NULL,
    lesson                   TEXT NOT NULL,
    -- Frequency-driven (legacy SQLite weight, kept for continuity).
    weight                   DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    success_count            INTEGER NOT NULL DEFAULT 0,
    failure_count            INTEGER NOT NULL DEFAULT 0,
    -- Outcome-driven (NEW — set by the dream pass; this is the
    -- distinctive contribution from credit assignment).
    confidence               DOUBLE PRECISION,
    -- Diagnostics from the dream pass.
    abstraction_loss_flag    BOOLEAN NOT NULL DEFAULT FALSE,
    environment_tag          TEXT,
    -- Provenance (also expressed as edges in Neo4j).
    source_run_id            TEXT,
    source_item_id           TEXT,
    superseded_by_uuid       UUID,
    last_dream_pass_id       TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lessons_current_category_idx   ON lessons_current(category);
CREATE INDEX IF NOT EXISTS lessons_current_weight_idx     ON lessons_current(weight DESC);
CREATE INDEX IF NOT EXISTS lessons_current_confidence_idx ON lessons_current(confidence DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS lessons_current_uuid_idx       ON lessons_current(reflective_uuid);

-- ---------------------------------------------------------------------------
-- run_events: full JSONL event stream, ingested for SQL-backed analysis.
-- The Run Analyst chat interface (Phase D follow-on) will query this.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_events (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL,
    elapsed_s   DOUBLE PRECISION NOT NULL,
    event_type  TEXT NOT NULL,
    agent       TEXT,
    iteration   INTEGER,
    rule_id     TEXT,                     -- denormalized from data->>'rule_id' for index speed
    data        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS run_events_run_idx        ON run_events(run_id);
CREATE INDEX IF NOT EXISTS run_events_type_idx       ON run_events(event_type);
CREATE INDEX IF NOT EXISTS run_events_rule_idx       ON run_events(rule_id);
CREATE INDEX IF NOT EXISTS run_events_ts_idx         ON run_events(ts);
CREATE INDEX IF NOT EXISTS run_events_data_gin_idx   ON run_events USING gin (data jsonb_path_ops);

-- ---------------------------------------------------------------------------
-- skill_settings: per-skill key/value tunables.
-- Loaded from skill.yaml at boot; can be tweaked at runtime via the
-- dashboard (Phase D follow-on).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_settings (
    key          TEXT PRIMARY KEY,
    value        JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by   TEXT NOT NULL DEFAULT current_user
);

-- ---------------------------------------------------------------------------
-- migrations_applied: track which migrations have run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS migrations_applied (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO migrations_applied (name) VALUES ('0001_base_schema')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Grants: forge_<skill> got default privileges on this schema during
-- bootstrap_skill.sh, so newly-created tables here are CRUD-able by
-- the runtime role automatically. We re-issue here as a belt-and-suspenders
-- for any tables created before ALTER DEFAULT PRIVILEGES took effect.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cve TO forge_cve;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA cve TO forge_cve;
