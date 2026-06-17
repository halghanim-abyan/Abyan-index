"""
db_setup.py — Database initialization for the Daily Inflation Index.

Creates the SQLite schema and seeds it with the basket defined in
`basket_config.BASKET`. Run once to bootstrap the database; safe to
re-run after editing basket_config.py to pick up new items.

To add a new item: edit basket_config.py, then re-run this script.
The schema is idempotent (CREATE TABLE IF NOT EXISTS) and item inserts
use INSERT OR IGNORE so existing items are preserved.
"""

import sqlite3
import os

from basket_config import normalized_basket, basket_stats

DB_PATH = os.path.join(os.path.dirname(__file__), "inflation_index.db")

SCRAPE_STATUSES = ("ok", "oos", "not_found", "timeout", "error", "blocked")
MATCH_TIERS = ("exact", "gastat_representative")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't already exist."""
    conn.executescript("""
        -- Master list of tracked items and their CPI basket weight.
        CREATE TABLE IF NOT EXISTS items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            category        TEXT    NOT NULL,
            weight_percentage REAL  NOT NULL CHECK(weight_percentage >= 0 AND weight_percentage <= 1),
            source_type     TEXT    NOT NULL DEFAULT 'supermarket',
            source_name     TEXT    NOT NULL DEFAULT 'Supermarket scraper'
        );

        -- One row per (item, store) mapping — the exact product page URL.
        CREATE TABLE IF NOT EXISTS item_urls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            store_name TEXT   NOT NULL,
            url        TEXT   NOT NULL,
            UNIQUE(item_id, store_name)
        );

        -- Raw price observations. price is NULL when there is no usable quote.
        -- scrape_status distinguishes true OOS from scraper/network failures.
        CREATE TABLE IF NOT EXISTS daily_prices (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,   -- ISO-8601 date string (YYYY-MM-DD)
            item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            store_name TEXT    NOT NULL,
            price      REAL,
            scrape_status TEXT NOT NULL DEFAULT 'ok'
                CHECK(scrape_status IN ('ok', 'oos', 'not_found', 'timeout', 'error', 'blocked')),
            failure_reason TEXT,
            observed_at TEXT,
            match_tier TEXT NOT NULL DEFAULT 'exact'
                CHECK(match_tier IN ('exact', 'gastat_representative')),
            observed_title TEXT,
            match_notes TEXT,
            UNIQUE(date, item_id, store_name)
        );

        -- The final computed index value per day.
        CREATE TABLE IF NOT EXISTS daily_index (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL UNIQUE,
            index_value REAL    NOT NULL
        );

        -- One row per pipeline attempt. The UI uses this to distinguish a
        -- completed index day from a raw scrape that is still partial.
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date       TEXT    NOT NULL,
            started_at     TEXT    NOT NULL,
            finished_at    TEXT,
            status         TEXT    NOT NULL DEFAULT 'running'
                CHECK(status IN ('running', 'complete', 'failed')),
            stage          TEXT    NOT NULL DEFAULT 'started',
            coverage_pct   REAL,
            ok_items       INTEGER,
            expected_items INTEGER,
            error          TEXT
        );

        -- Useful indexes for the queries we run daily.
        CREATE INDEX IF NOT EXISTS idx_daily_prices_date    ON daily_prices(date);
        CREATE INDEX IF NOT EXISTS idx_daily_prices_item    ON daily_prices(item_id);
        CREATE INDEX IF NOT EXISTS idx_daily_index_date     ON daily_index(date);
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_date   ON pipeline_runs(run_date);
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status, run_date);
    """)
    conn.commit()


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return column names for an existing SQLite table."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply safe additive migrations for existing local databases."""
    item_cols = _table_columns(conn, "items")
    if "source_type" not in item_cols:
        conn.execute("ALTER TABLE items ADD COLUMN source_type TEXT NOT NULL DEFAULT 'supermarket'")
    if "source_name" not in item_cols:
        conn.execute("ALTER TABLE items ADD COLUMN source_name TEXT NOT NULL DEFAULT 'Supermarket scraper'")

    cols = _table_columns(conn, "daily_prices")
    if "scrape_status" not in cols:
        conn.execute(
            "ALTER TABLE daily_prices ADD COLUMN scrape_status TEXT NOT NULL DEFAULT 'ok'"
        )
        # Legacy NULL rows blended out-of-stock and failures. Mark them as
        # not_found so the calculator does not carry stale prices forward.
        conn.execute(
            """
            UPDATE daily_prices
               SET scrape_status = CASE WHEN price IS NULL THEN 'not_found' ELSE 'ok' END
             WHERE scrape_status = 'ok'
            """
        )
    if "failure_reason" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN failure_reason TEXT")
    if "observed_at" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN observed_at TEXT")
    if "match_tier" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN match_tier TEXT NOT NULL DEFAULT 'exact'")
    if "observed_title" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN observed_title TEXT")
    if "match_notes" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN match_notes TEXT")
    conn.commit()


def seed_data(conn: sqlite3.Connection) -> None:
    """Seed the items + item_urls tables from basket_config.BASKET.

    Idempotent: uses INSERT OR IGNORE so re-running picks up new items
    without disturbing existing ones. Auto-normalises weights so the
    user can declare any positive numbers in basket_config and they all
    add up to 1.0 here.
    """
    basket = normalized_basket()      # weights summed to 1.0 by the loader
    stats  = basket_stats()

    cursor = conn.cursor()

    # ── 1. Insert items ──────────────────────────────────────────────────
    for item in basket:
        source = item.get("source") or {}
        source_type = source.get("type") or "supermarket"
        source_name = source.get("name") or "Supermarket scraper"
        cursor.execute(
            """
            INSERT OR IGNORE INTO items
                (name, category, weight_percentage, source_type, source_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item["name"], item["category"], item["weight"], source_type, source_name),
        )
        # If the item already existed (e.g. weights changed because the user
        # added more items elsewhere), keep weight in sync.
        cursor.execute(
            """
            UPDATE items
               SET category = ?,
                   weight_percentage = ?,
                   source_type = ?,
                   source_name = ?
             WHERE name = ?
            """,
            (item["category"], item["weight"], source_type, source_name, item["name"]),
        )
    conn.commit()

    # ── 2. Build name -> id lookup ───────────────────────────────────────
    item_ids: dict[str, int] = {}
    for row in cursor.execute("SELECT id, name FROM items"):
        item_ids[row[1]] = row[0]

    # ── 3. Insert URLs ───────────────────────────────────────────────────
    for item in basket:
        iid = item_ids.get(item["name"])
        if iid is None:
            continue
        for store_name, url in item.get("urls", {}).items():
            cursor.execute(
                """
                INSERT INTO item_urls (item_id, store_name, url)
                VALUES (?, ?, ?)
                ON CONFLICT(item_id, store_name) DO UPDATE SET url = excluded.url
                """,
                (iid, store_name, url),
            )
    conn.commit()

    # ── 4. Friendly summary ──────────────────────────────────────────────
    cat_summary = ", ".join(
        f"{c}={d['items']}" for c, d in sorted(stats["by_category"].items())
    )
    print(
        f"[db_setup] Seeded basket: {stats['item_count']} items "
        f"across {stats['category_count']} categories ({cat_summary})."
    )


def init_db(db_path: str = DB_PATH) -> None:
    """Full bootstrap: create tables + seed starter data."""
    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        seed_data(conn)
        print(f"[db_setup] Database initialized at {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
