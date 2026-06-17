"""
db.py — Unified data-access layer for Terminal v1.0.

ONE switch drives the whole project's storage:

    • DATABASE_URL unset            → local SQLite  (current behaviour, unchanged)
    • DATABASE_URL = postgresql://… → cloud Postgres (Supabase/Neon) — production

WHY
===
The dashboard ships to Streamlit Cloud (ephemeral filesystem — local .db files
would be wiped), while the scrapers keep running locally (Akamai needs a real
browser). So the durable data must live in a shared store both sides can reach:
managed Postgres. This module lets the SAME code read/write either engine with
no SQL rewrites — UPSERT (`ON CONFLICT … DO UPDATE SET x = excluded.x`) is
identical across SQLite and Postgres; only parameter style and a few PRAGMAs
differ, and those are handled here.

THREE LOGICAL DATABASES, ONE PHYSICAL STORE
===========================================
Locally there are three SQLite files; on Postgres they consolidate into one
database (table names don't collide). Callers address a logical name:

    db.get_connection("inflation")   # inflation_index.db  | pg public
    db.get_connection("funds")       # mutual_funds.db      | pg public
    db.get_connection("liquidity")   # liquidity_radar.db   | pg public

SAFETY
======
In SQLite mode `get_connection()` returns a real ``sqlite3.Connection`` with the
exact PRAGMAs used today — byte-for-byte compatible with the live system. The
Postgres path is only taken when DATABASE_URL is present.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

# ── Optional imports (must not break the live SQLite path if missing) ─────────
try:  # python-dotenv is optional; only needed when a local .env carries the URL
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv not installed / no .env
    pass

_PROJECT_DIR = Path(__file__).resolve().parent

# Logical DB name → local SQLite file (used only in SQLite mode).
_SQLITE_PATHS: dict[str, Path] = {
    "inflation": _PROJECT_DIR / "inflation_index.db",
    "funds":     _PROJECT_DIR / "mutual_funds.db",
    "liquidity": _PROJECT_DIR / "liquidity_radar.db",
}
DEFAULT_DB = "inflation"


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE_URL resolution
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_database_url() -> Optional[str]:
    """Find the Postgres URL, or None to fall back to local SQLite.

    Resolution order:
      1. Environment variable DATABASE_URL  (local .env for the scrapers)
      2. st.secrets["DATABASE_URL"]         (Streamlit Cloud dashboard)
    Returns None when neither is set → SQLite mode.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url.strip()

    # Streamlit Cloud injects secrets; accessing st.secrets without a secrets
    # file raises, so guard defensively and never let it break SQLite mode.
    try:
        import streamlit as st  # noqa: WPS433
        if "DATABASE_URL" in st.secrets:
            return str(st.secrets["DATABASE_URL"]).strip()
    except Exception:  # pragma: no cover - no streamlit / no secrets
        pass
    return None


def _normalize_pg_url(url: str) -> str:
    """Normalise a Postgres URL to the SQLAlchemy + psycopg2 driver form."""
    # Supabase/Neon often hand out `postgres://…`; SQLAlchemy wants `postgresql://`.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Pin the psycopg2 driver explicitly for predictability.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL: Optional[str] = _resolve_database_url()
IS_POSTGRES: bool = bool(DATABASE_URL)


# ══════════════════════════════════════════════════════════════════════════════
#  Engine factory (SQLAlchemy) — cached per logical DB
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=None)
def get_engine(db: str = DEFAULT_DB):
    """Return a cached SQLAlchemy Engine for the logical database `db`.

    Postgres: a single shared engine (all logical names map to it) with
    ``pool_pre_ping`` (Supabase/Neon drop idle connections) and a short
    ``pool_recycle``. SQLite: one engine per local file.
    """
    from sqlalchemy import create_engine  # lazy import — keeps module light

    if IS_POSTGRES:
        return create_engine(
            _normalize_pg_url(DATABASE_URL),
            pool_pre_ping=True,
            pool_recycle=300,
            future=True,
        )

    path = _SQLITE_PATHS.get(db)
    if path is None:
        raise ValueError(f"Unknown logical database: {db!r} (known: {list(_SQLITE_PATHS)})")
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


# ══════════════════════════════════════════════════════════════════════════════
#  Connection (write/DDL path) — preserves the legacy `conn.execute(...)` idiom
# ══════════════════════════════════════════════════════════════════════════════

_PLACEHOLDER_RE = re.compile(r"\?")


def _to_pg_paramstyle(sql: str) -> str:
    """Translate SQLite `?` placeholders to psycopg2 `%s`, escaping literal `%`.

    psycopg2 treats `%` specially when parameters are supplied, so any literal
    `%` (e.g. in a LIKE pattern) must be doubled. Applied only in Postgres mode.
    """
    sql = sql.replace("%", "%%")
    return _PLACEHOLDER_RE.sub("%s", sql)


class _PgConnection:
    """Thin adapter so psycopg2 connections speak the sqlite3 idiom.

    Mirrors the subset of ``sqlite3.Connection`` the codebase relies on:
    connection-level ``execute()`` returning a cursor, plus ``cursor()``,
    ``commit()``, ``rollback()``, ``close()``, and context-manager support.
    Placeholders are translated `?` → `%s` transparently.
    """

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Iterable[Any] = ()):  # noqa: A003
        cur = self._raw.cursor()
        cur.execute(_to_pg_paramstyle(sql), tuple(params) if params else None)
        return cur

    def executemany(self, sql: str, seq_of_params):
        cur = self._raw.cursor()
        cur.executemany(_to_pg_paramstyle(sql), list(seq_of_params))
        return cur

    def cursor(self):
        return self._raw.cursor()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def get_connection(db: str = DEFAULT_DB):
    """Return a write/DDL connection for the logical database `db`.

    • SQLite mode → a real ``sqlite3.Connection`` with WAL + foreign keys,
      identical to the legacy ``db_setup.get_connection`` so nothing changes
      for the live local pipeline.
    • Postgres mode → a ``_PgConnection`` adapter exposing the same idiom.
    """
    if IS_POSTGRES:
        return _PgConnection(get_engine(db).raw_connection())

    path = _SQLITE_PATHS.get(db)
    if path is None:
        raise ValueError(f"Unknown logical database: {db!r}")
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ══════════════════════════════════════════════════════════════════════════════
#  Read path — pandas DataFrame, engine-agnostic
# ══════════════════════════════════════════════════════════════════════════════

def read_sql(
    sql: str,
    params: Any = None,
    db: str = DEFAULT_DB,
    parse_dates: Any = None,
) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame, working on either engine.

    Uses a SQLAlchemy Connection (natively supported by pandas → no warning),
    with `?` placeholders translated to `%s` in Postgres mode. pandas routes
    positional params through the driver's own paramstyle (qmark for SQLite,
    pyformat for psycopg2). `params` may be a sequence (positional) or None.
    """
    query = _to_pg_paramstyle(sql) if IS_POSTGRES else sql
    with get_engine(db).connect() as conn:
        return pd.read_sql_query(query, conn, params=params, parse_dates=parse_dates)


# ══════════════════════════════════════════════════════════════════════════════
#  Cache-invalidation signature (replaces os.path.getmtime for Streamlit cache)
# ══════════════════════════════════════════════════════════════════════════════

def db_signature(db: str = DEFAULT_DB, table: str = "daily_index", date_col: str = "date") -> str:
    """Cheap content signature for `@st.cache_data` invalidation.

    ``os.path.getmtime`` is meaningless for a remote Postgres, so we derive a
    signature from the data itself: ``"<row_count>:<max_date>"``. Pass this as
    the cache-key argument the way `db_mtime` is passed today. Returns "0" if
    the table is unavailable (empty/uninitialised DB) so the UI degrades safely.
    """
    try:
        df = read_sql(
            f"SELECT COUNT(*) AS n, MAX({date_col}) AS mx FROM {table}",
            db=db,
        )
        if df.empty:
            return "0"
        n = df.iloc[0]["n"]
        mx = df.iloc[0]["mx"]
        return f"{int(n) if pd.notna(n) else 0}:{mx}"
    except Exception:
        return "0"


def ping(db: str = DEFAULT_DB) -> bool:
    """Lightweight availability check (replaces os.path.isfile for Postgres)."""
    try:
        raw = get_engine(db).raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            return True
        finally:
            raw.close()
    except Exception:
        return False


def backend() -> str:
    """Human-readable backend label for logs/diagnostics."""
    return "postgres" if IS_POSTGRES else "sqlite"


# Guard against accidental concurrent engine creation under Streamlit threads.
_engine_lock = threading.Lock()
