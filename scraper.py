"""
scraper.py — Async Playwright scraper for Saudi supermarket prices.

Each store uses a different scraping strategy:
  • Panda   → PLP landing + searchbar  (requires auth via --login)
  • Danube  → Direct search URL + Algolia results
  • Ninja   → Category-browse + scroll + title-match across grid

Title verification (is_title_match) protects CPI accuracy by rejecting
mismatched products.

PANDA AUTH (2026-03):
  Panda now requires phone-number login before showing any product results.
  We use Playwright's `storage_state` to persist cookies/localStorage after a
  one-time manual login.  Run `python scraper.py --login` to open a visible
  browser, log in manually, and save the auth state to `.runtime/panda_auth_state.json`
  unless `PANDA_AUTH_STATE_PATH` is set. Subsequent scraper runs load it.
"""

import asyncio
import random
import re
import logging
import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import requests
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html
from playwright.async_api import async_playwright, Page, TimeoutError as PwTimeout

from db_setup import get_connection, DB_PATH
from market_sources import (
    amazon_rule_for_item,
    noon_rule_for_item,
    official_price_rule_for_item_store,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# Path to the saved browser auth state (cookies + localStorage).
# Created by `python scraper.py --login`.  Loaded automatically if present.
PROJECT_DIR = Path(__file__).parent
LEGACY_AUTH_STATE_PATH = PROJECT_DIR / "auth_state.json"
DEFAULT_AUTH_STATE_PATH = PROJECT_DIR / ".runtime" / "panda_auth_state.json"
AUTH_STATE_PATH = Path(os.environ.get("PANDA_AUTH_STATE_PATH", DEFAULT_AUTH_STATE_PATH))

DEBUG_DIR = PROJECT_DIR / "debug_artifacts"
DEBUG_RETENTION_FILES = int(os.environ.get("SCRAPER_DEBUG_RETENTION", "50"))

SCRAPE_STATUS_OK = "ok"
SCRAPE_STATUS_OOS = "oos"
SCRAPE_STATUS_NOT_FOUND = "not_found"
SCRAPE_STATUS_TIMEOUT = "timeout"
SCRAPE_STATUS_ERROR = "error"
SCRAPE_STATUS_BLOCKED = "blocked"
VALID_SCRAPE_STATUSES = {
    SCRAPE_STATUS_OK,
    SCRAPE_STATUS_OOS,
    SCRAPE_STATUS_NOT_FOUND,
    SCRAPE_STATUS_TIMEOUT,
    SCRAPE_STATUS_ERROR,
    SCRAPE_STATUS_BLOCKED,
}

MATCH_TIER_EXACT = "exact"
MATCH_TIER_GASTAT_REPRESENTATIVE = "gastat_representative"
VALID_MATCH_TIERS = {
    MATCH_TIER_EXACT,
    MATCH_TIER_GASTAT_REPRESENTATIVE,
}


@dataclass(frozen=True)
class ExtractionResult:
    price: Optional[float]
    scrape_status: str
    failure_reason: Optional[str] = None
    match_tier: str = MATCH_TIER_EXACT
    observed_title: Optional[str] = None
    match_notes: Optional[str] = None


@dataclass(frozen=True)
class ScrapeResult:
    item_id: int
    store_name: str
    price: Optional[float]
    scrape_status: str
    failure_reason: Optional[str] = None
    match_tier: str = MATCH_TIER_EXACT
    observed_title: Optional[str] = None
    match_notes: Optional[str] = None


@dataclass(frozen=True)
class TitleMatchResult:
    matched: bool
    match_tier: str = MATCH_TIER_EXACT
    notes: Optional[str] = None


def _extracted_price(
    price: float,
    match_tier: str = MATCH_TIER_EXACT,
    observed_title: Optional[str] = None,
    match_notes: Optional[str] = None,
) -> ExtractionResult:
    if match_tier not in VALID_MATCH_TIERS:
        match_tier = MATCH_TIER_EXACT
    return ExtractionResult(
        price=price,
        scrape_status=SCRAPE_STATUS_OK,
        match_tier=match_tier,
        observed_title=observed_title,
        match_notes=match_notes,
    )


def _extraction_failure(status: str, reason: str) -> ExtractionResult:
    if status not in VALID_SCRAPE_STATUSES or status == SCRAPE_STATUS_OK:
        status = SCRAPE_STATUS_ERROR
    return ExtractionResult(price=None, scrape_status=status, failure_reason=reason)


def _scrape_result(
    item_id: int,
    store_name: str,
    price: Optional[float],
    scrape_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
    match_tier: Optional[str] = None,
    observed_title: Optional[str] = None,
    match_notes: Optional[str] = None,
) -> ScrapeResult:
    status = scrape_status or (SCRAPE_STATUS_OK if price is not None else SCRAPE_STATUS_NOT_FOUND)
    if status not in VALID_SCRAPE_STATUSES:
        status = SCRAPE_STATUS_ERROR
    tier = match_tier if match_tier in VALID_MATCH_TIERS else MATCH_TIER_EXACT
    if price is not None:
        status = SCRAPE_STATUS_OK
        failure_reason = None
    else:
        observed_title = None
        match_notes = None
    return ScrapeResult(
        item_id=item_id,
        store_name=store_name,
        price=price,
        scrape_status=status,
        failure_reason=failure_reason,
        match_tier=tier,
        observed_title=observed_title,
        match_notes=match_notes,
    )


def _save_daily_price(cursor, day: str, result: ScrapeResult, observed_at: str) -> None:
    cursor.execute(
        """
        INSERT INTO daily_prices (
            date, item_id, store_name, price, scrape_status, failure_reason,
            observed_at, match_tier, observed_title, match_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            day,
            result.item_id,
            result.store_name,
            result.price,
            result.scrape_status,
            result.failure_reason,
            observed_at,
            result.match_tier,
            result.observed_title,
            result.match_notes,
        ),
    )


def _auth_state_path() -> Path:
    """Return the preferred runtime auth path, with legacy fallback for old installs."""
    if AUTH_STATE_PATH.exists() or AUTH_STATE_PATH != DEFAULT_AUTH_STATE_PATH:
        return AUTH_STATE_PATH
    if LEGACY_AUTH_STATE_PATH.exists():
        return LEGACY_AUTH_STATE_PATH
    return AUTH_STATE_PATH


def _debug_file_path(store: str, tag: str, item_id: int, suffix: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    safe_store = re.sub(r"[^a-z0-9_-]+", "_", store.lower())
    safe_tag = re.sub(r"[^a-z0-9_-]+", "_", tag.lower())
    return DEBUG_DIR / f"debug_{safe_store}_{safe_tag}_{item_id}.{suffix}"


def _trim_debug_artifacts() -> None:
    if DEBUG_RETENTION_FILES <= 0 or not DEBUG_DIR.exists():
        return
    files = [p for p in DEBUG_DIR.iterdir() if p.is_file()]
    if len(files) <= DEBUG_RETENTION_FILES:
        return
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old_file in files[DEBUG_RETENTION_FILES:]:
        try:
            old_file.unlink()
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# STORE SELECTORS — targeting the FIRST product card on search / PLP results
# ══════════════════════════════════════════════════════════════════════════════
#
# For each store, define:
#   card   – the product grid container
#   title  – the product name of the FIRST card
#   price  – the current/sale price of the FIRST card (NOT the strikethrough)
#   oos    – (optional) out-of-stock badge inside the first card
#
# PANDA selectors confirmed via live DOM inspection (2026-03-25):
#   • Grid container:   div.grid.grid-cols-2
#   • Product card:     div.grid.grid-cols-2 > div
#   • Title (span):     span.font-bold.cursor-pointer.hover\:underline
#   • Regular price:    p.font-bold  (class contains "text-[12px]")
#   • Sale price:       p.font-bold.text-secondary  (red/highlighted)
#   • Struck-out price: p.line-through  (old price on discounted items)
#   • Category label:   span.font-bold.text-primary-light
#   • Size badge:       div.absolute.bottom-0.left-0
# ──────────────────────────────────────────────────────────────────────────────

STORE_SELECTORS: dict[str, dict[str, str]] = {
    "Panda": {
        # ── Confirmed live selectors (panda.sa, March 2026) ──────────────
        "card":      "div.grid.grid-cols-2 > div:first-child",
        "card_all":  "div.grid.grid-cols-2 > div",
        "card_title": "span.font-bold.cursor-pointer, span[class*='cursor-pointer'], img[alt]",
        "title":     "div.grid.grid-cols-2 > div:first-child span.font-bold.cursor-pointer",
        # Sale price takes precedence; fall back to regular price.
        "card_price_sale":    "p.font-bold.text-secondary, p[class*='text-secondary']",
        "card_price_regular": "p.font-bold:not(.line-through):not(.text-secondary)",
        "price_sale":         "div.grid.grid-cols-2 > div:first-child p.font-bold.text-secondary",
        "price_regular":      "div.grid.grid-cols-2 > div:first-child p.font-bold:not(.line-through):not(.text-secondary)",
        "oos":   "",
        # Panda requires a special scrape flow (see _scrape_panda below).
        "needs_searchbar": True,
        "scan_grid": True,
    },
    "Danube": {
        # ── Confirmed live selectors (danube.sa, March 2026) ───────────
        # Danube uses Algolia InstantSearch (ais-* classes).
        #
        # We now SCAN ALL result cards (not just :first-child) and pick the
        # first whose title passes verification — Algolia frequently returns
        # a wrong/bulk product as the first hit (this is what produced the
        # 509 SAR "Lusine bread" ghost). Scanning + title-match + guardrail
        # mirrors the robust Ninja strategy.
        "scan_grid":     True,
        "card_all":      ".ais-hits--item",          # every result card
        # Per-card RELATIVE selectors (queried inside each card element):
        "card_title":    ".product-box__name",
        "card_price_sale":    ".product-price--on-sale .product-price__current-price",
        "card_price_regular": ".product-price__current-price",
        # ── Legacy first-child selectors (kept as a fallback path) ──────
        "card":          ".ais-hits--item:first-child",
        "title":         ".ais-hits--item:first-child .product-box__name",
        "price_sale":    ".ais-hits--item:first-child .product-price--on-sale .product-price__current-price",
        "price_regular": ".ais-hits--item:first-child .product-price__current-price",
        "oos":           "",
        "needs_searchbar": False,
        # Danube shows a store-selection modal; we need to dismiss it.
        "needs_store_modal_dismiss": True,
    },
    "Ninja": {
        # ── Confirmed live selectors (ananinja.com, March 2026) ────────
        # Ninja has NO search URL.  We browse a category page and scan
        # ALL product cards for a title match.
        # Titles live in img[alt]; prices in <p> with Tailwind classes.
        # Grid: div.grid[class*="grid-cols-wrap"]
        # Card: direct children of the grid (each wraps an <a>)
        "grid":          "div.grid[class*='grid-cols-wrap']",
        "price":         "p.font-medium.text-gray-500",
        "oos":           "",
        "needs_category_browse": True,
    },
    "Tamimi": {
        "needs_tamimi_api": True,
    },
    "Noon": {
        "needs_noon_search": True,
    },
    "Amazon": {
        "needs_amazon_search": True,
    },
    "Saudi Aramco Retail Fuels": {
        "needs_official_price": True,
    },
    "GASCO Official LPG Tariff": {
        "needs_official_price": True,
    },
}


# ── Search URL templates (for stores that support direct search URLs) ────────
SEARCH_URL_TEMPLATES: dict[str, str] = {
    # Panda: direct search URLs redirect to homepage; use PLP + searchbar instead.
    "Panda":         "https://panda.sa/en/plp?category_id=311",  # any PLP as a landing pad
    "Danube":        "https://www.danube.sa/en/search?query={query}",
    # Ninja: no search URL — uses category pages directly. Template is just a fallback.
    "Ninja":         "https://ananinja.com/sa/ar/category/milk",
    "Tamimi":        "https://shop.tamimimarkets.com/api/layout/search?q={query}",
    "Noon":          "https://www.noon.com/saudi-en/search/?q={query}",
    "Amazon":        "https://www.amazon.sa/s?k={query}",
}


# ── Scraper tuning knobs ─────────────────────────────────────────────────────
PAGE_TIMEOUT_MS   = 60_000
NAV_TIMEOUT_MS    = 60_000
MAX_CONCURRENCY   = 3
HUMAN_DELAY_RANGE = (1.5, 4.0)
# Extra wait for JS-heavy pages after navigation (milliseconds).
JS_RENDER_WAIT_MS = 6_000


# ══════════════════════════════════════════════════════════════════════════════
# TITLE VERIFICATION — the heart of CPI accuracy
# ══════════════════════════════════════════════════════════════════════════════

# ── Bilingual translation dictionary ─────────────────────────────────────────
# Maps a normalised English keyword → set of equivalent strings (EN + AR).
# Used to expand English item names so they can match Arabic product titles.
_BILINGUAL: dict[str, set[str]] = {
    # ══════════════════════════════════════════════════════════════════════
    # BRANDS
    # ══════════════════════════════════════════════════════════════════════
    "almarai":      {"almarai", "المراعي"},
    "lusine":       {"lusine", "لوزين"},
    "saudia":       {"saudia", "السعودية"},
    "nescafe":      {"nescafe", "نسكافيه", "نسكافة"},
    "fairy":        {"fairy", "فيري"},
    "kass":         {"kass", "كاس"},
    "afia":         {"afia", "افيا", "عافية"},
    "alwataniah":   {"alwataniah", "الوطنية"},
    "americana":    {"americana", "امريكانا", "أمريكانا"},
    "goody":        {"goody", "قودي"},
    "majdi":        {"majdi", "مجدي"},
    "doha":         {"doha", "الدوحة"},
    "tayebat":      {"tayebat", "الطيبات"},
    "fakhama":      {"fakhama", "الفخامة"},
    "kuwaiti":      {"kuwaiti", "الكويتي"},
    "sunbullah":    {"sunbullah", "السنبلة"},
    "lurpak":       {"lurpak", "لورباك"},
    "wadi":         {"wadi", "الوادي"},
    "walima":       {"walima", "الوليمة", "وليمة"},
    "khair":        {"khair", "الخير"},
    "rabea":        {"rabea", "ربيع", "ربيعة"},
    "lipton":       {"lipton", "ليبتون"},
    "maatouk":      {"maatouk", "معتوق"},
    "bateel":       {"bateel", "بتيل"},
    "krinos":       {"krinos", "كرينوس"},
    "shifa":        {"shifa", "الشفاء"},
    "nova":         {"nova", "nove", "نوفا"},
    "berain":       {"berain", "بيرين"},
    "aquafina":     {"aquafina", "اكوافينا", "أكوافينا"},
    "dettol":       {"dettol", "ديتول"},
    "pantene":      {"pantene", "بانتين"},
    "colgate":      {"colgate", "كولجيت"},
    "always":       {"always", "اولويز", "أولويز"},
    "tide":         {"tide", "تايد"},
    "clorox":       {"clorox", "كلوركس"},
    "finish":       {"finish", "فنش", "فينش"},
    # NOTE: "al" and "abu" were removed — they are bare prefixes that appear
    # in nearly every Arabic product name and produced false-positive matches
    # (e.g. "Al Doha Red Lentils" matching unrelated items containing "ال").

    # ══════════════════════════════════════════════════════════════════════
    # 1. GRAINS & BREAD  (حبوب وخبز)
    # ══════════════════════════════════════════════════════════════════════
    "rice":         {"rice", "رز", "ارز", "أرز"},
    "basmati":      {"basmati", "بسمتي"},
    "bread":        {"bread", "خبز"},
    "sliced":       {"sliced", "شرائح", "توست"},
    "flour":        {"flour", "طحين", "دقيق"},

    # ══════════════════════════════════════════════════════════════════════
    # 2. MEAT  (لحوم)
    # ══════════════════════════════════════════════════════════════════════
    "chicken":      {"chicken", "دجاج"},
    "beef":         {"beef", "لحم", "لحم بقر"},
    "lamb":         {"lamb", "لحم غنم", "ضأن"},

    # ══════════════════════════════════════════════════════════════════════
    # 3. FISH & SEAFOOD  (أسماك ومأكولات بحرية)
    # ══════════════════════════════════════════════════════════════════════
    "shrimps":      {"shrimp", "shrimps", "روبيان", "جمبري", "قريدس"},
    "shrimp":       {"shrimp", "shrimps", "روبيان", "جمبري", "قريدس"},
    "fish":         {"fish", "سمك", "اسماك", "أسماك"},
    "tuna":         {"tuna", "تونة", "تونه"},
    "frozen":       {"frozen", "مجمد", "مجمدة"},

    # ══════════════════════════════════════════════════════════════════════
    # 4. DAIRY & EGGS  (ألبان وبيض)
    # ══════════════════════════════════════════════════════════════════════
    "milk":         {"milk", "حليب"},
    "eggs":         {"eggs", "egg", "بيض"},
    "cheese":       {"cheese", "جبن", "جبنة"},
    "yogurt":       {"yogurt", "yoghurt", "زبادي", "روب"},
    "butter":       {"butter", "زبدة"},

    # ══════════════════════════════════════════════════════════════════════
    # 5. OILS & FATS  (زيوت ودهون)
    # ══════════════════════════════════════════════════════════════════════
    "oil":          {"oil", "زيت"},
    "corn":         {"corn", "ذرة"},
    "sunflower":    {"sunflower", "دوار الشمس", "عباد الشمس"},

    # ══════════════════════════════════════════════════════════════════════
    # 6. FRUITS  (فواكه)
    # ══════════════════════════════════════════════════════════════════════
    "banana":       {"banana", "bananas", "موز"},
    "bananas":      {"banana", "bananas", "موز"},       # plural alias
    "apple":        {"apple", "apples", "تفاح"},
    "orange":       {"orange", "oranges", "برتقال"},
    "dates":        {"dates", "date", "تمر", "تمور"},

    # ══════════════════════════════════════════════════════════════════════
    # 7. VEGETABLES  (خضروات)
    # ══════════════════════════════════════════════════════════════════════
    "tomato":       {"tomato", "tomatoes", "طماطم"},
    "tomatoes":     {"tomato", "tomatoes", "طماطم"},
    "potato":       {"potato", "potatoes", "بطاطس"},
    "onion":        {"onion", "onions", "بصل"},
    "cucumber":     {"cucumber", "خيار"},

    # ══════════════════════════════════════════════════════════════════════
    # 8. SUGAR & SWEETS  (سكر وحلويات)
    # ══════════════════════════════════════════════════════════════════════
    "sugar":        {"sugar", "سكر"},

    # ══════════════════════════════════════════════════════════════════════
    # 9. BEVERAGES  (مشروبات)
    # ══════════════════════════════════════════════════════════════════════
    "coffee":       {"coffee", "قهوة"},
    "tea":          {"tea", "شاي"},
    "water":        {"water", "ماء", "مياه", "مويه"},
    "juice":        {"juice", "عصير"},

    # ══════════════════════════════════════════════════════════════════════
    # 10. CANNED FOOD  (أغذية معلبة)
    # ══════════════════════════════════════════════════════════════════════
    # "tuna" and "goody" already defined above.
    "canned":       {"canned", "معلب", "معلبة"},
    "light":        {"light", "لايت", "خفيف"},
    "meat":         {"meat", "لحم"},

    # ══════════════════════════════════════════════════════════════════════
    # 11. SPICES & NUTS  (توابل ومكسرات)
    # ══════════════════════════════════════════════════════════════════════
    "cardamom":     {"cardamom", "هيل", "هال", "حبهان"},
    "cinnamon":     {"cinnamon", "قرفة"},
    "black":        {"black", "اسود", "أسود"},
    "pepper":       {"pepper", "فلفل"},
    "nuts":         {"nuts", "مكسرات"},

    # ══════════════════════════════════════════════════════════════════════
    # 12. LEGUMES  (بقوليات)
    # ══════════════════════════════════════════════════════════════════════
    "lentils":      {"lentil", "lentils", "عدس"},
    "lentil":       {"lentil", "lentils", "عدس"},
    "red":          {"red", "احمر", "أحمر"},
    "chickpeas":    {"chickpea", "chickpeas", "حمص"},
    "beans":        {"bean", "beans", "فاصوليا", "فاصولياء"},
    "fava":         {"fava", "فول"},

    # ══════════════════════════════════════════════════════════════════════
    # DESCRIPTORS (shared across categories)
    # ══════════════════════════════════════════════════════════════════════
    "fresh":        {"fresh", "طازج", "طازجة"},
    "whole":        {"whole", "كامل"},
    "white":        {"white", "ابيض", "أبيض"},
    "fat":          {"fat", "دسم"},
    "full":         {"full", "كامل"},
    "local":        {"local", "محلي", "بلدي"},
    "classic":      {"classic", "كلاسيك"},

    # ── Household / Health (kept for future expansion) ───────────────────
    "soap":         {"soap", "صابون"},
    "dishwash":     {"dishwash", "dish", "غسيل", "جلي"},
    "liquid":       {"liquid", "سائل"},
    "detergent":    {"detergent", "منظف"},
}

_BILINGUAL.update({
    "pita": {"pita", "arabic bread"},
    "uht": {"uht", "long life", "long-life"},
    "triangles": {"triangles", "triangle"},
    "lemon": {"lemon", "lemons"},
    "lemons": {"lemon", "lemons"},
    "laban": {"laban"},
    "evaporated": {"evaporated"},
    "macaroni": {"macaroni", "macaroni pasta", "pasta", "fusilli", "elbow", "مكرونة", "معكرونة"},
    "spaghetti": {"spaghetti", "spaghetti pasta", "سباغيتي", "اسباجيتي", "سباجيتي", "اسباغتي"},
    "oats": {"oats", "oat", "شوفان"},
    "sweet": {"sweet"},
    "facial": {"facial", "وجه", "للوجه"},
    "tissue": {"tissue", "tissues", "مناديل", "منديل"},
    "sheets": {"sheet", "sheets"},
    "khalas": {"khalas"},
    "sukkari": {"sukkari", "sukkary"},
    "paste": {"paste"},
    "halawa": {"halawa", "halva"},
    "tahini": {"tahini", "tahina"},
    "minced": {"minced", "ground"},
    "instant": {"instant"},
    "bahar": {"bahar"},
    "bag": {"bag", "bags"},
    "bottled": {"bottled", "bottle"},
    "mineral": {"mineral"},
    "shampoo": {"shampoo"},
    "toothpaste": {"toothpaste"},
    "pads": {"pads", "pad"},
    "dishwasher": {"dishwasher"},
    "tablets": {"tablets", "tablet", "tabs"},
    "powder": {"powder"},
    "bleach": {"bleach"},
})
_BILINGUAL["dishwash"] = _BILINGUAL.get("dishwash", set()) | {"dishwashing"}


BRAND_TOKENS: set[str] = {
    "almarai", "lusine", "saudia", "nescafe", "fairy", "kass", "afia",
    "alwataniah", "americana", "goody", "majdi", "doha", "tayebat",
    "fakhama", "kuwaiti", "sunbullah", "lurpak", "wadi", "walima",
    "khair", "rabea", "lipton", "maatouk", "bateel", "krinos", "shifa",
    "nova", "berain", "aquafina", "dettol", "pantene", "colgate",
    "always", "tide", "clorox", "finish",
}


_EASTERN_TO_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize(text: str) -> str:
    """
    Lowercase, strip diacritics/tashkeel, collapse whitespace,
    remove non-alphanumeric chars (except digits, dots, and Arabic letters),
    and translate Eastern Arabic numerals (٠-٩) to Western digits (0-9).
    This lets us compare Arabic & English product names reliably.
    """
    # Eastern Arabic numerals → Western (e.g. ١٠٠٠ → 1000)
    text = text.translate(_EASTERN_TO_WESTERN)
    text = text.replace("×", "x")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Keep word chars (\w includes Arabic via Unicode), digits, dots, spaces
    text = re.sub(r"[^\w\d.\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Size regex — now recognises both Latin and Arabic units ───────────────────
# Latin:  l, ml, kg, g, pcs, pc, p, tabs, pads, pieces, litre, liter
# Arabic: لتر (liter), مل (ml), كجم/كيلو (kg), جرام/غرام/جم (g), حبة/قطعة (pcs)
_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)"           # numeric part: 2, 1.5, 500 …
    r"\s*"                        # optional whitespace
    r"("
    r"l|ml|kg|g|pcs|pc|packs?|tabs?|tablets?|pads?|sheets?|pieces?|piece|p|litre|liter"   # Latin units
    r"|لتر|مل"                        # Arabic: liter, milliliter
    r"|كجم|كغم|كيلو|كيلوجرام"          # Arabic: kilogram (كغم = Danube variant)
    r"|جرام|غرام|جم|غم"               # Arabic: gram (غم = Danube variant)
    r"|حبة|حبات|قطعة|قطع"             # Arabic: pieces
    r"|منديل|مناديل"
    r"|لتر|مل|كجم|كغم|كيلو|كيلوجرام"
    r"|جرام|غرام|جم|غم"
    r"|حبة|حبات|قطعة|قطع|عبوة|عبوات|علبة|علب"
    r")",
    re.IGNORECASE,
)

# Map Arabic units to their canonical Latin form for comparison.
_UNIT_CANONICAL: dict[str, str] = {
    "لتر": "l", "مل": "ml",
    "كجم": "kg", "كغم": "kg", "كيلو": "kg", "كيلوجرام": "kg",
    "جرام": "g", "غرام": "g", "جم": "g", "غم": "g",
    "حبة": "pcs", "حبات": "pcs", "قطعة": "pcs", "قطع": "pcs",
    "عبوة": "pcs", "عبوات": "pcs", "علبة": "pcs", "علب": "pcs",
    "l": "l", "litre": "l", "liter": "l", "لتر": "l",
    "ml": "ml", "مل": "ml",
    "kg": "kg", "كجم": "kg", "كغم": "kg", "كيلو": "kg", "كيلوجرام": "kg",
    "g": "g", "جرام": "g", "غرام": "g", "جم": "g", "غم": "g",
    "pcs": "pcs", "pc": "pcs", "p": "pcs", "pack": "pcs", "packs": "pcs", "tab": "pcs", "tabs": "pcs",
    "tablet": "pcs", "tablets": "pcs", "pad": "pcs", "pads": "pcs",
    "sheet": "pcs", "sheets": "pcs",
    "منديل": "pcs", "مناديل": "pcs",
    "piece": "pcs", "pieces": "pcs", "حبة": "pcs", "حبات": "pcs",
    "قطعة": "pcs", "قطع": "pcs",
}


def _extract_sizes(text: str) -> set[str]:
    """
    Pull out all size tokens, normalising Arabic units to Latin canonical form.
    E.g. '2 لتر' → {'2l'}, '5kg' → {'5kg'}, '30حبة' → {'30pcs'}.
    """
    sizes: set[str] = set()
    for m in _SIZE_RE.finditer(text):
        number = m.group(1)
        unit   = _UNIT_CANONICAL.get(m.group(2).lower(), m.group(2).lower())
        sizes.add(f"{number}{unit}")
    for m in re.finditer(r"\bpack\s+of\s+(\d+)\b", text):
        sizes.add(f"{m.group(1)}pcs")
    for m in re.finditer(r"\b(\d+)\s*[x]\s*\d+(?:\.\d+)?\s*(?:ml|l|kg|g)\b", text):
        sizes.add(f"{m.group(1)}pcs")
    return sizes


# ── Filler / descriptor words ────────────────────────────────────────────────
# These appear in OUR bureaucratic item names (or the store's) but are NOT
# distinctive product identifiers. They must NOT gate a title match: e.g. our
# "Local Tomatoes 1kg" should still match the store's "Tomatoes Pre Pack 1kg".
# Bilingual (EN + AR). Brand and core-noun tokens are deliberately NOT here.
_FILLER_WORDS: set[str] = {
    # origin / quality descriptors
    "local", "fresh", "premium", "pure", "natural", "original", "classic",
    "value", "family", "economy", "imported", "selected", "choice", "extra",
    "special", "best", "new", "saudi", "arabic",
    "محلي", "بلدي", "طازج", "طازجة", "فاخر", "فاخرة", "نقي", "نقية", "طبيعي",
    "طبيعية", "اصلي", "أصلي", "كلاسيك", "ممتاز", "ممتازة", "مستورد", "سعودي",
    # pack / format fillers
    "pack", "prepack", "pre", "packed", "piece", "pieces", "pcs", "ct",
    "بكج", "عبوة", "حبة", "قطعة", "شريحة",
    # common varietal adjectives that vary by store
    "indian", "white", "red", "long", "grain", "short", "whole", "skimmed",
    "هندي", "ابيض", "أبيض", "احمر", "أحمر", "كامل", "طويل",
}

# Fraction of CORE (non-filler) expected tokens that must appear in the scraped
# title for a match. Looser than the old 70 %-of-ALL-tokens rule because filler
# words are excluded from the denominator entirely.
CORE_MATCH_RATIO: float = 0.60

BULK_1KG_TERMS: set[str] = {
    "onion", "onions", "potato", "potatoes", "cucumber", "cucumbers",
    "apple", "apples", "orange", "oranges", "lemon", "lemons",
    "hamour", "fish", "beef",
}

PROCESSED_MEAT_TERMS: set[str] = {
    "mort", "mortadella", "sausage", "salami", "burger", "kebab", "kofta",
    "nugget", "nuggets", "meatball", "meatballs", "pepperoni",
}


def _allow_missing_bulk_kg_size(
    norm_expected: str,
    norm_scraped: str,
    expected_sizes: set[str],
    scraped_sizes: set[str],
) -> bool:
    if expected_sizes != {"1kg"} or scraped_sizes:
        return False
    return any(term in norm_expected and term in norm_scraped for term in BULK_1KG_TERMS)


def _has_piece_pack_size(sizes: set[str]) -> bool:
    return any(size.endswith("pcs") for size in sizes)


def _piece_pack_sizes(sizes: set[str]) -> set[str]:
    return {size for size in sizes if size.endswith("pcs")}


def is_title_match(expected_name: str, scraped_title: str) -> bool:
    """
    Decide whether the scraped search result is the same product we intended.

    TOKEN-INTERSECTION strategy (fuzzy, filler-tolerant):
      1. Size / quantity tokens MUST still match exactly (2L ≠ 1L, 5kg ≠ 1kg).
         This is the one hard gate — wrong size = wrong product.
      2. Split the expected keywords into CORE (distinctive: brand + product
         noun) and FILLER (origin/quality/format descriptors like "Local",
         "Fresh", "Pre Pack"). Filler is ignored entirely.
      3. Accept when at least CORE_MATCH_RATIO of the CORE tokens (expanded
         bilingually) appear ANYWHERE in the scraped title. Extra words in the
         scraped title never hurt.

    Example: expected "Local Tomatoes 1kg" → core={tomatoes}, size={1kg}.
    Scraped "Tomatoes Pre Pack 1kg" → size 1kg matches, "tomatoes" present
    → ACCEPT (the old 70 %-of-all rule rejected this because "local" was
    missing from the scraped title).

    Returns True if the product is acceptable, False to reject.
    """
    norm_expected = _normalize(expected_name)
    norm_scraped  = _normalize(scraped_title)

    # ── Rule 1 (HARD GATE): size tokens must match ──────────────────────
    expected_sizes = _extract_sizes(norm_expected)
    scraped_sizes  = _extract_sizes(norm_scraped)
    if expected_sizes and not expected_sizes.issubset(scraped_sizes):
        if not _allow_missing_bulk_kg_size(norm_expected, norm_scraped, expected_sizes, scraped_sizes):
            return False
    if _has_piece_pack_size(scraped_sizes) and not _has_piece_pack_size(expected_sizes):
        return False
    expected_piece_sizes = _piece_pack_sizes(expected_sizes)
    scraped_piece_sizes = _piece_pack_sizes(scraped_sizes)
    if expected_piece_sizes and (scraped_piece_sizes - expected_piece_sizes):
        return False

    if "beef" in norm_expected and any(term in norm_scraped for term in PROCESSED_MEAT_TERMS):
        return False
    if (
        any(term in norm_expected.split() for term in ("potato", "potatoes"))
        and re.search(r"(?<!\w)sweet(?!\w)", norm_scraped)
    ):
        return False

    # Brand-bearing basket items must match the same brand. Without this gate,
    # generic nouns like "dish liquid" or "cheese" can incorrectly match a
    # different brand that happens to share the same size.
    expected_brand_tokens = [w for w in norm_expected.split() if w in BRAND_TOKENS]
    for brand in expected_brand_tokens:
        expanded = {_normalize(variant) for variant in _BILINGUAL.get(brand, {brand})}
        if not any(variant and variant in norm_scraped for variant in expanded):
            return False

    # ── Tokenise: drop stopwords, pure numbers, and size-unit tokens ────
    stop = {"the", "of", "in", "a", "an", "and", "or", "من", "في", "ال"}
    words = [w for w in norm_expected.split() if w not in stop and len(w) > 1]
    words = [w for w in words if not re.fullmatch(r"\d+(?:\.\d+)?", w)]
    words = [w for w in words if not _SIZE_RE.fullmatch(w)]

    # ── Rule 2: split into CORE vs FILLER; score only the CORE tokens ───
    core = [w for w in words if w not in _FILLER_WORDS]
    if not core:
        # The name was all filler + size (rare) — size already matched.
        return True

    matches = 0
    for word in core:
        expanded = _BILINGUAL.get(word, {word})   # EN ↔ AR expansion
        if any(variant in norm_scraped for variant in expanded):
            matches += 1

    return (matches / len(core)) >= CORE_MATCH_RATIO


@dataclass(frozen=True)
class RepresentativeSubstituteRule:
    required_terms: tuple[str, ...]
    required_sizes: tuple[str, ...] = ()
    any_terms: tuple[str, ...] = ()
    reject_terms: tuple[str, ...] = ()
    size_tolerance_pct: float = 0.0
    allow_missing_bulk_kg_size: bool = False
    notes: str = "GASTAT representative substitute: same item class and comparable unit/pack"


REPRESENTATIVE_SUBSTITUTES: dict[str, RepresentativeSubstituteRule] = {
    "Alwataniah Chicken 1000g": RepresentativeSubstituteRule(
        required_terms=("chicken",),
        required_sizes=("1000g",),
        reject_terms=tuple(PROCESSED_MEAT_TERMS),
    ),
    "Saudia White Sugar 1kg": RepresentativeSubstituteRule(
        required_terms=("sugar",),
        required_sizes=("1kg",),
        reject_terms=("free", "cake", "mix", "brown", "diet", "zero"),
    ),
    "Nescafe Classic 200g": RepresentativeSubstituteRule(
        required_terms=("coffee",),
        required_sizes=("200g",),
        any_terms=("instant", "classic"),
        reject_terms=("capsule", "creamer", "mate", "3in1"),
    ),
    "Majdi Cardamom 50g": RepresentativeSubstituteRule(
        required_terms=("cardamom",),
        required_sizes=("50g",),
    ),
    "Al Doha Red Lentils 1kg": RepresentativeSubstituteRule(
        required_terms=("red", "lentils"),
        required_sizes=("1kg",),
    ),
    "Al Tayebat Arabic Pita Bread 6P": RepresentativeSubstituteRule(
        required_terms=("bread",),
        required_sizes=("6pcs",),
        any_terms=("pita", "arabic"),
        reject_terms=("sliced", "toast"),
    ),
    "Almarai Cheddar Cheese Triangles 8P": RepresentativeSubstituteRule(
        required_terms=("cheese", "triangles"),
        required_sizes=("8pcs",),
    ),
    "Al Fakhama Tomato Paste 8x135g": RepresentativeSubstituteRule(
        required_terms=("tomato", "paste"),
        required_sizes=("8pcs", "135g"),
    ),
    "Sunbullah Frozen Minced Meat 400g": RepresentativeSubstituteRule(
        required_terms=("minced", "meat"),
        required_sizes=("400g",),
        reject_terms=tuple(PROCESSED_MEAT_TERMS),
    ),
    "Almarai Brown Eggs 30P": RepresentativeSubstituteRule(
        required_terms=("eggs",),
        required_sizes=("30pcs",),
        reject_terms=("quail",),
    ),
    "Local Fresh Cucumbers 1kg": RepresentativeSubstituteRule(
        required_terms=("cucumber",),
        required_sizes=("1kg",),
        allow_missing_bulk_kg_size=True,
    ),
    "Al Wadi Al Akhdar Cooked Chickpeas 1kg": RepresentativeSubstituteRule(
        required_terms=("chickpeas",),
        required_sizes=("1kg",),
        reject_terms=("hummus", "spread", "dip"),
    ),
    "Saudia Long-Life UHT Milk 1L": RepresentativeSubstituteRule(
        required_terms=("milk",),
        required_sizes=("1l",),
        any_terms=("uht", "long"),
        reject_terms=("powder", "flavoured", "flavored", "chocolate", "strawberry", "evaporated"),
    ),
    "Al Khair Saudi Coffee Bahar 250g": RepresentativeSubstituteRule(
        required_terms=("coffee",),
        required_sizes=("250g",),
        any_terms=("saudi", "arabic", "bahar"),
        reject_terms=("capsule", "instant"),
    ),
    "Lipton Yellow Label Tea Bags 100P": RepresentativeSubstituteRule(
        required_terms=("tea",),
        required_sizes=("100pcs",),
        any_terms=("bag",),
        reject_terms=("green", "herbal", "chamomile"),
    ),
    "Bateel Khalas Dates 1kg": RepresentativeSubstituteRule(
        required_terms=("dates", "khalas"),
        required_sizes=("1kg",),
    ),
    "Sukkari Premium Dates 1kg": RepresentativeSubstituteRule(
        required_terms=("dates", "sukkari"),
        required_sizes=("1kg",),
    ),
    "Krinos Halawa Tahini 500g": RepresentativeSubstituteRule(
        required_terms=("halawa",),
        required_sizes=("500g",),
        any_terms=("tahini",),
    ),
    "Berain Bottled Water 600ml 12P": RepresentativeSubstituteRule(
        required_terms=("water",),
        required_sizes=("600ml", "12pcs"),
        reject_terms=("sparkling", "flavoured", "flavored"),
    ),
    "Aquafina Mineral Water 330ml 12P": RepresentativeSubstituteRule(
        required_terms=("water",),
        required_sizes=("330ml", "12pcs"),
        reject_terms=("sparkling", "flavoured", "flavored"),
    ),
    "Pantene Pro-V Classic Shampoo 700ml": RepresentativeSubstituteRule(
        required_terms=("shampoo",),
        required_sizes=("700ml",),
        reject_terms=("conditioner", "cream", "mask"),
    ),
    "Colgate Total Toothpaste 100ml": RepresentativeSubstituteRule(
        required_terms=("toothpaste",),
        required_sizes=("100ml",),
        reject_terms=("brush", "mouthwash"),
    ),
    "Always Cotton Soft Pads 16P": RepresentativeSubstituteRule(
        required_terms=("pads",),
        required_sizes=("16pcs",),
        reject_terms=("diaper", "diapers", "baby"),
    ),
    "Fairy Original Dish Liquid 750ml": RepresentativeSubstituteRule(
        required_terms=("dishwash", "liquid"),
        required_sizes=("750ml",),
        size_tolerance_pct=0.05,
    ),
    "Clorox Original Bleach 950ml": RepresentativeSubstituteRule(
        required_terms=("bleach",),
        required_sizes=("950ml",),
        size_tolerance_pct=0.05,
        reject_terms=("spray", "wipes"),
    ),
    "Finish Quantum Dishwasher Tablets 32P": RepresentativeSubstituteRule(
        required_terms=("dishwasher", "tablets"),
        required_sizes=("32pcs",),
        reject_terms=("gel", "liquid"),
    ),
    "Local Naemi Fresh Lamb 1kg": RepresentativeSubstituteRule(
        required_terms=("lamb",),
        required_sizes=("1kg",),
        reject_terms=tuple(PROCESSED_MEAT_TERMS | {"frozen"}),
        allow_missing_bulk_kg_size=True,
    ),
    "Fresh Norwegian Salmon Fillet 500g": RepresentativeSubstituteRule(
        required_terms=("salmon", "fillet"),
        required_sizes=("500g",),
        size_tolerance_pct=0.08,
        reject_terms=("smoked",),
    ),
    "Local Apples 1kg": RepresentativeSubstituteRule(
        required_terms=("apple",),
        required_sizes=("1kg",),
        allow_missing_bulk_kg_size=True,
    ),
    "Local Oranges 1kg": RepresentativeSubstituteRule(
        required_terms=("orange",),
        required_sizes=("1kg",),
        allow_missing_bulk_kg_size=True,
    ),
    "Local Lemons 1kg": RepresentativeSubstituteRule(
        required_terms=("lemon",),
        required_sizes=("1kg",),
        allow_missing_bulk_kg_size=True,
    ),
    "Fresh Laban 1L": RepresentativeSubstituteRule(
        required_terms=("laban",),
        required_sizes=("1l",),
        reject_terms=("flavoured", "flavored", "mango", "strawberry"),
    ),
    "Evaporated Milk 170g": RepresentativeSubstituteRule(
        required_terms=("evaporated", "milk"),
        required_sizes=("170g",),
        reject_terms=("powder", "condensed"),
    ),
    "Macaroni 500g": RepresentativeSubstituteRule(
        required_terms=("macaroni",),
        required_sizes=("500g",),
    ),
    "Spaghetti 500g": RepresentativeSubstituteRule(
        required_terms=("spaghetti",),
        required_sizes=("500g",),
    ),
    "Oats 500g": RepresentativeSubstituteRule(
        required_terms=("oats",),
        required_sizes=("500g",),
        reject_terms=("cookies", "biscuits"),
    ),
    "Canned Sweet Corn 340g": RepresentativeSubstituteRule(
        required_terms=("corn",),
        required_sizes=("340g",),
        any_terms=("sweet",),
        reject_terms=("popcorn",),
    ),
    "Facial Tissue 200 Sheets": RepresentativeSubstituteRule(
        required_terms=("tissue",),
        required_sizes=("200pcs",),
        any_terms=("facial",),
        reject_terms=("wipes", "wet", "kitchen", "towel"),
    ),
}


REPRESENTATIVE_SEARCH_QUERIES: dict[str, str] = {
    "Alwataniah Chicken 1000g": "Chicken 1000g",
    "Saudia White Sugar 1kg": "White Sugar 1kg",
    "Nescafe Classic 200g": "Instant Coffee 200g",
    "Majdi Cardamom 50g": "Cardamom 50g",
    "Al Doha Red Lentils 1kg": "Red Lentils 1kg",
    "Al Tayebat Arabic Pita Bread 6P": "Arabic Pita Bread 6 pcs",
    "Almarai Cheddar Cheese Triangles 8P": "Cheese Triangles 8 pcs",
    "Al Fakhama Tomato Paste 8x135g": "Tomato Paste 8x135g",
    "Sunbullah Frozen Minced Meat 400g": "Frozen Minced Meat 400g",
    "Almarai Brown Eggs 30P": "Eggs 30pcs",
    "Local Fresh Cucumbers 1kg": "Cucumber 1kg",
    "Al Wadi Al Akhdar Cooked Chickpeas 1kg": "Cooked Chickpeas 1kg",
    "Saudia Long-Life UHT Milk 1L": "UHT Milk 1L",
    "Al Khair Saudi Coffee Bahar 250g": "Saudi Coffee 250g",
    "Lipton Yellow Label Tea Bags 100P": "Tea Bags 100 pcs",
    "Bateel Khalas Dates 1kg": "Khalas Dates 1kg",
    "Sukkari Premium Dates 1kg": "Sukkari Dates 1kg",
    "Krinos Halawa Tahini 500g": "Halawa Tahini 500g",
    "Berain Bottled Water 600ml 12P": "Water 600ml 12 pcs",
    "Aquafina Mineral Water 330ml 12P": "Water 330ml 12 pcs",
    "Pantene Pro-V Classic Shampoo 700ml": "Shampoo 700ml",
    "Colgate Total Toothpaste 100ml": "Toothpaste 100ml",
    "Always Cotton Soft Pads 16P": "Pads 16pcs",
    "Fairy Original Dish Liquid 750ml": "Dishwashing Liquid 750ml",
    "Clorox Original Bleach 950ml": "Bleach 950ml",
    "Finish Quantum Dishwasher Tablets 32P": "Dishwasher Tablets 32 pcs",
    "Local Naemi Fresh Lamb 1kg": "Fresh Lamb",
    "Fresh Norwegian Salmon Fillet 500g": "Salmon Fillet 500g",
    "Local Apples 1kg": "Apples 1kg",
    "Local Oranges 1kg": "Oranges 1kg",
    "Local Lemons 1kg": "Lemons 1kg",
    "Fresh Laban 1L": "Laban 1L",
    "Evaporated Milk 170g": "Evaporated Milk 170g",
    "Macaroni 500g": "Macaroni 500g",
    "Spaghetti 500g": "Spaghetti 500g",
    "Oats 500g": "Oats 500g",
    "Canned Sweet Corn 340g": "Sweet Corn 340g",
    "Facial Tissue 200 Sheets": "Facial Tissue 200 Sheets",
}


def _phrase_present(norm_text: str, phrase: str) -> bool:
    phrase = _normalize(phrase)
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm_text) is not None


def _term_present(norm_text: str, term: str) -> bool:
    variants = _BILINGUAL.get(term, {term})
    return any(_phrase_present(norm_text, variant) for variant in variants)


def _size_to_base(size: str) -> Optional[tuple[float, str]]:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(l|ml|kg|g|pcs)", size)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "l":
        return value * 1000.0, "ml"
    if unit == "kg":
        return value * 1000.0, "g"
    return value, unit


def _sizes_satisfy_rule(
    required_sizes: set[str],
    scraped_sizes: set[str],
    tolerance_pct: float,
) -> bool:
    for required in required_sizes:
        if required in scraped_sizes:
            continue
        if tolerance_pct <= 0:
            return False

        req_base = _size_to_base(required)
        if not req_base or req_base[1] == "pcs":
            return False

        req_value, req_unit = req_base
        matched_near_size = False
        for scraped in scraped_sizes:
            scraped_base = _size_to_base(scraped)
            if not scraped_base:
                continue
            scraped_value, scraped_unit = scraped_base
            if scraped_unit != req_unit:
                continue
            if req_value and abs(scraped_value - req_value) / req_value <= tolerance_pct:
                matched_near_size = True
                break
        if not matched_near_size:
            return False
    return True


def _representative_substitute_match(
    expected_name: str,
    scraped_title: str,
) -> Optional[str]:
    rule = REPRESENTATIVE_SUBSTITUTES.get(expected_name)
    if rule is None:
        return None

    norm_expected = _normalize(expected_name)
    norm_scraped = _normalize(scraped_title)
    expected_sizes = set(rule.required_sizes) or _extract_sizes(norm_expected)
    scraped_sizes = _extract_sizes(norm_scraped)

    if expected_sizes and not _sizes_satisfy_rule(
        expected_sizes,
        scraped_sizes,
        rule.size_tolerance_pct,
    ):
        if not (
            rule.allow_missing_bulk_kg_size
            and expected_sizes == {"1kg"}
            and not scraped_sizes
        ):
            return None

    if _has_piece_pack_size(scraped_sizes) and not _has_piece_pack_size(expected_sizes):
        return None
    expected_piece_sizes = _piece_pack_sizes(expected_sizes)
    scraped_piece_sizes = _piece_pack_sizes(scraped_sizes)
    if expected_piece_sizes and (scraped_piece_sizes - expected_piece_sizes):
        return None

    if any(_term_present(norm_scraped, term) for term in rule.reject_terms):
        return None

    if not all(_term_present(norm_scraped, term) for term in rule.required_terms):
        return None

    if rule.any_terms and not any(_term_present(norm_scraped, term) for term in rule.any_terms):
        return None

    return rule.notes


def classify_title_match(expected_name: str, scraped_title: str) -> TitleMatchResult:
    """Return exact vs representative-match metadata for auditability."""
    if is_title_match(expected_name, scraped_title):
        return TitleMatchResult(matched=True)

    notes = _representative_substitute_match(expected_name, scraped_title)
    if notes:
        return TitleMatchResult(
            matched=True,
            match_tier=MATCH_TIER_GASTAT_REPRESENTATIVE,
            notes=notes,
        )
    return TitleMatchResult(matched=False)


# ══════════════════════════════════════════════════════════════════════════════
# PRICE GUARDRAILS — reject anomalous prices that would destroy the index
# ══════════════════════════════════════════════════════════════════════════════
# Absolute ceiling per item.  Any price above this is almost certainly a scraper
# mis-read (e.g. grabbing a "per-carton" bulk price, or concatenated digits).
# Values are generous — roughly 3-4× the expected retail price.

# Ceilings are ~3-4× typical Saudi retail so genuine price rises pass while
# gross scraper mis-reads (wrong product, bulk carton, concatenated digits)
# are rejected BEFORE they ever reach the database. Covers all 50 basket items.
MAX_PRICE_LIMITS: dict[str, float] = {
    # ── Grains ──────────────────────────────────────────────────────────
    "Abu Kass Basmati Rice 5kg":            100.0,
    "Al Walima Long Grain Rice 5kg":        110.0,
    "Al Tayebat Arabic Pita Bread 6P":       20.0,
    "Lusine White Sliced Bread 600g":        15.0,   # ← Hadi spec: reject >15
    "Kuwaiti Flour No.1 1kg":                25.0,
    "Macaroni 500g":                         20.0,
    "Spaghetti 500g":                        20.0,
    "Oats 500g":                             30.0,
    # ── Meat ────────────────────────────────────────────────────────────
    "Alwataniah Chicken 1000g":              45.0,
    "Sunbullah Frozen Minced Meat 400g":     50.0,
    "Local Naemi Fresh Lamb 1kg":           120.0,
    "Local Fresh Beef 1kg":                 110.0,
    # ── Fish & Seafood ──────────────────────────────────────────────────
    "Americana Shrimps 400g":                70.0,
    "Fresh Norwegian Salmon Fillet 500g":   130.0,
    "Local Fresh Hamour Fish 1kg":          150.0,
    # ── Dairy & Eggs ────────────────────────────────────────────────────
    "Almarai Fresh Milk 2L":                 22.0,
    "Almarai Cheddar Cheese Triangles 8P":   30.0,
    "Almarai Greek Yogurt 1kg":              35.0,
    "Almarai Brown Eggs 30P":                45.0,
    "Lurpak Salted Butter 200g":             35.0,
    "Saudia Long-Life UHT Milk 1L":          18.0,
    "Fresh Laban 1L":                        12.0,
    "Evaporated Milk 170g":                  12.0,
    # ── Oils & Fats ─────────────────────────────────────────────────────
    "Afia Corn Oil 1.5L":                    70.0,
    # ── Fruits ──────────────────────────────────────────────────────────
    "Local Bananas 1kg":                     20.0,
    "Local Apples 1kg":                      25.0,
    "Local Oranges 1kg":                     25.0,
    "Local Lemons 1kg":                      25.0,
    # ── Vegetables ──────────────────────────────────────────────────────
    "Local Tomatoes 1kg":                    25.0,
    "Local Fresh Cucumbers 1kg":             20.0,
    "Local Yellow Onions 1kg":               18.0,
    "Local Fresh Potatoes 1kg":              18.0,
    # ── Sugar & Sweets ──────────────────────────────────────────────────
    "Saudia White Sugar 1kg":                18.0,
    "Krinos Halawa Tahini 500g":             45.0,
    "Al Shifa Pure Natural Honey 250g":      60.0,
    # ── Beverages ───────────────────────────────────────────────────────
    "Nescafe Classic 200g":                  65.0,
    "Al Khair Saudi Coffee Bahar 250g":      50.0,
    "Rabea Premium Loose Tea 400g":          45.0,
    "Lipton Yellow Label Tea Bags 100P":     40.0,
    "Maatouk Turkish Coffee 200g":           50.0,
    "Nova Mineral Water 1.5L 6P":            25.0,
    "Berain Bottled Water 600ml 12P":        28.0,
    "Aquafina Mineral Water 330ml 12P":      25.0,
    # ── Canned Food ─────────────────────────────────────────────────────
    "Goody Tuna 185g":                       18.0,
    "Al Fakhama Tomato Paste 8x135g":        40.0,
    "Canned Sweet Corn 340g":                15.0,
    # ── Legumes ─────────────────────────────────────────────────────────
    "Al Doha Red Lentils 1kg":               30.0,
    "Al Wadi Al Akhdar Cooked Chickpeas 1kg":20.0,
    # ── Dates ───────────────────────────────────────────────────────────
    "Bateel Khalas Dates 1kg":              180.0,
    "Sukkari Premium Dates 1kg":             90.0,
    # ── Spices ──────────────────────────────────────────────────────────
    "Majdi Cardamom 50g":                    60.0,
    # ── Personal Care ───────────────────────────────────────────────────
    "Dettol Antiseptic Bar Soap 165g":       40.0,
    "Pantene Pro-V Classic Shampoo 700ml":   70.0,
    "Colgate Total Toothpaste 100ml":        45.0,
    "Always Cotton Soft Pads 16P":           45.0,
    "Facial Tissue 200 Sheets":              25.0,
    # ── Home Cleaning ───────────────────────────────────────────────────
    "Tide Original Powder Detergent 6kg":   120.0,
    "Fairy Original Dish Liquid 750ml":      35.0,
    "Clorox Original Bleach 950ml":          28.0,
    "Finish Quantum Dishwasher Tablets 32P":140.0,
}


def _price_within_guardrail(item_name: str, price: float, store: str) -> bool:
    """Return True if price is within the acceptable ceiling for this item."""
    ceiling = MAX_PRICE_LIMITS.get(item_name)
    if ceiling is None:
        return True  # no limit defined — allow
    if price > ceiling:
        log.warning(
            "    [%s] ANOMALY REJECTED: SAR %.2f exceeds ceiling of SAR %.2f for '%s'",
            store, price, ceiling, item_name,
        )
        return False
    return True


_PRICE_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def _price_candidates(raw: str) -> list[float]:
    """Parse common supermarket price formats from one price element."""
    text = raw.translate(_PRICE_DIGIT_TRANSLATION)
    text = text.replace("\u066b", ".").replace("\u066c", "")
    candidates: list[float] = []

    def add(value: str | float) -> None:
        try:
            price = round(float(str(value).replace(",", ".")), 2)
        except ValueError:
            return
        if price > 0 and price not in candidates:
            candidates.append(price)

    for match in re.finditer(r"\d{1,4}\s*[\.,]\s*\d{1,2}", text):
        add(re.sub(r"\s+", "", match.group(0)))

    for match in re.finditer(r"(?<!\d)(\d{1,4})\s+(\d{2})(?!\d)", text):
        add(f"{match.group(1)}.{match.group(2)}")

    for match in re.finditer(r"(?<!\d)(\d{1,5})(?!\d)", text):
        token = match.group(1)
        if len(token) in (3, 4, 5):
            add(int(token) / 100)
        add(token)

    return candidates


def _parse_price_text(raw: str, expected_name: str, store: str) -> Optional[float]:
    """Return the first parsed price candidate that passes the item guardrail."""
    candidates = _price_candidates(raw)
    for candidate in candidates:
        if _price_within_guardrail(expected_name, candidate, store):
            return candidate
    if candidates:
        log.warning("    [%s] No parsed price candidate passed guardrail from '%s'", store, raw)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PRICE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

async def _extract_from_search(
    page: Page,
    store: str,
    expected_name: str,
) -> ExtractionResult:
    """
    From a search/PLP results page, read the first product card's title & price.

    Returns the price only if the title passes verification.
    Returns a structured failure on mismatch, out-of-stock, or missing elements.
    """
    selectors = STORE_SELECTORS.get(store)
    if not selectors:
        log.warning("No selectors defined for store: %s", store)
        return _extraction_failure(SCRAPE_STATUS_ERROR, "missing_store_selectors")

    # ── Check out-of-stock badge (if selector provided) ──────────────────
    if selectors.get("oos"):
        oos = await page.query_selector(selectors["oos"])
        if oos and await oos.is_visible():
            log.info("    [%s] OOS badge detected", store)
            return _extraction_failure(SCRAPE_STATUS_OOS, "oos_badge")

    # ── Read the product title from the first card ───────────────────────
    title_el = await page.query_selector(selectors["title"])
    if not title_el:
        log.warning("    [%s] No title element found — empty results?", store)
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "missing_title")

    scraped_title = (await title_el.inner_text()).strip()
    if not scraped_title:
        log.warning("    [%s] Title element is empty", store)
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "empty_title")

    # ── Strict title verification ────────────────────────────────────────
    title_match = classify_title_match(expected_name, scraped_title)
    if not title_match.matched:
        log.warning(
            "    [%s] TITLE MISMATCH — expected '%s', got '%s' → REJECTED",
            store, expected_name, scraped_title,
        )
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "title_mismatch")

    log.info(
        "    [%s] Title verified (%s): '%s'",
        store,
        title_match.match_tier,
        scraped_title,
    )

    # ── Extract the price ────────────────────────────────────────────────
    # Prefer sale price (discounted items), fall back to regular price.
    price_el = None
    if selectors.get("price_sale"):
        price_el = await page.query_selector(selectors["price_sale"])
    if not price_el and selectors.get("price_regular"):
        price_el = await page.query_selector(selectors["price_regular"])

    if not price_el:
        log.warning("    [%s] No price element found", store)
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "missing_price")

    raw = (await price_el.inner_text()).strip()
    # Strip currency symbols, Arabic chars, "SAR", "ر.س" — keep digits + dot.
    price = _parse_price_text(raw, expected_name, store)
    if price is None:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "empty_price_text")

    # ── Price guardrail: reject anomalous prices ───────────────────────
    if not _price_within_guardrail(expected_name, price, store):
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "price_guardrail")

    return _extracted_price(
        price,
        title_match.match_tier,
        scraped_title,
        title_match.notes,
    )


# Max Panda result cards to scan before giving up on a title match.
PANDA_MAX_CARDS_TO_SCAN = 36

PANDA_CARD_SELECTORS: list[str] = [
    "div.grid.grid-cols-2 > div",
    "div[class*='grid-cols-2'] > div",
    "a[href*='/product']",
    "[class*='product']",
]

PANDA_TITLE_SELECTORS: list[str] = [
    "span.font-bold.cursor-pointer",
    "span[class*='cursor-pointer']",
    "img[alt]",
]

PANDA_PRICE_SELECTORS: list[str] = [
    "p.font-bold.text-secondary",
    "p[class*='text-secondary']",
    "p.font-bold:not(.line-through):not(.text-secondary)",
    "p[class*='font-bold']:not(.line-through)",
]


async def _element_text_or_alt(element, selector: str) -> str:
    if selector == "img[alt]" or selector.endswith("[alt]"):
        return ((await element.get_attribute("alt")) or "").strip()
    return (await element.inner_text()).strip()


async def _extract_from_panda_grid(
    page: Page,
    expected_name: str,
) -> ExtractionResult:
    """Scan Panda result cards and return the first verified price.

    Panda search often puts a loosely related product first. Reading only the
    first card caused many false "not_found" rows. We scan the visible grid,
    apply the same title and guardrail checks per card, and accept only the
    first product that passes those quality gates.
    """
    store = "Panda"
    selectors = STORE_SELECTORS.get(store, {})

    candidate_card_sels = [selectors.get("card_all"), *PANDA_CARD_SELECTORS]
    seen_selectors: set[str] = set()
    cards: list = []
    used_sel = None
    for sel in candidate_card_sels:
        if not sel or sel in seen_selectors:
            continue
        seen_selectors.add(sel)
        found = await page.query_selector_all(sel)
        if found:
            cards = found
            used_sel = sel
            break

    if not cards:
        log.warning("    [Panda] No result cards found")
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "missing_result_cards")

    title_sels: list[str] = []
    configured_title = selectors.get("card_title")
    if configured_title:
        title_sels.extend(sel.strip() for sel in configured_title.split(",") if sel.strip())
    title_sels.extend(PANDA_TITLE_SELECTORS)

    price_sels = [
        selectors.get("card_price_sale"),
        selectors.get("card_price_regular"),
        *PANDA_PRICE_SELECTORS,
    ]
    price_sels = [sel for sel in price_sels if sel]

    scan_limit = min(len(cards), PANDA_MAX_CARDS_TO_SCAN)
    log.info("    [Panda] Scanning %d result cards via '%s' for a title match...",
             scan_limit, used_sel)

    saw_title = False
    matched_title = False
    rejected_examples: list[str] = []
    for idx, card in enumerate(cards[:PANDA_MAX_CARDS_TO_SCAN]):
        scraped_title = ""
        for title_sel in title_sels:
            title_el = await card.query_selector(title_sel)
            if not title_el:
                continue
            scraped_title = await _element_text_or_alt(title_el, title_sel)
            if scraped_title:
                break

        if not scraped_title:
            continue
        saw_title = True

        title_match = classify_title_match(expected_name, scraped_title)
        if not title_match.matched:
            if len(rejected_examples) < 3:
                rejected_examples.append(scraped_title)
            continue

        matched_title = True
        log.info(
            "    [Panda] Card %d title verified (%s): '%s'",
            idx,
            title_match.match_tier,
            scraped_title,
        )

        price_el = None
        for price_sel in price_sels:
            price_el = await card.query_selector(price_sel)
            if price_el:
                break

        if not price_el:
            log.warning("    [Panda] Card %d matched title but no price element", idx)
            continue

        raw = (await price_el.inner_text()).strip()
        candidate = _parse_price_text(raw, expected_name, store)
        if candidate is None:
            continue

        if not _price_within_guardrail(expected_name, candidate, store):
            continue

        log.info("    [Panda] Price extracted from card %d: SAR %s", idx, candidate)
        return _extracted_price(
            candidate,
            title_match.match_tier,
            scraped_title,
            title_match.notes,
        )

    if not saw_title:
        log.warning("    [Panda] No title element found in %d scanned cards", scan_limit)
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "missing_title")
    if matched_title:
        log.warning("    [Panda] Title matched but no usable price passed guardrail")
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "price_guardrail")

    log.warning(
        "    [Panda] No card passed title+guardrail among %d scanned. Examples: %s",
        scan_limit,
        " | ".join(rejected_examples) if rejected_examples else "none",
    )
    return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_matching_card")


# Max Algolia result cards to scan before giving up on a title match.
ALGOLIA_MAX_CARDS_TO_SCAN = 24


async def _extract_from_algolia_grid(
    page: Page,
    store: str,
    expected_name: str,
) -> ExtractionResult:
    """Scan ALL Algolia result cards and return the price of the FIRST card
    whose title passes verification AND clears the price guardrail.

    This replaces the brittle ``:first-child`` read for Danube. Algolia ranks
    results by its own relevance model, so the first hit is often a wrong
    variant or a bulk carton (the source of the 509 SAR "Lusine bread"
    ghost). Walking the grid and title-matching each card — exactly like the
    Ninja flow — makes the wrong-first-hit problem impossible and lifts the
    match rate.

    Returns a structured failure when no card matches or all candidates fail.
    """
    selectors = STORE_SELECTORS.get(store, {})
    title_sel = selectors.get("card_title", ".product-box__name")
    sale_sel  = selectors.get("card_price_sale")
    reg_sel   = selectors.get("card_price_regular")

    # Probe the configured card selector first, then the version-robust
    # fallbacks — the live InstantSearch markup has changed across releases.
    candidate_card_sels = [selectors.get("card_all", ".ais-hits--item"), *DANUBE_HIT_SELECTORS]
    seen: set[str] = set()
    cards: list = []
    used_sel = None
    for sel in candidate_card_sels:
        if not sel or sel in seen:
            continue
        seen.add(sel)
        found = await page.query_selector_all(sel)
        if found:
            cards = found
            used_sel = sel
            break

    if not cards:
        log.warning("    [%s] No Algolia result cards found (tried %d selectors)",
                    store, len(seen))
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "missing_result_cards")

    log.info("    [%s] Scanning %d result cards via '%s' for a title match...",
             store, min(len(cards), ALGOLIA_MAX_CARDS_TO_SCAN), used_sel)

    for idx, card in enumerate(cards[:ALGOLIA_MAX_CARDS_TO_SCAN]):
        title_el = await card.query_selector(title_sel)
        if not title_el:
            continue
        scraped_title = (await title_el.inner_text()).strip()
        if not scraped_title:
            continue

        title_match = classify_title_match(expected_name, scraped_title)
        if not title_match.matched:
            continue

        log.info(
            "    [%s] Card %d title verified (%s): '%s'",
            store,
            idx,
            title_match.match_tier,
            scraped_title,
        )

        # Sale price takes precedence; fall back to regular price.
        price_el = None
        if sale_sel:
            price_el = await card.query_selector(sale_sel)
        if not price_el and reg_sel:
            price_el = await card.query_selector(reg_sel)
        if not price_el:
            log.warning("    [%s] Card %d matched title but no price element", store, idx)
            continue

        raw = (await price_el.inner_text()).strip()
        candidate = _parse_price_text(raw, expected_name, store)
        if candidate is None:
            continue

        # ── Price guardrail: reject anomalous prices (e.g. 509 SAR bread) ─
        if not _price_within_guardrail(expected_name, candidate, store):
            # Keep scanning — a later card may hold the correct variant.
            continue

        log.info("    [%s] Price extracted from card %d: SAR %s", store, idx, candidate)
        return _extracted_price(
            candidate,
            title_match.match_tier,
            scraped_title,
            title_match.notes,
        )

    log.warning("    [%s] No card passed title+guardrail among %d scanned",
                store, min(len(cards), ALGOLIA_MAX_CARDS_TO_SCAN))
    return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_matching_card")


# ══════════════════════════════════════════════════════════════════════════════
# PANDA-SPECIFIC SCRAPE FLOW
# ══════════════════════════════════════════════════════════════════════════════
# Panda now requires phone-number login.  We rely on the runtime auth state
# created via `python scraper.py --login` being loaded into the browser context.
# Strategy: land on a PLP page → use the on-page searchbar → read results.
# ══════════════════════════════════════════════════════════════════════════════

async def _scrape_panda(
    page: Page,
    item_id: int,
    item_name: str,
    landing_url: str,
) -> ScrapeResult:
    """
    Panda-specific scraping flow (requires prior auth via --login):
      1. Navigate to a PLP page (auth cookies bypass the login wall).
      2. Dismiss any remaining modal if present.
      3. Type item name into the search bar.
      4. Wait for the product grid to update.
      5. Scan result cards until a verified title + price is found.
    """
    store = "Panda"
    try:
        await asyncio.sleep(random.uniform(*HUMAN_DELAY_RANGE))

        # Step 1: Land on a PLP page that loads products.
        await page.goto(landing_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(JS_RENDER_WAIT_MS)

        # Step 2: Dismiss the login modal ("تجاهل" = Skip/Dismiss).
        dismiss_btn = await page.query_selector('button:has-text("تجاهل")')
        if dismiss_btn and await dismiss_btn.is_visible():
            await dismiss_btn.click()
            await page.wait_for_timeout(500)
            log.info("    [Panda] Dismissed login modal")

        # Step 3: Find the search bar, clear it, type the item name, press Enter.
        search_input = await page.query_selector(
            'input[placeholder*="Search"], input[placeholder*="ابحث"]'
        )
        if not search_input:
            log.warning("    [Panda] item_id=%d  Search bar not found", item_id)
            return _scrape_result(item_id, store, None, SCRAPE_STATUS_BLOCKED, "search_bar_missing")

        await search_input.click()
        await page.wait_for_timeout(300)
        # Select all existing text and overwrite with CLEAN keywords (brand +
        # noun + size), not the full bureaucratic name — short queries match
        # far better on supermarket search engines.
        query = _clean_search_query(item_name)
        log.info("    [Panda] Searching '%s' (from '%s')", query, item_name)
        await search_input.press("Control+a")
        await search_input.type(query, delay=80)  # human-like typing speed
        await page.wait_for_timeout(500)
        await search_input.press("Enter")

        # Step 4: Wait for the grid to reload with search results.
        await page.wait_for_timeout(JS_RENDER_WAIT_MS)

        # Step 5: Scan result cards; Panda often ranks a loose match first.
        extracted = await _extract_from_panda_grid(page, item_name)
        status = f"SAR {extracted.price}" if extracted.price is not None else extracted.scrape_status.upper()
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            extracted.price,
            extracted.scrape_status,
            extracted.failure_reason,
            extracted.match_tier,
            extracted.observed_title,
            extracted.match_notes,
        )

    except PwTimeout:
        log.warning("  %-15s  item_id=%d  TIMEOUT", store, item_id)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_TIMEOUT, "playwright_timeout")
    except Exception as exc:
        log.error("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


# ══════════════════════════════════════════════════════════════════════════════
# NINJA-SPECIFIC SCRAPE FLOW  (Category-Browse + Title Match)
# ══════════════════════════════════════════════════════════════════════════════
# Ninja has no search URL or search bar.  Products live on category pages
# (e.g. /sa/ar/category/milk).  The page uses virtual scrolling — product
# card titles are only rendered when the card enters the viewport.
# Strategy:
#   1. Navigate to the category URL.
#   2. Incrementally scroll the page so the browser renders each row of cards.
#   3. For each card, read the title from `img[alt]` (always populated, even
#      before the <p> title text renders) and match via `is_title_match()`.
#   4. When a match is found, extract the price from the sibling <p> element.
# ══════════════════════════════════════════════════════════════════════════════

# How many viewport-heights to scroll down when scanning Ninja's category grid.
NINJA_MAX_SCROLLS      = 12
NINJA_SCROLL_PAUSE_MS  = 1_500   # pause after each scroll for lazy-load

# Candidate selectors for Ninja product-card title images, tried in order.
# The original single hard-coded selector ('div.grid[class*=grid-cols-wrap]
# img[alt]') returned 0 cards in the field, so we probe progressively looser
# patterns until one yields elements. Titles live in img[alt]; a final
# fallback grabs any non-empty alt image.
NINJA_CARD_SELECTORS: list[str] = [
    "div.grid[class*='grid-cols-wrap'] img[alt]",   # original (specific)
    "div[class*='grid'] a[href*='/product'] img[alt]",
    "a[href*='/product'] img[alt]",                  # product links
    "a[href*='/p/'] img[alt]",                        # alt product-link shape
    "div[class*='grid'] img[alt]",                   # any grid image
    "[class*='card'] img[alt]",                       # generic card wrappers
    "[class*='product'] img[alt]",                   # product-classed wrappers
    "main img[alt]:not([alt=''])",                   # within <main>
    "img[alt]:not([alt=''])",                        # whole-page last resort
]
# Candidate price selectors queried inside a matched Ninja card's <a> ancestor.
NINJA_PRICE_SELECTORS: list[str] = [
    "p.font-medium.text-gray-500",                   # original
    "p[class*='font-medium']",
    "[class*='price']",
    "p:has-text('ر.س')",
    "span:has-text('ر.س')",
]

NINJA_CATEGORY_BASE_URL = "https://ananinja.com/sa/ar/category"
NINJA_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("milk", "laban", "evaporated milk"), "milk"),
    (("cheese",), "cheese"),
    (("yogurt", "eggs", "butter"), "dairy-eggs"),
    (("bread", "pita"), "bread-bakery"),
    (("rice", "lentil", "lentils", "flour", "macaroni", "spaghetti"), "pasta-rice-grains"),
    (("oats",), "spreads-honey-cereals"),
    (("chickpeas", "chickpea", "tuna", "tomato paste", "sweet corn"), "canned-food"),
    (("chicken", "lamb", "beef"), "fresh-poultry-meat"),
    (("shrimp", "shrimps", "salmon", "hamour", "fish", "minced meat"), "frozen-food"),
    (("banana", "bananas", "apple", "apples", "orange", "oranges", "lemon", "lemons",
      "tomato", "tomatoes", "cucumber", "cucumbers",
      "onion", "onions", "potato", "potatoes"), "fruits-vegetables"),
    (("oil", "sugar"), "oil-flour-cooking-needs"),
    (("dates", "date"), "dates"),
    (("cardamom",), "spices-seasoning"),
    (("water",), "water-ice"),
    (("tea",), "tea"),
    (("coffee", "nescafe"), "coffee"),
    (("honey",), "spreads-honey-cereals"),
    (("halawa",), "sweets"),
    (("soap",), "bath-shower"),
    (("shampoo",), "hair-care"),
    (("toothpaste",), "oral-care"),
    (("pads",), "feminine-care"),
    (("tissue", "tissues"), "tissues"),
    (("detergent", "tide"), "laundry-gnf"),
    (("dishwasher", "dish liquid", "bleach", "clorox", "finish", "fairy"), "cleaning-supplies-gnf"),
]


def _ninja_category_url_for_item(item_name: str) -> Optional[str]:
    norm = _normalize(item_name)
    for needles, slug in NINJA_CATEGORY_RULES:
        if any(needle in norm for needle in needles):
            return f"{NINJA_CATEGORY_BASE_URL}/{slug}"
    return None


async def _scrape_ninja(
    page: Page,
    item_id: int,
    item_name: str,
    category_url: str,
) -> ScrapeResult:
    """
    Ninja-specific scraping flow (category-browse + scroll + title-match):
      1. Navigate to a category page (e.g. /sa/ar/category/milk).
      2. Scroll incrementally to force virtual-scroll rendering.
      3. Scan every product card: read title from img[alt], verify via
         is_title_match(), and extract the price on match.
      4. Return (item_id, "Ninja", price) or None if no match is found.
    """
    store = "Ninja"
    try:
        await asyncio.sleep(random.uniform(*HUMAN_DELAY_RANGE))

        # Step 1: Navigate to the category page, then let lazy data settle.
        # Ninja's /search URLs currently render a 404 shell, so resolve basket
        # search links to a known category page before scanning.
        resolved_url = _ninja_category_url_for_item(item_name) or category_url
        if resolved_url != category_url:
            log.info("    [Ninja] Resolved category URL: %s", resolved_url)
        await page.goto(resolved_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
            log.info("    [Ninja] Network idle reached")
        except PwTimeout:
            log.info("    [Ninja] networkidle not reached in 15s — continuing")
        await page.wait_for_timeout(JS_RENDER_WAIT_MS)
        log.info("    [Ninja] Category page loaded: %s", page.url)

        # Lock onto whichever card selector actually yields elements on this
        # page (the markup has drifted; the original single selector returned
        # 0 cards). Probe once up-front and reuse the winner while scrolling.
        active_sel: Optional[str] = None
        for sel in NINJA_CARD_SELECTORS:
            probe = await page.query_selector_all(sel)
            if probe:
                active_sel = sel
                log.info("    [Ninja] Card selector locked: '%s' (%d cards)", sel, len(probe))
                break
        if active_sel is None:
            # Nothing matched any known pattern → dump DOM for offline diagnosis.
            log.warning("    [Ninja] No product cards found via any known selector "
                        "for item %d — dumping HTML for diagnosis.", item_id)
            await _dump_debug_html(page, store, item_id, "zero_cards")
            await _save_debug_screenshot(page, store, item_id, "zero_cards")
            log.info("  %-15s  item_id=%d  NO CARDS", store, item_id)
            return _scrape_result(item_id, store, None, SCRAPE_STATUS_NOT_FOUND, "zero_cards")

        # Step 2+3: Scroll and scan. After each scroll, check newly rendered
        #           cards for a title match. Stop early once nothing new loads.
        matched_price: Optional[float] = None
        matched_tier = MATCH_TIER_EXACT
        matched_title: Optional[str] = None
        matched_notes: Optional[str] = None
        seen_alts: set[str] = set()
        last_height = 0

        for scroll_idx in range(NINJA_MAX_SCROLLS):
            img_elements = await page.query_selector_all(active_sel)

            for img in img_elements:
                alt = await img.get_attribute("alt")
                if not alt or alt in seen_alts:
                    continue
                seen_alts.add(alt)

                title_match = classify_title_match(item_name, alt)
                if not title_match.matched:
                    continue

                log.info("    [Ninja] Title matched (%s): '%s'", title_match.match_tier, alt)

                # Walk up from <img> to the <a> card wrapper to find the price.
                card = await img.evaluate_handle(
                    """el => {
                        let node = el;
                        for (let i = 0; i < 6; i++) {
                            if (!node.parentElement) break;
                            node = node.parentElement;
                            if (node.tagName === 'A') return node;
                        }
                        return node;
                    }"""
                )

                # Try each candidate price selector inside the card.
                price_el = None
                for psel in NINJA_PRICE_SELECTORS:
                    try:
                        price_el = await card.query_selector(psel)
                    except Exception:
                        price_el = None
                    if price_el:
                        break
                if not price_el:
                    log.warning("    [Ninja] Matched title but no price element")
                    continue

                raw_price = (await price_el.inner_text()).strip()
                candidate = _parse_price_text(raw_price, item_name, store)
                if candidate is None:
                    log.warning("    [Ninja] Price text empty after cleanup: '%s'", raw_price)
                    continue

                # ── Price guardrail ────────────────────────────────────
                if not _price_within_guardrail(item_name, candidate, store):
                    continue

                matched_price = candidate
                matched_tier = title_match.match_tier
                matched_title = alt
                matched_notes = title_match.notes
                log.info("    [Ninja] Price extracted: SAR %s", matched_price)
                break  # found our match — stop scanning

            if matched_price is not None:
                break

            # Scroll to the bottom to trigger the next lazy-load batch, then
            # stop early if the page height stops growing (end of list).
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(NINJA_SCROLL_PAUSE_MS)
            new_height = await page.evaluate("document.body.scrollHeight")
            log.info(
                "    [Ninja] Scroll %d/%d — %d cards seen so far (height %d→%d)",
                scroll_idx + 1, NINJA_MAX_SCROLLS, len(seen_alts), last_height, new_height,
            )
            if new_height == last_height and scroll_idx > 0:
                log.info("    [Ninja] Page height stable — reached end of grid.")
                break
            last_height = new_height

        status = f"SAR {matched_price}" if matched_price is not None else "NOT FOUND / MISMATCH"
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            matched_price,
            SCRAPE_STATUS_OK if matched_price is not None else SCRAPE_STATUS_NOT_FOUND,
            None if matched_price is not None else "no_matching_card",
            matched_tier,
            matched_title,
            matched_notes,
        )

    except PwTimeout:
        log.warning("  %-15s  item_id=%d  TIMEOUT", store, item_id)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_TIMEOUT, "playwright_timeout")
    except Exception as exc:
        log.error("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


# ══════════════════════════════════════════════════════════════════════════════
# GENERIC SCRAPER (Danube, Carrefour, etc.)
# ══════════════════════════════════════════════════════════════════════════════

# Candidate selectors for an Algolia result CARD, tried in order. The live
# danube.sa markup has shifted across InstantSearch versions, so we probe a
# few known conventions instead of hard-coding one. (Verified against a
# captured danube_html.txt: the hits container is `ais-hits`, and an empty
# search renders `ais-hits__empty`.)
DANUBE_HIT_SELECTORS: list[str] = [
    ".ais-hits--item",                       # instantsearch.js classic
    ".ais-Hits-item",                        # react-instantsearch v6+
    ".ais-hits__item",                       # alt BEM modifier
    ".ais-infinite-hits--item",              # infinite-hits widget
    ".ais-hits .product-box",                # structural fallback
    "[class*='hits'] [class*='product-box']",
]
# Marks a search that returned ZERO results (not a timeout / not a block).
DANUBE_EMPTY_SELECTOR = ".ais-hits__empty, [class*='hits'][class*='empty']"


async def _dump_debug_html(page: Page, store: str, item_id: int, tag: str) -> None:
    """Persist the live DOM to a .html file for offline selector diagnosis.

    A screenshot shows pixels; the HTML shows the class names we actually need
    to target. Saving both on failure makes the NEXT iteration data-grounded
    instead of guesswork. Best-effort — never raises into the scrape flow.
    """
    try:
        html = await page.content()
        out = _debug_file_path(store, tag, item_id, "html")
        out.write_text(html, encoding="utf-8")
        log.info("    [%s] Debug HTML saved → %s", store, out.name)
        _trim_debug_artifacts()
    except Exception as exc:  # pragma: no cover - diagnostics only
        log.debug("    [%s] Could not dump debug HTML: %s", store, exc)


async def _save_debug_screenshot(page: Page, store: str, item_id: int, tag: str) -> None:
    """Persist a failure screenshot in the bounded debug artifact directory."""
    try:
        out = _debug_file_path(store, tag, item_id, "png")
        await page.screenshot(path=str(out), full_page=True)
        log.info("    [%s] Debug screenshot saved → %s", store, out.name)
        _trim_debug_artifacts()
    except Exception:
        pass


async def _wait_for_danube_grid(page: Page, store: str, item_id: int) -> bool:
    """Robustly wait for the Algolia product grid after the modal is dismissed.

    Layered strategy (addresses the 'Timed out waiting for product grid' log):
      1. Wait for network to go idle so post-modal Algolia XHRs can settle.
      2. Progressive STRUCTURAL wait — try each candidate hit selector in turn.
      3. If none appear, distinguish a genuinely EMPTY result set (the query
         matched nothing — captured markup shows `ais-hits__empty`) from a true
         render failure, and dump screenshot + HTML for offline diagnosis.

    Returns True when a product grid is detected, False otherwise.
    """
    # ── 1. Let post-modal search XHRs settle (don't fail hard on timeout). ──
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
        log.info("    [%s] Network idle reached after modal dismiss", store)
    except PwTimeout:
        log.info("    [%s] networkidle not reached in 15s — continuing to probe", store)

    # ── 2. Progressive structural wait across candidate hit selectors. ──
    for sel in DANUBE_HIT_SELECTORS:
        try:
            await page.wait_for_selector(sel, state="attached", timeout=8_000)
            log.info("    [%s] Product grid detected via '%s'", store, sel)
            await page.wait_for_timeout(1_500)   # brief hydration buffer
            return True
        except PwTimeout:
            continue

    # ── 3. No grid — empty result set, or a real failure? ──
    is_empty = await page.query_selector(DANUBE_EMPTY_SELECTOR)
    if is_empty is not None:
        log.warning(
            "    [%s] Search returned ZERO results (empty hits marker) for item %d — "
            "the query matched nothing; this is a SEARCH-QUERY issue, not a timeout.",
            store, item_id,
        )
    else:
        log.warning(
            "    [%s] Product grid never rendered for item %d (no hits, no empty "
            "marker) — possible block, layout change, or slow load.",
            store, item_id,
        )
    await _save_debug_screenshot(page, store, item_id, "grid_timeout")
    await _dump_debug_html(page, store, item_id, "grid_timeout")
    return False


async def _dismiss_danube_delivery_modal(page: Page, item_id: int) -> None:
    """
    Danube shows a lazy-loaded "Delivery Method" modal (الرجاء اختيار طريقة التسليم)
    that blocks the entire page.  It takes 1-3 seconds to appear AFTER the page's
    initial DOMContentLoaded, so we must WAIT for it before trying to dismiss.

    Strategy (ordered by reliability):
      1. Wait up to 8 seconds for any modal / overlay to materialise.
      2. Try clicking the green "اختار" (Choose) button.
      3. Fallback: try other known dismiss patterns.
      4. If the modal never appears (e.g. returning visitor with cookies),
         proceed silently — the grid may already be visible underneath.
    """
    store = "Danube"

    # ── Phase 1: WAIT for the modal to appear ──────────────────────────────
    # The modal is lazy-loaded; checking immediately always misses it.
    # We try multiple selectors that could identify the overlay.
    modal_selectors = [
        'button:has-text("اختار")',          # green "Choose" button (primary CTA)
        'button:has-text("اختر")',           # alternative spelling
        'button:has-text("Choose")',         # English fallback
        '.modal.show',                       # Bootstrap-style modal
        '[class*="modal"][class*="show"]',   # generic visible modal
        '[class*="overlay"][class*="show"]', # overlay variant
    ]

    modal_found = False
    for sel in modal_selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=8_000)
            modal_found = True
            log.info("    [%s] Delivery modal detected via '%s'", store, sel)
            break
        except PwTimeout:
            continue

    if not modal_found:
        log.info("    [%s] No delivery modal appeared after 8 s — continuing", store)
        return

    # ── Phase 2: DISMISS the modal ─────────────────────────────────────────
    # Try clicking the green "اختار" CTA first, then fallback patterns.
    dismiss_selectors = [
        # Primary: the green "Choose" / "اختار" button at the bottom of the modal.
        'button:has-text("اختار")',
        'button:has-text("اختر")',
        'button:has-text("Choose")',
        'button:has-text("اختيار")',
        # Fallback: any prominent primary/green button inside the modal.
        '.modal button.btn-primary',
        '.modal button.btn-success',
        'button[class*="green"]',
        'button[class*="primary"]',
        # Last resort: the close/X icon.
        '.modal .close',
        '.modal button[aria-label="Close"]',
        '[class*="modal"] button[class*="close"]',
    ]

    for sel in dismiss_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                # Wait for the modal fade-out animation to complete.
                await page.wait_for_timeout(1_500)
                log.info("    [%s] Modal dismissed via '%s'", store, sel)
                return
        except Exception:
            continue

    # ── Phase 3: Nuclear option — press Escape ─────────────────────────────
    log.warning("    [%s] Could not click any dismiss button — trying Escape key", store)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_500)

    # Take a debug screenshot if the modal is still blocking.
    await _save_debug_screenshot(page, store, item_id, "modal_stuck")
    log.warning("    [%s] Modal may still be visible after Escape", store)


NOON_MAX_RESULTS_TO_SCAN = 24
NOON_REQUEST_DELAY_RANGE = (1.2, 2.8)
NOON_RETRY_DELAY_RANGE = (8.0, 14.0)
NOON_PRODUCT_RE = re.compile(
    r'\\"brand\\":\\"(?P<brand>(?:\\\\.|[^\\"])*)\\",'
    r'\\"name\\":\\"(?P<name>(?:\\\\.|[^\\"])*)\\"'
    r'.*?\\"price\\":(?P<price>\d+(?:\.\d+)?)'
    r'(?:,\\"sale_price\\":(?P<sale_price>\d+(?:\.\d+)?))?'
    r',\\"url\\":\\"(?P<url>(?:\\\\.|[^\\"])*)\\"'
    r'.*?\\"is_buyable\\":(?P<is_buyable>true|false)',
    re.S,
)


def _decode_noon_field(value: str | None) -> str:
    if not value:
        return ""
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        return value


def _noon_products_from_html(html_text: str) -> list[dict[str, Any]]:
    """Extract Noon PLP products from Next/Flight HTML."""
    products: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in NOON_PRODUCT_RE.finditer(html_text):
        name = _decode_noon_field(match.group("name")).strip()
        brand = _decode_noon_field(match.group("brand")).strip()
        slug = _decode_noon_field(match.group("url")).strip()
        if not name:
            continue
        try:
            price = round(float(match.group("sale_price") or match.group("price")), 2)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        key = (brand, name)
        if key in seen:
            continue
        seen.add(key)
        products.append({
            "brand": brand,
            "name": name,
            "price": price,
            "url": slug,
            "is_buyable": match.group("is_buyable") == "true",
        })
    return products


def _noon_term_present(norm_title: str, term: str) -> bool:
    norm_term = _normalize(term)
    return bool(norm_term and norm_term in norm_title)


def _noon_rule_allows_title(item_name: str, title: str) -> bool:
    rule = noon_rule_for_item(item_name)
    if rule is None:
        return False
    norm_title = _normalize(title)
    if rule.required_terms and not all(
        _noon_term_present(norm_title, term) for term in rule.required_terms
    ):
        return False
    if any(_noon_term_present(norm_title, term) for term in rule.reject_terms):
        return False
    return True


def _extract_from_noon_html(html_text: str, item_name: str) -> ExtractionResult:
    """Pick the first verified buyable Noon result for a non-supermarket item."""
    rule = noon_rule_for_item(item_name)
    if rule is None:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_noon_rule")

    products = _noon_products_from_html(html_text)
    if not products:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "empty_results")

    matched_oos = False
    matched_rejected = False
    examples: list[str] = []
    for product in products[:NOON_MAX_RESULTS_TO_SCAN]:
        observed_title = " ".join(
            part for part in (product.get("brand"), product.get("name")) if part
        ).strip()
        if len(examples) < 5 and observed_title:
            examples.append(observed_title)

        title_match = classify_title_match(rule.query, observed_title)
        rule_allowed = _noon_rule_allows_title(item_name, observed_title)
        if not title_match.matched and not rule_allowed:
            continue
        if not rule_allowed:
            matched_rejected = True
            continue
        if not product.get("is_buyable"):
            matched_oos = True
            continue

        price = float(product["price"])
        if not _price_within_guardrail(item_name, price, "Noon"):
            matched_rejected = True
            continue
        match_tier = (
            title_match.match_tier
            if title_match.matched
            else MATCH_TIER_GASTAT_REPRESENTATIVE
        )
        return _extracted_price(
            price,
            match_tier,
            observed_title,
            title_match.notes or "Noon marketplace representative match",
        )

    if matched_oos:
        return _extraction_failure(SCRAPE_STATUS_OOS, "matched_product_oos")
    if matched_rejected:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "matched_product_rejected")
    log.warning("    [Noon] No matched product. Examples: %s", " | ".join(examples))
    return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_title_match")


def _fetch_noon_search(search_url: str, item_name: str) -> ExtractionResult:
    """Fetch and parse Noon public search results synchronously."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    response = None
    for attempt in range(2):
        response = curl_requests.get(
            search_url,
            timeout=35,
            impersonate="chrome",
            headers=headers,
        )
        if response.status_code not in {429, 503}:
            break
        if attempt == 0:
            delay = random.uniform(*NOON_RETRY_DELAY_RANGE)
            log.warning("    [Noon] rate limited; retrying in %.1fs", delay)
            import time
            time.sleep(delay)

    if response is None:
        return _extraction_failure(SCRAPE_STATUS_ERROR, "empty_response")
    if response.status_code in {401, 403, 429}:
        return _extraction_failure(SCRAPE_STATUS_BLOCKED, f"http_{response.status_code}")
    if response.status_code >= 500:
        return _extraction_failure(SCRAPE_STATUS_TIMEOUT, f"http_{response.status_code}")
    if response.status_code != 200:
        return _extraction_failure(SCRAPE_STATUS_ERROR, f"http_{response.status_code}")
    return _extract_from_noon_html(response.text, item_name)


async def scrape_noon_search(
    item_id: int,
    item_name: str,
    search_url: str,
) -> ScrapeResult:
    """Noon source: public search page, parsed without Playwright."""
    store = "Noon"
    try:
        await asyncio.sleep(random.uniform(*NOON_REQUEST_DELAY_RANGE))
        extracted = await asyncio.to_thread(_fetch_noon_search, search_url, item_name)
        status = f"SAR {extracted.price}" if extracted.price is not None else extracted.scrape_status.upper()
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            extracted.price,
            extracted.scrape_status,
            extracted.failure_reason,
            extracted.match_tier,
            extracted.observed_title,
            extracted.match_notes,
        )
    except Exception as exc:
        log.warning("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


AMAZON_MAX_RESULTS_TO_SCAN = 18
AMAZON_REQUEST_DELAY_RANGE = (1.0, 2.2)


def _parse_amazon_price(raw: str) -> float | None:
    text = (raw or "").replace("\xa0", " ")
    match = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", text)
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", "")), 2)
    except ValueError:
        return None


def _amazon_products_from_html(html_text: str) -> list[dict[str, Any]]:
    """Extract Amazon Saudi search-result titles and prices."""
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []

    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    cards = doc.xpath("//div[@data-component-type='s-search-result']")
    for card in cards:
        title = " ".join(
            part.strip()
            for part in card.xpath(".//h2//text() | .//span[contains(@class,'a-text-normal')]//text()")
            if part and part.strip()
        )
        if not title or title in seen:
            continue
        seen.add(title)
        price_texts = card.xpath(".//span[contains(@class,'a-price')]//span[@class='a-offscreen']/text()")
        price = None
        for price_text in price_texts:
            price = _parse_amazon_price(price_text)
            if price is not None:
                break
        if price is None:
            continue
        products.append({
            "name": title,
            "price": price,
            "is_buyable": True,
        })
    return products


def _amazon_rule_allows_title(item_name: str, title: str) -> bool:
    rule = amazon_rule_for_item(item_name)
    if rule is None:
        return False
    norm_title = _normalize(title)
    if rule.required_terms and not all(
        _noon_term_present(norm_title, term) for term in rule.required_terms
    ):
        return False
    if any(_noon_term_present(norm_title, term) for term in rule.reject_terms):
        return False
    return True


def _extract_from_amazon_html(html_text: str, item_name: str) -> ExtractionResult:
    """Pick the first verified Amazon result for a non-supermarket item."""
    rule = amazon_rule_for_item(item_name)
    if rule is None:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_amazon_rule")

    products = _amazon_products_from_html(html_text)
    if not products:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "empty_results")

    examples: list[str] = []
    matched_rejected = False
    for product in products[:AMAZON_MAX_RESULTS_TO_SCAN]:
        observed_title = str(product.get("name") or "").strip()
        if len(examples) < 5 and observed_title:
            examples.append(observed_title)
        title_match = classify_title_match(rule.query, observed_title)
        rule_allowed = _amazon_rule_allows_title(item_name, observed_title)
        if not title_match.matched and not rule_allowed:
            continue
        if not rule_allowed:
            matched_rejected = True
            continue
        price = float(product["price"])
        if not _price_within_guardrail(item_name, price, "Amazon"):
            matched_rejected = True
            continue
        match_tier = (
            title_match.match_tier
            if title_match.matched
            else MATCH_TIER_GASTAT_REPRESENTATIVE
        )
        return _extracted_price(
            price,
            match_tier,
            observed_title,
            title_match.notes or "Amazon marketplace representative match",
        )

    if matched_rejected:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "matched_product_rejected")
    log.warning("    [Amazon] No matched product. Examples: %s", " | ".join(examples))
    return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_title_match")


def _fetch_amazon_search(search_url: str, item_name: str) -> ExtractionResult:
    response = curl_requests.get(
        search_url,
        timeout=35,
        impersonate="chrome",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    )
    if response.status_code in {401, 403, 429}:
        return _extraction_failure(SCRAPE_STATUS_BLOCKED, f"http_{response.status_code}")
    if response.status_code >= 500:
        return _extraction_failure(SCRAPE_STATUS_TIMEOUT, f"http_{response.status_code}")
    if response.status_code != 200:
        return _extraction_failure(SCRAPE_STATUS_ERROR, f"http_{response.status_code}")
    return _extract_from_amazon_html(response.text, item_name)


async def scrape_amazon_search(
    item_id: int,
    item_name: str,
    search_url: str,
) -> ScrapeResult:
    store = "Amazon"
    try:
        await asyncio.sleep(random.uniform(*AMAZON_REQUEST_DELAY_RANGE))
        extracted = await asyncio.to_thread(_fetch_amazon_search, search_url, item_name)
        status = f"SAR {extracted.price}" if extracted.price is not None else extracted.scrape_status.upper()
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            extracted.price,
            extracted.scrape_status,
            extracted.failure_reason,
            extracted.match_tier,
            extracted.observed_title,
            extracted.match_notes,
        )
    except Exception as exc:
        log.warning("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


async def scrape_official_price(
    item_id: int,
    item_name: str,
    store: str,
) -> ScrapeResult:
    rule = official_price_rule_for_item_store(item_name, store)
    if rule is None:
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_NOT_FOUND, "no_official_price_rule")
    log.info("  %-27s  item_id=%d  SAR %s", store, item_id, rule.price)
    return _scrape_result(
        item_id,
        store,
        rule.price,
        SCRAPE_STATUS_OK,
        match_tier=MATCH_TIER_GASTAT_REPRESENTATIVE,
        observed_title=rule.observed_title,
        match_notes=rule.notes,
    )


def _tamimi_product_collection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract Tamimi ProductCollection rows from the public search API."""
    layouts = (
        payload.get("data", {})
               .get("page", {})
               .get("layouts", [])
    )
    for layout in layouts:
        if layout.get("name") != "ProductCollection":
            continue
        collection = layout.get("value", {}).get("collection", {})
        products = collection.get("product") or []
        if isinstance(products, list):
            return [p for p in products if isinstance(p, dict)]
    return []


def _tamimi_price_from_store_data(store_data: dict[str, Any]) -> tuple[float | None, int | None]:
    """Return Tamimi's current selling price and stock from storeSpecificData."""
    try:
        mrp = float(store_data.get("mrp"))
    except (TypeError, ValueError):
        return None, None
    try:
        discount = float(store_data.get("discount") or 0)
    except (TypeError, ValueError):
        discount = 0.0
    price = mrp - discount if 0 < discount < mrp else mrp
    try:
        stock = int(float(store_data.get("stock")))
    except (TypeError, ValueError):
        stock = None
    return round(price, 2), stock


def _extract_from_tamimi_payload(payload: dict[str, Any], item_name: str) -> ExtractionResult:
    """Pick the first verified in-stock Tamimi variant for a basket item."""
    products = _tamimi_product_collection(payload)
    if not products:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "empty_results")

    matched_oos = False
    matched_price_guardrail = False
    examples: list[str] = []
    for product in products:
        brand = product.get("brand") or {}
        brand_name = brand.get("name") if isinstance(brand, dict) else ""
        product_name = str(product.get("name") or "")
        variants = product.get("variants") or []
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_title = str(
                variant.get("fullName")
                or " ".join(x for x in (product_name, str(variant.get("name") or "")) if x)
            )
            observed_title = " ".join(
                x for x in (str(brand_name or ""), variant_title) if x
            ).strip()
            if len(examples) < 5 and observed_title:
                examples.append(observed_title)

            title_match = classify_title_match(item_name, observed_title)
            if not title_match.matched:
                continue

            store_rows = variant.get("storeSpecificData") or []
            if not isinstance(store_rows, list) or not store_rows:
                matched_oos = True
                continue
            for store_data in store_rows:
                if not isinstance(store_data, dict):
                    continue
                price, stock = _tamimi_price_from_store_data(store_data)
                if stock is not None and stock <= 0:
                    matched_oos = True
                    continue
                if price is None:
                    continue
                if not _price_within_guardrail(item_name, price, "Tamimi"):
                    matched_price_guardrail = True
                    continue
                return _extracted_price(
                    price,
                    title_match.match_tier,
                    observed_title,
                    title_match.notes,
                )

    if matched_price_guardrail:
        return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "price_guardrail")
    if matched_oos:
        return _extraction_failure(SCRAPE_STATUS_OOS, "matched_variant_oos")
    log.warning("    [Tamimi] No matched variant. Examples: %s", " | ".join(examples))
    return _extraction_failure(SCRAPE_STATUS_NOT_FOUND, "no_title_match")


def _fetch_tamimi_api(search_url: str, item_name: str) -> ExtractionResult:
    """Fetch and parse Tamimi public search API synchronously."""
    cleaned_url = _clean_url_query(search_url, item_name)
    response = requests.get(
        cleaned_url,
        timeout=25,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://shop.tamimimarkets.com/en/search",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    )
    if response.status_code in {401, 403, 429}:
        return _extraction_failure(SCRAPE_STATUS_BLOCKED, f"http_{response.status_code}")
    if response.status_code >= 500:
        return _extraction_failure(SCRAPE_STATUS_TIMEOUT, f"http_{response.status_code}")
    if response.status_code != 200:
        return _extraction_failure(SCRAPE_STATUS_ERROR, f"http_{response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return _extraction_failure(SCRAPE_STATUS_ERROR, "invalid_json")
    return _extract_from_tamimi_payload(payload, item_name)


async def scrape_tamimi_api(
    item_id: int,
    item_name: str,
    search_url: str,
) -> ScrapeResult:
    """Tamimi source: public API search, no Playwright browser required."""
    store = "Tamimi"
    try:
        extracted = await asyncio.to_thread(_fetch_tamimi_api, search_url, item_name)
        status = f"SAR {extracted.price}" if extracted.price is not None else extracted.scrape_status.upper()
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            extracted.price,
            extracted.scrape_status,
            extracted.failure_reason,
            extracted.match_tier,
            extracted.observed_title,
            extracted.match_notes,
        )
    except requests.Timeout:
        log.warning("  %-15s  item_id=%d  TIMEOUT", store, item_id)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_TIMEOUT, "requests_timeout")
    except requests.RequestException as exc:
        log.warning("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])
    except Exception as exc:
        log.error("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


async def scrape_search(
    page: Page,
    item_id: int,
    item_name: str,
    store: str,
    search_url: str,
) -> ScrapeResult:
    """
    Navigate to a store's search results page via direct URL,
    verify the first result, and extract the price.
    """
    try:
        await asyncio.sleep(random.uniform(*HUMAN_DELAY_RANGE))

        # Shorten the embedded search query to clean keywords — a full
        # catalogue name often yields Danube's `ais-hits__empty` (zero results).
        cleaned_url = _clean_url_query(search_url, item_name)
        if cleaned_url != search_url:
            log.info("    [%s] Clean query URL: %s", store, cleaned_url)
        await page.goto(cleaned_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(JS_RENDER_WAIT_MS)

        # Danube shows a lazy-loaded delivery-method modal that blocks the grid.
        selectors = STORE_SELECTORS.get(store, {})
        if selectors.get("needs_store_modal_dismiss"):
            await _dismiss_danube_delivery_modal(page, item_id)
            # Robust, layered wait for the Algolia grid (networkidle + progressive
            # structural probe + empty-vs-failure diagnosis).
            grid_ready = await _wait_for_danube_grid(page, store, item_id)
            if not grid_ready:
                is_empty = await page.query_selector(DANUBE_EMPTY_SELECTOR)
                failure_status = SCRAPE_STATUS_NOT_FOUND if is_empty is not None else SCRAPE_STATUS_BLOCKED
                failure_reason = "empty_results" if is_empty is not None else "grid_not_rendered"
                return _scrape_result(item_id, store, None, failure_status, failure_reason)

        # Algolia stores (Danube): scan ALL cards and title-match each one.
        # Other direct-search stores: read the single first card.
        if selectors.get("scan_grid"):
            extracted = await _extract_from_algolia_grid(page, store, item_name)
        else:
            extracted = await _extract_from_search(page, store, item_name)

        # ── DEBUG capture: if extraction failed, save page state for diagnosis ──
        if extracted.price is None and store in ("Danube",):
            await _save_debug_screenshot(page, store, item_id, "no_result")
            await _dump_debug_html(page, store, item_id, "no_result")

        status = f"SAR {extracted.price}" if extracted.price is not None else extracted.scrape_status.upper()
        log.info("  %-15s  item_id=%d  %s", store, item_id, status)
        return _scrape_result(
            item_id,
            store,
            extracted.price,
            extracted.scrape_status,
            extracted.failure_reason,
            extracted.match_tier,
            extracted.observed_title,
            extracted.match_notes,
        )

    except PwTimeout:
        log.warning("  %-15s  item_id=%d  TIMEOUT", store, item_id)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_TIMEOUT, "playwright_timeout")
    except Exception as exc:
        log.error("  %-15s  item_id=%d  ERROR: %s", store, item_id, exc)
        return _scrape_result(item_id, store, None, SCRAPE_STATUS_ERROR, str(exc)[:300])


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH URL BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _clean_search_query(text: str, max_tokens: int = 4) -> str:
    """Reduce a bureaucratic product name to clean SEARCH keywords.

    Supermarket search engines match short brand+noun+size queries far better
    than a full catalogue name. We drop stopwords and filler/descriptor words
    (Indian, White, Local, Fresh, Pre Pack …) while KEEPING brand tokens, the
    core product noun, and any size token. Works on EN or AR text (the filler
    set is bilingual), so it can clean both Panda's typed query and Danube's
    Arabic URL query without translating.

        "Abu Kass Indian White Basmati Rice 5kg" → "Abu Kass Basmati Rice 5kg"
        "Almarai Fresh Milk 2L"                   → "Almarai Milk 2L"

    Always preserves the original size token even if it falls past max_tokens.
    Falls back to the original text if cleaning would empty the query.
    """
    if not text:
        return text
    representative_query = REPRESENTATIVE_SEARCH_QUERIES.get(text)
    if representative_query:
        return representative_query
    stop = {"the", "of", "in", "a", "an", "and", "or", "من", "في", "ال"}
    unit_words = set(_UNIT_CANONICAL.keys())            # kg, g, l, ml, كيلو, لتر …
    raw = re.split(r"[\s+]+", text.strip())

    content: list[str] = []      # brand + product-noun tokens (capped)
    size_toks: list[str] = []    # size tokens kept IN FULL (number + unit)
    i = 0
    while i < len(raw):
        tok = raw[i]
        low = _normalize(tok)
        if not low or low in stop or low in _FILLER_WORDS:
            i += 1
            continue
        # One-token size, e.g. "5kg" / "2l".
        if _extract_sizes(low):
            size_toks.append(tok)
            i += 1
            continue
        # Two-token size, e.g. "5" + "كيلو" / "500" + "g" — keep BOTH.
        if re.fullmatch(r"\d+(?:\.\d+)?", low):
            if i + 1 < len(raw) and _normalize(raw[i + 1]) in unit_words:
                size_toks.extend([tok, raw[i + 1]])
                i += 2
            else:
                i += 1            # bare lone number → drop
            continue
        content.append(tok)
        i += 1

    kept = content[:max_tokens] + size_toks
    return " ".join(kept) if kept else text


def _clean_url_query(url: str, item_name: Optional[str] = None) -> str:
    """Rewrite the ``query=``/``q=`` parameter of a search URL with clean
    keywords (used for Danube, whose query is embedded in the DB URL)."""
    from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

    try:
        parts = urlsplit(url)
        qs = parse_qs(parts.query, keep_blank_values=True)
        changed = False
        representative_query = (
            REPRESENTATIVE_SEARCH_QUERIES.get(item_name or "")
            if item_name
            else None
        )
        for key in ("query", "q", "search"):
            if key in qs and qs[key]:
                qs[key] = [representative_query or _clean_search_query(qs[key][0])]
                changed = True
        if not changed:
            return url
        new_query = urlencode(qs, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    except Exception:
        return url   # never break the scrape over a URL-cleaning hiccup


def build_search_url(store: str, item_name: str) -> Optional[str]:
    """
    Build a search URL for the given store using the item name as the query.

    For Panda: returns the PLP landing page (search is done via searchbar).
    For others: replaces {query} in the template with the URL-encoded item name.
    """
    template = SEARCH_URL_TEMPLATES.get(store)
    if not template:
        return None
    # Panda template has no {query} placeholder — it's a static PLP landing page.
    if "{query}" in template:
        return template.replace("{query}", quote_plus(item_name))
    return template


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

async def run_scraper(db_path: str = DB_PATH) -> str:
    """
    Main entry point: scrape all stores, persist results.

    Routes each (item, store) pair to the correct scrape strategy:
      • Panda  → _scrape_panda   (PLP landing + searchbar + auth)
      • Ninja  → _scrape_ninja   (category-browse + scroll + title-match)
      • Others → scrape_search   (direct search URL)
    """
    today = date.today().isoformat()
    log.info("=== Scraping run for %s ===", today)

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT iu.item_id, i.name AS item_name, iu.store_name, iu.url
          FROM item_urls iu
          JOIN items i ON i.id = iu.item_id
         ORDER BY
             CASE iu.store_name
                 WHEN 'Panda' THEN 0
                 WHEN 'Ninja' THEN 1
                 WHEN 'Danube' THEN 2
                 ELSE 3
             END,
             iu.item_id
        """
    ).fetchall()
    conn.close()

    store_filter = {
        store.strip().lower()
        for store in os.environ.get("SCRAPER_STORE_FILTER", "").split(",")
        if store.strip()
    }
    if store_filter:
        rows = [row for row in rows if row[2].lower() in store_filter]
        log.info("Store filter active: %s (%d rows)", ", ".join(sorted(store_filter)), len(rows))

    item_filter = {
        item.strip().lower()
        for item in os.environ.get("SCRAPER_ITEM_FILTER", "").split(",")
        if item.strip()
    }
    if item_filter:
        rows = [
            row for row in rows
            if str(row[0]).lower() in item_filter or row[1].lower() in item_filter
        ]
        log.info("Item filter active: %s (%d rows)", ", ".join(sorted(item_filter)), len(rows))

    if not rows:
        log.warning("No URLs found in item_urls. Did you run db_setup.py?")
        return today

    results: list[ScrapeResult] = []

    # Check for saved auth state (required for Panda login wall).
    auth_state = _auth_state_path()
    has_auth = auth_state.exists()
    if has_auth:
        log.info("Loading saved auth state from %s", auth_state)
    else:
        log.warning(
            "No Panda auth state found — Panda will likely fail. "
            "Run `python scraper.py --login` first to authenticate, or set PANDA_AUTH_STATE_PATH."
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",          # Prevent HTTP/2 protocol errors on some stores
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            locale="ar-SA",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            # Load saved cookies/localStorage if available.
            **({"storage_state": str(auth_state)} if has_auth else {}),
        )
        # Remove the Playwright-injected navigator.webdriver flag.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        context.set_default_timeout(PAGE_TIMEOUT_MS)

        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        noon_sem = asyncio.Semaphore(int(os.environ.get("NOON_MAX_CONCURRENCY", "1")))

        async def _bounded_scrape(
            item_id: int, item_name: str, store: str, url: str
        ):
            async with sem:
                selectors = STORE_SELECTORS.get(store, {})
                search_url = (
                    url.strip() if url and url.strip()
                    else build_search_url(store, item_name)
                )
                if not search_url:
                    log.warning("  %-15s  item_id=%d  No search URL template", store, item_id)
                    return _scrape_result(
                        item_id, store, None, SCRAPE_STATUS_ERROR, "missing_search_url",
                    )

                if official_price_rule_for_item_store(item_name, store) is not None:
                    return await scrape_official_price(item_id, item_name, store)
                if selectors.get("needs_tamimi_api"):
                    return await scrape_tamimi_api(item_id, item_name, search_url)
                if selectors.get("needs_noon_search"):
                    async with noon_sem:
                        return await scrape_noon_search(item_id, item_name, search_url)
                if selectors.get("needs_amazon_search"):
                    return await scrape_amazon_search(item_id, item_name, search_url)
                if selectors.get("needs_official_price"):
                    return await scrape_official_price(item_id, item_name, store)

                page = await context.new_page()
                try:
                    # Route to the correct scrape strategy.
                    if selectors.get("needs_searchbar"):
                        return await _scrape_panda(page, item_id, item_name, search_url)
                    elif selectors.get("needs_category_browse"):
                        return await _scrape_ninja(page, item_id, item_name, search_url)
                    else:
                        return await scrape_search(page, item_id, item_name, store, search_url)
                finally:
                    await page.close()

        tasks = [
            asyncio.create_task(_bounded_scrape(iid, iname, st, u))
            for iid, iname, st, u in rows
        ]

        # Persist each completed row immediately. The scraper can take a long
        # time because stores are slow and flaky; this prevents good prices
        # from being lost when a later store times out or a run is interrupted.
        conn = get_connection(db_path)
        cursor = conn.cursor()
        try:
            for completed in asyncio.as_completed(tasks):
                try:
                    result = await completed
                except Exception as exc:
                    log.exception("Unexpected scrape task failure: %s", exc)
                    continue
                results.append(result)
                observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _save_daily_price(cursor, today, result, observed_at)
                conn.commit()
        finally:
            conn.close()
            await browser.close()

    log.info("=== Scraping complete — %d results saved ===", len(results))
    return today


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL LOGIN FLOW  — `python scraper.py --login`
# ══════════════════════════════════════════════════════════════════════════════
# Opens a VISIBLE browser so you can log in to Panda manually (phone number +
# OTP).  After you finish logging in, the script saves all cookies and
# localStorage to the runtime auth-state path for future headless runs.
# ══════════════════════════════════════════════════════════════════════════════

LOGIN_WAIT_SECONDS = 120  # Time to complete manual login before auto-save.


async def manual_login() -> None:
    """
    Launch a visible Chromium browser at panda.sa, wait for manual login,
    then persist the browser storage state to AUTH_STATE_PATH.
    """
    auth_state = AUTH_STATE_PATH
    auth_state.parent.mkdir(parents=True, exist_ok=True)
    print("=" * 65)
    print("  PANDA MANUAL LOGIN")
    print("=" * 65)
    print(f"  A browser window will open to https://panda.sa/")
    print(f"  Please log in with your phone number and complete the OTP.")
    print(f"  You have {LOGIN_WAIT_SECONDS} seconds to finish.")
    print(f"  Auth state will be saved to: {auth_state}")
    print("=" * 65)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="ar-SA",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://panda.sa/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

        print(f"\n  >>> Browser is open. Log in now. Waiting {LOGIN_WAIT_SECONDS}s ...\n")

        # Wait in 10-second intervals, printing countdown.
        for remaining in range(LOGIN_WAIT_SECONDS, 0, -10):
            await asyncio.sleep(min(10, remaining))
            print(f"  ... {max(0, remaining - 10)}s remaining")

        # Save the authenticated browser state.
        await context.storage_state(path=str(auth_state))
        print(f"\n  Auth state saved to {auth_state}")
        print("  You can now run `python scraper.py` (or `python main.py`) normally.\n")

        await browser.close()


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if "--login" in sys.argv:
        asyncio.run(manual_login())
    else:
        asyncio.run(run_scraper())
