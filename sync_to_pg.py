"""
sync_to_pg.py — mirror local SQLite → cloud Postgres (data only).

Run this AFTER the daily scrapers/calculator have written to SQLite (e.g. a
scheduled task at 15:30, after the 15:00 job). It makes Postgres an exact
copy of SQLite so the Streamlit Cloud dashboard always reflects the latest
local data.

SAFETY (this is the whole point):
  • SQLite is READ-ONLY here — it is never modified. It remains the
    source of truth and your permanent second copy.
  • Each database is synced inside ONE Postgres transaction: all tables are
    cleared and refilled atomically, so a dashboard reader never sees an
    empty/partial table (MVCC shows the old snapshot until commit).
  • BEST-EFFORT: any Postgres error is caught and logged; it NEVER raises
    into the caller, so it cannot affect the scrapers or your data.
  • The live data-collection code is NOT touched at all.

Usage:
    python sync_to_pg.py            # sync all three databases
    python sync_to_pg.py --verify   # compare row counts only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, MetaData, select, text, types as satypes,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger("sync_to_pg")

PROJECT = Path(__file__).resolve().parent
SQLITE_DBS = {
    "inflation": PROJECT / "inflation_index.db",
    "funds":     PROJECT / "mutual_funds.db",
    "liquidity": PROJECT / "liquidity_radar.db",
}
BATCH = 1000


def _pg_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL not set (.env) — cannot sync. SQLite is untouched.")
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True, future=True)


def _coerce_types(meta: MetaData) -> None:
    """Match migrate_to_pg: NullType→TEXT, and REAL/FLOAT→DOUBLE PRECISION so
    any self-healed (newly created) table preserves financial precision."""
    for table in meta.tables.values():
        for col in table.columns:
            if isinstance(col.type, satypes.NullType):
                col.type = satypes.Text()
            elif isinstance(col.type, satypes.Float):
                col.type = DOUBLE_PRECISION()


def sync() -> int:
    pg = _pg_engine()
    if pg is None:
        return 1

    any_fail = False
    for logical, path in SQLITE_DBS.items():
        if not path.is_file():
            log.warning("%s: %s not found — skipped", logical, path.name)
            continue
        try:
            src = create_engine(f"sqlite:///{path.as_posix()}", future=True)
            meta = MetaData()
            meta.reflect(bind=src)
            _coerce_types(meta)
            tables = meta.sorted_tables  # FK order

            # Self-heal: create any table that exists in SQLite but not yet in
            # Postgres (the schema occasionally grows, e.g. new real-estate
            # tables). Existing tables are left as-is.
            meta.create_all(bind=pg, checkfirst=True)

            # Read the full snapshot from SQLite FIRST (read-only).
            snapshot: dict[str, list[dict]] = {}
            with src.connect() as s:
                for t in tables:
                    rows = s.execute(select(t)).fetchall()
                    snapshot[t.name] = [dict(r._mapping) for r in rows]

            # Atomically refresh Postgres: clear (children first) + refill.
            with pg.begin() as d:
                for t in reversed(tables):
                    d.execute(t.delete())
                for t in tables:
                    payload = snapshot[t.name]
                    for i in range(0, len(payload), BATCH):
                        d.execute(t.insert(), payload[i:i + BATCH])
            total = sum(len(v) for v in snapshot.values())
            log.info("%s: synced %d tables, %d rows -> Postgres", logical, len(tables), total)
        except Exception as exc:  # never let a PG problem touch SQLite/pipeline
            any_fail = True
            log.error("%s: sync FAILED (SQLite untouched, pipeline safe): %s",
                      logical, str(exc)[:200])

    if any_fail:
        log.warning("Sync completed WITH ERRORS — Postgres may be stale; SQLite is intact.")
        return 1
    log.info("Sync OK — Postgres now mirrors SQLite.")
    return 0


def verify() -> int:
    pg = _pg_engine()
    if pg is None:
        return 1
    from sqlalchemy import inspect
    ok = True
    for logical, path in SQLITE_DBS.items():
        if not path.is_file():
            continue
        src = create_engine(f"sqlite:///{path.as_posix()}", future=True)
        with src.connect() as s, pg.connect() as d:
            for n in inspect(src).get_table_names():
                a = s.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar()
                try:
                    b = d.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar()
                except Exception:
                    b = -1
                same = (a == b)
                ok &= same
                print(f"  {n:<32}{a:>8}{b:>8}  {'OK' if same else 'DIFF'}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mirror SQLite -> Postgres (data only).")
    ap.add_argument("--verify", action="store_true", help="compare counts only")
    args = ap.parse_args()
    sys.exit(verify() if args.verify else sync())
