"""
foreign_liquidity_scraper.py — Foreign Headroom Radar (رادار سيولة الأجانب)

Scrapes the Saudi Exchange (Tadawul) "Foreign Ownership Headroom" page,
extracts per-company foreign-ownership data, and stores daily snapshots
in liquidity_radar.db.

────────────────────────────────────────────────────────────────────────
SOURCE OF TRUTH
────────────────────────────────────────────────────────────────────────
Tadawul publishes a live HTML table at:

    https://www.saudiexchange.sa/wps/portal/saudiexchange/
        newsandreports/reports-publications/market-reports/
        foreign-headroom-main

────────────────────────────────────────────────────────────────────────
WHY PLAYWRIGHT HEADED MODE?
────────────────────────────────────────────────────────────────────────
  1. The table is JS-rendered — a static HTTP GET returns an empty skeleton.
  2. Headless Chromium is detected by Tadawul's Akamai Bot Manager — it
     serves a 395-byte challenge page and withholds all session cookies.
  3. HEADED mode (a visible browser window) passes the Akamai challenge.

Proven in funds_scraper.py's "tadawul_hybrid" strategy.

Strategy:
  a) Launch Playwright in HEADED mode with anti-automation stealth
  b) Navigate to Tadawul homepage — triggers Akamai JS challenge
  c) Wait 10 seconds for challenge to complete + cookies to set
  d) Navigate to headroom page with valid session cookies
  e) Wait for table rows to populate
  f) Grab page.content() — fully rendered HTML
  g) Close browser, hand HTML to parse_headroom_table()

────────────────────────────────────────────────────────────────────────
PIPELINE
────────────────────────────────────────────────────────────────────────
    [ Playwright HEADED (Akamai bypass) ]
                 │
                 ▼
    [ fetch_headroom_page() ── fully-rendered HTML ]
                 │
                 ▼
    [ parse_headroom_table() ── pandas.read_html → clean DataFrame ]
                 │
                 ▼
    [ upsert_rows() ── liquidity_radar.db / foreign_ownership_daily ]

────────────────────────────────────────────────────────────────────────
DEPENDENCIES (run once)
────────────────────────────────────────────────────────────────────────
    pip install playwright pandas lxml
    playwright install chromium

  • playwright  → headed browser to bypass Akamai + render JS tables
  • pandas      → read_html table extraction + data wrangling
  • lxml        → fast HTML parser backend for pandas.read_html

Usage:
    python foreign_liquidity_scraper.py                 (live fetch)
    python foreign_liquidity_scraper.py --html dump.html (offline test)
    python foreign_liquidity_scraper.py --dry-run        (parse only, no DB write)
    python foreign_liquidity_scraper.py --summary        (print DB stats after run)
    python foreign_liquidity_scraper.py --daemon         (stay running, scrape daily at 16:30)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
import traceback
import unicodedata
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Any

# ── Hard dependencies ────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    print("[ERROR] playwright is required.")
    print("        pip install playwright pandas lxml")
    print("        playwright install chromium")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("[ERROR] pandas is required.")
    print("        pip install pandas lxml")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "liquidity_radar.db")

TADAWUL_HOME = "https://www.saudiexchange.sa/"

HEADROOM_URL = (
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
    "newsandreports/reports-publications/market-reports/"
    "foreign-headroom-main"
)

# Column mapping is purely positional (structural detection).
# The Tadawul headroom table layout is fixed:
#   Col 0 = Symbol | Col 1 = Company | Col 2 = Limit | Col 3 = Actual | Col 4 = Headroom

# Timeouts (ms)
NAV_TIMEOUT    = 60_000   # homepage navigation
TABLE_TIMEOUT  = 45_000   # wait for table rows after navigating to headroom page
AKAMAI_SETTLE  = 10_000   # time for Akamai JS challenge to complete


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("foreign_headroom")


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════════════

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS foreign_ownership_daily (
    date              TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    company_name      TEXT    NOT NULL,
    ownership_limit   REAL,
    actual_ownership  REAL,
    headroom          REAL,
    PRIMARY KEY (date, symbol)
);
"""

UPSERT_SQL = """
INSERT INTO foreign_ownership_daily
    (date, symbol, company_name, ownership_limit, actual_ownership, headroom)
VALUES
    (:date, :symbol, :company_name, :ownership_limit, :actual_ownership, :headroom)
ON CONFLICT(date, symbol) DO UPDATE SET
    company_name     = excluded.company_name,
    ownership_limit  = excluded.ownership_limit,
    actual_ownership = excluded.actual_ownership,
    headroom         = excluded.headroom;
"""


def init_db(db_path: str = DB_PATH) -> None:
    """Create the foreign_ownership_daily table if it doesn't exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(SCHEMA_SQL)
        conn.commit()
    log.info("DB ready at %s", db_path)


def upsert_rows(rows: list[dict[str, Any]], db_path: str = DB_PATH) -> int:
    """Idempotently insert/update ownership rows. Returns rows written."""
    if not rows:
        log.warning("No rows to upsert -- skipping DB write.")
        return 0
    with sqlite3.connect(db_path) as conn:
        conn.executemany(UPSERT_SQL, rows)
        conn.commit()
    log.info("Upserted %d row(s) into foreign_ownership_daily.", len(rows))
    return len(rows)


def print_db_summary(db_path: str = DB_PATH) -> None:
    """Print a quick summary of the DB contents."""
    with sqlite3.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM foreign_ownership_daily").fetchone()[0]
        dates = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM foreign_ownership_daily"
        ).fetchone()[0]
        symbols = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM foreign_ownership_daily"
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(date) FROM foreign_ownership_daily"
        ).fetchone()[0]

    log.info("--- DB Summary -------------------------------------------")
    log.info("  Total rows   : %s", f"{total:,}")
    log.info("  Unique dates : %d", dates)
    log.info("  Unique stocks: %d", symbols)
    log.info("  Latest date  : %s", latest or "N/A")
    log.info("----------------------------------------------------------")


# ══════════════════════════════════════════════════════════════════════════════
# DELTA ENGINE — daily change, accumulation detection, top movers
# ══════════════════════════════════════════════════════════════════════════════

def _load_ownership_history(db_path: str = DB_PATH) -> pd.DataFrame:
    """Load the full foreign_ownership_daily table into a DataFrame."""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT date, symbol, company_name, actual_ownership "
            "FROM foreign_ownership_daily "
            "WHERE actual_ownership IS NOT NULL "
            "ORDER BY date, symbol",
            conn,
        )
    return df


def compute_daily_delta(db_path: str = DB_PATH) -> pd.DataFrame | None:
    """
    Compare today's actual_ownership with the most recent previous date
    for each symbol.  Returns a DataFrame with columns:
        symbol, company_name, today_pct, prev_pct, delta, prev_date

    Returns None if fewer than 2 dates exist in the DB.
    """
    df = _load_ownership_history(db_path)
    if df.empty:
        log.info("[Delta] No ownership data in DB yet.")
        return None

    dates = sorted(df["date"].unique())
    if len(dates) < 2:
        log.info("[Delta] Only %d date(s) in DB -- need at least 2 for comparison.", len(dates))
        return None

    today_date = dates[-1]
    prev_date = dates[-2]

    today_df = df[df["date"] == today_date][["symbol", "company_name", "actual_ownership"]].copy()
    today_df = today_df.rename(columns={"actual_ownership": "today_pct"})

    prev_df = df[df["date"] == prev_date][["symbol", "actual_ownership"]].copy()
    prev_df = prev_df.rename(columns={"actual_ownership": "prev_pct"})

    merged = today_df.merge(prev_df, on="symbol", how="inner")
    merged["delta"] = (merged["today_pct"] - merged["prev_pct"]).round(4)
    merged["prev_date"] = prev_date

    log.info("[Delta] Computed deltas for %d symbols (%s vs %s).",
             len(merged), today_date, prev_date)
    return merged


def detect_accumulation(n_days: int = 3, db_path: str = DB_PATH) -> pd.DataFrame | None:
    """
    Identify stocks with a CONTINUOUS increase in actual_ownership for
    the last `n_days` consecutive trading days.

    Returns a DataFrame with columns:
        symbol, company_name, streak_start_pct, latest_pct, total_gain
    or None if not enough history.
    """
    df = _load_ownership_history(db_path)
    if df.empty:
        return None

    dates = sorted(df["date"].unique())
    if len(dates) < n_days:
        log.info("[Delta] Only %d date(s) in DB -- need %d for accumulation detection.",
                 len(dates), n_days)
        return None

    # Take the last n_days dates
    recent_dates = dates[-n_days:]
    recent = df[df["date"].isin(recent_dates)].copy()

    # Pivot: rows=symbol, columns=date, values=actual_ownership
    pivot = recent.pivot_table(
        index="symbol", columns="date", values="actual_ownership",
    )

    # Only keep symbols that have data for ALL n_days dates
    pivot = pivot.dropna()
    if pivot.empty:
        return None

    # Sort columns chronologically
    pivot = pivot[sorted(pivot.columns)]

    # Check for continuous increase: each day > previous day
    diffs = pivot.diff(axis=1).iloc[:, 1:]  # drop first NaN column
    accumulating_mask = (diffs > 0).all(axis=1)

    if not accumulating_mask.any():
        log.info("[Delta] No stocks with %d-day continuous accumulation.", n_days)
        return None

    acc_symbols = pivot.index[accumulating_mask].tolist()

    # Build result
    names = df[["symbol", "company_name"]].drop_duplicates().set_index("symbol")
    rows = []
    for sym in acc_symbols:
        vals = pivot.loc[sym]
        rows.append({
            "symbol": sym,
            "company_name": names.loc[sym, "company_name"] if sym in names.index else "",
            "streak_start_pct": round(vals.iloc[0], 4),
            "latest_pct": round(vals.iloc[-1], 4),
            "total_gain": round(vals.iloc[-1] - vals.iloc[0], 4),
        })

    result = pd.DataFrame(rows).sort_values("total_gain", ascending=False)
    log.info("[Delta] Found %d stock(s) with %d-day accumulation streak.",
             len(result), n_days)
    return result


def get_market_summary(delta_df: pd.DataFrame | None) -> dict[str, int]:
    """
    Quick stat: count of companies with foreign inflows (delta > 0)
    vs outflows (delta < 0) vs unchanged (delta == 0).
    """
    if delta_df is None or delta_df.empty:
        return {"inflows": 0, "outflows": 0, "unchanged": 0, "total": 0}

    return {
        "inflows":   int((delta_df["delta"] > 0).sum()),
        "outflows":  int((delta_df["delta"] < 0).sum()),
        "unchanged": int((delta_df["delta"] == 0).sum()),
        "total":     len(delta_df),
    }


def print_top_movers(delta_df: pd.DataFrame | None, top_n: int = 5) -> None:
    """Print Top N gainers and losers as Markdown tables to the console."""
    if delta_df is None or delta_df.empty:
        return

    # ── Top gainers ─────────────────────────────────────────────────────────
    gainers = (
        delta_df[delta_df["delta"] > 0]
        .nlargest(top_n, "delta")
        .reset_index(drop=True)
    )
    losers = (
        delta_df[delta_df["delta"] < 0]
        .nsmallest(top_n, "delta")
        .reset_index(drop=True)
    )

    def _md_table(title: str, df_sub: pd.DataFrame) -> str:
        lines = [f"\n### {title}\n"]
        lines.append("| # | Symbol | Company | Prev % | Today % | Delta |")
        lines.append("|---|--------|---------|-------:|--------:|------:|")
        if df_sub.empty:
            lines.append("| - | - | No movers | - | - | - |")
        else:
            for i, row in df_sub.iterrows():
                name = row["company_name"]
                if len(name) > 25:
                    name = name[:23] + ".."
                lines.append(
                    f"| {i + 1} | {row['symbol']} | {name} "
                    f"| {row['prev_pct']:.2f} | {row['today_pct']:.2f} "
                    f"| {row['delta']:+.4f} |"
                )
        return "\n".join(lines)

    print(_md_table(f"Top {top_n} Foreign Inflows (Accumulation)", gainers))
    print(_md_table(f"Top {top_n} Foreign Outflows (Distribution)", losers))


def run_delta_engine(db_path: str = DB_PATH) -> None:
    """
    Full Delta Engine run:
      1. Compute daily deltas
      2. Print market breadth summary
      3. Print top movers (Markdown tables)
      4. Detect and print accumulation streaks
    """
    log.info("")
    log.info("=" * 60)
    log.info("DELTA ENGINE -- Foreign Ownership Change Analysis")
    log.info("=" * 60)

    # 1. Daily delta
    delta_df = compute_daily_delta(db_path)
    if delta_df is None:
        log.info("[Delta] Not enough historical data yet. Run the scraper "
                 "for at least 2 trading days to enable delta analysis.")
        return

    # 2. Market breadth
    summary = get_market_summary(delta_df)
    log.info("")
    log.info("--- Market Breadth Summary -------------------------------")
    log.info("  Foreign Inflows  (ownership up)   : %d stocks", summary["inflows"])
    log.info("  Foreign Outflows (ownership down)  : %d stocks", summary["outflows"])
    log.info("  Unchanged                          : %d stocks", summary["unchanged"])
    log.info("  Total compared                     : %d stocks", summary["total"])
    log.info("----------------------------------------------------------")

    # 3. Top movers
    print_top_movers(delta_df)

    # 4. Accumulation detection
    acc_df = detect_accumulation(n_days=3, db_path=db_path)
    if acc_df is not None and not acc_df.empty:
        print(f"\n### 3-Day Continuous Accumulation ({len(acc_df)} stocks)\n")
        print("| # | Symbol | Company | Start % | Latest % | Total Gain |")
        print("|---|--------|---------|--------:|---------:|-----------:|")
        for i, row in acc_df.iterrows():
            name = row["company_name"]
            if len(name) > 25:
                name = name[:23] + ".."
            print(
                f"| {i + 1} | {row['symbol']} | {name} "
                f"| {row['streak_start_pct']:.2f} | {row['latest_pct']:.2f} "
                f"| {row['total_gain']:+.4f} |"
            )
    else:
        log.info("[Delta] No 3-day accumulation streaks detected.")

    log.info("")
    log.info("Delta Engine complete.")


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPER ENGINE — Playwright HEADED (Akamai bypass + JS rendering)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_headroom_page() -> str | None:
    """
    Launch Playwright in HEADED mode, pass the Akamai challenge via the
    homepage, then navigate to the headroom page and grab the fully-rendered
    HTML after the JS-populated table appears.

    HEADED MODE IS REQUIRED:
      Tadawul's Akamai Bot Manager detects headless Chromium and serves a
      395-byte challenge page with zero session cookies.  A visible browser
      window passes the challenge — proven in funds_scraper.py.

    Flow:
      1. Launch Chromium (headed, anti-automation stealth)
      2. Navigate to Tadawul homepage → triggers Akamai JS challenge
      3. Wait 10s for challenge completion + cookie setting
      4. Navigate to headroom page (now with valid session cookies)
      5. Wait for table rows to appear
      6. Return page.content()
    """
    log.info("Launching Playwright (HEADED — required for Akamai bypass)...")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,   # MUST be headed — headless gets blocked
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-http2",
                    "--no-sandbox",
                    "--window-size=1366,768",
                ],
            )
            context = browser.new_context(
                locale="en-US",
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )

            # Mask automation signals (same stealth as funds_scraper.py)
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;
                window.chrome = { runtime: { onConnect: { addListener: function() {} } } };
            """)

            page = context.new_page()

            # ── Step 1: Hit homepage to pass Akamai challenge ───────────
            log.info("Step 1/3: Navigating to Tadawul homepage (Akamai challenge)...")
            try:
                page.goto(TADAWUL_HOME, wait_until="networkidle", timeout=NAV_TIMEOUT)
            except PwTimeout:
                log.warning("Homepage networkidle timed out — continuing anyway.")
            except Exception as exc:
                log.warning("Homepage navigation: %s — continuing.", exc)

            log.info("Step 1/3: Waiting %ds for Akamai JS challenge...",
                     AKAMAI_SETTLE // 1000)
            page.wait_for_timeout(AKAMAI_SETTLE)

            # Log harvested cookies
            cookies = context.cookies()
            cookie_names = [c["name"] for c in cookies]
            session_keys = {"JSESSIONID", "TS01fdeb15"}
            found = [k for k in session_keys if k in cookie_names]
            log.info("Harvested %d cookies (session keys: %s).",
                     len(cookies), ", ".join(found) or "none")

            if not cookies:
                log.warning("Zero cookies harvested — Akamai may have blocked us.")

            # ── Step 2: Navigate to headroom page ───────────────────────
            log.info("Step 2/3: Navigating to headroom page...")
            try:
                page.goto(HEADROOM_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            except PwTimeout:
                log.warning("Headroom page domcontentloaded timed out — continuing.")
            except Exception as exc:
                log.warning("Headroom navigation: %s — continuing.", exc)

            # ── Step 3: Wait for table to populate ──────────────────────
            log.info("Step 3/3: Waiting for table data to load...")
            selectors = [
                "table tbody tr",
                "table tr td",
                "table tr:nth-child(2)",
            ]
            table_found = False
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, timeout=TABLE_TIMEOUT, state="attached")
                    table_found = True
                    log.info("Table selector matched: %s", sel)
                    break
                except PwTimeout:
                    log.info("Selector timed out: %s", sel)
                    continue

            if not table_found:
                log.info("No selector matched — waiting for networkidle...")
                try:
                    page.wait_for_load_state("networkidle", timeout=TABLE_TIMEOUT)
                except PwTimeout:
                    log.warning("networkidle timed out — grabbing HTML anyway.")

            # Extra settle for any final JS rendering
            page.wait_for_timeout(3000)

            html = page.content()
            log.info("Got rendered HTML: %d bytes.", len(html))

            # Save a debug dump on small responses (likely WAF block)
            if len(html) < 2000:
                dump_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "debug_headroom_dump.html",
                )
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(html)
                log.warning("HTML suspiciously small (%d bytes) — saved to %s",
                            len(html), dump_path)

            browser.close()
            return html

    except Exception as exc:
        log.error("Playwright failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_arabic(text: str) -> str:
    """
    Normalize Arabic text for reliable comparison.

    1. Strip ALL whitespace (fixes 'ة ك ر ش ل ا' -> 'ةكرشلا')
    2. Normalize Alif variants (أ إ آ ٱ) -> ا
    3. Normalize Ya variants (ي ى) -> ي
    4. Strip diacritics (tashkeel)
    5. Unicode NFKC normalization
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = re.sub(r"[\u064b-\u065f\u0640]", "", s)
    s = re.sub(r"[أإآٱ]", "ا", s)
    s = re.sub(r"[ىئ]", "ي", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _to_float(value: Any) -> float | None:
    """
    Aggressively coerce a table cell to a float.

    Strips EVERYTHING except digits, dots, and minus signs.
    Handles: percentages, thousands separators, Arabic-Indic digits,
    parenthesized negatives, disjointed/spaced characters, currency markers.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    s = str(value).strip()
    if not s or s in {"-", "--", "\u2014", "\u2013", "N/A", "n/a"}:
        return None

    # Strip % sign and currency markers
    s = s.replace("%", "").replace("\uff05", "")
    s = re.sub(r"(?i)(SAR|SR|ر\.?س)", "", s)

    # Parentheses -> negative
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]

    # Arabic-Indic digits -> ASCII
    arabic_digits = "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
    s = s.translate(str.maketrans(arabic_digits, "0123456789"))

    # Arabic decimal separator -> dot
    s = s.replace("\u066b", ".")

    # Collapse ALL whitespace (fixes "4 9 . 0 0" -> "49.00")
    s = s.replace(",", "").replace("\u00a0", "")
    s = re.sub(r"\s+", "", s)

    # Nuclear option: strip anything that isn't digit, dot, or minus
    s_clean = re.sub(r"[^\d.\-]", "", s)
    if not s_clean:
        return None

    # Guard against multiple dots: keep only the last as decimal
    parts = s_clean.split(".")
    if len(parts) > 2:
        s_clean = "".join(parts[:-1]) + "." + parts[-1]

    try:
        result = float(s_clean)
    except ValueError:
        return None
    return -result if negative else result


def _score_table_as_headroom(df: pd.DataFrame) -> float:
    """
    Score how likely a DataFrame is the Tadawul headroom table.

    Returns the fraction of Column-0 values that look like Tadawul stock
    symbols (4-5 digit numbers, whitespace-collapsed).  A score >= 0.3
    is a strong signal.
    """
    if df.empty or len(df.columns) < 2:
        return 0.0
    first_col = df.iloc[:, 0].astype(str).str.replace(r"\s+", "", regex=True)
    symbol_like = first_col.str.match(r"^\d{4,5}$", na=False)
    return symbol_like.sum() / len(df) if len(df) > 0 else 0.0


def _positional_mapping(df: pd.DataFrame) -> dict[str, str]:
    """Hard-coded positional mapping — the Tadawul headroom layout is fixed."""
    cols = list(df.columns)
    mapping = {"symbol": cols[0], "company_name": cols[1]}
    if len(cols) >= 3:
        mapping["ownership_limit"] = cols[2]
    if len(cols) >= 4:
        mapping["actual_ownership"] = cols[3]
    if len(cols) >= 5:
        mapping["headroom"] = cols[4]
    return mapping


def parse_headroom_table(html: str) -> pd.DataFrame | None:
    """
    Parse the Foreign Headroom page and return a cleaned DataFrame with columns:
        symbol, company_name, ownership_limit, actual_ownership, headroom

    Strategy — Zero-Strictness:
      1. pandas.read_html extracts every <table> on the page.
      2. Score EVERY table by what % of Column-0 values look like 4-5 digit
         Tadawul symbols.  The highest-scoring table with score >= 0.3 wins.
      3. If no table passes 0.3, force-accept the single/largest table if it
         has >= 5 columns.
      4. Apply hard-coded positional mapping — never rely on header text.
    """
    if not html:
        log.warning("Empty HTML -- nothing to parse.")
        return None

    # ── Step 1: extract all tables ──────────────────────────────────────────
    try:
        tables = pd.read_html(StringIO(html), flavor="lxml")
    except ValueError:
        log.error("pandas.read_html found no <table> elements.")
        return None
    except Exception as exc:
        log.error("pandas.read_html failed: %s", exc)
        return None

    log.info("read_html found %d table(s).", len(tables))

    if not tables:
        log.error("No tables extracted.")
        return None

    # ── Step 2: score every table ───────────────────────────────────────────
    scored: list[tuple[int, float, pd.DataFrame]] = []
    for idx, tbl in enumerate(tables):
        score = _score_table_as_headroom(tbl)
        log.info(
            "  Table #%d: %d rows x %d cols, symbol-score=%.0f%%, cols=%s",
            idx, len(tbl), len(tbl.columns), score * 100,
            [str(c)[:40] for c in tbl.columns],
        )
        scored.append((idx, score, tbl))

    # Sort by score descending, then by row count (largest first as tiebreak)
    scored.sort(key=lambda t: (t[1], len(t[2])), reverse=True)

    target_df: pd.DataFrame | None = None

    # Primary: take the best table with score >= 0.3
    if scored[0][1] >= 0.3:
        best_idx, best_score, target_df = scored[0]
        log.info(
            "LOCKED table #%d (symbol-score %.0f%%, %d rows).",
            best_idx, best_score * 100, len(target_df),
        )

    # Force-run fallback: largest table with >= 5 cols
    if target_df is None:
        for idx, score, tbl in scored:
            if len(tbl.columns) >= 5 and len(tbl) >= 5:
                target_df = tbl
                log.info(
                    "FORCE-ACCEPTED table #%d (%d rows x %d cols, "
                    "score %.0f%% -- only viable candidate).",
                    idx, len(tbl), len(tbl.columns), score * 100,
                )
                break

    if target_df is None:
        log.error("No viable table found on the page.")
        for idx, score, tbl in scored:
            log.info("  Table #%d: %d x %d, score=%.0f%%",
                     idx, len(tbl), len(tbl.columns), score * 100)
        return None

    # ── Step 3: hard-coded positional mapping ───────────────────────────────
    col_map = _positional_mapping(target_df)
    log.info("Positional mapping: %s", col_map)

    rename_map = {actual: canonical for canonical, actual in col_map.items()}
    df = target_df.rename(columns=rename_map)

    keep = [c for c in ["symbol", "company_name", "ownership_limit",
                         "actual_ownership", "headroom"] if c in df.columns]
    df = df[keep].copy()

    # ── Step 4: clean symbol column ─────────────────────────────────────────
    df["symbol"] = (
        df["symbol"].astype(str)
        .str.replace(r"\s+", "", regex=True)
        .str.strip()
    )
    df = df[df["symbol"].str.match(r"^\d{4,5}$", na=False)].copy()

    if df.empty:
        log.warning("All rows filtered out after symbol cleanup.")
        return None

    # Clean company_name
    if "company_name" in df.columns:
        df["company_name"] = df["company_name"].astype(str).str.strip()
    else:
        df["company_name"] = ""

    # Clean percentage columns -> float (aggressive _to_float)
    for col in ["ownership_limit", "actual_ownership", "headroom"]:
        if col in df.columns:
            df[col] = df[col].apply(_to_float)
        else:
            df[col] = None

    df = df.reset_index(drop=True)
    log.info("Parsed %d company rows.", len(df))
    return df


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    local_html: str | None = None,
    dry_run: bool = False,
    summary: bool = False,
    no_delta: bool = False,
) -> int:
    """
    End-to-end: fetch -> parse -> upsert -> delta analysis.

    Args:
        local_html: path to a saved HTML file (offline test mode)
        dry_run:    if True, parse only -- don't write to DB
        summary:    if True, print DB stats after run
        no_delta:   if True, skip the Delta Engine after upsert

    Returns: number of rows written (0 in dry-run mode).
    """
    log.info("=" * 60)
    log.info("Foreign Headroom Radar -- daily run (source: Tadawul HTML)")
    log.info("=" * 60)

    if not dry_run:
        init_db()

    # ── Fetch ───────────────────────────────────────────────────────────────
    if local_html:
        log.info("OFFLINE MODE -- reading %s", local_html)
        try:
            with open(local_html, "r", encoding="utf-8") as f:
                html = f.read()
        except OSError as exc:
            log.error("Cannot read %s: %s", local_html, exc)
            return 0
    else:
        html = fetch_headroom_page()
        if not html:
            log.error("Aborting: page unavailable.")
            return 0

    # ── Parse ───────────────────────────────────────────────────────────────
    df = parse_headroom_table(html)
    if df is None or df.empty:
        log.error("Aborting: no data parsed.")
        return 0

    # Today's date as the snapshot date
    today_iso = date.today().isoformat()

    # Quick stats for the log
    if "actual_ownership" in df.columns:
        non_null = df["actual_ownership"].notna()
        log.info(
            "Top 5 by actual ownership:\n%s",
            df.loc[non_null]
            .nlargest(5, "actual_ownership")[["symbol", "company_name", "actual_ownership"]]
            .to_string(index=False),
        )

    # ── Build row dicts ─────────────────────────────────────────────────────
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        rows.append({
            "date":             today_iso,
            "symbol":           r["symbol"],
            "company_name":     r.get("company_name", ""),
            "ownership_limit":  r.get("ownership_limit"),
            "actual_ownership": r.get("actual_ownership"),
            "headroom":         r.get("headroom"),
        })

    log.info("Prepared %d rows for date %s.", len(rows), today_iso)

    if dry_run:
        log.info("DRY RUN -- skipping DB write.")
        return 0

    # ── Upsert ──────────────────────────────────────────────────────────────
    written = upsert_rows(rows)
    log.info("Done. %d row(s) committed for %s.", written, today_iso)

    if summary:
        print_db_summary()

    # ── Delta Engine ────────────────────────────────────────────────────────
    if not no_delta:
        run_delta_engine()

    return written


# ══════════════════════════════════════════════════════════════════════════════
# DAEMON SCHEDULER — run daily at 16:30, skip weekends (Fri/Sat)
# ══════════════════════════════════════════════════════════════════════════════

# Tadawul weekend: Friday (4) and Saturday (5)
TADAWUL_WEEKEND = {4, 5}   # datetime.weekday(): Mon=0 … Sun=6
DAILY_RUN_TIME  = "16:30"  # HH:MM — after Saudi market close


def _next_run_dt(run_time: str = DAILY_RUN_TIME) -> datetime:
    """
    Compute the next valid run datetime, skipping Tadawul weekends.

    If today is a working day and the scheduled time hasn't passed yet,
    returns today at run_time.  Otherwise returns the next working day.
    """
    hour, minute = map(int, run_time.split(":"))
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If today's slot already passed, start from tomorrow
    if candidate <= now:
        candidate += timedelta(days=1)

    # Skip Tadawul weekends (Friday / Saturday)
    while candidate.weekday() in TADAWUL_WEEKEND:
        candidate += timedelta(days=1)

    return candidate


def run_daemon(summary: bool = False) -> None:
    """
    Persistent scheduler loop.

    Calculates the next valid run time (skipping Fri/Sat), sleeps until
    that moment, then executes the full pipeline.  Errors during a run
    are logged but never crash the daemon — it simply waits for the next
    scheduled slot.

    Press Ctrl+C to exit gracefully.
    """
    log.info("")
    log.info("=" * 60)
    log.info("DAEMON MODE — Foreign Headroom Radar")
    log.info("Scheduled daily at %s (skips Fri & Sat)", DAILY_RUN_TIME)
    log.info("Press Ctrl+C to stop.")
    log.info("=" * 60)

    while True:
        next_run = _next_run_dt()
        wait_seconds = (next_run - datetime.now()).total_seconds()

        log.info("")
        log.info("[WAITING] Next run scheduled for %s  (in %.0f min)",
                 next_run.strftime("%Y-%m-%d %H:%M"), wait_seconds / 60)

        # ── Sleep in short increments so Ctrl+C is responsive ──────────
        while True:
            remaining = (next_run - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))  # wake every 30s max

        # ── Execute the daily run ──────────────────────────────────────
        log.info("")
        log.info("[RUN] Starting scheduled scrape at %s",
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            written = run_pipeline(summary=summary)
            log.info("[RUN] Finished — %d row(s) committed.", written)
        except Exception:
            log.error("[RUN] Pipeline failed — will retry at next scheduled time.")
            log.error(traceback.format_exc())

        # Brief pause before computing next slot (avoids double-fire)
        time.sleep(5)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Foreign Headroom Radar -- scrape Tadawul foreign ownership data.",
    )
    parser.add_argument(
        "--html", metavar="PATH",
        help="Parse a local HTML file instead of fetching Tadawul (offline test).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and log results without writing to the database.",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print database statistics after the run.",
    )
    parser.add_argument(
        "--no-delta", action="store_true",
        help="Skip the Delta Engine analysis after scraping.",
    )
    parser.add_argument(
        "--delta-only", action="store_true",
        help="Run ONLY the Delta Engine on existing DB data (no scraping).",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Stay running and scrape automatically every day at 16:30 "
             "(skips Tadawul weekends: Friday & Saturday).",
    )
    args = parser.parse_args()

    try:
        # Daemon mode: persistent scheduler
        if args.daemon:
            run_daemon(summary=args.summary)
            return

        # Delta-only mode: skip scraping entirely
        if args.delta_only:
            run_delta_engine()
            return

        run_pipeline(
            local_html=args.html,
            dry_run=args.dry_run,
            summary=args.summary,
            no_delta=args.no_delta,
        )
    except KeyboardInterrupt:
        log.warning("\nInterrupted by user — exiting gracefully.")
        sys.exit(130)


if __name__ == "__main__":
    main()
