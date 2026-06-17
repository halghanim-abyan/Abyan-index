"""
mod_real_estate.py — rent and real-estate price index data layer.

Rent comes from the existing inflation_index.db Housing CPI rows. Real estate
prices come from the KAPSARC public data portal dataset published from GASTAT
tables: Real Estate Price Index by Sector (2023=100).
"""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
from typing import Any

import pandas as pd
import requests
import streamlit as st

import db  # unified data layer: SQLite locally, Postgres on Streamlit Cloud

try:
    from curl_cffi import requests as curl_requests
except Exception:  # noqa: BLE001
    curl_requests = None


class EjarRateLimitError(RuntimeError):
    """Raised when the Ejar public API reports its hourly request quota."""


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "inflation_index.db")
_DB = "inflation"  # logical DB name for the unified db layer (Postgres on cloud)

RENTAL_NOWCAST_SOURCE_NAME = "Aqar public rental listing pages"
RENTAL_NOWCAST_SOURCE_URL = "https://sa.aqar.fm/en/apartment-for-rent"
RENTAL_NOWCAST_MIN_ANNUAL_RENT = float(os.getenv("RENTAL_NOWCAST_MIN_ANNUAL_RENT", "6000"))
RENTAL_NOWCAST_MAX_ANNUAL_RENT = float(os.getenv("RENTAL_NOWCAST_MAX_ANNUAL_RENT", "500000"))

RENTAL_NOWCAST_CITY_SOURCES = [
    {
        "slug": "riyadh",
        "city": "Riyadh",
        "region": "\u0627\u0644\u0648\u0633\u0637\u0649",
    },
    {
        "slug": "dammam",
        "city": "Dammam",
        "region": "\u0627\u0644\u0634\u0631\u0642\u064a\u0629",
    },
    {
        "slug": "al-khobar",
        "city": "Al Khobar",
        "region": "\u0627\u0644\u0634\u0631\u0642\u064a\u0629",
    },
    {
        "slug": "al-jubail",
        "city": "Al Jubail",
        "region": "\u0627\u0644\u0634\u0631\u0642\u064a\u0629",
    },
    {
        "slug": "al-hofuf",
        "city": "Al Hofuf",
        "region": "\u0627\u0644\u0634\u0631\u0642\u064a\u0629",
    },
    {
        "slug": "al-qatif",
        "city": "Al Qatif",
        "region": "\u0627\u0644\u0634\u0631\u0642\u064a\u0629",
    },
    {
        "slug": "jeddah",
        "city": "Jeddah",
        "region": "\u0627\u0644\u063a\u0631\u0628\u064a\u0629",
    },
    {
        "slug": "mecca",
        "city": "Mecca",
        "region": "\u0627\u0644\u063a\u0631\u0628\u064a\u0629",
    },
    {
        "slug": "medina",
        "city": "Medina",
        "region": "\u0627\u0644\u063a\u0631\u0628\u064a\u0629",
    },
    {
        "slug": "abha",
        "city": "Abha",
        "region": "\u0627\u0644\u062c\u0646\u0648\u0628",
    },
    {
        "slug": "khamis-mushait",
        "city": "Khamis Mushait",
        "region": "\u0627\u0644\u062c\u0646\u0648\u0628",
    },
    {
        "slug": "jazan",
        "city": "Jazan",
        "region": "\u0627\u0644\u062c\u0646\u0648\u0628",
    },
    {
        "slug": "najran",
        "city": "Najran",
        "region": "\u0627\u0644\u062c\u0646\u0648\u0628",
    },
    {
        "slug": "al-bahah",
        "city": "Al Bahah",
        "region": "\u0627\u0644\u062c\u0646\u0648\u0628",
    },
    {
        "slug": "tabuk",
        "city": "Tabuk",
        "region": "\u0627\u0644\u0634\u0645\u0627\u0644",
    },
    {
        "slug": "hail",
        "city": "Hail",
        "region": "\u0627\u0644\u0634\u0645\u0627\u0644",
    },
    {
        "slug": "arar",
        "city": "Arar",
        "region": "\u0627\u0644\u0634\u0645\u0627\u0644",
    },
    {
        "slug": "sakaka",
        "city": "Sakaka",
        "region": "\u0627\u0644\u0634\u0645\u0627\u0644",
    },
]

KAPSARC_REPI_API_URL = (
    "https://datasource.kapsarc.org/api/explore/v2.1/catalog/datasets/"
    "real-estate-price-index-by-sector-2023-100/records"
)
KAPSARC_REPI_PAGE_URL = (
    "https://data.kapsarc.org/explore/dataset/"
    "real-estate-price-index-by-sector-2023-100/"
)
REPI_SOURCE_NAME = "KAPSARC / GASTAT Real Estate Price Index (2023=100)"

KAPSARC_REGIONAL_REPI_API_URL = (
    "https://datasource.kapsarc.org/api/explore/v2.1/catalog/datasets/"
    "real-estate-indices-by-regions-2023-100/records"
)
KAPSARC_REGIONAL_REPI_PAGE_URL = (
    "https://datasource.kapsarc.org/explore/assets/"
    "real-estate-indices-by-regions-2023-100/"
)
REGIONAL_REPI_SOURCE_NAME = "KAPSARC / GASTAT Real Estate Indices by Regions (2023=100)"
KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_API_URL = (
    "https://datasource.kapsarc.org/api/explore/v2.1/catalog/datasets/"
    "real-estate-indices-by-regions"
)
KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_PAGE_URL = (
    "https://datasource.kapsarc.org/explore/assets/"
    "real-estate-indices-by-regions/"
)
LEGACY_REGIONAL_SECTOR_REPI_SOURCE_NAME = (
    "KAPSARC / GASTAT Real Estate Indices by Regions and Sectors (2014=100)"
)
SPLICED_REGIONAL_REPI_SOURCE_NAME = (
    "KAPSARC / GASTAT Regional REPI Continuous Series (linked 2014/2023 bases)"
)
SPLICED_REGIONAL_REPI_SOURCE_URL = KAPSARC_REGIONAL_REPI_PAGE_URL

EJAR_API_BASE_URL = "https://rentalrei.rega.gov.sa/RegaIndicatorsAPIs/api/"
EJAR_SOURCE_NAME = "Ejar / Sakani Rental Indicators"
EJAR_SOURCE_URL = "https://sakani.sa/reports-and-data/rental-units"
EJAR_EARLIEST_MONTH = pd.Timestamp(2019, 1, 1)
DEFAULT_EJAR_HISTORY_YEARS = int(os.getenv("EJAR_RENT_HISTORY_YEARS", "5"))
MAX_EJAR_HISTORY_YEARS = int(os.getenv("EJAR_RENT_MAX_HISTORY_YEARS", "10"))
EJAR_MAX_FETCH_CALLS_PER_LOAD = int(os.getenv("EJAR_MAX_FETCH_CALLS_PER_LOAD", "90"))
EJAR_RATE_LIMIT_COOLDOWN_MINUTES = int(os.getenv("EJAR_RATE_LIMIT_COOLDOWN_MINUTES", "60"))

REGIONAL_RENT_CITY_GROUPS = {
    "الوسطى": [
        {"city_id": 21282, "city_ar": "الرياض", "city_en": "Riyadh"},
    ],
    "الشرقية": [
        {"city_id": 11048, "city_ar": "الدمام", "city_en": "Dammam"},
        {"city_id": 11045, "city_ar": "الخبر", "city_en": "Khobar"},
        {"city_id": 19366, "city_ar": "الجبيل", "city_en": "Jubail"},
        {"city_id": 13789, "city_ar": "الاحساء", "city_en": "Al Ahsa"},
        {"city_id": 6121, "city_ar": "القطيف", "city_en": "Qatif"},
    ],
    "الغربية": [
        {"city_id": 18394, "city_ar": "جدة", "city_en": "Jeddah"},
        {"city_id": 15423, "city_ar": "مكه المكرمه", "city_en": "Makkah"},
        {"city_id": 14001, "city_ar": "المدينه المنوره", "city_en": "Madinah"},
    ],
    "الجنوب": [
        {"city_id": 6166, "city_ar": "ابها", "city_en": "Abha"},
        {"city_id": 15716, "city_ar": "جازان", "city_en": "Jazan"},
        {"city_id": 3731, "city_ar": "نجران", "city_en": "Najran"},
        {"city_id": 14244, "city_ar": "الباحة", "city_en": "Al Baha"},
    ],
    "الشمال": [
        {"city_id": 19375, "city_ar": "تبوك", "city_en": "Tabuk"},
        {"city_id": 13674, "city_ar": "حائل", "city_en": "Hail"},
        {"city_id": 1778, "city_ar": "عرعر", "city_en": "Arar"},
        {"city_id": 12532, "city_ar": "سكاكا", "city_en": "Sakaka"},
    ],
}

RESIDENTIAL_UNIT_NAMES = {"appartment", "duplex", "floor", "studio", "villa"}


def db_available() -> bool:
    # On the cloud the local .db file does not exist; reachability is a live
    # ping to Postgres instead. Locally, keep the cheap file-exists check.
    if db.IS_POSTGRES:
        return db.ping(_DB)
    return os.path.isfile(DB_PATH)


@st.cache_data(ttl=300)
def db_fingerprint():
    # Cache key for the loaders. On Postgres, os.path.getmtime is meaningless
    # (no local file), so derive a content signature from the data instead.
    if db.IS_POSTGRES:
        return db.db_signature(_DB, "daily_prices")
    try:
        return os.path.getmtime(DB_PATH)
    except OSError:
        return 0.0


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    from db_setup import create_tables, migrate_schema  # noqa: WPS433

    create_tables(conn)
    migrate_schema(conn)
    return conn


def _http_get_json(url: str) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://rei.sakani.sa",
        "Referer": "https://rei.sakani.sa/",
    }
    for attempt in range(4):
        if curl_requests is not None:
            response = curl_requests.get(
                url,
                headers=headers,
                impersonate="chrome124",
                timeout=30,
            )
        else:
            response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 429 and "quota exceeded" in response.text.lower():
            raise EjarRateLimitError(response.text.strip())
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            response.raise_for_status()
            return response.json()
        time.sleep(_retry_delay_seconds(response, attempt))
    raise RuntimeError("Unreachable Ejar GET retry state.")


def _http_post_json(url: str, payload: dict[str, Any]) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://rei.sakani.sa",
        "Referer": "https://rei.sakani.sa/",
    }
    for attempt in range(4):
        if curl_requests is not None:
            response = curl_requests.post(
                url,
                headers=headers,
                json=payload,
                impersonate="chrome124",
                timeout=60,
            )
        else:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code == 429 and "quota exceeded" in response.text.lower():
            raise EjarRateLimitError(response.text.strip())
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            response.raise_for_status()
            return response.json()
        time.sleep(_retry_delay_seconds(response, attempt))
    raise RuntimeError("Unreachable Ejar POST retry state.")


def _retry_delay_seconds(response: Any, attempt: int) -> float:
    retry_after = None
    try:
        retry_after = response.headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        retry_after = None
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(1.5 * (attempt + 1), 6.0)


def _empty_rental_nowcast_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str | None]:
    series = pd.DataFrame(
        columns=[
            "observed_date",
            "region",
            "median_annual_rent",
            "listing_count",
            "cities_observed",
            "cities_expected",
            "asking_index_common",
            "asking_index_local",
            "mom_pct",
            "baseline_date",
            "baseline_median_annual_rent",
            "source_name",
            "source_url",
        ]
    )
    latest = pd.DataFrame(
        columns=[
            "Region",
            "Latest Snapshot",
            "Asking Index",
            "Median Annual Asking Rent (SAR)",
            "Listings",
            "Cities",
            "MoM",
        ]
    )
    source_mix = pd.DataFrame(
        columns=[
            "Source",
            "Raw Rows",
            "Usable Annual Rows",
            "Cities",
            "Latest Snapshot",
            "Source Link",
        ]
    )
    return series, latest, source_mix, None


def _ensure_rental_nowcast_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rental_listing_observations (
            observed_date TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            region TEXT NOT NULL,
            city TEXT NOT NULL,
            city_slug TEXT NOT NULL,
            listing_id TEXT NOT NULL,
            listing_url TEXT,
            title TEXT,
            district TEXT,
            property_type TEXT,
            bedrooms REAL,
            bathrooms REAL,
            area_sqm REAL,
            annual_rent_sar REAL,
            raw_price REAL,
            rent_period_text TEXT,
            source_total_count INTEGER,
            usable INTEGER NOT NULL DEFAULT 1,
            quality_flag TEXT,
            PRIMARY KEY (observed_date, source_name, listing_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rental_listing_observations_region_date
            ON rental_listing_observations (region, observed_date)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rental_nowcast_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_date TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            source_name TEXT NOT NULL,
            status TEXT NOT NULL,
            cities_requested INTEGER,
            cities_ok INTEGER,
            raw_rows INTEGER,
            usable_rows INTEGER,
            error TEXT
        )
        """
    )
    conn.commit()


def _aqar_city_url(city_slug: str) -> str:
    return f"{RENTAL_NOWCAST_SOURCE_URL}/{city_slug}"


def _http_get_text(url: str) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    for attempt in range(3):
        if curl_requests is not None:
            response = curl_requests.get(
                url,
                headers=headers,
                impersonate="chrome124",
                timeout=35,
            )
        else:
            response = requests.get(url, headers=headers, timeout=35)
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
            response.raise_for_status()
            return response.text
        time.sleep(_retry_delay_seconds(response, attempt))
    raise RuntimeError("Unreachable rental listing GET retry state.")


def _extract_next_payload_strings(html_text: str) -> list[str]:
    payloads: list[str] = []
    for match in re.finditer(
        r"self\.__next_f\.push\((\[.*?\])\)</script>",
        html_text,
        flags=re.S,
    ):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if len(value) > 1 and isinstance(value[1], str):
            payloads.append(value[1])
    return payloads


def _extract_json_array_after_key(payload: str, key: str) -> list[dict[str, Any]]:
    marker = f'"{key}":'
    idx = payload.find(marker)
    if idx < 0:
        return []
    start = payload.find("[", idx + len(marker))
    if start < 0:
        return []

    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(payload)):
        char = payload[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                data = json.loads(payload[start : pos + 1])
                return [row for row in data if isinstance(row, dict)]

    return []


def _extract_aqar_total_count(payloads: list[str]) -> int | None:
    for payload in payloads:
        if "sov_listings" not in payload and '"listings"' not in payload:
            continue
        match = re.search(r'"count":(\d+)', payload)
        if match:
            return int(match.group(1))
    return None


def _extract_aqar_listing_payloads(html_text: str) -> tuple[list[dict[str, Any]], int | None]:
    payloads = _extract_next_payload_strings(html_text)
    listings: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if "sov_listings" not in payload and '"listings"' not in payload:
            continue
        for key in ("sov_listings", "listings"):
            for row in _extract_json_array_after_key(payload, key):
                listing_id = row.get("id")
                if listing_id is None:
                    continue
                row["_aqar_bucket"] = key
                listings[str(listing_id)] = row
    return list(listings.values()), _extract_aqar_total_count(payloads)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _normalize_aqar_listing(
    listing: dict[str, Any],
    city_cfg: dict[str, str],
    *,
    observed_date: str,
    observed_at: str,
    source_total_count: int | None,
) -> dict[str, Any] | None:
    listing_id = listing.get("id")
    if listing_id is None:
        return None

    raw_price = _to_float(listing.get("price"))
    if raw_price is None:
        raw_price = _to_float(listing.get("rega_total_price"))

    rent_period_text = listing.get("rent_period_text")
    period_label = "" if rent_period_text is None else str(rent_period_text).strip().lower()
    annual_rent = raw_price if period_label == "annually" else None

    usable = 1
    quality_flag = "ok"
    if annual_rent is None:
        usable = 0
        quality_flag = "non_annual_or_unknown_period"
    elif annual_rent < RENTAL_NOWCAST_MIN_ANNUAL_RENT or annual_rent > RENTAL_NOWCAST_MAX_ANNUAL_RENT:
        usable = 0
        quality_flag = "annual_rent_out_of_bounds"

    path = listing.get("path")
    listing_url = f"https://sa.aqar.fm/en{path}" if isinstance(path, str) and path.startswith("/") else None
    city = str(listing.get("city") or city_cfg["city"])

    return {
        "observed_date": observed_date,
        "observed_at": observed_at,
        "source_name": RENTAL_NOWCAST_SOURCE_NAME,
        "source_url": _aqar_city_url(city_cfg["slug"]),
        "region": city_cfg["region"],
        "city": city,
        "city_slug": city_cfg["slug"],
        "listing_id": str(listing_id),
        "listing_url": listing_url,
        "title": listing.get("title"),
        "district": listing.get("district"),
        "property_type": listing.get("categoryName") or listing.get("ga_property_category"),
        "bedrooms": _to_float(listing.get("beds")),
        "bathrooms": _to_float(listing.get("wc")),
        "area_sqm": _to_float(listing.get("area")),
        "annual_rent_sar": annual_rent if usable else None,
        "raw_price": raw_price,
        "rent_period_text": rent_period_text,
        "source_total_count": source_total_count,
        "usable": usable,
        "quality_flag": quality_flag,
    }


def _fetch_aqar_city_observations(
    city_cfg: dict[str, str],
    *,
    observed_date: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    html_text = _http_get_text(_aqar_city_url(city_cfg["slug"]))
    listings, source_total_count = _extract_aqar_listing_payloads(html_text)
    rows: list[dict[str, Any]] = []
    for listing in listings:
        normalized = _normalize_aqar_listing(
            listing,
            city_cfg,
            observed_date=observed_date,
            observed_at=observed_at,
            source_total_count=source_total_count,
        )
        if normalized is not None:
            rows.append(normalized)
    return rows


def _save_rental_listing_observations(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    columns = [
        "observed_date",
        "observed_at",
        "source_name",
        "source_url",
        "region",
        "city",
        "city_slug",
        "listing_id",
        "listing_url",
        "title",
        "district",
        "property_type",
        "bedrooms",
        "bathrooms",
        "area_sqm",
        "annual_rent_sar",
        "raw_price",
        "rent_period_text",
        "source_total_count",
        "usable",
        "quality_flag",
    ]
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"observed_date", "source_name", "listing_id"}
    )
    conn.executemany(
        f"""
        INSERT INTO rental_listing_observations ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(observed_date, source_name, listing_id) DO UPDATE SET
            {assignments}
        """,
        [tuple(row.get(column) for column in columns) for row in rows],
    )
    conn.commit()
    return len(rows)


def refresh_rental_listing_nowcast(
    db_path: str = DB_PATH,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch current asking-rent listings and store one daily nowcast snapshot."""
    observed_date = pd.Timestamp.now(tz="Asia/Riyadh").date().isoformat()
    observed_at = pd.Timestamp.utcnow().isoformat()
    started_at = observed_at

    all_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cities_ok = 0
    for city_cfg in RENTAL_NOWCAST_CITY_SOURCES:
        try:
            city_rows = _fetch_aqar_city_observations(
                city_cfg,
                observed_date=observed_date,
                observed_at=observed_at,
            )
            all_rows.extend(city_rows)
            cities_ok += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{city_cfg['city']}: {exc}")

    usable_rows = sum(1 for row in all_rows if int(row.get("usable") or 0) == 1)
    status = "complete" if cities_ok == len(RENTAL_NOWCAST_CITY_SOURCES) else "partial"
    if not all_rows:
        status = "failed"

    if not dry_run:
        conn = _conn(db_path)
        try:
            _ensure_rental_nowcast_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO rental_nowcast_runs (
                    observed_date, started_at, source_name, status,
                    cities_requested, cities_ok, raw_rows, usable_rows, error
                )
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, NULL)
                """,
                (
                    observed_date,
                    started_at,
                    RENTAL_NOWCAST_SOURCE_NAME,
                    len(RENTAL_NOWCAST_CITY_SOURCES),
                ),
            )
            run_id = int(cur.lastrowid)
            saved_rows = _save_rental_listing_observations(conn, all_rows)
            conn.execute(
                """
                UPDATE rental_nowcast_runs
                   SET finished_at = ?,
                       status = ?,
                       cities_ok = ?,
                       raw_rows = ?,
                       usable_rows = ?,
                       error = ?
                 WHERE id = ?
                """,
                (
                    pd.Timestamp.utcnow().isoformat(),
                    status,
                    cities_ok,
                    saved_rows,
                    usable_rows,
                    " | ".join(errors[:5]) if errors else None,
                    run_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "observed_date": observed_date,
        "source": RENTAL_NOWCAST_SOURCE_NAME,
        "status": status,
        "cities_requested": len(RENTAL_NOWCAST_CITY_SOURCES),
        "cities_ok": cities_ok,
        "raw_rows": len(all_rows),
        "usable_rows": usable_rows,
        "errors": errors,
        "dry_run": dry_run,
    }


@st.cache_data(ttl=300, show_spinner=False)
def load_rental_listing_nowcast(
    _db_mtime: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str | None]:
    """Daily asking-rent nowcast from public real-estate listing pages."""
    if not db_available():
        return _empty_rental_nowcast_frames()

    if db.IS_POSTGRES:
        # Cloud: read the pre-synced snapshot from Postgres (no live scraping,
        # no DDL). These SELECTs are ANSI-standard so the same text runs as-is.
        observations = db.read_sql(
            "SELECT * FROM rental_listing_observations "
            "ORDER BY observed_date ASC, region ASC, city ASC",
            db=_DB,
            parse_dates=["observed_date", "observed_at"],
        )
        latest_run = db.read_sql(
            "SELECT * FROM rental_nowcast_runs ORDER BY id DESC LIMIT 1",
            db=_DB,
            parse_dates=["observed_date", "started_at", "finished_at"],
        )
    else:
        conn = _conn()
        try:
            _ensure_rental_nowcast_tables(conn)
            observations = pd.read_sql_query(
                """
                SELECT *
                  FROM rental_listing_observations
                 ORDER BY observed_date ASC, region ASC, city ASC
                """,
                conn,
                parse_dates=["observed_date", "observed_at"],
            )
            latest_run = pd.read_sql_query(
                """
                SELECT *
                  FROM rental_nowcast_runs
                 ORDER BY id DESC
                 LIMIT 1
                """,
                conn,
                parse_dates=["observed_date", "started_at", "finished_at"],
            )
        finally:
            conn.close()

    if observations.empty:
        return _empty_rental_nowcast_frames()

    usable = observations[
        (observations["usable"] == 1)
        & observations["annual_rent_sar"].notna()
        & (observations["annual_rent_sar"] > 0)
    ].copy()
    if usable.empty:
        series, latest, source_mix, _ = _empty_rental_nowcast_frames()
        return series, latest, source_mix, "No usable annual asking-rent listings were found in the latest snapshot."

    city_daily = (
        usable.groupby(["observed_date", "region", "city"], as_index=False)
              .agg(
                  city_median_annual_rent=("annual_rent_sar", "median"),
                  listing_count=("listing_id", "nunique"),
              )
    )
    expected_cities = (
        pd.DataFrame(RENTAL_NOWCAST_CITY_SOURCES)
          .groupby("region")["city"]
          .nunique()
          .to_dict()
    )
    series = (
        city_daily.groupby(["observed_date", "region"], as_index=False)
                  .agg(
                      median_annual_rent=("city_median_annual_rent", "median"),
                      listing_count=("listing_count", "sum"),
                      cities_observed=("city", "nunique"),
                  )
                  .sort_values(["region", "observed_date"])
    )
    series["cities_expected"] = series["region"].map(expected_cities).fillna(1).astype(int)
    series["source_name"] = RENTAL_NOWCAST_SOURCE_NAME
    series["source_url"] = RENTAL_NOWCAST_SOURCE_URL
    series["asking_index_local"] = series.groupby("region")["median_annual_rent"].transform(
        lambda values: values / values.iloc[0] * 100.0 if len(values) and values.iloc[0] else pd.NA
    )

    baseline_candidates = (
        series.groupby("observed_date", as_index=False)
              .agg(
                  baseline_median_annual_rent=("median_annual_rent", "mean"),
                  regions_observed=("region", "nunique"),
              )
              .sort_values("observed_date")
    )
    min_regions_for_baseline = min(2, len(expected_cities) or 1)
    baseline_candidates = baseline_candidates[
        baseline_candidates["regions_observed"] >= min_regions_for_baseline
    ]
    warning = None
    if baseline_candidates.empty:
        series["asking_index_common"] = pd.NA
        series["baseline_date"] = pd.NaT
        series["baseline_median_annual_rent"] = pd.NA
        warning = "Asking-rent common baseline needs at least two regions in one snapshot."
    else:
        baseline = baseline_candidates.iloc[0]
        baseline_value = float(baseline["baseline_median_annual_rent"])
        baseline_date = pd.Timestamp(baseline["observed_date"])
        series["asking_index_common"] = (
            series["median_annual_rent"] / baseline_value * 100.0
            if baseline_value
            else pd.NA
        )
        series["baseline_date"] = baseline_date
        series["baseline_median_annual_rent"] = baseline_value

    series["mom_pct"] = series.groupby("region")["median_annual_rent"].pct_change() * 100.0
    latest_rows = (
        series.sort_values("observed_date")
              .groupby("region", as_index=False)
              .tail(1)
              .sort_values("median_annual_rent", ascending=False)
              .copy()
    )
    latest_rows["Region"] = latest_rows["region"]
    latest_rows["Latest Snapshot"] = latest_rows["observed_date"].dt.strftime("%Y-%m-%d")
    latest_rows["Asking Index"] = latest_rows["asking_index_common"]
    latest_rows["Median Annual Asking Rent (SAR)"] = latest_rows["median_annual_rent"].round(0).astype(int)
    latest_rows["Listings"] = latest_rows["listing_count"].astype(int)
    latest_rows["Cities"] = (
        latest_rows["cities_observed"].astype(int).astype(str)
        + "/"
        + latest_rows["cities_expected"].astype(int).astype(str)
    )
    latest_rows["MoM"] = latest_rows["mom_pct"]
    latest = latest_rows[
        [
            "Region",
            "Latest Snapshot",
            "Asking Index",
            "Median Annual Asking Rent (SAR)",
            "Listings",
            "Cities",
            "MoM",
        ]
    ].reset_index(drop=True)

    latest_date = observations["observed_date"].max()
    latest_observations = observations[observations["observed_date"] == latest_date].copy()
    source_mix = (
        latest_observations.groupby("source_name", as_index=False)
                           .agg(
                               raw_rows=("listing_id", "nunique"),
                               usable_rows=("usable", "sum"),
                               cities=("city", "nunique"),
                               source_link=("source_url", "first"),
                           )
    )
    source_mix["Latest Snapshot"] = pd.Timestamp(latest_date).strftime("%Y-%m-%d")
    source_mix["source_link"] = RENTAL_NOWCAST_SOURCE_URL
    source_mix = source_mix.rename(
        columns={
            "source_name": "Source",
            "raw_rows": "Raw Rows",
            "usable_rows": "Usable Annual Rows",
            "cities": "Cities",
            "source_link": "Source Link",
        }
    )

    if not latest_run.empty and str(latest_run.iloc[0].get("status")) != "complete":
        run_error = latest_run.iloc[0].get("error")
        warning = f"Latest asking-rent refresh is {latest_run.iloc[0].get('status')}: {run_error or 'partial source coverage'}"

    keep_cols = [
        "observed_date",
        "region",
        "median_annual_rent",
        "listing_count",
        "cities_observed",
        "cities_expected",
        "asking_index_common",
        "asking_index_local",
        "mom_pct",
        "baseline_date",
        "baseline_median_annual_rent",
        "source_name",
        "source_url",
    ]
    return series[keep_cols].reset_index(drop=True), latest, source_mix, warning


def _empty_rent_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "rent_index",
            "rent_index_rebased",
            "mom_pct",
            "yoy_pct",
            "items",
            "source_name",
            "source_url",
        ]
    )


@st.cache_data(ttl=300)
def load_rent_index_history(_db_mtime: float | None = None) -> pd.DataFrame:
    """Monthly rent CPI index from the Housing basket rows already in the DB."""
    if not db_available():
        return _empty_rent_frame()

    if db.IS_POSTGRES:
        # Same aggregation against the migrated daily_prices, but: strftime is
        # SQLite-only (use RIGHT(date,2)='01' for the first of each month), and
        # we avoid LIKE '%' so the db layer's %-escaping can't bite — LEFT(...)
        # is equivalent for the "Residential rent" prefix match.
        df = db.read_sql(
            """
            SELECT dp.date,
                   AVG(dp.price)          AS rent_index,
                   COUNT(DISTINCT i.id)   AS items
              FROM daily_prices dp
              JOIN items i ON i.id = dp.item_id
             WHERE i.category = 'Housing'
               AND dp.price IS NOT NULL
               AND COALESCE(dp.scrape_status, 'ok') = 'ok'
               AND RIGHT(CAST(dp.date AS TEXT), 2) = '01'
               AND (
                    dp.store_name = 'GASTAT CPI Category Index'
                    OR i.source_name = 'GASTAT CPI Category Index'
                    OR LEFT(i.name, 16) = 'Residential rent'
               )
             GROUP BY dp.date
             ORDER BY dp.date ASC
            """,
            db=_DB,
            parse_dates=["date"],
        )
    else:
        conn = _conn()
        try:
            df = pd.read_sql_query(
                """
                SELECT dp.date,
                       AVG(dp.price)          AS rent_index,
                       COUNT(DISTINCT i.id)   AS items
                  FROM daily_prices dp
                  JOIN items i ON i.id = dp.item_id
                 WHERE i.category = 'Housing'
                   AND dp.price IS NOT NULL
                   AND COALESCE(dp.scrape_status, 'ok') = 'ok'
                   AND strftime('%d', dp.date) = '01'
                   AND (
                        dp.store_name = 'GASTAT CPI Category Index'
                        OR i.source_name = 'GASTAT CPI Category Index'
                        OR i.name LIKE 'Residential rent%'
                   )
                 GROUP BY dp.date
                 ORDER BY dp.date ASC
                """,
                conn,
                parse_dates=["date"],
            )
        finally:
            conn.close()

    if df.empty:
        return _empty_rent_frame()

    df["rent_index"] = pd.to_numeric(df["rent_index"], errors="coerce")
    df = df.dropna(subset=["date", "rent_index"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return _empty_rent_frame()

    first_value = float(df["rent_index"].iloc[0])
    df["rent_index_rebased"] = (
        df["rent_index"] / first_value * 100.0 if first_value else df["rent_index"]
    )
    df["mom_pct"] = df["rent_index"].pct_change() * 100.0
    df["yoy_pct"] = df["rent_index"].pct_change(12) * 100.0
    df["source_name"] = "GASTAT CPI Category Index - Actual rentals paid by tenants"
    df["source_url"] = "https://www.stats.gov.sa/"
    return df


def _empty_regional_rent_frames() -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    series = pd.DataFrame(
        columns=[
            "date",
            "region",
            "avg_annual_rent",
            "contracts",
            "rent_index",
            "rent_index_local",
            "rent_index_common",
            "mom_pct",
            "cities_observed",
            "cities_expected",
            "baseline_date",
            "baseline_avg_annual_rent",
            "source_name",
            "source_url",
        ]
    )
    latest = pd.DataFrame(
        columns=[
            "Region",
            "Cities",
            "Latest Period",
            "Unified Index",
            "Avg Annual Rent (SAR)",
            "Contracts",
            "MoM",
            "Coverage",
            "Source",
        ]
    )
    return series, latest, None


def _ensure_ejar_cache_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ejar_city_monthly (
            city_id INTEGER NOT NULL,
            city_ar TEXT,
            city_en TEXT,
            region TEXT NOT NULL,
            date TEXT NOT NULL,
            unit_name TEXT NOT NULL,
            sum_rent REAL NOT NULL,
            contracts REAL NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY (city_id, date, unit_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ejar_fetch_windows (
            city_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY (city_id, start_date, end_date)
        )
        """
    )
    conn.commit()


def _ejar_date_key(value: pd.Timestamp) -> str:
    return pd.Timestamp(value.year, value.month, 1).strftime("%Y-%m-%d")


def _load_ejar_window_statuses(conn: sqlite3.Connection) -> dict[tuple[int, str, str], tuple[str, str | None]]:
    rows = conn.execute(
        """
        SELECT city_id, start_date, end_date, status, fetched_at
          FROM ejar_fetch_windows
        """
    ).fetchall()
    return {
        (int(city_id), str(start_date), str(end_date)): (str(status), fetched_at)
        for city_id, start_date, end_date, status, fetched_at in rows
    }


def _is_recent_rate_limit(status: str | None, fetched_at: str | None) -> bool:
    if status != "rate_limited" or not fetched_at:
        return False
    fetched_at_ts = pd.to_datetime(fetched_at, errors="coerce", utc=True)
    if pd.isna(fetched_at_ts):
        return False
    return pd.Timestamp.utcnow() - fetched_at_ts < pd.Timedelta(minutes=EJAR_RATE_LIMIT_COOLDOWN_MINUTES)


def _save_ejar_window_status(
    conn: sqlite3.Connection,
    city_id: int,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    status: str,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ejar_fetch_windows (
            city_id, start_date, end_date, fetched_at, status, error
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(city_id, start_date, end_date) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            status = excluded.status,
            error = excluded.error
        """,
        (
            int(city_id),
            _ejar_date_key(start_date),
            _ejar_date_key(end_date),
            pd.Timestamp.utcnow().isoformat(),
            status,
            error,
        ),
    )


def _save_ejar_city_rows(
    conn: sqlite3.Connection,
    region: str,
    city: dict[str, Any],
    city_rows: list[dict[str, Any]],
    earliest_start: pd.Timestamp,
    end_date: pd.Timestamp,
) -> int:
    saved = 0
    observed_at = pd.Timestamp.utcnow().isoformat()
    for row in city_rows:
        unit_name = str(row.get("unitName") or "")
        if unit_name not in RESIDENTIAL_UNIT_NAMES:
            continue
        year = pd.to_numeric(row.get("s_year"), errors="coerce")
        month = pd.to_numeric(row.get("s_month"), errors="coerce")
        sum_rent = pd.to_numeric(row.get("sumRent"), errors="coerce")
        contracts = pd.to_numeric(row.get("sumMaxCount"), errors="coerce")
        if pd.isna(year) or pd.isna(month) or pd.isna(sum_rent) or pd.isna(contracts):
            continue
        if float(contracts) <= 0:
            continue
        row_date = pd.Timestamp(int(year), int(month), 1)
        if row_date < earliest_start or row_date > end_date:
            continue
        conn.execute(
            """
            INSERT INTO ejar_city_monthly (
                city_id, city_ar, city_en, region, date, unit_name,
                sum_rent, contracts, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city_id, date, unit_name) DO UPDATE SET
                city_ar = excluded.city_ar,
                city_en = excluded.city_en,
                region = excluded.region,
                sum_rent = excluded.sum_rent,
                contracts = excluded.contracts,
                observed_at = excluded.observed_at
            """,
            (
                int(city["city_id"]),
                city["city_ar"],
                city["city_en"],
                region,
                _ejar_date_key(row_date),
                unit_name,
                float(sum_rent),
                float(contracts),
                observed_at,
            ),
        )
        saved += 1
    return saved


def _cached_ejar_max_date(conn: sqlite3.Connection) -> pd.Timestamp | None:
    row = conn.execute("SELECT MAX(date) FROM ejar_city_monthly").fetchone()
    if not row or not row[0]:
        return None
    max_date = pd.to_datetime(row[0], errors="coerce")
    if pd.isna(max_date):
        return None
    return pd.Timestamp(max_date.year, max_date.month, 1)


def _load_cached_ejar_rows(
    conn: sqlite3.Connection,
    earliest_start: pd.Timestamp,
    end_date: pd.Timestamp,
) -> list[dict[str, Any]]:
    df = pd.read_sql_query(
        """
        SELECT region, city_ar, city_en, city_id, date, sum_rent, contracts
          FROM ejar_city_monthly
         WHERE date >= ?
           AND date <= ?
         ORDER BY region, city_id, date
        """,
        conn,
        params=(_ejar_date_key(earliest_start), _ejar_date_key(end_date)),
        parse_dates=["date"],
    )
    if df.empty:
        return []
    return df.to_dict("records")


def _ejar_period_payload(city_id: int, start_date: pd.Timestamp, end_date: pd.Timestamp) -> dict[str, Any]:
    return {
        "trigger_Points": "0",
        "strt_date": start_date.strftime("%Y-%m-01T00:00:00.000Z"),
        "end_date": end_date.strftime("%Y-%m-01T00:00:00.000Z"),
        "cityId": city_id,
        "RentalUnitUsage": 0,
        "PeriodType": 1,
        "totalRooms": 0,
    }


def _bounded_ejar_history_years(history_years: int | None) -> int:
    if history_years is None:
        history_years = DEFAULT_EJAR_HISTORY_YEARS
    try:
        years = int(history_years)
    except (TypeError, ValueError):
        years = DEFAULT_EJAR_HISTORY_YEARS
    return max(1, min(years, MAX_EJAR_HISTORY_YEARS))


def _ejar_period_windows(end_date: pd.Timestamp, history_years: int) -> tuple[list[tuple[pd.Timestamp, pd.Timestamp]], pd.Timestamp]:
    requested_start = end_date - pd.DateOffset(months=history_years * 12 - 1)
    earliest_start = max(
        pd.Timestamp(requested_start.year, requested_start.month, 1),
        EJAR_EARLIEST_MONTH,
    )

    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    window_end = pd.Timestamp(end_date.year, end_date.month, 1)
    while window_end >= earliest_start:
        window_start = window_end - pd.DateOffset(months=11)
        windows.append((
            pd.Timestamp(window_start.year, window_start.month, 1),
            pd.Timestamp(window_end.year, window_end.month, 1),
        ))
        window_end = window_start - pd.DateOffset(months=1)

    return windows, earliest_start


def _latest_ejar_period() -> tuple[pd.Timestamp, pd.Timestamp, str | None]:
    try:
        payload = _http_get_json(EJAR_API_BASE_URL + "IndicatorEjar/GetLastContractDate")
        raw_date = (payload.get("data") or {}).get("maxContractDate2")
        end_date = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(end_date):
            raise ValueError("Ejar latest contract date was not parseable.")
    except Exception as exc:  # noqa: BLE001
        return pd.Timestamp.utcnow().normalize(), pd.Timestamp.utcnow().normalize(), str(exc)

    end_date = pd.Timestamp(end_date.year, end_date.month, 1)
    start_date = end_date - pd.DateOffset(months=11)
    return start_date, end_date, None


def _fetch_ejar_city_chart(city_id: int, start_date: pd.Timestamp, end_date: pd.Timestamp) -> list[dict[str, Any]]:
    payload = _ejar_period_payload(city_id, start_date, end_date)
    response = _http_post_json(
        EJAR_API_BASE_URL + "IndicatorEjar/GetChartClassicIndicatorEjar",
        payload,
    )
    rows = response.get("data") or []
    if not isinstance(rows, list):
        return []
    return rows


def _apply_ejar_index_scales(series: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """Add local and common rent index scales to a regional Ejar series."""
    series = series.copy()
    series["rent_index_local"] = series.groupby("region")["avg_annual_rent"].transform(
        lambda values: values / values.iloc[0] * 100.0 if len(values) and values.iloc[0] else values
    )
    series["rent_index"] = series["rent_index_local"]
    series["rent_index_common"] = pd.NA
    series["baseline_date"] = pd.NaT
    series["baseline_avg_annual_rent"] = pd.NA

    expected_regions = len(REGIONAL_RENT_CITY_GROUPS)
    min_regions_for_common_index = min(2, expected_regions)
    valid = series[
        series["avg_annual_rent"].notna()
        & (series["avg_annual_rent"] > 0)
    ].copy()
    if valid.empty:
        return series, "Common Ejar rent baseline is unavailable because no positive regional rents were found."

    baseline_candidates = (
        valid.groupby("date", as_index=False)
             .agg(
                 baseline_avg_annual_rent=("avg_annual_rent", "mean"),
                 regions_observed=("region", "nunique"),
             )
             .sort_values("date")
    )
    baseline_candidates = baseline_candidates[
        baseline_candidates["regions_observed"] >= min_regions_for_common_index
    ]
    if baseline_candidates.empty:
        return (
            series,
            "Common Ejar rent baseline needs at least two regions in one month; "
            "Annual Rent SAR remains available for the rows already cached.",
        )

    baseline = baseline_candidates.iloc[0]
    baseline_value = float(baseline["baseline_avg_annual_rent"])
    if baseline_value <= 0:
        return series, "Common Ejar rent baseline is unavailable because the baseline value is not positive."

    baseline_date = pd.Timestamp(baseline["date"])
    regions_observed = int(baseline["regions_observed"])
    series["rent_index_common"] = series["avg_annual_rent"] / baseline_value * 100.0
    series["baseline_date"] = baseline_date
    series["baseline_avg_annual_rent"] = baseline_value

    warning = None
    if regions_observed < expected_regions:
        warning = (
            f"Common Ejar rent baseline is temporary: it uses {regions_observed}/{expected_regions} "
            "regions until the regional cache fills from the Ejar API."
        )
    return series, warning


def _build_ejar_regional_frames(
    rows: list[dict[str, Any]],
    errors: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Turn cached Ejar city rows into the regional series + latest tables.

    Engine-agnostic: it only consumes already-loaded rows, so both the local
    (SQLite + live fetch) path and the cloud (Postgres, read-only) path share
    exactly the same aggregation/scaling logic.
    """
    if not rows:
        series, latest, _ = _empty_regional_rent_frames()
        message = "No Ejar regional rent rows returned."
        if errors:
            message += " " + " | ".join(errors[:3])
        return series, latest, message

    city_monthly = pd.DataFrame(rows)
    series = (
        city_monthly.groupby(["region", "date"], as_index=False)
                    .agg(
                        sum_rent=("sum_rent", "sum"),
                        contracts=("contracts", "sum"),
                        cities_observed=("city_id", "nunique"),
                    )
    )
    expected = {
        region: len(cities)
        for region, cities in REGIONAL_RENT_CITY_GROUPS.items()
    }
    series["cities_expected"] = series["region"].map(expected)
    series["avg_annual_rent"] = series["sum_rent"] / series["contracts"]
    series = series.sort_values(["region", "date"]).reset_index(drop=True)
    series, common_index_warning = _apply_ejar_index_scales(series)
    if common_index_warning:
        errors.append(common_index_warning)
    series["mom_pct"] = series.groupby("region")["avg_annual_rent"].pct_change() * 100.0
    series["source_name"] = EJAR_SOURCE_NAME
    series["source_url"] = EJAR_SOURCE_URL

    latest_rows = (
        series.sort_values("date")
              .groupby("region", as_index=False)
              .tail(1)
              .sort_values("avg_annual_rent", ascending=False)
              .copy()
    )
    city_names = {
        region: "، ".join(city["city_ar"] for city in cities)
        for region, cities in REGIONAL_RENT_CITY_GROUPS.items()
    }
    latest_rows["Region"] = latest_rows["region"]
    latest_rows["Cities"] = latest_rows["region"].map(city_names)
    latest_rows["Latest Period"] = latest_rows["date"].dt.strftime("%Y-%m")
    latest_rows["Unified Index"] = latest_rows["rent_index_common"]
    latest_rows["Avg Annual Rent (SAR)"] = latest_rows["avg_annual_rent"].round(0).astype(int)
    latest_rows["Contracts"] = latest_rows["contracts"].round(0).astype(int)
    latest_rows["MoM"] = latest_rows["mom_pct"]
    latest_rows["Coverage"] = (
        latest_rows["cities_observed"].astype(int).astype(str)
        + "/"
        + latest_rows["cities_expected"].astype(int).astype(str)
        + " cities"
    )
    latest_rows["Source"] = "Ejar/Sakani"
    latest = latest_rows[
        [
            "Region",
            "Cities",
            "Latest Period",
            "Unified Index",
            "Avg Annual Rent (SAR)",
            "Contracts",
            "MoM",
            "Coverage",
            "Source",
        ]
    ].reset_index(drop=True)

    keep_cols = [
        "date",
        "region",
        "avg_annual_rent",
        "contracts",
        "rent_index",
        "rent_index_local",
        "rent_index_common",
        "mom_pct",
        "cities_observed",
        "cities_expected",
        "baseline_date",
        "baseline_avg_annual_rent",
        "source_name",
        "source_url",
    ]
    error = " | ".join(errors[:5]) if errors else None
    return series[keep_cols], latest, error


def _load_ejar_regional_rent_index_pg(history_years: int | None) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Cloud path: build the regional Ejar series purely from the Postgres
    cache (``ejar_city_monthly``). No live Ejar API calls — those run only on
    the local pipeline and sync their results into Postgres."""
    history_years = _bounded_ejar_history_years(history_years)
    mxdf = db.read_sql("SELECT MAX(date) AS mx FROM ejar_city_monthly", db=_DB)
    max_raw = mxdf.iloc[0]["mx"] if not mxdf.empty else None
    max_date = pd.to_datetime(max_raw, errors="coerce") if max_raw is not None else pd.NaT
    if pd.isna(max_date):
        series, latest, _ = _empty_regional_rent_frames()
        return series, latest, "No cached Ejar regional rent data is available yet."

    end_date = pd.Timestamp(max_date.year, max_date.month, 1)
    _windows, earliest_start = _ejar_period_windows(end_date, history_years)
    rows_df = db.read_sql(
        "SELECT region, city_ar, city_en, city_id, date, sum_rent, contracts "
        "FROM ejar_city_monthly WHERE date >= ? AND date <= ? "
        "ORDER BY region, city_id, date",
        params=(_ejar_date_key(earliest_start), _ejar_date_key(end_date)),
        db=_DB,
        parse_dates=["date"],
    )
    rows = rows_df.to_dict("records") if not rows_df.empty else []
    return _build_ejar_regional_frames(rows, [])


@st.cache_data(ttl=15 * 60, show_spinner=False)
def load_ejar_regional_rent_index(history_years: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Regional rent index from Ejar documented residential rental contracts.

    The level is an average annual rent, not CPI. Regional monthly index values
    are rebased to the first available month so regional direction is readable.
    """
    if db.IS_POSTGRES:
        return _load_ejar_regional_rent_index_pg(history_years)

    history_years = _bounded_ejar_history_years(history_years)
    _start_date, end_date, period_error = _latest_ejar_period()
    live_period_available = period_error is None
    errors: list[str] = []

    conn = _conn()
    try:
        _ensure_ejar_cache_tables(conn)
        if period_error:
            cached_max_date = _cached_ejar_max_date(conn)
            if cached_max_date is None:
                series, latest, _ = _empty_regional_rent_frames()
                return series, latest, period_error
            end_date = cached_max_date
            errors.append(
                f"Live Ejar source unavailable; using cached data through {end_date:%Y-%m}. {period_error}"
            )

        windows, earliest_start = _ejar_period_windows(end_date, history_years)

        if live_period_available:
            statuses = _load_ejar_window_statuses(conn)
            fetch_count = 0
            quota_hit = False
            max_calls_hit = False

            for start_date, window_end in windows:
                if quota_hit or max_calls_hit:
                    break
                for region, cities in REGIONAL_RENT_CITY_GROUPS.items():
                    if quota_hit or max_calls_hit:
                        break
                    for city in cities:
                        key = (
                            int(city["city_id"]),
                            _ejar_date_key(start_date),
                            _ejar_date_key(window_end),
                        )
                        status, fetched_at = statuses.get(key, (None, None))
                        if status == "ok" or _is_recent_rate_limit(status, fetched_at):
                            continue
                        if fetch_count >= EJAR_MAX_FETCH_CALLS_PER_LOAD:
                            errors.append(
                                "Ejar fetch paused to stay under the public API hourly quota; "
                                "cached rows are shown and the next refresh can continue."
                            )
                            max_calls_hit = True
                            break

                        fetch_count += 1
                        try:
                            city_rows = _fetch_ejar_city_chart(
                                int(city["city_id"]),
                                start_date,
                                window_end,
                            )
                        except EjarRateLimitError as exc:
                            _save_ejar_window_status(
                                conn,
                                int(city["city_id"]),
                                start_date,
                                window_end,
                                "rate_limited",
                                str(exc),
                            )
                            conn.commit()
                            errors.append(
                                "Ejar API hourly quota reached; cached rows are shown. "
                                "Try again after the quota window resets."
                            )
                            quota_hit = True
                            break
                        except Exception as exc:  # noqa: BLE001
                            _save_ejar_window_status(
                                conn,
                                int(city["city_id"]),
                                start_date,
                                window_end,
                                "error",
                                str(exc),
                            )
                            conn.commit()
                            errors.append(
                                f"{region}/{city['city_ar']} {start_date:%Y-%m}..{window_end:%Y-%m}: {exc}"
                            )
                            continue

                        _save_ejar_city_rows(conn, region, city, city_rows, earliest_start, end_date)
                        _save_ejar_window_status(
                            conn,
                            int(city["city_id"]),
                            start_date,
                            window_end,
                            "ok",
                        )
                        conn.commit()

        rows = _load_cached_ejar_rows(conn, earliest_start, end_date)
    finally:
        conn.close()

    return _build_ejar_regional_frames(rows, errors)


def _empty_repi_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "period_date",
            "year",
            "quarter",
            "sector",
            "sector_display",
            "value",
            "qoq_pct",
            "yoy_pct",
            "source_name",
            "source_url",
        ]
    )


def _empty_regional_repi_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "period_date",
            "year",
            "quarter",
            "region",
            "region_type",
            "sector",
            "sector_display",
            "value",
            "qoq_pct",
            "yoy_pct",
            "source_name",
            "source_url",
        ]
    )


def _quarter_period_end(year: int, quarter: str) -> pd.Timestamp:
    quarter_num = int(str(quarter).upper().replace("Q", "").strip())
    month = quarter_num * 3
    return pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)


def _legacy_sector_display(item: str, current_group: str | None) -> tuple[str, str | None]:
    raw = " ".join(str(item).strip().split())
    key = raw.lower().replace(" / ", "/ ")
    group = current_group

    if key == "general index":
        return "General index", None
    if key == "residential":
        return "Residential total", "Residential"
    if key == "commercial":
        return "Commercial total", "Commercial"
    if key == "agricultural":
        return "Agricultural total", "Agricultural"
    if key == "plot" and group:
        return f"{group} Plot", group
    if key == "building" and group:
        return f"{group} Building", group
    if group == "Residential" and key in {"villa", "apartment", "house"}:
        return f"Residential {raw}", group
    if group == "Commercial" and key == "gallery/ shop":
        return "Commercial Gallery/ Shop", group
    if group == "Commercial" and key == "commercial center":
        return "Commercial Center", group
    if group == "Agricultural" and key == "agricultural land":
        return "Agricultural Land", group
    return raw, group


def _normalize_repi_region(region_name: str) -> str:
    aliases = {
        "All Regions": "Saudi Arabia",
        "Kingdom": "Saudi Arabia",
        "Kingdoom": "Saudi Arabia",
        "KSA": "Saudi Arabia",
        "Ar Riyad": "Riyadh",
        "Ar Riyadh": "Riyadh",
        "Eastern": "Eastern Province",
        "Eastern Region": "Eastern Province",
        "Qassim": "Al Qaseem",
        "Asir": "Aseer",
        "Tabuk": "Tabouk",
        "Madinh": "Madinah",
        "Madina": "Madinah",
        "Northern": "Northern Borders",
        "المملكة": "Saudi Arabia",
        "الرياض": "Riyadh",
        "مكة المكرمة": "Makkah",
        "المدينة المنورة": "Madinah",
        "القصيم": "Al Qaseem",
        "الشرقية": "Eastern Province",
        "عسير": "Aseer",
        "تبوك": "Tabouk",
        "حائل": "Hail",
        "الحدود الشمالية": "Northern Borders",
        "جازان": "Jazan",
        "نجران": "Najran",
        "الباحة": "Al Baha",
        "الجوف": "Al Jouf",
    }
    clean = " ".join(str(region_name).strip().split())
    return aliases.get(clean, clean)


def _parse_year_quarter_from_export(title: str, export_id: str) -> tuple[int, str] | None:
    text = f"{title} {export_id}"
    patterns = [
        r"(20\d{2})\s*[-_ ]?Q([1-4])",
        r"Q([1-4])\s*[-_ ]?(20\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        first, second = match.groups()
        if first.startswith("20"):
            return int(first), f"Q{second}"
        return int(second), f"Q{first}"
    return None


def _parse_legacy_region_sector_workbook(
    content: bytes,
    year: int,
    quarter: str,
) -> list[dict[str, Any]]:
    try:
        excel = pd.ExcelFile(io.BytesIO(content))
    except Exception:  # noqa: BLE001
        return []
    if "Region_I" not in excel.sheet_names:
        return _parse_old_legacy_region_sector_workbook(content, excel, year, quarter)

    df = pd.read_excel(io.BytesIO(content), sheet_name="Region_I", header=None)
    header_idx = None
    for idx, row in df.iterrows():
        if row.astype(str).str.contains("Sector and Type of Real Estate", case=False, na=False).any():
            header_idx = int(idx)
            break
    if header_idx is None:
        return []

    header = df.iloc[header_idx]
    region_cols: dict[int, str] = {}
    for col_idx, value in header.items():
        if col_idx == 0 or pd.isna(value):
            continue
        region_name = " ".join(str(value).strip().split())
        if region_name and "index" not in region_name.lower():
            region_cols[int(col_idx)] = region_name
    if not region_cols:
        return []

    data_start = None
    for idx in range(header_idx + 1, len(df)):
        first_cell = df.iat[idx, 0]
        if pd.isna(first_cell):
            continue
        if str(first_cell).strip().lower() == "general index":
            data_start = idx
            break
    if data_start is None:
        return []

    rows: list[dict[str, Any]] = []
    current_group: str | None = None
    period_date = _quarter_period_end(year, quarter)
    for idx in range(data_start, len(df)):
        item_cell = df.iat[idx, 0]
        if pd.isna(item_cell):
            break
        item_text = " ".join(str(item_cell).strip().split())
        if not item_text:
            break
        sector_display, current_group = _legacy_sector_display(item_text, current_group)

        for col_idx, region_name in region_cols.items():
            value = pd.to_numeric(df.iat[idx, col_idx], errors="coerce")
            if pd.isna(value):
                continue
            region = _normalize_repi_region(region_name)
            rows.append({
                "period_date": period_date,
                "year": str(year),
                "quarter": quarter,
                "region": region,
                "region_type": (
                    "National" if region == "Saudi Arabia" else "Administrative Region"
                ),
                "sector": sector_display,
                "sector_display": sector_display,
                "value": float(value),
            })
    return rows


def _append_legacy_region_value(
    rows: list[dict[str, Any]],
    period_date: pd.Timestamp,
    year: int,
    quarter: str,
    region_name: Any,
    sector_display: str,
    value: Any,
) -> None:
    value_num = pd.to_numeric(value, errors="coerce")
    if pd.isna(value_num):
        return
    region = _normalize_repi_region(str(region_name))
    if not region or region.lower() == "nan":
        return
    rows.append({
        "period_date": period_date,
        "year": str(year),
        "quarter": quarter,
        "region": region,
        "region_type": (
            "National" if region == "Saudi Arabia" else "Administrative Region"
        ),
        "sector": sector_display,
        "sector_display": sector_display,
        "value": float(value_num),
    })


def _parse_city_i_region_sector_sheet(
    content: bytes,
    year: int,
    quarter: str,
    sheet_name: str = "City_I",
) -> list[dict[str, Any]]:
    df = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None)
    header_idx = None
    for idx, row in df.iterrows():
        text = " ".join(str(value) for value in row.dropna().tolist())
        if "Ar Riy" in text and ("Kingdoom" in text or "Kingdom" in text):
            header_idx = int(idx)
            break
    if header_idx is None:
        return []

    region_cols: dict[int, str] = {}
    for col_idx, value in df.iloc[header_idx].items():
        if col_idx == 0 or pd.isna(value):
            continue
        region = _normalize_repi_region(value)
        if region:
            region_cols[int(col_idx)] = region
    if not region_cols:
        return []

    rows: list[dict[str, Any]] = []
    current_group: str | None = None
    period_date = _quarter_period_end(year, quarter)
    for idx in range(header_idx + 1, len(df)):
        item_cell = df.iat[idx, 0]
        if pd.isna(item_cell):
            continue
        item_text = " ".join(str(item_cell).strip().split())
        if not item_text:
            continue
        sector_display, current_group = _legacy_sector_display(item_text, current_group)
        for col_idx, region_name in region_cols.items():
            _append_legacy_region_value(
                rows,
                period_date,
                year,
                quarter,
                region_name,
                sector_display,
                df.iat[idx, col_idx],
            )
    return rows


def _parse_arabic_region_totals_sheet(
    content: bytes,
    year: int,
    quarter: str,
    sheet_name: str = "على مستوى المناطق",
) -> list[dict[str, Any]]:
    df = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None)
    header_idx = None
    for idx, row in df.iterrows():
        text = " ".join(str(value) for value in row.dropna().tolist())
        if "الرقم العام" in text and "القطاع السكني" in text:
            header_idx = int(idx)
            break
    if header_idx is None:
        return []

    sector_cols: dict[int, str] = {}
    for col_idx, value in df.iloc[header_idx].items():
        label = " ".join(str(value).strip().split())
        if not label or label.lower() == "nan":
            continue
        if "الرقم العام" in label or "general index" in label.lower():
            sector_cols[int(col_idx)] = "General index"
        elif "السكني" in label or "residential" in label.lower():
            sector_cols[int(col_idx)] = "Residential total"
        elif "التجاري" in label or "commercial" in label.lower():
            sector_cols[int(col_idx)] = "Commercial total"
        elif "الزراعي" in label or "agricultural" in label.lower():
            sector_cols[int(col_idx)] = "Agricultural total"
    if not sector_cols:
        return []

    english_region_col = df.shape[1] - 1
    rows: list[dict[str, Any]] = []
    period_date = _quarter_period_end(year, quarter)
    for idx in range(header_idx + 1, len(df)):
        arabic_region = df.iat[idx, 0] if df.shape[1] else None
        english_region = df.iat[idx, english_region_col] if english_region_col > 0 else None
        region_name = english_region if not pd.isna(english_region) else arabic_region
        if pd.isna(region_name):
            continue
        for col_idx, sector_display in sector_cols.items():
            _append_legacy_region_value(
                rows,
                period_date,
                year,
                quarter,
                region_name,
                sector_display,
                df.iat[idx, col_idx],
            )
    return rows


def _parse_old_legacy_region_sector_workbook(
    content: bytes,
    excel: pd.ExcelFile,
    year: int,
    quarter: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "City_I" in excel.sheet_names:
        rows.extend(_parse_city_i_region_sector_sheet(content, year, quarter))
    if "على مستوى المناطق" in excel.sheet_names:
        rows.extend(_parse_arabic_region_totals_sheet(content, year, quarter))
    return rows


def _sector_display(sector: str) -> str:
    labels = {
        "Index number": "General index",
        "Residential: Total": "Residential total",
        "Commercial: Total": "Commercial total",
        "Agricultural: Total": "Agricultural total",
    }
    return labels.get(sector, sector.replace(":", " -"))


def _fetch_kapsarc_records(api_url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total_count: int | None = None
    query = dict(params or {})
    while total_count is None or offset < total_count:
        response = requests.get(
            api_url,
            params={**query, "limit": 100, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        total_count = int(payload.get("total_count") or 0)
        batch = payload.get("results") or []
        rows.extend(batch)
        if not batch:
            break
        offset += len(batch)
    return rows


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_real_estate_price_index_history() -> tuple[pd.DataFrame, str | None]:
    """Quarterly Real Estate Price Index by sector, base 2023=100."""
    try:
        rows = _fetch_kapsarc_records(KAPSARC_REPI_API_URL)
    except Exception as exc:  # noqa: BLE001
        return _empty_repi_frame(), str(exc)

    if not rows:
        return _empty_repi_frame(), "No rows returned from the real estate index source."

    df = pd.DataFrame(rows)
    required = {"periodicity", "measure", "date", "sector", "value", "year", "quarter"}
    missing = required.difference(df.columns)
    if missing:
        return _empty_repi_frame(), f"Missing columns from source: {', '.join(sorted(missing))}"

    df = df[
        (df["periodicity"].eq("Quarterly"))
        & (df["measure"].eq("Index"))
        & df["date"].notna()
    ].copy()
    if df.empty:
        return _empty_repi_frame(), "No quarterly index rows returned from the source."

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period_date"] = pd.to_datetime(df["date"].astype(str) + "-01", errors="coerce")
    df["period_date"] = df["period_date"] + pd.offsets.MonthEnd(0)
    df = df.dropna(subset=["period_date", "sector", "value"])
    if df.empty:
        return _empty_repi_frame(), "Source rows could not be parsed into index dates."

    df["sector_display"] = df["sector"].astype(str).map(_sector_display)
    df = df.sort_values(["sector", "period_date"]).reset_index(drop=True)
    df["qoq_pct"] = df.groupby("sector")["value"].pct_change() * 100.0
    df["yoy_pct"] = df.groupby("sector")["value"].pct_change(4) * 100.0
    df["source_name"] = REPI_SOURCE_NAME
    df["source_url"] = KAPSARC_REPI_PAGE_URL

    keep = [
        "period_date",
        "year",
        "quarter",
        "sector",
        "sector_display",
        "value",
        "qoq_pct",
        "yoy_pct",
        "source_name",
        "source_url",
    ]
    return df[keep].sort_values(["period_date", "sector_display"]).reset_index(drop=True), None


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_regional_real_estate_price_index_history() -> tuple[pd.DataFrame, str | None]:
    """Quarterly official REPI by administrative region, base 2023=100.

    The current GASTAT/KAPSARC regional source publishes the general real
    estate price index by administrative region. Sector/type splits remain in
    the national sector dataset, so this frame marks all rows as General index.
    """
    try:
        rows = _fetch_kapsarc_records(KAPSARC_REGIONAL_REPI_API_URL)
    except Exception as exc:  # noqa: BLE001
        return _empty_regional_repi_frame(), str(exc)

    if not rows:
        return _empty_regional_repi_frame(), "No rows returned from the regional real estate index source."

    df = pd.DataFrame(rows)
    required = {"periodicity", "measure", "date", "city", "value", "year", "quarter"}
    missing = required.difference(df.columns)
    if missing:
        return _empty_regional_repi_frame(), f"Missing columns from source: {', '.join(sorted(missing))}"

    df = df[
        (df["periodicity"].eq("Quarterly"))
        & (df["measure"].eq("Index"))
        & df["date"].notna()
        & df["city"].notna()
    ].copy()
    if df.empty:
        return _empty_regional_repi_frame(), "No quarterly regional index rows returned from the source."

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period_date"] = pd.to_datetime(df["date"].astype(str) + "-01", errors="coerce")
    df["period_date"] = df["period_date"] + pd.offsets.MonthEnd(0)
    df["region"] = df["city"].astype(str).replace({"Index Numbers": "Saudi Arabia"})
    df["region_type"] = df["region"].map(
        lambda value: "National" if value == "Saudi Arabia" else "Administrative Region"
    )
    df = df.dropna(subset=["period_date", "region", "value"])
    if df.empty:
        return _empty_regional_repi_frame(), "Source rows could not be parsed into regional index dates."

    df["sector"] = "General index"
    df["sector_display"] = "General index"
    df = df.sort_values(["region", "period_date"]).reset_index(drop=True)
    df["qoq_pct"] = df.groupby("region")["value"].pct_change() * 100.0
    df["yoy_pct"] = df.groupby("region")["value"].pct_change(4) * 100.0
    df["source_name"] = REGIONAL_REPI_SOURCE_NAME
    df["source_url"] = KAPSARC_REGIONAL_REPI_PAGE_URL

    keep = [
        "period_date",
        "year",
        "quarter",
        "region",
        "region_type",
        "sector",
        "sector_display",
        "value",
        "qoq_pct",
        "yoy_pct",
        "source_name",
        "source_url",
    ]
    return df[keep].sort_values(["period_date", "region"]).reset_index(drop=True), None


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_legacy_region_sector_real_estate_price_index_history() -> tuple[pd.DataFrame, str | None]:
    """Official legacy REPI by administrative region and sector/type, base 2014=100."""
    try:
        meta_response = requests.get(KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_API_URL, timeout=30)
        meta_response.raise_for_status()
        exports = meta_response.json().get("alternative_exports") or []
    except Exception as exc:  # noqa: BLE001
        return _empty_regional_repi_frame(), str(exc)

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for export in exports:
        export_id = str(export.get("id") or "")
        title = str(export.get("title") or "")
        url = str(export.get("url") or "")
        if not export_id.lower().endswith(("xlsx", "xls")):
            continue
        period = _parse_year_quarter_from_export(title, export_id)
        if period is None or not url:
            continue
        year, quarter = period
        try:
            response = requests.get(url, timeout=45)
            response.raise_for_status()
            parsed_rows = _parse_legacy_region_sector_workbook(response.content, year, quarter)
            if parsed_rows:
                rows.extend(parsed_rows)
            else:
                errors.append(f"{title or export_id}: no regional sector rows parsed")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{title or export_id}: {exc}")

    if not rows:
        message = "No legacy regional sector rows returned from original Excel exports."
        if errors:
            message += " " + " | ".join(errors[:3])
        return _empty_regional_repi_frame(), message

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["period_date", "region", "sector_display", "value"])
    if df.empty:
        return _empty_regional_repi_frame(), "Legacy regional sector rows could not be parsed into dates and values."

    df = (
        df.drop_duplicates(subset=["period_date", "region", "sector_display"], keep="last")
          .sort_values(["region", "sector_display", "period_date"])
          .reset_index(drop=True)
    )
    df["qoq_pct"] = df.groupby(["region", "sector_display"])["value"].pct_change() * 100.0
    df["yoy_pct"] = df.groupby(["region", "sector_display"])["value"].pct_change(4) * 100.0
    df["source_name"] = LEGACY_REGIONAL_SECTOR_REPI_SOURCE_NAME
    df["source_url"] = KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_PAGE_URL

    keep = [
        "period_date",
        "year",
        "quarter",
        "region",
        "region_type",
        "sector",
        "sector_display",
        "value",
        "qoq_pct",
        "yoy_pct",
        "source_name",
        "source_url",
    ]
    warning = None
    if errors:
        warning = "Some legacy Excel exports were skipped: " + " | ".join(errors[:3])
    return df[keep].sort_values(["period_date", "region", "sector_display"]).reset_index(drop=True), warning


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_spliced_regional_real_estate_price_index_history() -> tuple[pd.DataFrame, str | None]:
    """Regional general REPI linked from legacy 2014-base and current 2023-base sources."""
    current_df, current_error = load_regional_real_estate_price_index_history()
    legacy_df, legacy_error = load_legacy_region_sector_real_estate_price_index_history()
    if current_df.empty or legacy_df.empty:
        errors = [error for error in [current_error, legacy_error] if error]
        message = "Both legacy and current regional REPI sources are required to build the continuous series."
        if errors:
            message += " " + " | ".join(errors[:2])
        return _empty_regional_repi_frame(), message

    current_general = current_df[current_df["sector_display"].eq("General index")].copy()
    legacy_general = legacy_df[legacy_df["sector_display"].eq("General index")].copy()
    for frame in [current_general, legacy_general]:
        frame["period_date"] = pd.to_datetime(frame["period_date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame.dropna(subset=["period_date", "region", "value"], inplace=True)

    rows: list[pd.DataFrame] = []
    skipped_regions: list[str] = []
    common_regions = sorted(set(current_general["region"]) & set(legacy_general["region"]))
    for region in common_regions:
        old = legacy_general[legacy_general["region"].eq(region)].sort_values("period_date").copy()
        new = current_general[current_general["region"].eq(region)].sort_values("period_date").copy()
        overlap_dates = sorted(set(old["period_date"]) & set(new["period_date"]))
        if not overlap_dates:
            skipped_regions.append(region)
            continue

        join_date = overlap_dates[-1]
        old_join = old[old["period_date"].eq(join_date)]["value"].iloc[-1]
        new_join = new[new["period_date"].eq(join_date)]["value"].iloc[-1]
        if not new_join:
            skipped_regions.append(region)
            continue

        factor = float(old_join) / float(new_join)
        old_part = old[old["period_date"] < join_date].copy()
        new_part = new[new["period_date"] >= join_date].copy()
        old_part["value"] = pd.to_numeric(old_part["value"], errors="coerce")
        new_part["value"] = pd.to_numeric(new_part["value"], errors="coerce") * factor
        old_part["source_component"] = "Legacy 2014=100"
        new_part["source_component"] = "Current 2023=100 linked to legacy overlap"
        rows.extend([old_part, new_part])

    if not rows:
        return _empty_regional_repi_frame(), "No overlapping region rows were available to link legacy and current REPI sources."

    df = pd.concat(rows, ignore_index=True)
    df = (
        df.dropna(subset=["period_date", "region", "value"])
          .drop_duplicates(subset=["period_date", "region", "sector_display"], keep="last")
          .sort_values(["region", "period_date"])
          .reset_index(drop=True)
    )
    df["sector"] = "General index"
    df["sector_display"] = "General index"
    df["year"] = df["period_date"].dt.year.astype(str)
    df["quarter"] = "Q" + (((df["period_date"].dt.month - 1) // 3) + 1).astype(str)
    df["qoq_pct"] = df.groupby("region")["value"].pct_change() * 100.0
    df["yoy_pct"] = df.groupby("region")["value"].pct_change(4) * 100.0
    df["source_name"] = SPLICED_REGIONAL_REPI_SOURCE_NAME
    df["source_url"] = SPLICED_REGIONAL_REPI_SOURCE_URL

    keep = [
        "period_date",
        "year",
        "quarter",
        "region",
        "region_type",
        "sector",
        "sector_display",
        "value",
        "qoq_pct",
        "yoy_pct",
        "source_name",
        "source_url",
    ]
    warning = None
    if skipped_regions:
        warning = "Continuous REPI skipped regions without source overlap: " + ", ".join(skipped_regions[:5])
    return df[keep].sort_values(["period_date", "region"]).reset_index(drop=True), warning
