"""
mod_inflation.py — Data layer for the Saudi Daily Inflation Index.

Reads from the inflation database (tables: daily_index, daily_prices, items,
item_urls, pipeline_runs) via the unified `db` layer, so the SAME code reads
local SQLite (dev / local pipeline) or cloud Postgres (Streamlit Cloud).

READ-ONLY module: it never writes or runs DDL, so it is safe against a
read-only Postgres role on Streamlit Cloud.
"""

import pandas as pd
import streamlit as st

import db  # unified data-access layer (SQLite locally, Postgres on the cloud)

_DB = "inflation"
QUALITY_COVERAGE_THRESHOLD = 80.0


@st.cache_data(ttl=300)
def db_fingerprint() -> str:
    """Content signature for Streamlit cache invalidation.

    Replaces the old ``os.path.getmtime`` (meaningless for remote Postgres)
    with a cheap data-derived signature that changes whenever a new scrape
    lands. Passed as the cache-key argument to the cached loaders below.

    Cached (ttl=300): this signature query (COUNT + MAX on daily_prices) used
    to run on EVERY rerun, several times per page — each one a cross-region
    Postgres round trip (the DB is in Tokyo, the app in the US). Caching it
    for 5 minutes collapses that to ~one query per 5 min; daily data still
    surfaces well within that window, and the loaders keyed on it then serve
    straight from cache during navigation.
    """
    return db.db_signature(_DB, "daily_prices")


@st.cache_data(ttl=300)
def load_index_history(db_mtime=None) -> pd.DataFrame:
    """Full daily_index table sorted by date."""
    return db.read_sql(
        "SELECT date, index_value FROM daily_index ORDER BY date ASC",
        db=_DB,
        parse_dates=["date"],
    )


def _date_key(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value)
    return text[:10] if len(text) >= 10 else text


@st.cache_data(ttl=300)
def load_latest_prices(
    db_mtime=None,
    selected_date: str | None = None,
) -> pd.DataFrame:
    """Scraped prices for a selected date, joined with item master.

    When ``selected_date`` is omitted, this falls back to the most recent raw
    scrape date for backward compatibility. The Macro page passes an explicit
    date so a partial raw day cannot silently replace the last computed index.
    """
    selected_date = _date_key(selected_date)
    return db.read_sql(
        """
        SELECT dp.date,
               i.name        AS item_name,
               i.category,
               dp.store_name,
               dp.price,
               COALESCE(
                   dp.scrape_status,
                   CASE WHEN dp.price IS NULL THEN 'not_found' ELSE 'ok' END
               ) AS scrape_status,
               dp.failure_reason,
               COALESCE(dp.match_tier, 'exact') AS match_tier,
               dp.observed_title,
               dp.match_notes,
               i.weight_percentage,
               COALESCE(i.source_type, 'supermarket') AS source_type,
               COALESCE(i.source_name, 'Supermarket scraper') AS source_name
          FROM daily_prices dp
          JOIN items i ON i.id = dp.item_id
         WHERE dp.date = COALESCE(?, (SELECT MAX(date) FROM daily_prices))
         ORDER BY i.category, i.name, dp.store_name
        """,
        params=(selected_date,),
        db=_DB,
    )


@st.cache_data(ttl=300)
def load_price_coverage_by_date(db_mtime=None) -> pd.DataFrame:
    """Return raw scrape coverage for every date in daily_prices."""
    from basket_config import normalized_basket  # noqa: WPS433

    expected_items = len(normalized_basket())
    df = db.read_sql(
        """
        SELECT date,
               COUNT(*) AS rows,
               COUNT(DISTINCT item_id) AS observed_items,
               COUNT(DISTINCT CASE
                         WHEN COALESCE(scrape_status, 'ok') = 'ok'
                          AND price IS NOT NULL
                         THEN item_id END) AS ok_items,
               SUM(CASE
                         WHEN COALESCE(scrape_status, 'ok') = 'ok'
                          AND price IS NOT NULL
                         THEN 1 ELSE 0 END) AS ok_rows,
               SUM(CASE
                         WHEN COALESCE(scrape_status, 'ok') != 'ok'
                           OR price IS NULL
                         THEN 1 ELSE 0 END) AS non_ok_rows
          FROM daily_prices
         GROUP BY date
         ORDER BY date ASC
        """,
        db=_DB,
    )
    if df.empty:
        return df
    df["expected_items"] = expected_items
    df["coverage_pct"] = (
        df["ok_items"].fillna(0).astype(float) / expected_items * 100.0
        if expected_items else 0.0
    )
    return df


@st.cache_data(ttl=300)
def load_pipeline_runs(db_mtime=None, limit: int = 20) -> pd.DataFrame:
    """Recent pipeline attempts, newest first."""
    return db.read_sql(
        """
        SELECT run_date, started_at, finished_at, status, stage,
               coverage_pct, ok_items, expected_items, error
          FROM pipeline_runs
         ORDER BY started_at DESC, id DESC
         LIMIT ?
        """,
        params=(limit,),
        db=_DB,
    )


@st.cache_data(ttl=300)
def load_source_health_for_date(
    db_mtime=None,
    selected_date: str | None = None,
) -> pd.DataFrame:
    """Source-level row coverage for a selected raw scrape date."""
    selected_date = _date_key(selected_date)
    url_expected = db.read_sql(
        """
        SELECT store_name, COUNT(DISTINCT item_id) AS expected_items
          FROM item_urls
         GROUP BY store_name
        """,
        db=_DB,
    )
    historical_expected = db.read_sql(
        """
        SELECT store_name, MAX(items) AS expected_items
          FROM (
                SELECT store_name, date, COUNT(DISTINCT item_id) AS items
                  FROM daily_prices
                 GROUP BY store_name, date
               ) AS per_day
         GROUP BY store_name
        """,
        db=_DB,
    )
    actual = db.read_sql(
        """
        SELECT store_name,
               COUNT(*) AS rows,
               COUNT(DISTINCT item_id) AS observed_items,
               COUNT(DISTINCT CASE
                     WHEN COALESCE(scrape_status, 'ok') = 'ok'
                      AND price IS NOT NULL
                     THEN item_id END) AS ok_items,
               SUM(CASE
                     WHEN COALESCE(scrape_status, 'ok') = 'ok'
                      AND price IS NOT NULL
                     THEN 1 ELSE 0 END) AS ok_rows
          FROM daily_prices
         WHERE date = COALESCE(?, (SELECT MAX(date) FROM daily_prices))
         GROUP BY store_name
        """,
        params=(selected_date,),
        db=_DB,
    )

    expected = pd.concat([url_expected, historical_expected], ignore_index=True)
    if expected.empty and actual.empty:
        return pd.DataFrame()
    expected = (
        expected.groupby("store_name", as_index=False)["expected_items"].max()
                .rename(columns={"store_name": "Source"})
    )
    actual = actual.rename(columns={"store_name": "Source"})
    out = expected.merge(actual, on="Source", how="outer")
    for col in ("expected_items", "rows", "observed_items", "ok_items", "ok_rows"):
        if col not in out.columns:
            out[col] = 0
        out[col] = out[col].fillna(0).astype(int)
    out["Status"] = out.apply(
        lambda row: "present" if int(row["rows"]) > 0 else "missing this date",
        axis=1,
    )
    out = out.rename(
        columns={
            "expected_items": "Expected Items",
            "rows": "Rows",
            "observed_items": "Observed Items",
            "ok_items": "OK Items",
            "ok_rows": "OK Rows",
        }
    )
    return out.sort_values(["Status", "Rows", "Source"], ascending=[False, False, True])


@st.cache_data(ttl=300)
def get_latest_price_context(
    db_mtime=None,
    quality_threshold: float = QUALITY_COVERAGE_THRESHOLD,
) -> dict:
    """Choose the dashboard display date and summarize partial raw scrapes."""
    coverage_df = load_price_coverage_by_date(db_mtime)
    index_df = load_index_history(db_mtime)
    runs_df = load_pipeline_runs(db_mtime, limit=20)

    latest_index_date = None
    latest_index_value = None
    if not index_df.empty:
        latest_index_row = index_df.sort_values("date").iloc[-1]
        latest_index_date = _date_key(latest_index_row["date"])
        latest_index_value = float(latest_index_row["index_value"])

    latest_raw_date = None
    latest_raw = {}
    if not coverage_df.empty:
        latest_raw_row = coverage_df.sort_values("date").iloc[-1]
        latest_raw_date = _date_key(latest_raw_row["date"])
        latest_raw = latest_raw_row.to_dict()

    display_date = latest_index_date or latest_raw_date
    display_coverage = {}
    if display_date and not coverage_df.empty:
        matches = coverage_df[coverage_df["date"].astype(str).str[:10] == display_date]
        if not matches.empty:
            display_coverage = matches.iloc[-1].to_dict()

    latest_run = {}
    if latest_raw_date and not runs_df.empty:
        run_matches = runs_df[runs_df["run_date"].astype(str).str[:10] == latest_raw_date]
        if not run_matches.empty:
            latest_run = run_matches.iloc[0].to_dict()

    raw_coverage = float(latest_raw.get("coverage_pct", 0.0) or 0.0)
    raw_is_newer = bool(
        latest_raw_date
        and latest_index_date
        and latest_raw_date > latest_index_date
    )
    raw_is_partial = bool(raw_is_newer and raw_coverage < quality_threshold)

    return {
        "display_date": display_date,
        "latest_index_date": latest_index_date,
        "latest_index_value": latest_index_value,
        "latest_raw_date": latest_raw_date,
        "latest_raw": latest_raw,
        "display_coverage": display_coverage,
        "latest_run": latest_run,
        "raw_is_newer": raw_is_newer,
        "raw_is_partial": raw_is_partial,
        "quality_threshold": quality_threshold,
    }


@st.cache_data(ttl=300)
def load_basket_summary(db_mtime=None) -> pd.DataFrame:
    """Item basket grouped by category with weight totals."""
    return db.read_sql(
        """
        SELECT category,
               COUNT(*)              AS items,
               SUM(weight_percentage) AS total_weight
          FROM items
         GROUP BY category
         ORDER BY total_weight DESC
        """,
        db=_DB,
    )


@st.cache_data(ttl=300)
def load_item_count_and_stores(db_mtime=None) -> tuple[int, int]:
    """Return (basket_item_count, source_count).

    Sourced from ``basket_config.BASKET`` — the canonical source of truth
    for the basket composition. The database ``items`` table can lag
    behind whenever ``db_setup.py`` has not been re-run after a basket
    expansion (e.g. growing from 12 → 50 items), so anchoring the
    dashboard KPI to the config keeps the "Items Tracked" number honest.
    """
    # Inline import keeps mod_inflation cheap to load.
    from basket_config import normalized_basket  # noqa: WPS433

    basket = normalized_basket()
    n_items = len(basket)
    sources: set[str] = set()
    for item in basket:
        sources.update(item.get("urls", {}).keys())
        source_name = item.get("source", {}).get("name")
        if source_name and source_name != "External CPI Proxy":
            sources.add(source_name)
    if db_available():
        live = db.read_sql(
            """
            SELECT DISTINCT store_name
              FROM daily_prices
             WHERE date = (SELECT MAX(date) FROM daily_prices)
               AND price IS NOT NULL
               AND COALESCE(scrape_status, 'ok') = 'ok'
            """,
            db=_DB,
        )
        sources.update(str(s) for s in live["store_name"].tolist() if s)
    return n_items, len(sources)


def get_latest_inflation_kpi() -> dict:
    """Return dict with keys: value, dod_pct, latest_date — for the Overview KPI."""
    df = load_index_history(db_fingerprint())
    if df.empty:
        return {"value": None, "dod_pct": 0.0, "latest_date": "N/A"}

    latest = df["index_value"].iloc[-1]
    latest_date = df["date"].iloc[-1]

    if len(df) >= 2:
        prev = df["index_value"].iloc[-2]
        dod_pct = ((latest - prev) / prev) * 100 if prev != 0 else 0.0
    else:
        dod_pct = 0.0

    return {
        "value": round(latest, 4),
        "dod_pct": round(dod_pct, 2),
        "latest_date": latest_date.strftime("%Y-%m-%d") if hasattr(latest_date, "strftime") else str(latest_date),
    }


@st.cache_data(ttl=300)
def db_available() -> bool:
    """True when the inflation database is reachable (file exists / PG up).

    Cached (ttl=300): the sidebar status dots call db_available() for all
    three databases on every rerun — uncached that is 3 cross-region pings
    per interaction. A 5-minute cache removes that per-render cost.
    """
    return db.ping(_DB)


# ──────────────────────────────────────────────────────────────────────────
#  Promotional-sale filter view (lets the dashboard show what was excluded)
# ──────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_promo_events(threshold: float = 0.10) -> pd.DataFrame:
    """Return every per-(item, store, date) row that the promo filter rejected.

    Uses the SAME logic as calculator._apply_promo_filter so the dashboard
    and the calculator never disagree. Pulls a small helper from calculator
    rather than re-implementing it.

    Output columns:
        date, item_id, item_name, category, store_name,
        price (raw), clean_price (carried forward), discount_pct
    """
    if not db_available():
        return pd.DataFrame()

    # Inline import — keeps `mod_inflation` cheap when the calculator
    # isn't wanted (avoids the module-level argparse import etc.).
    from calculator import _apply_promo_filter   # noqa: WPS433

    raw = db.read_sql(
        """
        SELECT dp.date,
               dp.item_id,
               dp.store_name,
               dp.price,
               COALESCE(
                   dp.scrape_status,
                   CASE WHEN dp.price IS NULL THEN 'not_found' ELSE 'ok' END
               ) AS scrape_status,
               dp.failure_reason,
               COALESCE(dp.match_tier, 'exact') AS match_tier,
               dp.observed_title,
               dp.match_notes,
               i.name     AS item_name,
               i.category
          FROM daily_prices dp
          JOIN items i ON i.id = dp.item_id
         ORDER BY dp.date, dp.item_id, dp.store_name
        """,
        db=_DB,
    )

    if raw.empty:
        return raw

    cleaned = _apply_promo_filter(raw, threshold=threshold)
    promos = cleaned[cleaned["is_promo"]].copy()
    if promos.empty:
        return promos

    promos["discount_pct"] = (
        (1.0 - promos["price"] / promos["clean_price"]) * 100.0
    ).round(2)
    return promos[
        ["date", "item_id", "item_name", "category", "store_name",
         "price", "clean_price", "discount_pct"]
    ].sort_values(["date", "category", "item_name"])
