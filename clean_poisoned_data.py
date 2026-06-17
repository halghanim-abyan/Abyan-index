"""
clean_poisoned_data.py — Delete rows poisoned by the global-scope DOM bug.

The bug: funds_scraper.py extracted the TASI index value (1516.53) and the
site's Hijri header date (1447-10-14) for every fund, because the DOM
selectors were not scoped to the fund container.

This script deletes all rows matching either poison marker:
  - nav_price = 1516.53   (TASI index value, not a real fund NAV)
  - date = '1447-10-14'   (Hijri date from the global page header)

Usage:
    python clean_poisoned_data.py           # dry-run (show what would be deleted)
    python clean_poisoned_data.py --fix     # actually delete the rows
"""

import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mutual_funds.db")

POISON_NAV = 1516.53
POISON_DATE = "1447-10-14"


def main():
    dry_run = "--fix" not in sys.argv

    if not os.path.isfile(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()

    # ── Preview affected rows ────────────────────────────────────────────
    cursor.execute(
        """
        SELECT id, date, fund_name, nav_price
        FROM nav_history
        WHERE nav_price = ? OR date = ?
        ORDER BY date, fund_name
        """,
        (POISON_NAV, POISON_DATE),
    )
    rows = cursor.fetchall()

    if not rows:
        print("No poisoned rows found. Database is clean.")
        conn.close()
        return

    print(f"Found {len(rows)} poisoned row(s):\n")
    print(f"  {'ID':>6}  {'Date':<12}  {'NAV':>10}  Fund")
    print(f"  {'─'*6}  {'─'*12}  {'─'*10}  {'─'*30}")
    for row_id, dt, fund, nav in rows:
        markers = []
        if nav == POISON_NAV:
            markers.append("BAD NAV")
        if dt == POISON_DATE:
            markers.append("HIJRI DATE")
        print(f"  {row_id:>6}  {dt:<12}  {nav:>10.4f}  {fund}  [{', '.join(markers)}]")

    if dry_run:
        print(f"\nDry run — no rows deleted. Run with --fix to delete.")
        conn.close()
        return

    # ── Delete ───────────────────────────────────────────────────────────
    cursor.execute(
        """
        DELETE FROM nav_history
        WHERE nav_price = ? OR date = ?
        """,
        (POISON_NAV, POISON_DATE),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    print(f"\nDeleted {deleted} poisoned row(s) from {DB_PATH}")


if __name__ == "__main__":
    main()
