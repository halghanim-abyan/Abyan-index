"""
repair_index.py — Rebuild daily_index with the quality-aware CPI rules.

Dry-run mode copies the database to a temporary file first, so the source
database is not mutated while migrations/calculations are tested.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pandas as pd

from calculator import (
    INDEX_SMOOTH_WINDOW,
    MIN_DAILY_ITEM_COVERAGE,
    _compute_index_for_date,
    _load_history,
    _prepare_enriched_history,
    _rebase_index_series,
    _smooth_index_series,
    _upsert_index,
)
from db_setup import DB_PATH, create_tables, get_connection, migrate_schema


def _coverage_for_date(enriched: pd.DataFrame, run_date: str) -> tuple[int, int, float]:
    day = enriched[enriched["date"] == run_date]
    total_items = int(enriched["item_id"].nunique())
    if day.empty or total_items <= 0:
        return 0, total_items, 0.0
    pr_col = "price_relative_smooth" if "price_relative_smooth" in day.columns else "price_relative"
    active = day.dropna(subset=[pr_col, "base_price"])
    active_items = int(active["item_id"].nunique())
    return active_items, total_items, active_items / total_items


def _rebuild(db_path: str, apply: bool) -> dict[str, int]:
    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        raw = _load_history(conn)
        if raw.empty:
            return {"dates": 0, "computed": 0, "refused": 0, "weak": 0}

        enriched = _prepare_enriched_history(raw)
        dates = sorted(enriched["date"].unique().tolist())
        raw_index: dict[str, float] = {}
        weak = 0

        for run_date in dates:
            _active, _total, coverage = _coverage_for_date(enriched, run_date)
            if coverage < MIN_DAILY_ITEM_COVERAGE:
                weak += 1
            value = _compute_index_for_date(enriched, run_date)
            if value is not None:
                raw_index[run_date] = value

        smoothed = _smooth_index_series(raw_index, window=INDEX_SMOOTH_WINDOW)
        rebased = _rebase_index_series(smoothed)
        if apply:
            _backup_daily_index(conn)
            conn.execute("DELETE FROM daily_index")
            for run_date in dates:
                value = rebased.get(run_date)
                if value is not None:
                    _upsert_index(conn, run_date, value)
            conn.commit()

        return {
            "dates": len(dates),
            "computed": len(rebased),
            "refused": len(dates) - len(rebased),
            "weak": weak,
        }
    finally:
        conn.close()


def _backup_daily_index(conn: sqlite3.Connection) -> None:
    """Keep an audit copy of replaced index rows before repair applies."""
    backup_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_index_repair_backup (
            backup_run TEXT NOT NULL,
            date TEXT NOT NULL,
            index_value REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_index_repair_backup (backup_run, date, index_value)
        SELECT ?, date, index_value FROM daily_index
        """,
        (backup_run,),
    )
    conn.commit()


def _backup_database(src_path: str, dst_path: str) -> None:
    """Copy SQLite safely, including any active WAL contents."""
    with sqlite3.connect(src_path) as src, sqlite3.connect(dst_path) as dst:
        src.backup(dst)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild daily_index using quality-aware inflation rules.",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--apply", action="store_true", help="Persist rebuilt daily_index")
    parser.add_argument("--dry-run", action="store_true", help="Preview without touching source DB")
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        raise SystemExit(f"Database not found: {args.db}")

    apply = bool(args.apply)
    if args.dry_run:
        apply = False

    if apply:
        report = _rebuild(args.db, apply=True)
        mode = "APPLY"
    else:
        with tempfile.NamedTemporaryFile(prefix="repair_index_", suffix=".db", delete=False) as tmp:
            temp_db = tmp.name
        try:
            _backup_database(args.db, temp_db)
            report = _rebuild(temp_db, apply=False)
        finally:
            for path in (temp_db, f"{temp_db}-wal", f"{temp_db}-shm"):
                try:
                    os.remove(path)
                except OSError:
                    pass
        mode = "DRY RUN"

    print("=" * 62)
    print(f"  Inflation Index Repair — {mode}")
    print("=" * 62)
    print(f"  Source DB       : {args.db}")
    print(f"  Dates scanned   : {report['dates']}")
    print(f"  Dates computed  : {report['computed']}")
    print(f"  Dates refused   : {report['refused']}")
    print(f"  Weak coverage   : {report['weak']} (< {MIN_DAILY_ITEM_COVERAGE:.0%})")
    print("=" * 62)


if __name__ == "__main__":
    main()
