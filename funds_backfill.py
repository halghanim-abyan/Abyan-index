"""
funds_backfill.py — Historical NAV backfill for Saudi Mutual Funds.

Fetches full historical NAV data from Tadawul's internal chart API
(MutualFundChartDataDownloader) and upserts it into mutual_funds.db.

ENGINE:
    curl_cffi with impersonate="chrome131" — same WAF-bypass approach
    as funds_scraper.py.  No browser needed.

API FLOW (Tadawul):
    1. GET hidden fund profile page → extract content-location header
       + NJgetJWTTokenUrl portlet path
    2. GET the JWT portlet → receive {"jwtToken": "eyJ..."}
    3. GET /tadawul.eportal.charts.v2/MutualFundChartDataDownloader
       ?actionTarget=mutualFundChartData
       &mutualFundSymbol=<SYMBOL>
       &jwtToken=<JWT>
       Returns JSON array:
         [{"valuationDate":"2026-04-01","unitPrice":144.6346,...}, ...]

    One JWT token (valid ~5 min) works for ALL fund symbols.

FUND COVERAGE:
    009003  SNB Capital Al Sunbullah SAR   — Tadawul (6,900+ records since 2001)
    159002  Alpha Murabaha Fund            — Tadawul (1,800+ records since 2018)
    012063  Al Rajhi Awaeed Fund           — Tadawul (550+ records since 2024)

Usage:
    python funds_backfill.py                       # backfill all funds, full history
    python funds_backfill.py --days 90             # last 90 days only
    python funds_backfill.py --fund 009003         # single fund
    python funds_backfill.py --fund 159002         # Alpha Murabaha only
    python funds_backfill.py --dry-run             # preview without DB writes
    python funds_backfill.py --debug               # verbose logging
    python funds_backfill.py --summary             # show DB stats and exit
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional

from funds_db import get_connection, create_tables, DB_PATH, TARGET_FUNDS

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Eastern Arabic → Western digits ──────────────────────────────────────────
_EASTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# ── Poison guards (same as funds_scraper.py) ─────────────────────────────────
POISON_NAVS: set[float] = {1516.53}
POISON_DATE = "1447-10-14"


# ═══════════════════════════════════════════════════════════════════════════════
# URLS
# ═══════════════════════════════════════════════════════════════════════════════

TADAWUL_HIDDEN_MF_BASE = (
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
    "hidden/company-profile-mutual-fund/!ut/p/z1/"
    "04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz83Y0DDAwC3QL8PM0DzYwNAo30"
    "I4EKzBEKDMKcTQzMDPxN3H19LAwNPEz1w8syU8v1wwkpK8hOMgUAof6qaw!!/"
)

CHART_DATA_URL = (
    "https://www.saudiexchange.sa"
    "/tadawul.eportal.charts.v2/MutualFundChartDataDownloader"
)

# All 3 target funds use Tadawul chart API (confirmed working with historical data)
TADAWUL_FUNDS = {"009003", "159002", "012063"}


# ═══════════════════════════════════════════════════════════════════════════════
# CURL_CFFI SESSION
# ═══════════════════════════════════════════════════════════════════════════════

def create_session():
    """Create a curl_cffi session with Chrome TLS fingerprint."""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        log.error("curl_cffi not installed.  Run: pip install curl_cffi")
        sys.exit(1)

    session = curl_requests.Session(impersonate="chrome131")
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    return session


# ═══════════════════════════════════════════════════════════════════════════════
# JWT TOKEN ACQUISITION
# ═══════════════════════════════════════════════════════════════════════════════

def acquire_jwt(session) -> Optional[str]:
    """
    Acquire a JWT token from Tadawul's hidden fund profile page.

    The JWT is embedded directly in the server-side rendered HTML of any
    fund profile page on the hidden WPS portal path.  The page's JavaScript
    contains literal strings like:
        jwtToken="+'eyJhbGciOiJIUzI1NiJ9...'
    so we extract the token with a regex — no separate portlet call needed.

    The JWT is valid for ~5 minutes and works for ALL fund symbols.
    """
    log.info("Acquiring JWT token ...")

    # Fetch any working fund profile page from the hidden path
    profile_url = f"{TADAWUL_HIDDEN_MF_BASE}?selectedFund=009003"
    try:
        r = session.get(profile_url, timeout=30)
    except Exception as e:
        log.error("  Failed to fetch profile page: %s", e)
        return None

    if r.status_code != 200:
        log.error("  HTTP %d from profile page", r.status_code)
        return None

    html = r.text
    log.info("  Profile page fetched: %d bytes", len(html))

    # The JWT is embedded in JS as:  jwtToken="+'eyJ...' or jwtToken='eyJ...'
    jwt_match = re.search(
        r"""jwtToken=["']?\+?["']?(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)""",
        html,
    )
    if not jwt_match:
        log.error("  No embedded JWT found in profile HTML")
        # Fallback: try the portlet URL approach
        return _acquire_jwt_via_portlet(session, html, dict(r.headers))

    jwt = jwt_match.group(1)
    log.info("  JWT extracted from HTML (length=%d, valid ~5 min)", len(jwt))
    return jwt


def _acquire_jwt_via_portlet(session, html: str, headers: dict) -> Optional[str]:
    """
    Fallback: acquire JWT via the getJWTTokenUrl portlet resource.

    Used if the JWT is not embedded in the HTML (e.g., if Tadawul changes
    their rendering strategy).
    """
    log.info("  Trying portlet fallback for JWT ...")

    # Extract portlet path (var getJWTTokenUrl = 'p0/IZ7_...=NJgetJWTTokenUrl=/')
    portlet_match = re.search(
        r"""getJWTTokenUrl\s*=\s*['"]([^'"]+)['"]""",
        html,
    )
    if not portlet_match:
        log.error("  getJWTTokenUrl portlet path not found")
        return None

    portlet_path = portlet_match.group(1)

    # Extract content-location header for base URL
    cl = headers.get("content-location", "")
    if not cl.startswith("/"):
        log.error("  No content-location header")
        return None

    cl_base = "https://www.saudiexchange.sa" + cl
    jwt_url = cl_base.rstrip("/") + "/" + portlet_path

    try:
        jr = session.get(
            jwt_url,
            timeout=15,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            params={"pageName": "CompanyProfileMutualFund"},
        )
    except Exception as e:
        log.error("  JWT portlet request failed: %s", e)
        return None

    if jr.status_code != 200:
        log.error("  JWT portlet returned HTTP %d", jr.status_code)
        return None

    try:
        data = json.loads(jr.text)
    except json.JSONDecodeError:
        log.error("  JWT portlet response is not JSON: %s", jr.text[:200])
        return None

    jwt = data.get("jwtToken") or data.get("token")
    if not jwt:
        log.error("  No jwtToken in portlet response: %s", list(data.keys()))
        return None

    log.info("  JWT acquired via portlet (length=%d)", len(jwt))
    return jwt


# ═══════════════════════════════════════════════════════════════════════════════
# TADAWUL HISTORICAL DATA FETCH
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_tadawul_history(
    session,
    symbol: str,
    jwt: str,
    days: Optional[int] = None,
) -> list[dict]:
    """
    Fetch historical NAV data from Tadawul's MutualFundChartDataDownloader.

    The API returns the FULL history for the fund (thousands of records going
    back to inception in some cases).

    Args:
        session:  curl_cffi session
        symbol:   Tadawul fund symbol (e.g. '009003')
        jwt:      JWT token from acquire_jwt()
        days:     If set, only keep the last N days of data

    Returns:
        Sorted list of {"date": "YYYY-MM-DD", "nav_price": float}
    """
    log.info("  Fetching historical chart data for %s ...", symbol)

    try:
        r = session.get(
            CHART_DATA_URL,
            timeout=30,
            params={
                "actionTarget": "mutualFundChartData",
                "mutualFundSymbol": symbol,
                "pageName": "CompanyProfileMutualFund",
                "jwtToken": jwt,
                "methodType": "parsingMethod",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Referer": f"{TADAWUL_HIDDEN_MF_BASE}?selectedFund={symbol}",
            },
        )
    except Exception as e:
        log.error("  Chart data request failed: %s", e)
        return []

    if r.status_code != 200:
        log.error("  Chart data HTTP %d", r.status_code)
        return []

    try:
        raw_data = json.loads(r.text)
    except json.JSONDecodeError:
        log.error("  Response is not JSON (%d bytes): %s",
                  len(r.text), r.text[:200])
        return []

    if not isinstance(raw_data, list):
        log.error("  Expected JSON array, got %s", type(raw_data).__name__)
        return []

    log.info("  Received %d raw records from API", len(raw_data))

    if not raw_data:
        return []

    # ── Date cutoff (for --days filter) ───────────────────────────────
    cutoff = None
    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()

    # ── Parse and validate each record ────────────────────────────────
    results: list[dict] = []
    skipped = 0

    for rec in raw_data:
        # --- Date ---
        val_date = rec.get("valuationDate", "")
        if not val_date or not isinstance(val_date, str):
            skipped += 1
            continue

        val_date = val_date.strip().translate(_EASTERN)
        parsed_date = _parse_date(val_date)
        if not parsed_date:
            skipped += 1
            continue

        # Reject Hijri poison date
        if POISON_DATE in parsed_date:
            skipped += 1
            continue

        # Apply --days cutoff
        if cutoff and parsed_date < cutoff:
            continue

        # --- NAV price ---
        nav_raw = rec.get("unitPrice")
        if nav_raw is None:
            nav_raw = rec.get("indexPrice")  # fallback field (same value)
        if nav_raw is None:
            skipped += 1
            continue

        try:
            nav_val = float(nav_raw)
        except (ValueError, TypeError):
            skipped += 1
            continue

        # Validate range
        if nav_val <= 0 or nav_val > 50_000:
            skipped += 1
            continue

        # Reject poison NAVs
        is_poison = False
        for poison in POISON_NAVS:
            if abs(nav_val - poison) < 0.01:
                is_poison = True
                break
        if is_poison:
            skipped += 1
            continue

        results.append({
            "date": parsed_date,
            "nav_price": round(nav_val, 6),
        })

    if skipped:
        log.debug("  Skipped %d invalid/out-of-range records", skipped)

    # Sort by date ascending
    results.sort(key=lambda x: x["date"])

    if results:
        log.info("  Valid records: %d  (%s to %s)",
                 len(results), results[0]["date"], results[-1]["date"])
    else:
        log.warning("  No valid records after parsing")

    return results


def _parse_date(raw: str) -> Optional[str]:
    """Parse various date formats → 'YYYY-MM-DD'.  Returns None on failure."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year < 2000 or dt.year > 2100:
                return None
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# RIYAD CAPITAL — CURRENT DAY ONLY (no historical API exists)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_riyadcapital_current(session, fund_code: str) -> list[dict]:
    """
    Scrape current-day NAV from riyadcapital.com fund-prices page.

    LIMITATION: riyadcapital.com has NO historical data endpoint.
    Their fund prices portlet (Liferay 7.x) serves only the latest day's
    NAV — no date picker, no pagination, no chart API, no REST endpoint.
    This function returns at most 1 record.
    """
    log.info("  Fetching current NAV from riyadcapital.com (fund_code=%s) ...", fund_code)
    log.warning("  NOTE: riyadcapital.com has NO historical API — current day only")

    try:
        r = session.get(RIYADCAPITAL_URL, timeout=30)
    except Exception as e:
        log.error("  Request failed: %s", e)
        return []

    if r.status_code != 200:
        log.error("  HTTP %d", r.status_code)
        return []

    html = r.text
    log.info("  Page: %d bytes", len(html))

    row_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    strip_tags = lambda s: re.sub(r"<[^>]+>", "", s).strip()

    for row_match in row_pat.finditer(html):
        cells = cell_pat.findall(row_match.group(1))
        if len(cells) < 6:
            continue

        code = strip_tags(cells[0])
        if code.lstrip("0") != fund_code.lstrip("0") and code != fund_code:
            continue

        nav_raw = strip_tags(cells[3]).translate(_EASTERN).replace(",", "")
        date_raw = strip_tags(cells[2]).translate(_EASTERN)

        try:
            nav_val = float(nav_raw)
        except (ValueError, TypeError):
            log.error("  Bad NAV value: '%s'", nav_raw)
            return []

        if nav_val <= 0 or nav_val > 50_000:
            log.error("  NAV out of range: %.4f", nav_val)
            return []

        parsed_date = _parse_date(date_raw)
        if not parsed_date:
            log.warning("  Could not parse date '%s', using today", date_raw)
            parsed_date = date.today().isoformat()

        log.info("  Current NAV: %.6f  date=%s", nav_val, parsed_date)
        return [{"date": parsed_date, "nav_price": round(nav_val, 6)}]

    log.error("  Fund code '%s' not found on page", fund_code)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE UPSERT
# ═══════════════════════════════════════════════════════════════════════════════

def upsert_records(
    records: list[dict],
    fund_name: str,
    db_path: str = DB_PATH,
) -> tuple[int, int]:
    """
    Upsert historical records into nav_history.

    Uses INSERT ... ON CONFLICT to avoid duplicates and update NAV if the
    source has a newer/corrected value for an existing date.

    Returns (inserted_count, updated_count).
    """
    conn = get_connection(db_path)
    try:
        create_tables(conn)
    except Exception:
        pass

    cursor = conn.cursor()
    inserted = 0
    updated = 0

    for rec in records:
        # Check if a row already exists for this date + fund
        cursor.execute(
            "SELECT nav_price FROM nav_history WHERE date = ? AND fund_name = ?",
            (rec["date"], fund_name),
        )
        existing = cursor.fetchone()

        cursor.execute(
            """
            INSERT INTO nav_history (date, fund_name, nav_price)
            VALUES (?, ?, ?)
            ON CONFLICT(date, fund_name)
                DO UPDATE SET nav_price = excluded.nav_price
            """,
            (rec["date"], fund_name, rec["nav_price"]),
        )

        if existing is None:
            inserted += 1
        elif abs(existing[0] - rec["nav_price"]) > 0.000001:
            updated += 1

    conn.commit()
    conn.close()
    return inserted, updated


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def show_summary(db_path: str = DB_PATH) -> None:
    """Print a summary of the nav_history table."""
    if not os.path.isfile(db_path):
        log.info("No database found at %s", db_path)
        return

    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM nav_history")
    total = cursor.fetchone()[0]

    print(f"\n{'=' * 65}")
    print(f"  NAV History — Database Summary")
    print(f"  Path: {db_path}")
    print(f"  Total records: {total:,}")
    print(f"{'=' * 65}")

    if total == 0:
        print("  (empty)")
        conn.close()
        return

    cursor.execute("""
        SELECT
            fund_name,
            COUNT(*)     AS records,
            MIN(date)    AS first_date,
            MAX(date)    AS last_date,
            MIN(nav_price) AS min_nav,
            MAX(nav_price) AS max_nav
        FROM nav_history
        GROUP BY fund_name
        ORDER BY fund_name
    """)
    rows = cursor.fetchall()

    print(f"\n  {'Fund':<36} {'Rows':>7} {'From':>12} {'To':>12}"
          f" {'Min NAV':>10} {'Max NAV':>10}")
    print(f"  {'-'*36} {'-'*7} {'-'*12} {'-'*12} {'-'*10} {'-'*10}")
    for name, cnt, first, last, mn, mx in rows:
        print(f"  {name[:36]:<36} {cnt:>7,} {first:>12} {last:>12}"
              f" {mn:>10.4f} {mx:>10.4f}")

    print()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical NAV data for Saudi Mutual Funds",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Only fetch last N days (default: full history)",
    )
    parser.add_argument(
        "--fund", default=None,
        help="Backfill only this fund symbol (e.g. 009003)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch data but don't write to database",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--db", default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Show database summary and exit",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Summary-only mode ────────────────────────────────────────────
    if args.summary:
        show_summary(db_path=args.db)
        return

    log.info("=" * 65)
    log.info("Saudi Mutual Fund NAV — Historical Backfill")
    log.info("Engine: curl_cffi (impersonate=chrome131)")
    log.info("=" * 65)
    log.info("Days filter : %s", f"last {args.days}" if args.days else "full history")
    log.info("Database    : %s", args.db)
    log.info("Dry run     : %s", args.dry_run)

    # ── Filter funds ─────────────────────────────────────────────────
    funds = TARGET_FUNDS
    if args.fund:
        funds = [f for f in funds if f["symbol"] == args.fund]
        if not funds:
            log.error("Fund symbol '%s' not found in TARGET_FUNDS", args.fund)
            sys.exit(1)

    log.info("Funds       : %d", len(funds))
    for f in funds:
        log.info("  %s  %-35s  [Tadawul chart API]", f["symbol"], f["name"])

    # ── Create curl_cffi session ─────────────────────────────────────
    session = create_session()
    log.info("curl_cffi session ready")

    # ── Acquire JWT (needed for all funds — all use Tadawul chart API) ─
    jwt = acquire_jwt(session)
    if not jwt:
        log.error("FATAL: Could not acquire JWT — backfill impossible")
        sys.exit(1)

    # ── Process each fund ────────────────────────────────────────────
    grand_fetched = 0
    grand_inserted = 0
    grand_updated = 0

    for fund in funds:
        symbol = fund["symbol"]
        name = fund["name"]

        log.info("")
        log.info("-" * 55)
        log.info("Fund: %s (%s)", name, symbol)
        log.info("-" * 55)

        records: list[dict] = []

        # ── Fetch from Tadawul chart API ────────────────────────────
        records = fetch_tadawul_history(session, symbol, jwt, days=args.days)

        if not records:
            log.warning("  No records retrieved — skipping")
            continue

        grand_fetched += len(records)

        # ── Preview ──────────────────────────────────────────────────
        log.info("  Records   : %d", len(records))
        log.info("  Date range: %s  →  %s", records[0]["date"], records[-1]["date"])
        log.info("  NAV range : %.6f  →  %.6f",
                 min(r["nav_price"] for r in records),
                 max(r["nav_price"] for r in records))

        # Show last 5 rows
        tail = records[-5:] if len(records) > 5 else records
        log.info("  Latest %d records:", len(tail))
        for rec in tail:
            log.info("    %s  NAV = %.6f", rec["date"], rec["nav_price"])

        # ── Upsert to DB ────────────────────────────────────────────
        if args.dry_run:
            log.info("  [dry-run] Would upsert %d records for '%s'", len(records), name)
        else:
            inserted, updated = upsert_records(records, name, db_path=args.db)
            grand_inserted += inserted
            grand_updated += updated
            log.info("  DB result: %d new, %d updated", inserted, updated)

    # ── Final summary ────────────────────────────────────────────────
    log.info("")
    log.info("=" * 65)
    log.info("BACKFILL COMPLETE")
    log.info("=" * 65)
    log.info("  Total records fetched  : %d", grand_fetched)
    if not args.dry_run:
        log.info("  Total inserted (new)   : %d", grand_inserted)
        log.info("  Total updated (changed): %d", grand_updated)
        log.info("  Database               : %s", args.db)
    else:
        log.info("  [dry-run — no database writes]")

    # ── Post-backfill DB stats ───────────────────────────────────────
    if not args.dry_run:
        show_summary(db_path=args.db)


if __name__ == "__main__":
    main()
