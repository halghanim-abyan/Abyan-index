"""Regression checks for non-supermarket CPI proxy seeding."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from basket_config import normalized_basket
from external_sources import seed_external_prices


def test_external_proxy_rows_are_seeded_into_daily_prices() -> None:
    fd, db_path = tempfile.mkstemp(prefix="external_sources_", suffix=".db")
    os.close(fd)
    try:
        expected_external = sum(
            1
            for item in normalized_basket()
            if item.get("source", {}).get("type") == "external_proxy"
        )
        report = seed_external_prices(db_path, run_date="2026-06-03", apply=True)

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT item_id) AS items,
                       MIN(price) AS min_price,
                       MAX(price) AS max_price
                  FROM daily_prices
                 WHERE date = '2026-06-03'
                   AND store_name = 'External CPI Proxy'
                   AND scrape_status = 'ok'
                """
            ).fetchone()
        finally:
            conn.close()

        assert report["items"] == expected_external
        assert report["rows_written"] == expected_external
        assert row[0] == expected_external
        assert row[1] == expected_external
        assert row[2] == 100.0
        assert row[3] == 100.0
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(f"{db_path}{suffix}")
            except OSError:
                pass


if __name__ == "__main__":
    test_external_proxy_rows_are_seeded_into_daily_prices()
    print("external source tests passed")
