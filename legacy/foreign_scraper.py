"""
foreign_scraper.py — Tadawul Foreign Ownership Scraper.

Scrapes the official Tadawul Foreign Ownership report page and stores
daily snapshots of foreign ownership percentages for all listed Saudi
equities (~400+ companies).

DATA SOURCE:
    https://www.saudiexchange.sa/.../foreign-ownership?locale=en

    The page renders a server-side <table id="issuerTable"> with columns:
      Symbol | Company | Max Limit | Actual Foreign % | Strategic Investors %

    All ~411 rows are loaded in a single page (paging: false in DataTables).
    No pagination handling required.

STRATEGY:
    Primary:  curl_cffi with Chrome TLS fingerprint (data is in the HTML).
    Fallback: Playwright headed mode (if WAF blocks curl_cffi).

    curl_cffi is preferred because the data is fully server-side rendered
    in the HTML <tbody> — no JavaScript execution is needed.

Usage:
    python foreign_scraper.py                # curl_cffi (fast, headless)
    python foreign_scraper.py --playwright   # Playwright fallback
    python foreign_scraper.py --visible      # Playwright visible browser
"""

import argparse
import asyncio
import logging
import os
import platform
import re
import sys
from datetime import date

from foreign_db import get_connection, init_db, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Tadawul Foreign Ownership page URL ──────────────────────────────────────
FOREIGN_OWNERSHIP_URL = (
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
    "newsandreports/reports-publications/foreign-ownership?locale=en"
)

# ── Table structure (from recon) ────────────────────────────────────────────
# <table id="issuerTable">
#   <thead>: Symbol | Company | Max Limit | Actual | Strategic Investors
#   <tbody>: ~411 <tr> rows, each with 5 <td> cells
TABLE_ID = "issuerTable"


# ══════════════════════════════════════════════════════════════════════════════
# PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _clean_pct(raw: str) -> float | None:
    """Parse '5.43%' or '49.0%' → 5.43 or 49.0.  Returns None on failure."""
    raw = raw.strip().replace(",", "").replace("%", "").replace("٪", "")
    # Translate Eastern Arabic numerals
    eastern = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    raw = raw.translate(eastern)
    try:
        val = float(raw)
        if 0.0 <= val <= 100.0:
            return round(val, 4)
    except ValueError:
        pass
    return None


def parse_html_table(html: str) -> list[dict]:
    """
    Extract rows from <table id="issuerTable"> in the raw HTML.

    Returns a list of dicts:
      {symbol, company_name, max_limit, foreign_pct, strategic_pct}
    """
    # Locate the tbody of issuerTable
    table_match = re.search(
        rf'<table[^>]*id="{TABLE_ID}"[^>]*>.*?<tbody>(.*?)</tbody>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not table_match:
        log.error("Could not find <table id='%s'> in the HTML", TABLE_ID)
        return []

    tbody = table_match.group(1)

    rows_raw = re.findall(r"<tr>(.*?)</tr>", tbody, re.DOTALL)
    log.info("Found %d raw <tr> rows in %s", len(rows_raw), TABLE_ID)

    results: list[dict] = []
    for row_html in rows_raw:
        cells = re.findall(r"<td>(.*?)</td>", row_html, re.DOTALL)
        cells = [c.strip() for c in cells]

        if len(cells) < 5:
            continue

        symbol = cells[0].strip()
        company_name = cells[1].strip()
        foreign_pct = _clean_pct(cells[3])  # "Actual" column (index 3)

        if not symbol or not company_name:
            continue

        if foreign_pct is None:
            log.warning("  Skipping %s (%s) — bad pct: '%s'", symbol, company_name, cells[3])
            continue

        results.append({
            "symbol": symbol,
            "company_name": company_name,
            "foreign_pct": foreign_pct,
        })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: curl_cffi (preferred — fast, no browser needed)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_with_curl() -> list[dict]:
    """Fetch the foreign ownership page via curl_cffi and parse the HTML."""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        log.error("curl_cffi not installed. Run: pip install curl_cffi")
        return []

    log.info("Fetching foreign ownership page via curl_cffi ...")
    r = curl_requests.get(
        FOREIGN_OWNERSHIP_URL,
        impersonate="chrome131",
        timeout=30,
    )

    if r.status_code != 200:
        log.error("HTTP %d from Tadawul", r.status_code)
        return []

    log.info("Page fetched: %d bytes", len(r.text))
    return parse_html_table(r.text)


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: Playwright fallback (if curl_cffi is blocked)
# ══════════════════════════════════════════════════════════════════════════════

async def scrape_with_playwright(headless: bool = False) -> list[dict]:
    """Use Playwright to render the page and extract the table."""
    from playwright.async_api import async_playwright

    log.info("Launching Playwright (headless=%s) ...", headless)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            locale="en",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()
        await page.goto(FOREIGN_OWNERSHIP_URL, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(5_000)

        # Wait for the issuerTable to have data rows
        await page.wait_for_selector(
            f"#{TABLE_ID} tbody tr td",
            timeout=30_000,
        )
        log.info("Table rendered — extracting rows via JS ...")

        rows = await page.evaluate("""() => {
            const table = document.getElementById('issuerTable');
            if (!table) return [];
            const trs = table.querySelectorAll('tbody tr');
            return Array.from(trs).map(tr => {
                const tds = tr.querySelectorAll('td');
                if (tds.length < 5) return null;
                return {
                    symbol: tds[0].textContent.trim(),
                    company_name: tds[1].textContent.trim(),
                    foreign_pct_raw: tds[3].textContent.trim(),
                };
            }).filter(Boolean);
        }""")

        await browser.close()

    results: list[dict] = []
    for row in rows:
        pct = _clean_pct(row["foreign_pct_raw"])
        if pct is None:
            log.warning("  Skipping %s — bad pct: '%s'", row["symbol"], row["foreign_pct_raw"])
            continue
        results.append({
            "symbol": row["symbol"],
            "company_name": row["company_name"],
            "foreign_pct": pct,
        })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def save_to_db(records: list[dict], scrape_date: str, db_path: str = DB_PATH) -> int:
    """
    Upsert records into daily_ownership.
    Returns count of rows written.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    saved = 0
    for rec in records:
        cursor.execute(
            """
            INSERT INTO daily_ownership (date, symbol, company_name, foreign_pct)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date, symbol)
                DO UPDATE SET
                    company_name = excluded.company_name,
                    foreign_pct  = excluded.foreign_pct
            """,
            (scrape_date, rec["symbol"], rec["company_name"], rec["foreign_pct"]),
        )
        saved += 1

    conn.commit()
    conn.close()
    return saved


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Tadawul Foreign Ownership data",
    )
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="Use Playwright instead of curl_cffi",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show the browser window (implies --playwright)",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Override scrape date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    use_playwright = args.playwright or args.visible

    log.info("=== Tadawul Foreign Ownership Scraper ===")
    log.info("Date: %s | Strategy: %s", args.date, "Playwright" if use_playwright else "curl_cffi")

    # ── Scrape ───────────────────────────────────────────────────────────────
    if use_playwright:
        if platform.system() == "Windows":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        headless = not args.visible
        records = asyncio.run(scrape_with_playwright(headless=headless))
    else:
        records = scrape_with_curl()

    if not records:
        log.error("No records extracted — aborting.")
        sys.exit(1)

    log.info("Extracted %d companies", len(records))

    # ── Top 10 preview ───────────────────────────────────────────────────────
    log.info("── Top 10 by foreign ownership ──")
    top10 = sorted(records, key=lambda r: r["foreign_pct"], reverse=True)[:10]
    for r in top10:
        log.info("  %s  %-28s  %6.2f%%", r["symbol"], r["company_name"], r["foreign_pct"])

    # ── Save ─────────────────────────────────────────────────────────────────
    saved = save_to_db(records, args.date, db_path=args.db)
    log.info("=== Done — %d records saved to %s for %s ===", saved, args.db, args.date)


if __name__ == "__main__":
    main()
