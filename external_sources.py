"""Seed non-supermarket CPI proxy observations.

This script writes one daily ``daily_prices`` observation for every basket
item whose ``source_type`` is not ``supermarket``. These rows are deliberately
stored in the same audit table as scraped prices so the calculator can treat
all basket components uniformly.

The initial value is an index base of 100.0. Later, this script can be extended
to read official monthly sub-indices, provider APIs, or a manual CSV quote file.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import sqlite3

from basket_config import normalized_basket
from db_setup import DB_PATH, create_tables, get_connection, migrate_schema, seed_data

DEFAULT_EXTERNAL_PRICE = 100.0
DEFAULT_EXTERNAL_SOURCE_NAME = "External CPI Proxy"


def _basket_source_lookup() -> dict[str, dict]:
    return {item["name"]: item.get("source", {}) for item in normalized_basket()}


def seed_external_prices(
    db_path: str = DB_PATH,
    run_date: str | None = None,
    apply: bool = True,
) -> dict[str, int]:
    """Insert/update today's external CPI proxy observations.

    Returns a small report with ``items`` and ``rows_written`` counts.
    """
    run_date = run_date or date.today().isoformat()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    basket_sources = _basket_source_lookup()

    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        seed_data(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, category, source_type, source_name
              FROM items
             WHERE source_type <> 'supermarket'
             ORDER BY category, name
            """
        ).fetchall()

        if not apply:
            return {"items": len(rows), "rows_written": 0}

        written = 0
        for row in rows:
            source = basket_sources.get(row["name"], {})
            source_name = source.get("name") or row["source_name"] or DEFAULT_EXTERNAL_SOURCE_NAME
            price = float(source.get("price", DEFAULT_EXTERNAL_PRICE))
            notes = source.get("notes") or "External CPI proxy observation"
            observed_title = f"{source_name}: {row['name']} (base={price:g})"

            conn.execute(
                """
                INSERT INTO daily_prices (
                    date, item_id, store_name, price, scrape_status, failure_reason,
                    observed_at, match_tier, observed_title, match_notes
                )
                VALUES (?, ?, ?, ?, 'ok', NULL, ?, 'gastat_representative', ?, ?)
                ON CONFLICT(date, item_id, store_name) DO UPDATE SET
                    price = excluded.price,
                    scrape_status = excluded.scrape_status,
                    failure_reason = excluded.failure_reason,
                    observed_at = excluded.observed_at,
                    match_tier = excluded.match_tier,
                    observed_title = excluded.observed_title,
                    match_notes = excluded.match_notes
                """,
                (
                    run_date,
                    int(row["id"]),
                    source_name,
                    price,
                    observed_at,
                    observed_title,
                    notes,
                ),
            )
            written += 1
        conn.commit()
        return {"items": len(rows), "rows_written": written}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed non-supermarket CPI proxy observations into daily_prices.",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--date", default=date.today().isoformat(), help="Observation date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing daily_prices")
    args = parser.parse_args()

    report = seed_external_prices(args.db, run_date=args.date, apply=not args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLY"
    print("=" * 62)
    print(f"  External CPI Proxy Seeder — {mode}")
    print("=" * 62)
    print(f"  Source DB       : {args.db}")
    print(f"  Date            : {args.date}")
    print(f"  External items  : {report['items']}")
    print(f"  Rows written    : {report['rows_written']}")
    print("=" * 62)


if __name__ == "__main__":
    main()
