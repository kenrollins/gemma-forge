"""Shared Postgres connection pool for the harness.

Phase C3 of the memory refactor (ADR-0016). Replaces the per-process
``sqlite3.connect()`` pattern that the retired ``SQLiteMemoryStore``
used. One ``psycopg_pool.ConnectionPool`` per Python process, lazy-
initialized on first use, sized for the current single-worker harness
and the upcoming clutch parallelism (default min=1, max=10).

Why a pool when the harness is largely sequential today: the dashboard
and the run analyst both want to read run history while the harness is
writing fresh attempts. The pool gives us safe concurrent reads/writes
without the SQLite WAL contortions, and it pre-buys headroom for when
the clutch starts running multiple Workers per category.

Connection target on the reference XR7620 is the ``supabase-db``
container on the ``supabase_default`` Docker network — see
``ADR-0016`` amendment 1 and the migration tools for the rationale
behind bypassing the Supavisor pooler.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def _load_dotenv_once() -> None:
    """Populate ``os.environ`` from the repo-root ``.env`` if present.

    The bootstrap scripts wrote credentials into ``.env`` at install
    time. We honor any value already exported in the environment, so
    container-level overrides win.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _conninfo(role: str) -> str:
    """Build a Postgres connection string for ``role``.

    Roles supported here are skill-scoped (``forge_stig``, ...) and
    the admin role (``forge_admin``). Each role's password lives in
    ``.env`` under ``PG_FORGE_<ROLE_UPPER>_PASSWORD``.
    """
    _load_dotenv_once()
    pw_var = f"PG_{role.upper()}_PASSWORD"
    pw = os.environ.get(pw_var)
    if not pw:
        raise RuntimeError(f"db: {pw_var} missing from environment / .env")
    host = os.environ.get("PG_HOST")
    if not host:
        raise RuntimeError("db: PG_HOST missing from environment / .env")
    port = os.environ.get("PG_PORT", "5432")
    dbname = os.environ.get("PG_DATABASE", "gemma_forge")
    return f"host={host} port={port} dbname={dbname} user={role} password={pw}"


def get_pool(role: str = "forge_stig", *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first use.

    Pool sizing defaults to (min=1, max=10): one connection idle most of
    the time, capacity for the dashboard, the harness, and a handful of
    clutch workers without contention. Override per-process via the
    ``PG_POOL_MIN`` / ``PG_POOL_MAX`` env vars before first call.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        env_min = int(os.environ.get("PG_POOL_MIN", str(min_size)))
        env_max = int(os.environ.get("PG_POOL_MAX", str(max_size)))
        conninfo = _conninfo(role)
        # search_path is a property of the role (set during bootstrap)
        # so the pool itself doesn't need ``options=-c search_path=...``.
        pool = ConnectionPool(
            conninfo=conninfo,
            min_size=env_min,
            max_size=env_max,
            kwargs={"autocommit": False},
            open=False,
        )
        pool.open(wait=True, timeout=10.0)
        _pool = pool
        logger.info(
            "db: opened Postgres pool role=%s host=%s db=%s min=%d max=%d",
            role,
            os.environ.get("PG_HOST"),
            os.environ.get("PG_DATABASE", "gemma_forge"),
            env_min,
            env_max,
        )
        return _pool


def close_pool() -> None:
    """Close the process-wide pool. Safe to call when no pool is open."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
            logger.info("db: pool closed")


def reset_pool_for_tests() -> None:
    """Drop the singleton so the next ``get_pool()`` re-reads the env.

    Used by the test fixture that creates per-test temp schemas with
    different role / search-path configurations.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
