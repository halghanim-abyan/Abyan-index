"""
main.py - Entry point for the Daily Inflation Index pipeline.

Orchestrates the full daily workflow:
  1. Ensure the database exists.
  2. Scrape prices from configured daily sources.
  3. Carry official GASTAT sources to the app date.
  4. Calculate and persist the daily index when coverage passes the gate.

Useful commands:
    python main.py
    python main.py --finalize-date 2026-06-07
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from calculator import calculate_daily_index
from db_setup import DB_PATH, get_connection, init_db
from gastat_average_prices import import_gastat_average_prices
from gastat_cpi_indices import import_gastat_cpi_indices
from scraper import run_scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

OFFICIAL_CARRY_FORWARD_STORES = (
    "GASTAT Average Prices",
    "GASTAT CPI Category Index",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coverage_for_date(run_date: str) -> dict[str, float | int]:
    """Return raw item-level coverage for one app date."""
    from basket_config import normalized_basket  # noqa: WPS433

    expected_items = len(normalized_basket())
    conn = get_connection(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT CASE
                       WHEN COALESCE(scrape_status, 'ok') = 'ok'
                        AND price IS NOT NULL
                       THEN item_id END) AS ok_items,
                   COUNT(DISTINCT item_id) AS observed_items
              FROM daily_prices
             WHERE date = ?
            """,
            (run_date,),
        ).fetchone()
    finally:
        conn.close()

    ok_items = int(row[0] or 0) if row else 0
    observed_items = int(row[1] or 0) if row else 0
    coverage_pct = (ok_items / expected_items * 100.0) if expected_items else 0.0
    return {
        "coverage_pct": round(coverage_pct, 4),
        "ok_items": ok_items,
        "observed_items": observed_items,
        "expected_items": expected_items,
    }


def _start_pipeline_run(run_date: str, stage: str = "started") -> int:
    conn = get_connection(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs (run_date, started_at, status, stage)
            VALUES (?, ?, 'running', ?)
            """,
            (run_date, _utc_now(), stage),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _update_pipeline_run(
    run_id: int | None,
    *,
    run_date: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    coverage_pct: float | None = None,
    ok_items: int | None = None,
    expected_items: int | None = None,
    error: str | None = None,
    finished: bool = False,
) -> None:
    if run_id is None:
        return

    updates: list[str] = []
    params: list[object] = []
    for column, value in (
        ("run_date", run_date),
        ("status", status),
        ("stage", stage),
        ("coverage_pct", coverage_pct),
        ("ok_items", ok_items),
        ("expected_items", expected_items),
        ("error", error),
    ):
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)
    if finished:
        updates.append("finished_at = ?")
        params.append(_utc_now())
    if not updates:
        return

    params.append(run_id)
    conn = get_connection(DB_PATH)
    try:
        conn.execute(
            f"UPDATE pipeline_runs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def carry_forward_cached_official_sources(run_date: str) -> int:
    """Use cached official GASTAT rows when the live workbook cannot be fetched."""
    observed_at = _utc_now()
    placeholders = ",".join("?" for _ in OFFICIAL_CARRY_FORWARD_STORES)

    conn = get_connection(DB_PATH)
    conn.row_factory = None
    try:
        rows = conn.execute(
            f"""
            SELECT dp.item_id,
                   dp.store_name,
                   dp.price,
                   COALESCE(dp.match_tier, 'gastat_representative') AS match_tier,
                   dp.observed_title,
                   dp.match_notes,
                   dp.date AS source_date
              FROM daily_prices dp
              JOIN (
                    SELECT item_id, store_name, MAX(date) AS source_date
                      FROM daily_prices
                     WHERE date < ?
                       AND store_name IN ({placeholders})
                       AND COALESCE(scrape_status, 'ok') = 'ok'
                       AND price IS NOT NULL
                     GROUP BY item_id, store_name
                   ) latest
                ON latest.item_id = dp.item_id
               AND latest.store_name = dp.store_name
               AND latest.source_date = dp.date
            """,
            (run_date, *OFFICIAL_CARRY_FORWARD_STORES),
        ).fetchall()

        for row in rows:
            item_id, store_name, price, match_tier, title, notes, source_date = row
            fallback_note = (
                f"{notes or ''} Cached official source carried forward from "
                f"{source_date} because the live GASTAT workbook was unavailable."
            ).strip()
            conn.execute(
                """
                INSERT INTO daily_prices (
                    date, item_id, store_name, price, scrape_status, failure_reason,
                    observed_at, match_tier, observed_title, match_notes
                )
                VALUES (?, ?, ?, ?, 'ok', NULL, ?, ?, ?, ?)
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
                    item_id,
                    store_name,
                    price,
                    observed_at,
                    match_tier,
                    title,
                    fallback_note,
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def refresh_official_sources(run_date: str) -> None:
    """Carry current official GASTAT inputs to the app date, with cache fallback."""
    try:
        avg_report = import_gastat_average_prices(carry_to_date=run_date)
        log.info(
            "GASTAT Average Prices carried: %s rows for %s",
            avg_report.get("carry_rows_written", 0),
            run_date,
        )
        cpi_report = import_gastat_cpi_indices(carry_to_date=run_date)
        log.info(
            "GASTAT CPI category rows carried: %s rows for %s",
            cpi_report.get("carry_rows_written", 0),
            run_date,
        )
    except Exception as exc:
        log.warning("Live GASTAT refresh failed: %s", exc)
        copied = carry_forward_cached_official_sources(run_date)
        log.warning("Carried forward %d cached official GASTAT rows for %s.", copied, run_date)


def refresh_rental_nowcast_safe() -> None:
    """Refresh the faster asking-rent signal without failing the inflation run."""
    try:
        disabled_level = logging.root.manager.disable
        logging.disable(logging.WARNING)
        from modules.mod_real_estate import refresh_rental_listing_nowcast  # noqa: WPS433
        logging.disable(disabled_level)

        summary = refresh_rental_listing_nowcast(DB_PATH)
        log.info(
            "Rental asking nowcast refreshed: %s/%s cities, %s usable rows (%s raw), status=%s.",
            summary.get("cities_ok"),
            summary.get("cities_requested"),
            summary.get("usable_rows"),
            summary.get("raw_rows"),
            summary.get("status"),
        )
        if summary.get("errors"):
            log.warning("Rental nowcast warnings: %s", " | ".join(summary["errors"][:3]))
    except Exception as exc:  # noqa: BLE001
        logging.disable(logging.NOTSET)
        log.warning("Rental asking nowcast refresh failed: %s", exc)


def finalize_inflation_day(run_date: str) -> float | None:
    """Finish a partial date by adding official sources and recalculating."""
    init_db()
    run_id = _start_pipeline_run(run_date, stage="finalize_started")
    try:
        log.info("Finalizing inflation day %s.", run_date)
        _update_pipeline_run(run_id, stage="official_sources")
        refresh_official_sources(run_date)

        coverage = _coverage_for_date(run_date)
        _update_pipeline_run(
            run_id,
            stage="calculating",
            coverage_pct=float(coverage["coverage_pct"]),
            ok_items=int(coverage["ok_items"]),
            expected_items=int(coverage["expected_items"]),
        )

        index = calculate_daily_index(run_date)
        coverage = _coverage_for_date(run_date)
        if index is None:
            _update_pipeline_run(
                run_id,
                status="failed",
                stage="quality_gate",
                coverage_pct=float(coverage["coverage_pct"]),
                ok_items=int(coverage["ok_items"]),
                expected_items=int(coverage["expected_items"]),
                error="Coverage gate rejected date or no usable index data.",
                finished=True,
            )
            log.warning("Finalize finished without an index for %s.", run_date)
            return None

        _update_pipeline_run(
            run_id,
            status="complete",
            stage="complete",
            coverage_pct=float(coverage["coverage_pct"]),
            ok_items=int(coverage["ok_items"]),
            expected_items=int(coverage["expected_items"]),
            finished=True,
        )
        log.info("Finalize complete. Index for %s = %.4f", run_date, index)
        return index
    except Exception as exc:
        _update_pipeline_run(
            run_id,
            status="failed",
            stage="failed",
            error=str(exc),
            finished=True,
        )
        raise


async def main(*, skip_rental_nowcast: bool = False) -> None:
    log.info("=" * 64)
    log.info("Daily Inflation Index - Pipeline Start")
    log.info("=" * 64)

    init_db()
    run_date = datetime.now().date().isoformat()
    run_id = _start_pipeline_run(run_date, stage="started")
    scrape_error: Exception | None = None

    try:
        _update_pipeline_run(run_id, stage="scraping")
        try:
            scraped_date = await run_scraper()
            if scraped_date:
                run_date = str(scraped_date)
                _update_pipeline_run(run_id, run_date=run_date)
        except Exception as exc:
            scrape_error = exc
            log.exception("Scraper failed; continuing with official sources for %s.", run_date)
            _update_pipeline_run(
                run_id,
                stage="scrape_failed_official_refresh",
                error=f"Scraper failed: {exc}",
            )

        _update_pipeline_run(run_id, stage="official_sources")
        refresh_official_sources(run_date)

        coverage = _coverage_for_date(run_date)
        _update_pipeline_run(
            run_id,
            stage="calculating",
            coverage_pct=float(coverage["coverage_pct"]),
            ok_items=int(coverage["ok_items"]),
            expected_items=int(coverage["expected_items"]),
        )

        index = calculate_daily_index(run_date)
        coverage = _coverage_for_date(run_date)

        if index is not None:
            status_error = f"Scraper warning: {scrape_error}" if scrape_error else None
            _update_pipeline_run(
                run_id,
                status="complete",
                stage="complete",
                coverage_pct=float(coverage["coverage_pct"]),
                ok_items=int(coverage["ok_items"]),
                expected_items=int(coverage["expected_items"]),
                error=status_error,
                finished=True,
            )
            log.info("Pipeline finished. Index for %s = %.4f", run_date, index)
        else:
            error = "Coverage gate rejected date or no usable index data."
            if scrape_error:
                error = f"Scraper failed: {scrape_error}; {error}"
            _update_pipeline_run(
                run_id,
                status="failed",
                stage="quality_gate",
                coverage_pct=float(coverage["coverage_pct"]),
                ok_items=int(coverage["ok_items"]),
                expected_items=int(coverage["expected_items"]),
                error=error,
                finished=True,
            )
            log.warning("Pipeline finished. No index could be computed for %s.", run_date)

        if not skip_rental_nowcast:
            refresh_rental_nowcast_safe()

        log.info("=" * 64)
    except Exception as exc:
        coverage = _coverage_for_date(run_date)
        _update_pipeline_run(
            run_id,
            status="failed",
            stage="failed",
            coverage_pct=float(coverage["coverage_pct"]),
            ok_items=int(coverage["ok_items"]),
            expected_items=int(coverage["expected_items"]),
            error=str(exc),
            finished=True,
        )
        raise


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or finalize the inflation pipeline.")
    parser.add_argument(
        "--finalize-date",
        type=str,
        default=None,
        help="Add official/cached sources for an existing date and recalculate daily_index.",
    )
    parser.add_argument(
        "--skip-rental-nowcast",
        action="store_true",
        help="Skip the faster rental asking-price nowcast refresh.",
    )
    parser.add_argument(
        "--rental-nowcast-only",
        action="store_true",
        help="Only refresh the rental asking-price nowcast and exit.",
    )
    return parser


if __name__ == "__main__":
    args = _build_argparser().parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if args.rental_nowcast_only:
        init_db()
        refresh_rental_nowcast_safe()
    elif args.finalize_date:
        finalize_inflation_day(args.finalize_date)
    else:
        asyncio.run(main(skip_rental_nowcast=args.skip_rental_nowcast))
