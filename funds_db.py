"""
funds_db.py — Database setup for Saudi Mutual Funds NAV Tracker.

Creates mutual_funds.db with a nav_history table for storing daily
Net Asset Value (سعر الوحدة) per fund.  Run once to bootstrap.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "mutual_funds.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nav_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,        -- YYYY-MM-DD (valuation date)
            fund_name  TEXT    NOT NULL,
            nav_price  REAL    NOT NULL,
            UNIQUE(date, fund_name)
        );

        CREATE INDEX IF NOT EXISTS idx_nav_date ON nav_history(date);
        CREATE INDEX IF NOT EXISTS idx_nav_fund ON nav_history(fund_name);
    """)
    conn.commit()


# ── Target funds ──────────────────────────────────────────────────────────────
# symbol : code on Tadawul's mutual-fund profile page
# name   : human-readable label stored in nav_history.fund_name
#
# All 3 funds are confirmed working via:
#   - Tadawul hidden WPS portal path (SSR data in editableTable)
#   - MutualFundChartDataDownloader chart API (full historical JSON)
#
# Verified 2026-04-05:
#   009003: 6,933 historical records since 2001-08-29
#   159002: 1,859 historical records since 2018-10-16
#   012063:   551 historical records since 2024-01-15
TARGET_FUNDS: list[dict[str, str]] = [
    {
        "symbol": "009003",
        "name":   "SNB Capital Al Sunbullah SAR",
    },
    {
        "symbol": "159002",
        "name":   "Alpha Murabaha Fund",
    },
    {
        "symbol": "012063",
        "name":   "Al Rajhi Awaeed Fund",
    },
]


def init_db(db_path: str = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        create_tables(conn)
        print(f"[funds_db] Database initialized at {db_path}")
        print(f"[funds_db] Tracking {len(TARGET_FUNDS)} funds:")
        for f in TARGET_FUNDS:
            print(f"  • {f['symbol']}  {f['name']}")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
