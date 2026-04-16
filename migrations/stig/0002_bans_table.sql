-- migrations/stig/0002_bans_table.sql
--
-- Surface fix discovered during the Phase C3 smoke test: the legacy
-- SQLite memory store stored banned patterns as `_global_ban` pseudo-
-- attempts (1926 of them across the migrated runs). The new attempts
-- table has a FK (run_id, item_id) -> work_items, which would reject
-- those rows. The migration filter dropped them; ralph.py's
-- end-of-run ban-persist code path would also fail on Run 4 against
-- the new FK.
--
-- Right design: bans are a separate concern from attempts. New table
-- here. PostgresMemoryStore.save_attempt detects the `_global_ban`
-- sentinel item_id and routes to this table; load_global_bans reads
-- from this table. The historical 1926 patterns are backfilled by
-- tools/migrate_sqlite_to_postgres.py rerunning under --reset.

SET search_path TO stig;

CREATE TABLE IF NOT EXISTS bans (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    pattern     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A pattern can repeat across runs; we want fast deduped reads.
CREATE INDEX IF NOT EXISTS bans_pattern_idx ON bans (pattern);
CREATE INDEX IF NOT EXISTS bans_run_idx     ON bans (run_id);
-- Within a single run, the same pattern shouldn't be stored twice.
CREATE UNIQUE INDEX IF NOT EXISTS bans_run_pattern_uidx
    ON bans (run_id, pattern);

INSERT INTO migrations_applied (name) VALUES ('0002_bans_table')
ON CONFLICT (name) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE bans TO forge_stig;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE bans_id_seq TO forge_stig;
