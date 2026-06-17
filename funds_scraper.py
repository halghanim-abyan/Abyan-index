"""
funds_scraper.py — Tadawul-focused curl_cffi scraper for Saudi Mutual Fund NAV.

Engine: curl_cffi with impersonate="chrome131" (Chrome TLS fingerprint).
Fallback: Playwright headed mode for Akamai cookie harvesting.

STRATEGIES (tried in order):

  Strategy 1 — Tadawul Hidden Profile (curl_cffi only — PRIMARY)
      The "hidden" WPS portal path bypasses the Akamai WAF entirely:
          /wps/portal/saudiexchange/hidden/company-profile-mutual-fund/
      Unlike the public /mutualfunds/mutualfundprofile path (which gets
      soft-redirected to the homepage), the hidden path serves the REAL
      mutual fund profile with server-side rendered data in <editableTable>.
      NAV Per Unit and Valuation Date are embedded in the raw HTML.

      WORKS for: 009003, 159002, 012063  (NAV is SSR in editableTable)

  Strategy 2 — Hybrid Cookie Injection (Playwright headed → curl_cffi)
      For funds where Strategy 1 returns NAV=0 (JS-populated only):
        a) Launch Playwright in HEADED mode (headless gets 0 cookies from
           Tadawul — Akamai detects headless and withholds cookies)
        b) Navigate to Tadawul homepage, wait for Akamai JS challenge
        c) Extract cookies (JSESSIONID, TS01*, BIGip*, GA, etc.)
        d) Inject into curl_cffi session with matching Sec-Ch-Ua headers
        e) Request public fund profile URL + hidden path
        f) Parse editableTable / portlet JSON from the response

      NOTE: Live testing shows even headed Playwright gets soft-redirected
      to the homepage on individual fund URLs.  This strategy is kept as
      a fallback for when Tadawul changes its WAF behavior.

  Strategy 3 — Riyad Capital (fund-manager website, for 010001)
      Server-side rendered HTML table at riyadcapital.com/fund-prices.

Usage:
    python funds_scraper.py                    # scrape all funds
    python funds_scraper.py --date 2026-04-01  # override date
    python funds_scraper.py --dry-run          # don't save to DB
    python funds_scraper.py --debug            # verbose logging
    python funds_scraper.py --fund 009003      # single fund
    python funds_scraper.py --no-playwright    # skip hybrid strategy
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import re
import sys
from datetime import date, datetime
from typing import Optional

from funds_db import get_connection, create_tables, DB_PATH, TARGET_FUNDS

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Eastern Arabic → Western digits ────────────────────────────────────────────
_EASTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# ══════════════════════════════════════════════════════════════════════════════
# POISON GUARDS
# ══════════════════════════════════════════════════════════════════════════════

POISON_NAVS: set[float] = {
    1516.53,    # MT30 index value
}
POISON_DATE = "1447-10-14"  # Hijri date from page header


# ══════════════════════════════════════════════════════════════════════════════
# URLS
# ══════════════════════════════════════════════════════════════════════════════

# Hidden portal path — bypasses Akamai WAF, serves real fund profiles
TADAWUL_HIDDEN_MF_BASE = (
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
    "hidden/company-profile-mutual-fund/!ut/p/z1/"
    "04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz83Y0DDAwC3QL8PM0DzYwNAo30"
    "I4EKzBEKDMKcTQzMDPxN3H19LAwNPEz1w8syU8v1wwkpK8hOMgUAof6qaw!!/"
)

# Public profile URLs — need Akamai cookies to work
TADAWUL_PUBLIC_URLS = [
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/mutualfunds/mutualfundprofile?symbol={symbol}",
    "https://www.saudiexchange.sa/wps/portal/saudiexchange/mutualfunds/mutual-fund-profile?symbol={symbol}",
]

# Tadawul homepage — used for Akamai cookie warmup
TADAWUL_HOME = "https://www.saudiexchange.sa/wps/portal/saudiexchange/home?locale=en"

# Riyad Capital fund prices
RIYADCAPITAL_URL = "https://www.riyadcapital.com/en/asset-management/fund-prices"


# ══════════════════════════════════════════════════════════════════════════════
# PER-FUND SOURCE CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

FUND_SOURCES: dict[str, list[dict]] = {
    "009003": [
        {"strategy": "tadawul_hidden"},
        {"strategy": "tadawul_hybrid"},
    ],
    "159002": [
        {"strategy": "tadawul_hidden"},
        {"strategy": "tadawul_hybrid"},
    ],
    "012063": [
        {"strategy": "tadawul_hidden"},
        {"strategy": "tadawul_hybrid"},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES — Price / Date cleaning
# ══════════════════════════════════════════════════════════════════════════════

def clean_nav(raw: str) -> Optional[float]:
    """Parse a NAV price string → float.  Rejects poison values."""
    if not raw:
        return None
    raw = raw.strip().translate(_EASTERN)
    raw = raw.replace(",", "").replace(" ", "")
    raw = re.sub(r"[A-Za-z%]+$", "", raw).strip()
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0 or val > 50_000:
        return None
    for poison in POISON_NAVS:
        if abs(val - poison) < 0.01:
            log.warning("  NAV rejected (poison): %.4f", val)
            return None
    return round(val, 6)


def clean_date(raw: str) -> Optional[str]:
    """Parse a date string → 'YYYY-MM-DD'.  Rejects Hijri dates."""
    if not raw:
        return None
    raw = raw.strip().translate(_EASTERN)
    if POISON_DATE in raw:
        return None
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y",
        "%d-%m-%Y", "%d %B %Y", "%B %d, %Y",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year < 2000 or dt.year > 2100:
                return None
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _is_homepage(html: str) -> bool:
    """Detect if Tadawul served homepage instead of fund profile."""
    title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
    if title and "home" in title.group(1).lower():
        return True
    if '"TCPI"' in html and "editableTable" not in html:
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# SESSION
# ══════════════════════════════════════════════════════════════════════════════

def create_session():
    """Create a curl_cffi session with Chrome TLS fingerprint."""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        log.error("curl_cffi not installed. Run: pip install curl_cffi")
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


# ══════════════════════════════════════════════════════════════════════════════
# PARSING — Extract NAV data from a fund profile page
# ══════════════════════════════════════════════════════════════════════════════

def _parse_fund_profile(html: str, symbol: str) -> Optional[dict]:
    """
    Extract NAV + date from a Tadawul mutual fund profile page.

    Data lives in:
      - <table id="editableTable">:  Name | NAV Per Unit columns
      - <span> near "VALUATION DATE": YYYY/MM/DD

    Returns {"nav_price": float, "date": str} or None.
    """
    # ── editableTable: extract NAV ────────────────────────────────────
    table_match = re.search(
        r'id=["\']editableTable["\'][^>]*>.*?<tbody>(.*?)</tbody>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not table_match:
        log.debug("  No editableTable found")
        return None

    tbody = table_match.group(1)

    # Extract <td data-field="level"> which holds the NAV
    level_match = re.search(
        r'data-field=["\']level["\'][^>]*>(.*?)</td>',
        tbody, re.DOTALL | re.IGNORECASE,
    )
    if level_match:
        nav_raw = re.sub(r"<[^>]+>", "", level_match.group(1)).strip()
    else:
        # Fallback: get all <td> cells
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tbody, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        cells = [c for c in cells if c and c != "0"]
        # Find the numeric cell (NAV)
        nav_raw = None
        for c in cells:
            if re.match(r"^\d+\.\d+$", c.replace(",", "")):
                nav_raw = c
                break
        if not nav_raw:
            log.debug("  editableTable has no valid NAV cells")
            return None

    nav_val = clean_nav(nav_raw)
    if not nav_val:
        log.debug("  editableTable NAV rejected: '%s'", nav_raw)
        return None

    # ── Valuation date ────────────────────────────────────────────────
    # Pattern: "VALUATION DATE" ... <span> YYYY/MM/DD </span>
    date_val = None

    # Method 1: Look near "VALUATION DATE" label
    val_match = re.search(
        r"VALUATION\s+DATE.*?<span>\s*(20\d{2}/\d{2}/\d{2})\s*</span>",
        html, re.DOTALL | re.IGNORECASE,
    )
    if val_match:
        date_val = clean_date(val_match.group(1))

    # Method 2: Find any YYYY/MM/DD in <span> tags
    if not date_val:
        date_spans = re.findall(r"<span>\s*(20\d{2}/\d{2}/\d{2})\s*</span>", html)
        for ds in date_spans:
            d = clean_date(ds)
            if d:
                date_val = d
                break

    return {"nav_price": nav_val, "date": date_val}


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: Tadawul Hidden Profile (curl_cffi only, no Akamai)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_tadawul_hidden(session, symbol: str, **_kw) -> Optional[dict]:
    """
    Fetch the mutual fund profile via the hidden WPS portal path.

    This path bypasses Akamai WAF entirely — the 'hidden' URL namespace
    is NOT protected by the bot manager JS challenge.  The page is ~700KB
    and contains server-side rendered NAV data in <editableTable> for most
    (but not all) mutual funds.

    Some funds return NAV=0 in the table, meaning the data is populated
    client-side by JavaScript.  In that case, we fall through to Strategy 2.
    """
    url = f"{TADAWUL_HIDDEN_MF_BASE}?selectedFund={symbol}"
    log.info("  [hidden] GET hidden profile for %s ...", symbol)

    try:
        r = session.get(url, timeout=30)
    except Exception as e:
        log.error("  [hidden] Request failed: %s", e)
        return None

    if r.status_code != 200:
        log.error("  [hidden] HTTP %d", r.status_code)
        return None

    html = r.text
    log.info("  [hidden] Page: %d bytes", len(html))

    # Verify we got a fund profile (not homepage)
    if _is_homepage(html):
        log.warning("  [hidden] Got homepage content — unexpected")
        return None

    if symbol not in html:
        log.warning("  [hidden] Symbol %s not in page — wrong profile", symbol)
        return None

    # Parse NAV data
    result = _parse_fund_profile(html, symbol)
    if not result:
        log.info("  [hidden] No NAV data extracted")
        return None

    if result["nav_price"]:
        result["source"] = "tadawul_hidden"
        log.info("  [hidden] NAV=%.4f  date=%s", result["nav_price"], result.get("date"))
        return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: Hybrid Cookie Injection (Playwright → curl_cffi)
# ══════════════════════════════════════════════════════════════════════════════

_akamai_cookies: Optional[dict] = None  # cached across funds


async def _harvest_akamai_cookies() -> dict[str, str]:
    """
    Launch Playwright in HEADED mode, visit Tadawul homepage, wait for the
    Akamai Bot Manager JS to execute, and return all cookies.

    IMPORTANT: Headless mode returns 0 cookies — Akamai detects headless
    Playwright and withholds all cookies.  Headed mode is required.

    Key cookies harvested:
      JSESSIONID  — WPS server session (portal state)
      TS01fdeb15  — F5/Akamai traffic management cookie
      BIGip*      — F5 load balancer persistence cookie
      _ga*        — Google Analytics (harmless but expected by WAF)
      RT          — Akamai mPulse real-user monitoring cookie
    """
    from playwright.async_api import async_playwright

    log.info("  [hybrid] Launching Playwright (headed) to harvest cookies ...")

    cookies_out: dict[str, str] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,   # MUST be headed — headless gets 0 cookies
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--no-sandbox",
                "--window-size=1366,768",
            ],
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        # Mask automation signals
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            delete navigator.__proto__.webdriver;
            window.chrome = { runtime: { onConnect: { addListener: function() {} } } };
        """)

        page = await context.new_page()

        # Navigate to homepage — triggers Akamai JS challenge
        log.info("  [hybrid] Navigating to Tadawul homepage ...")
        try:
            await page.goto(TADAWUL_HOME, wait_until="networkidle", timeout=60_000)
        except Exception as e:
            log.warning("  [hybrid] Homepage nav: %s (continuing)", e)

        # Wait for Akamai JS challenge to complete (typically 3-8 seconds)
        log.info("  [hybrid] Waiting 10s for Akamai JS challenge ...")
        await page.wait_for_timeout(10_000)

        # Extract ALL cookies
        browser_cookies = await context.cookies()
        for cookie in browser_cookies:
            cookies_out[cookie["name"]] = cookie["value"]

        await browser.close()

    session_keys = {"JSESSIONID", "TS01fdeb15",
                    "BIGipServerSaudiExchange.sa.app~SaudiExchange.sa_pool",
                    "com.ibm.wps.state.preprocessors.locale.LanguageCookie"}
    found = [k for k in session_keys if k in cookies_out]
    log.info("  [hybrid] Harvested %d cookies (session keys: %s)",
             len(cookies_out), ", ".join(found) or "none")

    return cookies_out


def _get_akamai_cookies() -> Optional[dict[str, str]]:
    """Get or cache Akamai cookies (launches Playwright once, reuses for all funds)."""
    global _akamai_cookies
    if _akamai_cookies is not None:
        return _akamai_cookies

    try:
        if platform.system() == "Windows":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        cookies = asyncio.run(_harvest_akamai_cookies())
        if cookies:
            _akamai_cookies = cookies
            return cookies
    except ImportError:
        log.error("  [hybrid] Playwright not installed. Run: pip install playwright && playwright install chromium")
    except Exception as e:
        log.error("  [hybrid] Cookie harvesting failed: %s", e)

    return None


def scrape_tadawul_hybrid(session, symbol: str, **_kw) -> Optional[dict]:
    """
    Hybrid strategy: use Playwright-harvested Akamai cookies with curl_cffi.

    1. Harvest cookies (once, cached for all funds)
    2. Create a new curl_cffi session with those cookies + matching headers
    3. Request the public fund profile URL
    4. Parse NAV from the response
    """
    log.info("  [hybrid] Trying hybrid cookie injection for %s ...", symbol)

    # ── Step 1: Get Akamai cookies ────────────────────────────────────
    cookies = _get_akamai_cookies()
    if not cookies:
        log.warning("  [hybrid] No cookies available — skipping")
        return None

    # ── Step 2: Create authenticated curl_cffi session ────────────────
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None

    auth_session = curl_requests.Session(impersonate="chrome131")

    # Inject Playwright cookies
    for name, value in cookies.items():
        auth_session.cookies.set(name, value, domain=".saudiexchange.sa")

    # Match headers to Playwright's Chrome UA exactly
    auth_session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8,"
                  "application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
                   "mutualfunds/mutualfundslist?locale=en",
    })

    # ── Step 3: Request public fund profile ───────────────────────────
    for url_template in TADAWUL_PUBLIC_URLS:
        url = url_template.format(symbol=symbol)
        log.info("  [hybrid] GET %s", url[:80] + "...")

        try:
            r = auth_session.get(url, timeout=30, allow_redirects=True)
        except Exception as e:
            log.error("  [hybrid] Request failed: %s", e)
            continue

        if r.status_code != 200:
            log.debug("  [hybrid] HTTP %d", r.status_code)
            continue

        html = r.text
        log.info("  [hybrid] Response: %d bytes", len(html))

        if _is_homepage(html):
            log.info("  [hybrid] Still got homepage — cookies may be expired")
            continue

        if symbol not in html:
            log.info("  [hybrid] Symbol not in response — wrong page")
            continue

        # ── Try parsing fund data from HTML ───────────────────────────
        result = _parse_fund_profile(html, symbol)
        if result and result.get("nav_price"):
            result["source"] = "tadawul_hybrid"
            log.info("  [hybrid] NAV=%.4f  date=%s", result["nav_price"], result.get("date"))
            return result

        # ── Try portlet endpoints with authenticated session ──────────
        portlets = _extract_portlet_urls(html)
        if portlets:
            log.info("  [hybrid] Found %d portlet URLs", len(portlets))
            cl = r.headers.get("content-location", "")
            portlet_base = ("https://www.saudiexchange.sa" + cl) if cl.startswith("/") else None

            # Get JWT
            jwt = None
            jwt_key = next((k for k in portlets if "JWT" in k.upper()), None)
            if jwt_key:
                jwt_url = portlets[jwt_key]
                if portlet_base and not jwt_url.startswith("http"):
                    jwt_url = portlet_base.rstrip("/") + "/" + jwt_url
                try:
                    jr = auth_session.get(jwt_url, timeout=10,
                                          headers={"X-Requested-With": "XMLHttpRequest"})
                    if jr.status_code == 200:
                        jd = json.loads(jr.text)
                        jwt = jd.get("jwtToken") or jd.get("token")
                except Exception:
                    pass

            # Try updatePriceBox and other portlets
            for key, purl in portlets.items():
                if "JWT" in key.upper() or "mfa" in key.lower() or "otp" in key.lower():
                    continue
                if portlet_base and not purl.startswith("http"):
                    purl = portlet_base.rstrip("/") + "/" + purl

                headers = {
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                }
                if jwt:
                    headers["Authorization"] = f"Bearer {jwt}"

                try:
                    pr = auth_session.get(purl, headers=headers, timeout=15)
                except Exception:
                    continue

                if pr.status_code != 200 or not pr.text.strip():
                    continue
                if len(pr.text) > 10_000:
                    continue  # Skip large HTML responses

                try:
                    pdata = json.loads(pr.text)
                except json.JSONDecodeError:
                    continue

                if isinstance(pdata, dict):
                    nav_raw = (pdata.get("navPrice") or pdata.get("unitPrice")
                               or pdata.get("lastTadePrice") or pdata.get("lastTradePrice"))
                    if nav_raw and str(nav_raw) not in ("-", "--", "N/A", "0", ""):
                        nav_val = clean_nav(str(nav_raw))
                        if nav_val:
                            date_raw = (pdata.get("valuationDate") or pdata.get("date")
                                        or pdata.get("lastTadeDate"))
                            date_val = clean_date(str(date_raw)) if date_raw else None
                            log.info("  [hybrid] Portlet %s: NAV=%.4f", key, nav_val)
                            return {
                                "nav_price": nav_val,
                                "date": date_val,
                                "source": "tadawul_hybrid",
                            }

        log.debug("  [hybrid] No NAV from %s", url[:60])

    # ── Step 4: Try hidden path with authenticated cookies ────────────
    hidden_url = f"{TADAWUL_HIDDEN_MF_BASE}?selectedFund={symbol}"
    log.info("  [hybrid] Trying hidden path with Akamai cookies ...")
    try:
        r = auth_session.get(hidden_url, timeout=30)
        if r.status_code == 200 and not _is_homepage(r.text):
            result = _parse_fund_profile(r.text, symbol)
            if result and result.get("nav_price"):
                result["source"] = "tadawul_hybrid"
                log.info("  [hybrid] Hidden+cookies: NAV=%.4f", result["nav_price"])
                return result
    except Exception as e:
        log.debug("  [hybrid] Hidden+cookies failed: %s", e)

    log.warning("  [hybrid] All hybrid attempts failed for %s", symbol)
    return None


def _extract_portlet_urls(html: str) -> dict[str, str]:
    """Extract NJ* portlet resource URLs from the fund profile HTML."""
    portlets: dict[str, str] = {}
    # Format 1: var NJname = 'url';
    for m in re.finditer(r"var\s+(NJ\w+)\s*=\s*['\"]([^'\"]+)['\"]", html):
        portlets[m.group(1)] = m.group(2)
    # Format 2: name = p0/IZ7_.../NJname=/
    for m in re.finditer(r"(\w+)\s*=\s*(p0/[A-Z0-9_]+=C[A-Z0-9_]+=NJ\w+=/)", html):
        portlets[m.group(1)] = m.group(2)
    return portlets


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: Riyad Capital — server-side HTML table
# ══════════════════════════════════════════════════════════════════════════════

def scrape_riyadcapital(session, symbol: str, fund_code: str, **_kw) -> Optional[dict]:
    """Scrape riyadcapital.com fund-prices page.  Server-side rendered."""
    log.info("  [riyadcapital] Fetching fund code=%s ...", fund_code)

    try:
        r = session.get(RIYADCAPITAL_URL, timeout=30)
    except Exception as e:
        log.error("  [riyadcapital] Request failed: %s", e)
        return None

    if r.status_code != 200:
        log.error("  [riyadcapital] HTTP %d", r.status_code)
        return None

    html = r.text
    log.info("  [riyadcapital] Page: %d bytes", len(html))

    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    strip = lambda s: re.sub(r"<[^>]+>", "", s).strip()

    for row_match in row_pattern.finditer(html):
        cells = cell_pattern.findall(row_match.group(1))
        if len(cells) < 6:
            continue
        code = strip(cells[0])
        if code.lstrip("0") != fund_code.lstrip("0") and code != fund_code:
            continue

        nav_val = clean_nav(strip(cells[3]))
        date_val = clean_date(strip(cells[2]))
        if not nav_val:
            return None

        log.info("  [riyadcapital] NAV=%.4f  date=%s", nav_val, date_val)
        return {"nav_price": nav_val, "date": date_val, "source": "riyadcapital"}

    log.warning("  [riyadcapital] Fund code '%s' not found", fund_code)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

_STRATEGIES = {
    "tadawul_hidden":  scrape_tadawul_hidden,
    "tadawul_hybrid":  scrape_tadawul_hybrid,
    "riyadcapital":    scrape_riyadcapital,
}


def scrape_fund(
    session,
    symbol: str,
    fund_name: str,
    override_date: Optional[str] = None,
    skip_playwright: bool = False,
) -> Optional[dict]:
    """Try all configured sources for a fund.  First success wins."""
    sources = FUND_SOURCES.get(symbol, [
        {"strategy": "tadawul_hidden"},
        {"strategy": "tadawul_hybrid"},
    ])

    for src in sources:
        strategy_name = src["strategy"]

        # Skip hybrid if --no-playwright
        if skip_playwright and strategy_name == "tadawul_hybrid":
            log.info("  Skipping %s (--no-playwright)", strategy_name)
            continue

        strategy_fn = _STRATEGIES.get(strategy_name)
        if not strategy_fn:
            log.error("  Unknown strategy: %s", strategy_name)
            continue

        log.info("  Strategy: %s", strategy_name)
        try:
            result = strategy_fn(session, symbol=symbol, **{
                k: v for k, v in src.items() if k != "strategy"
            })
        except Exception as e:
            log.error("  %s raised: %s", strategy_name, e)
            continue

        if result and result.get("nav_price"):
            final_date = result.get("date") or override_date
            if not final_date:
                log.warning("  %s returned NAV=%.4f but no date, and no --date override",
                            strategy_name, result["nav_price"])
                log.warning("  Skipping — cannot save without a valid date")
                continue

            return {
                "fund_name": fund_name,
                "nav_price": result["nav_price"],
                "date": final_date,
                "source": result.get("source", strategy_name),
            }

    return None


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def save_to_db(records: list[dict], db_path: str = DB_PATH) -> int:
    """Upsert records into nav_history.  Returns count of rows written."""
    conn = get_connection(db_path)
    try:
        create_tables(conn)
    except Exception:
        pass

    cursor = conn.cursor()
    saved = 0
    for rec in records:
        try:
            cursor.execute(
                """
                INSERT INTO nav_history (date, fund_name, nav_price)
                VALUES (?, ?, ?)
                ON CONFLICT(date, fund_name)
                    DO UPDATE SET nav_price = excluded.nav_price
                """,
                (rec["date"], rec["fund_name"], rec["nav_price"]),
            )
            saved += 1
        except Exception as e:
            log.error("  DB error for %s: %s", rec["fund_name"], e)

    conn.commit()
    conn.close()
    return saved


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Saudi Mutual Fund NAV data (curl_cffi + Playwright hybrid)",
    )
    parser.add_argument("--date", default=None, help="Override valuation date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--fund", default=None, help="Scrape only this fund symbol")
    parser.add_argument("--no-playwright", action="store_true",
                        help="Skip hybrid Playwright strategy (curl_cffi only)")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    override_date = args.date
    if override_date:
        try:
            datetime.strptime(override_date, "%Y-%m-%d")
        except ValueError:
            log.error("Invalid date format: %s (use YYYY-MM-DD)", override_date)
            sys.exit(1)

    log.info("=" * 60)
    log.info("Saudi Mutual Fund NAV Scraper")
    log.info("Engine: curl_cffi + Playwright hybrid")
    log.info("=" * 60)
    log.info("Date override : %s", override_date or "(use source date)")
    log.info("Database      : %s", args.db)
    log.info("Dry run       : %s", args.dry_run)
    log.info("Playwright    : %s", "disabled" if args.no_playwright else "enabled (fallback)")

    # ── Filter funds ─────────────────────────────────────────────────
    funds = TARGET_FUNDS
    if args.fund:
        funds = [f for f in funds if f["symbol"] == args.fund]
        if not funds:
            log.error("Fund symbol '%s' not found in TARGET_FUNDS", args.fund)
            sys.exit(1)

    log.info("Scraping %d fund(s):", len(funds))
    for f in funds:
        sources = FUND_SOURCES.get(f["symbol"], [{"strategy": "tadawul_hidden"}])
        primary = sources[0]["strategy"]
        log.info("  %s  %-35s  [%s]", f["symbol"], f["name"], primary)

    # ── Create session ───────────────────────────────────────────────
    session = create_session()
    log.info("curl_cffi session ready (impersonate=chrome131)")

    # ── Scrape each fund ─────────────────────────────────────────────
    results: list[dict] = []
    failures: list[str] = []

    for fund in funds:
        symbol = fund["symbol"]
        name = fund["name"]

        log.info("")
        log.info("-" * 50)
        log.info("Fund: %s (%s)", name, symbol)
        log.info("-" * 50)

        result = scrape_fund(session, symbol, name, override_date,
                             skip_playwright=args.no_playwright)

        if result:
            log.info("  >> NAV = %.6f  Date = %s  Source = %s",
                     result["nav_price"], result["date"], result["source"])
            results.append(result)
        else:
            log.error("  >> ALL STRATEGIES FAILED for %s (%s)", name, symbol)
            failures.append(f"{symbol} ({name})")

    # ── Summary ──────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("RESULTS SUMMARY")
    log.info("=" * 60)
    log.info("  Succeeded: %d / %d", len(results), len(funds))

    if results:
        log.info("")
        log.info("  %-35s  %12s  %-12s  %s", "Fund", "NAV", "Date", "Source")
        log.info("  %-35s  %12s  %-12s  %s", "-" * 35, "-" * 12, "-" * 12, "-" * 18)
        for r in results:
            log.info("  %-35s  %12.6f  %-12s  %s",
                     r["fund_name"][:35], r["nav_price"], r["date"], r["source"])

    if failures:
        log.info("")
        log.warning("  FAILED:")
        for f in failures:
            log.warning("    x %s", f)

    # ── Save to DB ───────────────────────────────────────────────────
    if results and not args.dry_run:
        saved = save_to_db(results, db_path=args.db)
        log.info("")
        log.info("Saved %d record(s) to %s", saved, args.db)
    elif args.dry_run:
        log.info("")
        log.info("Dry run -- no records saved.")

    if failures:
        sys.exit(1 if not results else 0)


if __name__ == "__main__":
    main()
