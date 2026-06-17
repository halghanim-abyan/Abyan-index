"""
migrate_to_pg.py — one-time SQLite → Postgres migration with a verification gate.

Copies every table from the three local SQLite databases into the cloud
Postgres pointed to by DATABASE_URL (.env), preserving ids, then:
  • converts integer single-column PKs to IDENTITY and re-syncs the sequence
    so future app inserts keep auto-incrementing,
  • verifies row counts table-by-table — the migration FAILS LOUDLY on any
    mismatch.

SAFETY: reads SQLite (never mutates it — it stays your second copy). On
Postgres it DROPs+recreates each table (the target starts empty). Re-runnable.

Usage:
    python migrate_to_pg.py            # migrate + verify
    python migrate_to_pg.py --verify   # verify counts only (no copy)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, MetaData, Table, select, text, inspect, types as satypes,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION

load_dotenv()

PROJECT = Path(__file__).resolve().parent

# logical name → local SQLite file
SQLITE_DBS = {
    "inflation": PROJECT / "inflation_index.db",
    "funds":     PROJECT / "mutual_funds.db",
    "liquidity": PROJECT / "liquidity_radar.db",
}

BATCH = 1000


def _pg_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("ERROR: DATABASE_URL not set (.env). Aborting.")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True, future=True)


def _coerce_types(meta: MetaData) -> None:
    """Fix SQLite→Postgres type mapping issues before create_all:

    • NullType (SQLite columns with no declared type) → TEXT, else create fails.
    • REAL/FLOAT → DOUBLE PRECISION. SQLite REAL is 8-byte double, but it
      reflects as SQLAlchemy REAL which renders as Postgres `real` (float4,
      single precision) — that silently TRUNCATES financial values. Force
      8-byte double precision to preserve prices/NAVs exactly.
    """
    for table in meta.tables.values():
        for col in table.columns:
            if isinstance(col.type, satypes.NullType):
                col.type = satypes.Text()
            elif isinstance(col.type, satypes.Float):
                col.type = DOUBLE_PRECISION()


def _autoinc_pk(table: Table) -> str | None:
    """Return the column name if the table has a single integer PK (SQLite
    rowid-alias / AUTOINCREMENT), else None — those need IDENTITY on Postgres."""
    pk_cols = list(table.primary_key.columns)
    if len(pk_cols) != 1:
        return None
    col = pk_cols[0]
    if isinstance(col.type, (satypes.Integer, satypes.BigInteger)):
        return col.name
    return None


def migrate() -> int:
    pg = _pg_engine()
    overall_ok = True
    summary: list[tuple[str, int, int, bool]] = []

    for logical, path in SQLITE_DBS.items():
        if not path.is_file():
            print(f"[skip] {logical}: {path.name} not found")
            continue
        print(f"\n=== {logical}  ({path.name}) ===")
        src = create_engine(f"sqlite:///{path.as_posix()}", future=True)
        meta = MetaData()
        meta.reflect(bind=src)
        _coerce_types(meta)

        tables = meta.sorted_tables  # FK-dependency order for inserts

        # 1) (re)create schema on Postgres
        for t in reversed(tables):
            t.drop(bind=pg, checkfirst=True)
        meta.create_all(bind=pg)
        print(f"  created {len(tables)} tables on Postgres")

        # 2) copy data (SQLAlchemy core → preserves types + None)
        with src.connect() as s, pg.begin() as d:
            for t in tables:
                rows = s.execute(select(t)).fetchall()
                if rows:
                    payload = [dict(r._mapping) for r in rows]
                    for i in range(0, len(payload), BATCH):
                        d.execute(t.insert(), payload[i:i + BATCH])
                print(f"    copied {t.name}: {len(rows)} rows")

        # 3) reseed sequences. create_all already made integer PKs SERIAL
        #    (auto-increment), but copying explicit ids does NOT advance the
        #    sequence — so reseed it to MAX(id) to avoid future PK collisions.
        with pg.begin() as d:
            for t in tables:
                idcol = _autoinc_pk(t)
                if not idcol:
                    continue
                seq = d.execute(
                    text("SELECT pg_get_serial_sequence(:tbl, :col)"),
                    {"tbl": t.name, "col": idcol},
                ).scalar()
                if not seq:
                    continue
                d.execute(text(
                    f"SELECT setval('{seq}', "
                    f'COALESCE((SELECT MAX("{idcol}") FROM "{t.name}"), 1))'
                ))
                print(f"    reseed sequence: {t.name}.{idcol} -> {seq}")

        # 4) verify row counts
        with src.connect() as s, pg.connect() as d:
            for t in tables:
                n_src = s.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar()
                n_dst = d.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar()
                ok = (n_src == n_dst)
                overall_ok &= ok
                summary.append((t.name, n_src, n_dst, ok))

    # ── verification gate ──────────────────────────────────────────────
    print("\n" + "=" * 56)
    print(f"{'table':<32}{'sqlite':>8}{'pg':>8}{'ok':>4}")
    print("-" * 56)
    for name, a, b, ok in summary:
        print(f"{name:<32}{a:>8}{b:>8}{'✓' if ok else '✗':>4}")
    print("=" * 56)
    if overall_ok:
        print("RESULT: ✅ ALL ROW COUNTS MATCH — migration verified.")
        return 0
    print("RESULT: ❌ MISMATCH — do NOT trust the migration. Investigate.")
    return 1


def verify_only() -> int:
    """Re-check counts without copying (idempotent health check)."""
    pg = _pg_engine()
    overall_ok = True
    for logical, path in SQLITE_DBS.items():
        if not path.is_file():
            continue
        src = create_engine(f"sqlite:///{path.as_posix()}", future=True)
        names = inspect(src).get_table_names()
        with src.connect() as s, pg.connect() as d:
            for n in names:
                a = s.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar()
                try:
                    b = d.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar()
                except Exception:
                    b = -1
                ok = (a == b)
                overall_ok &= ok
                print(f"  {n:<32}{a:>8}{b:>8}{'✓' if ok else '✗':>4}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Migrate SQLite → Postgres with verification.")
    ap.add_argument("--verify", action="store_true", help="verify counts only")
    args = ap.parse_args()
    sys.exit(verify_only() if args.verify else migrate())
