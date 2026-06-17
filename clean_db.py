"""
clean_db.py — Remove historical NAV records for funds no longer tracked.

Deletes all rows from nav_history whose fund_name does NOT match one of
the current TARGET_FUNDS in funds_db.py.

Usage:
    python clean_db.py              # dry-run (preview what would be deleted)
    python clean_db.py --confirm    # actually delete the rows
"""

import os
import sqlite3
import sys

from funds_db import DB_PATH, TARGET_FUNDS, get_connection

# The fund names we want to KEEP
KEEP_NAMES = {f["name"] for f in TARGET_FUNDS}


def main():
    confirm = "--confirm" in sys.argv

    if not os.path.isfile(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = get_connection(DB_PATH)
    cursor = conn.cursor()

    # ── Find all fund names currently in the database ────────────────
    cursor.execute("""
        SELECT fund_name, COUNT(*) AS rows, MIN(date), MAX(date)
        FROM nav_history
        GROUP BY fund_name
        ORDER BY fund_name
    """)
    all_funds = cursor.fetchall()

    if not all_funds:
        print("Database is empty — nothing to clean.")
        conn.close()
        return

    # ── Categorize: keep vs remove ───────────────────────────────────
    print(f"{'=' * 65}")
    print(f"  Database Cleanup — mutual_funds.db")
    print(f"  Target funds (KEEP): {', '.join(sorted(KEEP_NAMES))}")
    print(f"{'=' * 65}")
    print()

    keep_rows = 0
    remove_rows = 0
    remove_funds = []

    for fund_name, count, min_date, max_date in all_funds:
        if fund_name in KEEP_NAMES:
            status = "KEEP"
            keep_rows += count
        else:
            status = "DELETE"
            remove_rows += count
            remove_funds.append(fund_name)

        marker = "[+]" if status == "KEEP" else "[x]"
        print(f"  {marker} {status:<7} {fund_name:<40} {count:>7,} rows  ({min_date} to {max_date})")

    print()
    print(f"  Rows to KEEP  : {keep_rows:>7,}")
    print(f"  Rows to DELETE : {remove_rows:>7,}")
    print()

    if not remove_funds:
        print("Nothing to delete — all funds in the database are current targets.")
        conn.close()
        return

    if not confirm:
        print("  DRY RUN — no rows deleted.")
        print(f"  Run with --confirm to delete {remove_rows:,} rows from {len(remove_funds)} fund(s).")
        conn.close()
        return

    # ── Delete ───────────────────────────────────────────────────────
    placeholders = ",".join("?" for _ in remove_funds)
    cursor.execute(
        f"DELETE FROM nav_history WHERE fund_name IN ({placeholders})",
        remove_funds,
    )
    deleted = cursor.rowcount
    conn.commit()

    # ── VACUUM to reclaim disk space ─────────────────────────────────
    conn.execute("VACUUM")
    conn.close()

    print(f"  DELETED {deleted:,} rows from {len(remove_funds)} fund(s).")
    print(f"  Database vacuumed.")

    # ── Post-cleanup summary ─────────────────────────────────────────
    conn = get_connection(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM nav_history")
    remaining = cursor.fetchone()[0]
    db_size = os.path.getsize(DB_PATH)
    conn.close()

    print()
    print(f"  Remaining rows : {remaining:,}")
    print(f"  Database size  : {db_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
