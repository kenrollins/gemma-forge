-- First-boot Postgres initialization for the GemmaForge memory stack.
--
-- This script runs ONCE on an empty PGDATA directory, before the container
-- accepts connections. It creates the per-skill database and the forge role.
-- Schema DDL is applied separately by tools/migrate_sqlite_to_postgres.py
-- (see docs/drafts/memory-refactor-plan.md, Phase C) so schema evolution
-- stays under version control.
--
-- Per ADR-0016: one database per skill. The first skill is stig-rhel9.
-- Future skills add their own database via CREATE DATABASE, following
-- the same pattern.

-- The 'forge' role is the primary application user. The superuser
-- (POSTGRES_USER / POSTGRES_PASSWORD from the compose env) is reserved
-- for administrative tasks. Application code connects as 'forge'.
CREATE ROLE forge WITH LOGIN PASSWORD 'CHANGE_ME_AT_BOOTSTRAP';

-- The first skill database.
CREATE DATABASE gemma_forge_stig OWNER forge;

-- Enable extensions in the skill database. pgvector is included because
-- future work (embeddings for semantic-similarity retrieval, fine-tuning
-- dataset staging) will want it and enabling at provisioning time is
-- cheaper than a migration later.
\connect gemma_forge_stig
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Grant schema-level privileges to forge on the skill database.
-- Schema DDL (CREATE SCHEMA episodic, semantic, events, config) is
-- applied by the migration tool, not here.
GRANT ALL PRIVILEGES ON DATABASE gemma_forge_stig TO forge;
