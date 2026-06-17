"""
foreign_db.py — Database setup for Tadawul Foreign Ownership Tracker.

Creates foreign_flows.db with a daily_ownership table for storing
foreign ownership percentages across all listed Saudi equities.

Table: daily_ownership
  id           INTEGER PRIMARY KEY
  date         TEXT    (YYYY-MM-DD)
  symbol       TEXT    (Tadawul ticker, e.g. '2222')
  company_name TEXT    (e.g. 'SAUDI ARAMCO')
  foreign_pct  REAL    (actual foreign ownership %, e.g. 5.43)

Unique constraint on (date, symbol) prevents duplicate daily entries.

Run once to bootstrap:
    python foreign_db.py
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "foreign_flows.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_ownership (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT    NOT NULL,
            symbol        TEXT    NOT NULL,
            company_name  TEXT    NOT NULL,
            foreign_pct   REAL    NOT NULL,
            UNIQUE(date, symbol)
        );

        CREATE INDEX IF NOT EXISTS idx_ownership_date
            ON daily_ownership(date);
        CREATE INDEX IF NOT EXISTS idx_ownership_symbol
            ON daily_ownership(symbol);
    """)
    conn.commit()


def init_db(db_path: str = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        create_tables(conn)
        count = conn.execute("SELECT COUNT(*) FROM daily_ownership").fetchone()[0]
        print(f"[foreign_db] Database initialized at {db_path}")
        print(f"[foreign_db] Existing records: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
