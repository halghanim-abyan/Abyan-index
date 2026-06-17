"""
mod_robo.py — Robo-Advisor Portfolio Performance.

Pulls last-close prices for US ETFs via yfinance, computes per-ETF daily
returns, then aggregates weighted returns for each portfolio config.

Portfolio configs live here so the API and UI stay in sync via one source
of truth.

Returned payload shape (build_payload):
    {
        "as_of_date": "2026-04-21",
        "previous_date": "2026-04-18",
        "portfolios": [
            {
                "name": "Abyan Growth Portfolio",
                "tag": "Shariah",
                "weighted_return_pct": 1.24,
                "holdings": [
                    {"ticker": "HLAL", "weight": 0.60, "daily_return_pct": 1.12,
                     "close": 52.31, "prev_close": 51.73, "contribution_pct": 0.672},
                    ...
                ]
            },
            ...
        ],
        "etf_returns": {"HLAL": 1.12, "SPUS": 1.42, ...},
        "data_status": "ok" | "stale" | "empty",
        "errors": [...],
    }
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]
    _YF_AVAILABLE = False


# ---------------------------------------------------------------------------
# Strategic Portfolio Catalogue — hardcoded source of truth
#
# Eight portfolios across four risk tiers, each tier pairing one Abyan
# (`is_house=True`) portfolio against the matching Standard benchmark
# (`is_house=False`). Weights are fractions and sum to 1.00 per portfolio.
# Tickers are real Yahoo Finance symbols:
#   1120.SR   = Al Rajhi Bank (Tadawul)
#   ISDW.L    = iShares MSCI World Islamic UCITS (LSE)
#   ISDE.L    = iShares MSCI Emerging Markets Islamic UCITS (LSE)
#   SPTE/HLAL/SPWO/IBIT/GLD/SPSK/SLV/SPUS/SPRE = US-listed ETFs
# ---------------------------------------------------------------------------

# Risk tier labels used to pair Abyan vs Standard for head-to-head views.
TIER_ULTRA    = "ultra_growth"
TIER_GROWTH   = "growth"
TIER_BALANCED = "balanced"
TIER_SAFE     = "safe"

TIER_LABELS_AR: dict[str, str] = {
    TIER_ULTRA:    "النمو الفائق",
    TIER_GROWTH:   "النمو",
    TIER_BALANCED: "المتوازنة",
    TIER_SAFE:     "الآمنة",
}
TIER_LABELS_EN: dict[str, str] = {
    TIER_ULTRA:    "Ultra Growth",
    TIER_GROWTH:   "Growth",
    TIER_BALANCED: "Balanced",
    TIER_SAFE:     "Conservative",
}
TIER_ORDER: list[str] = [TIER_ULTRA, TIER_GROWTH, TIER_BALANCED, TIER_SAFE]


# Universal benchmark — used as the comparison side for ALL 4 Abyan tiers.
TIER_BENCHMARK = "benchmark"

PORTFOLIOS: list[dict[str, Any]] = [
    # ── 4 Abyan house portfolios ───────────────────────────────────────────
    {
        "name":     "أبيان النمو الفائق",
        "name_en":  "Abyan Ultra Growth",
        "tag":      "Aggressive Shariah",
        "is_house": True,
        "risk_tier": TIER_ULTRA,
        "holdings": {"SPTE": 0.45, "HLAL": 0.35, "SPWO": 0.10,
                     "1120.SR": 0.05, "IBIT": 0.05},
    },
    {
        "name":     "أبيان النمو",
        "name_en":  "Abyan Growth",
        "tag":      "Shariah Growth",
        "is_house": True,
        "risk_tier": TIER_GROWTH,
        "holdings": {"SPTE": 0.05, "HLAL": 0.45, "SPWO": 0.20,
                     "1120.SR": 0.06, "GLD": 0.10, "SPSK": 0.10, "SLV": 0.04},
    },
    {
        "name":     "أبيان المتوازنة",
        "name_en":  "Abyan Balanced",
        "tag":      "Shariah Balanced",
        "is_house": True,
        "risk_tier": TIER_BALANCED,
        "holdings": {"HLAL": 0.30, "SPWO": 0.10, "1120.SR": 0.10,
                     "GLD": 0.10, "SPSK": 0.30, "SPRE": 0.10},
    },
    {
        "name":     "أبيان الآمنة",
        "name_en":  "Abyan Conservative",
        "tag":      "Capital Preservation",
        "is_house": True,
        "risk_tier": TIER_SAFE,
        "holdings": {"HLAL": 0.05, "SPWO": 0.05, "1120.SR": 0.05,
                     "IBIT": 0.05, "GLD": 0.10, "SPSK": 0.70},
    },
    # ── محافظ تمرة (Tamra Portfolios) ──────────────────────────────────────
    {
        "name":     "محفظة تمرة: النمو الفائق",
        "name_en":  "Tamra Ultra Growth",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_ULTRA,
        "holdings": {"GLD": 0.10, "SPSK": 0.11, "SPRE": 0.05,
                     "SPUS": 0.54, "ISDW.L": 0.10, "ISDE.L": 0.10},
    },
    {
        "name":     "محفظة تمرة: النمو",
        "name_en":  "Tamra Growth",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_GROWTH,
        "holdings": {"GLD": 0.10, "SPSK": 0.30, "SPRE": 0.10,
                     "SPUS": 0.35, "ISDW.L": 0.10, "ISDE.L": 0.05},
    },
    {
        "name":     "محفظة تمرة: المتوازنة",
        "name_en":  "Tamra Balanced",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_BALANCED,
        "holdings": {"GLD": 0.10, "SPSK": 0.40, "SPRE": 0.10,
                     "SPUS": 0.30, "ISDW.L": 0.05, "ISDE.L": 0.05},
    },
    {
        "name":     "محفظة تمرة: الآمنة",
        "name_en":  "Tamra Conservative",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_SAFE,
        "holdings": {"GLD": 0.10, "SPSK": 0.60, "SPRE": 0.05,
                     "SPUS": 0.20, "ISDW.L": 0.05},
    },
    # ── محافظ دراهم (Drahim Portfolios) ────────────────────────────────────
    {
        "name":     "محفظة دراهم: العالية",
        "name_en":  "Drahim High",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_GROWTH,
        "holdings": {"SPWO": 0.10, "SPSK": 0.10, "SPRE": 0.20,
                     "SPUS": 0.40, "1120.SR": 0.20},
    },
    {
        "name":     "محفظة دراهم: المتوازنة",
        "name_en":  "Drahim Balanced",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_BALANCED,
        "holdings": {"SPWO": 0.05, "SPSK": 0.45, "SPRE": 0.15,
                     "SPUS": 0.30, "1120.SR": 0.05},
    },
    {
        "name":     "محفظة دراهم: الآمنة",
        "name_en":  "Drahim Safe",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_SAFE,
        "holdings": {"SPWO": 0.05, "SPSK": 0.70, "SPRE": 0.15,
                     "SPUS": 0.10},
    },
    # ── محافظ ملاءة (Malaa Portfolios) ─────────────────────────────────────
    {
        "name":     "محفظة ملاءة: العالية جداً",
        "name_en":  "Malaa Ultra Growth",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_ULTRA,
        "holdings": {"HLAL": 0.30, "GLD": 0.05, "SPRE": 0.05,
                     "STEF": 0.27, "UMMA": 0.23, "ITFS": 0.05, "SRTF": 0.05},
    },
    {
        "name":     "محفظة ملاءة: العالية",
        "name_en":  "Malaa Growth",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_GROWTH,
        "holdings": {"HLAL": 0.20, "GLD": 0.10, "SPSK": 0.10, "SPRE": 0.05,
                     "STEF": 0.20, "UMMA": 0.20, "ITFS": 0.10, "SRTF": 0.05},
    },
    {
        "name":     "محفظة ملاءة: المتوسطة",
        "name_en":  "Malaa Balanced",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_BALANCED,
        "holdings": {"HLAL": 0.15, "GLD": 0.10, "SPSK": 0.15, "SPRE": 0.05,
                     "STEF": 0.18, "UMMA": 0.12, "ITFS": 0.15, "SRTF": 0.10},
    },
    {
        "name":     "محفظة ملاءة: الآمنة",
        "name_en":  "Malaa Conservative",
        "tag":      "Competitor Portfolio",
        "is_house": False,
        "risk_tier": TIER_SAFE,
        "holdings": {"HLAL": 0.10, "GLD": 0.10, "SPSK": 0.20, "SPRE": 0.10,
                     "STEF": 0.10, "UMMA": 0.05, "ITFS": 0.20, "SRTF": 0.15},
    },
    # ── Universal index benchmark — 100% SPUS ──────────────────────────────
    {
        "name":     "المعيار: مؤشر SPUS",
        "name_en":  "Standard: SPUS Index",
        "tag":      "Shariah US Benchmark",
        "is_house": False,
        "risk_tier": TIER_BENCHMARK,
        "holdings": {"SPUS": 1.0},
    },
]


def unique_tickers() -> list[str]:
    """De-duplicated list of ETF symbols used across all portfolios.

    Note: international tickers (e.g. `1120.SR`, `ISDW.L`) are preserved as-is —
    the suffix is part of the Yahoo Finance symbol.
    """
    seen: set[str] = set()
    for p in PORTFOLIOS:
        for t in p["holdings"]:
            seen.add(str(t).upper())
    return sorted(seen)


def get_universal_benchmark() -> dict[str, Any] | None:
    """Return the single universal index benchmark portfolio (100% SPUS).

    Used as a sensible default for cross-tier comparisons. The 4 tier-specific
    competitor portfolios still exist alongside this one (they're matched by
    `risk_tier`); this helper specifically returns the SPUS index.
    """
    return next(
        (p for p in PORTFOLIOS if p.get("risk_tier") == TIER_BENCHMARK),
        None,
    )


def get_tier_pair(risk_tier: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (house_portfolio, benchmark_portfolio) for the given tier.

    The house side is the Abyan portfolio at that tier. The benchmark side
    falls back to the universal SPUS benchmark since there are no longer
    tier-specific competitor portfolios.
    """
    house = next((p for p in PORTFOLIOS
                  if p.get("risk_tier") == risk_tier and p.get("is_house")), None)
    # Try a tier-specific benchmark first (none exist now, but kept for forward-
    # compat); fall back to the single universal SPUS benchmark.
    bench = next((p for p in PORTFOLIOS
                  if p.get("risk_tier") == risk_tier and not p.get("is_house")), None)
    if bench is None:
        bench = get_universal_benchmark()
    return house, bench


def get_portfolio_by_name(name: str) -> dict[str, Any] | None:
    """Look up a portfolio by Arabic name or English (`name_en`). Case-insensitive."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for p in PORTFOLIOS:
        if (p.get("name", "").strip().lower() == needle
                or p.get("name_en", "").strip().lower() == needle):
            return p
    return None


# ---------------------------------------------------------------------------
# Price fetch — cached to avoid hammering Yahoo
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_closes(tickers: tuple[str, ...]) -> tuple[pd.DataFrame, list[str]]:
    """Fetch last ~5 trading days of adjusted closes.

    Returns (DataFrame indexed by date with one column per ticker, list_of_errors).
    Uses a tuple argument because Streamlit's cache key must be hashable.
    """
    errors: list[str] = []

    if not _YF_AVAILABLE:
        return pd.DataFrame(), ["yfinance not installed"]

    if not tickers:
        return pd.DataFrame(), ["no tickers provided"]

    try:
        raw = yf.download(
            list(tickers),
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="column",
        )
    except Exception as exc:
        return pd.DataFrame(), [f"yfinance.download failed: {exc}"]

    if raw is None or raw.empty:
        return pd.DataFrame(), ["yfinance returned no data"]

    # Normalize: we always want a frame where columns == tickers and values == closes.
    if isinstance(raw.columns, pd.MultiIndex):
        # Common shape: top level = price field ('Close', 'Open', ...), second = ticker
        if "Close" in raw.columns.get_level_values(0):
            closes = raw["Close"].copy()
        elif "Adj Close" in raw.columns.get_level_values(0):
            closes = raw["Adj Close"].copy()
        else:
            # Inverted: top level = ticker. Try xs for each.
            try:
                closes = raw.xs("Close", axis=1, level=-1)
            except KeyError:
                return pd.DataFrame(), ["unexpected yfinance column layout"]
    else:
        # Single-ticker call returns a flat frame.
        if "Close" in raw.columns:
            closes = raw[["Close"]].copy()
            closes.columns = list(tickers)
        else:
            return pd.DataFrame(), ["unexpected yfinance column layout (single)"]

    closes = closes.dropna(how="all")
    if closes.empty:
        return pd.DataFrame(), ["all closes were NaN"]

    # Flag tickers that came back entirely NaN.
    for t in tickers:
        if t not in closes.columns or closes[t].dropna().empty:
            errors.append(f"no data for {t}")

    return closes, errors


# ---------------------------------------------------------------------------
# Return math
# ---------------------------------------------------------------------------

def compute_etf_returns(closes: pd.DataFrame) -> dict[str, dict[str, float]]:
    """For each ticker, return {close, prev_close, daily_return_pct}.

    Uses the last two *non-NaN* observations per ticker (handles staggered
    holidays across markets cleanly).
    """
    out: dict[str, dict[str, float]] = {}
    if closes is None or closes.empty:
        return out

    for ticker in closes.columns:
        series = pd.to_numeric(closes[ticker], errors="coerce").dropna()
        if len(series) < 2:
            continue
        prev_close = float(series.iloc[-2])
        close = float(series.iloc[-1])
        if prev_close == 0 or pd.isna(prev_close) or pd.isna(close):
            continue
        daily_pct = (close - prev_close) / prev_close * 100.0
        out[str(ticker).upper()] = {
            "close": round(close, 4),
            "prev_close": round(prev_close, 4),
            "daily_return_pct": round(daily_pct, 4),
        }
    return out


def compute_portfolio_returns(
    etf_returns: dict[str, dict[str, float]],
    portfolios: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Apply portfolio weights to the per-ETF returns.

    If `portfolios` is None, uses the built-in PORTFOLIOS config.
    Otherwise processes the supplied list — used for dynamic / user-defined
    portfolios from POST /api/portfolio-compare.
    """
    configs = portfolios if portfolios is not None else PORTFOLIOS
    results: list[dict[str, Any]] = []

    for p in configs:
        holdings_out: list[dict[str, Any]] = []
        weighted_sum = 0.0
        total_weight_used = 0.0

        for ticker, weight in p["holdings"].items():
            data = etf_returns.get(ticker.upper())
            if data is None:
                holdings_out.append({
                    "ticker": ticker,
                    "weight": weight,
                    "daily_return_pct": None,
                    "close": None,
                    "prev_close": None,
                    "contribution_pct": 0.0,
                    "missing": True,
                })
                continue
            contribution = weight * data["daily_return_pct"]
            weighted_sum += contribution
            total_weight_used += weight
            holdings_out.append({
                "ticker": ticker,
                "weight": weight,
                "daily_return_pct": data["daily_return_pct"],
                "close": data["close"],
                "prev_close": data["prev_close"],
                "contribution_pct": round(contribution, 4),
                "missing": False,
            })

        # Renormalize if some tickers were missing — so a partial fetch
        # doesn't silently skew the portfolio return downward.
        if 0 < total_weight_used < 1:
            portfolio_pct = weighted_sum / total_weight_used
        else:
            portfolio_pct = weighted_sum

        results.append({
            "name": p["name"],
            "tag": p.get("tag", ""),
            "is_house": p.get("is_house", False),
            "weighted_return_pct": round(portfolio_pct, 4) if total_weight_used > 0 else None,
            "coverage_pct": round(total_weight_used * 100, 1),
            "holdings": holdings_out,
        })

    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_payload() -> dict[str, Any]:
    """Top-level: fetch → compute ETF returns → compute portfolio returns → pack."""
    tickers = tuple(unique_tickers())
    closes, errors = fetch_closes(tickers)

    etf_returns = compute_etf_returns(closes)
    portfolio_returns = compute_portfolio_returns(etf_returns)

    # Dates (if available)
    as_of = prev = None
    if not closes.empty:
        valid_dates = closes.dropna(how="all").index
        if len(valid_dates) >= 2:
            as_of = pd.to_datetime(valid_dates[-1]).date().isoformat()
            prev = pd.to_datetime(valid_dates[-2]).date().isoformat()
        elif len(valid_dates) == 1:
            as_of = pd.to_datetime(valid_dates[-1]).date().isoformat()

    if not etf_returns:
        data_status = "empty"
    elif len(etf_returns) < len(tickers):
        data_status = "stale"
    else:
        data_status = "ok"

    return {
        "as_of_date": as_of or date.today().isoformat(),
        "previous_date": prev,
        "portfolios": portfolio_returns,
        "etf_returns": {t: d["daily_return_pct"] for t, d in etf_returns.items()},
        "etf_details": etf_returns,
        "data_status": data_status,
        "errors": errors,
    }


def is_available() -> bool:
    """True if the yfinance library is importable."""
    return _YF_AVAILABLE


# ---------------------------------------------------------------------------
# Historical time-series — for the cumulative-return line chart
# ---------------------------------------------------------------------------

# Periods we accept from the UI / API. Mirrors yfinance's `period` arg.
ALLOWED_PERIODS: tuple[str, ...] = ("1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_historical_closes(
    tickers: tuple[str, ...],
    period: str = "6mo",
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch historical adjusted closes for a list of tickers.

    Returns (DataFrame indexed by date with one column per ticker, list_of_errors).
    Cached for 1 hour to avoid repeated Yahoo hits when the user toggles the
    multi-select. Tuple parameter so the cache key is hashable.
    """
    errors: list[str] = []

    if not _YF_AVAILABLE:
        return pd.DataFrame(), ["yfinance not installed"]
    if not tickers:
        return pd.DataFrame(), ["no tickers provided"]
    if period not in ALLOWED_PERIODS:
        return pd.DataFrame(), [f"unsupported period: {period!r}"]

    try:
        raw = yf.download(
            list(tickers),
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="column",
        )
    except Exception as exc:
        return pd.DataFrame(), [f"yfinance.download failed: {exc}"]

    if raw is None or raw.empty:
        return pd.DataFrame(), ["yfinance returned no data"]

    # Same column-flattening logic as fetch_closes() — keep behaviour consistent.
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            closes = raw["Close"].copy()
        elif "Adj Close" in raw.columns.get_level_values(0):
            closes = raw["Adj Close"].copy()
        else:
            try:
                closes = raw.xs("Close", axis=1, level=-1)
            except KeyError:
                return pd.DataFrame(), ["unexpected yfinance column layout"]
    else:
        if "Close" in raw.columns:
            closes = raw[["Close"]].copy()
            closes.columns = list(tickers)
        else:
            return pd.DataFrame(), ["unexpected yfinance column layout (single)"]

    closes = closes.dropna(how="all").sort_index()
    if closes.empty:
        return pd.DataFrame(), ["all closes were NaN"]

    for t in tickers:
        if t not in closes.columns or closes[t].dropna().empty:
            errors.append(f"no historical data for {t}")

    return closes, errors


def compute_cumulative_returns(
    closes: pd.DataFrame,
    portfolios: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Compute the daily cumulative weighted return for each portfolio.

    Math:
        For each portfolio p:
            ticker_daily[t]   = closes[t].pct_change()              # daily simple returns
            portfolio_daily   = Σ_t  weight[t] * ticker_daily[t]    # weighted avg
            cumulative[d]     = ∏_{i ≤ d} (1 + portfolio_daily[i]) − 1   # cumprod

    If a ticker is missing entirely, its weight is dropped and the remaining
    weights are renormalized so the cumulative line still sums to ~100% coverage
    (same renormalization logic as `compute_portfolio_returns`).

    Returns a tidy long-format DataFrame:
        date | portfolio_name | cumulative_return_pct | coverage_pct
    """
    configs = portfolios if portfolios is not None else PORTFOLIOS

    if closes is None or closes.empty:
        return pd.DataFrame(columns=["date", "portfolio_name", "cumulative_return_pct", "coverage_pct"])

    # pct_change is NaN on the first row by definition. Fill with 0 so cumprod
    # has a clean t=0 anchor (cumulative return = 0% on day 1).
    daily_returns = closes.pct_change().fillna(0.0)

    rows: list[dict[str, Any]] = []
    for p in configs:
        weights_raw: dict[str, float] = {
            str(t).upper(): float(w) for t, w in p["holdings"].items()
        }

        # Drop tickers that don't have data; renormalize over what remains.
        present = {t: w for t, w in weights_raw.items() if t in daily_returns.columns}
        total_w = sum(present.values())
        if total_w <= 0:
            continue
        weights = {t: (w / total_w) for t, w in present.items()}
        coverage_pct = round(total_w * 100, 1)

        # Weighted-sum across tickers → portfolio daily return series
        cols = list(weights.keys())
        weighted_daily = (daily_returns[cols] * pd.Series(weights)).sum(axis=1)

        # Cumulative compounded return → percentage points
        cumulative_pct = ((1.0 + weighted_daily).cumprod() - 1.0) * 100.0

        for ts, val in cumulative_pct.items():
            if pd.isna(val):
                continue
            rows.append({
                "date": pd.to_datetime(ts).date().isoformat(),
                "portfolio_name": p["name"],
                "name_en": p.get("name_en", p["name"]),
                "is_house": bool(p.get("is_house", False)),
                "risk_tier": p.get("risk_tier", ""),
                "cumulative_return_pct": round(float(val), 4),
                "coverage_pct": coverage_pct,
            })

    return pd.DataFrame(rows)


def build_historical_payload(period: str = "6mo") -> dict[str, Any]:
    """Top-level: fetch historical closes → compute cumulative returns → pack.

    Output shape:
        {
            "period": "6mo",
            "as_of_date": "2026-04-21",
            "start_date": "2025-10-22",
            "tickers": ["1120.SR", "GLD", ...],
            "portfolios": [
                {"name": "أبيان النمو الفائق", "name_en": "Abyan Ultra Growth",
                 "is_house": true, "risk_tier": "ultra_growth", "coverage_pct": 100.0,
                 "final_return_pct": 12.34},
                ...
            ],
            "series": [
                {"date": "2025-10-22", "portfolio_name": "أبيان النمو الفائق",
                 "cumulative_return_pct": 0.0},
                ...
            ],
            "data_status": "ok" | "stale" | "empty",
            "errors": [...],
        }
    """
    if period not in ALLOWED_PERIODS:
        period = "6mo"

    tickers = tuple(unique_tickers())
    closes, errors = fetch_historical_closes(tickers, period=period)
    series_df = compute_cumulative_returns(closes)

    if not series_df.empty:
        # Per-portfolio summary: latest cumulative return + coverage
        summary: list[dict[str, Any]] = []
        for name, grp in series_df.groupby("portfolio_name", sort=False):
            last = grp.iloc[-1]
            summary.append({
                "name":             name,
                "name_en":          str(last["name_en"]),
                "is_house":         bool(last["is_house"]),
                "risk_tier":        str(last["risk_tier"]),
                "coverage_pct":     float(last["coverage_pct"]),
                "final_return_pct": float(last["cumulative_return_pct"]),
            })

        as_of_date = series_df["date"].max()
        start_date = series_df["date"].min()
    else:
        summary = []
        as_of_date = None
        start_date = None

    if series_df.empty:
        data_status = "empty"
    elif errors:
        data_status = "stale"
    else:
        data_status = "ok"

    # Convert series_df to a list of dicts — Pydantic and json serializable.
    series_records = series_df[
        ["date", "portfolio_name", "cumulative_return_pct"]
    ].to_dict(orient="records") if not series_df.empty else []

    return {
        "period":         period,
        "as_of_date":     as_of_date,
        "start_date":     start_date,
        "tickers":        list(tickers),
        "portfolios":     summary,
        "series":         series_records,
        "data_status":    data_status,
        "errors":         errors,
    }


# ---------------------------------------------------------------------------
# Dynamic backtest — accepts an arbitrary list of user-defined sleeves
# from the React frontend (name + ticker + weight per row).
# ---------------------------------------------------------------------------

def run_dynamic_backtest(
    sleeves: list[dict[str, Any]],
    period: str = "6mo",
    portfolio_label: str = "Custom Portfolio",
) -> dict[str, Any]:
    """Run a historical cumulative-return backtest on a user-defined portfolio.

    `sleeves` is the dynamic list emitted by the Dashboard.jsx CRUD UI:
        [
            {"id": 1, "name": "Core",   "ticker": "SPY",  "weight": 40},
            {"id": 2, "name": "Growth", "ticker": "QQQ",  "weight": 30},
            {"id": 3, "name": "Bonds",  "ticker": "AGG",  "weight": 30},
        ]

    Weights may be expressed as percentages (0-100) or fractions (0-1);
    the engine auto-detects via the `>1.5` heuristic in normalize_custom_portfolios.

    Validation:
        * At least one sleeve required.
        * Weights must sum to ~100% (1% tolerance) — caller should enforce
          stricter UI gating but the engine refuses garbage too.
        * Each sleeve needs a non-empty ticker; the human-readable `name`
          is preserved in the response so the UI can render it.

    Returns the same shape as build_historical_payload(), so the React side
    can reuse one rendering path:

        {
            "period":      "6mo",
            "as_of_date":  "2026-04-21",
            "start_date":  "2025-10-22",
            "tickers":     ["SPY", "QQQ", "AGG"],
            "portfolios":  [{"name": "Custom Portfolio", "final_return_pct": 7.41,
                             "coverage_pct": 100.0, "is_house": True,
                             "risk_tier": "custom"}],
            "sleeves":     [{"name": "Core", "ticker": "SPY", "weight_pct": 40.0,
                             "final_return_pct": 12.34}, ...],
            "series":      [{"date": "...", "portfolio_name": "Custom Portfolio",
                             "cumulative_return_pct": 0.0}, ...],
            "data_status": "ok",
            "errors":      [],
        }

    Raises ValueError on malformed input so the FastAPI layer returns 400.
    """
    if period not in ALLOWED_PERIODS:
        raise ValueError(
            f"Unsupported period {period!r}. Allowed: {', '.join(ALLOWED_PERIODS)}"
        )

    if not sleeves or not isinstance(sleeves, list):
        raise ValueError("At least one portfolio sleeve is required.")

    # Build the holdings dict: ticker -> weight (auto % vs fraction detection).
    holdings: dict[str, float] = {}
    sleeve_meta: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()

    for idx, sleeve in enumerate(sleeves):
        if not isinstance(sleeve, dict):
            raise ValueError(f"Sleeve #{idx + 1} must be an object.")

        name   = str(sleeve.get("name", "")).strip() or f"Sleeve {idx + 1}"
        ticker = str(sleeve.get("ticker", "")).strip().upper()
        weight = sleeve.get("weight")

        if not ticker:
            raise ValueError(f"Sleeve {name!r} is missing a ticker.")
        if ticker in seen_tickers:
            raise ValueError(f"Duplicate ticker in sleeves: {ticker!r}")
        seen_tickers.add(ticker)

        try:
            w = float(weight)
        except (TypeError, ValueError):
            raise ValueError(f"Sleeve {name!r}: weight is not a number ({weight!r}).")
        if w < 0:
            raise ValueError(f"Sleeve {name!r}: weight must be non-negative.")

        # Stash original weight for the response BEFORE % -> fraction conversion.
        sleeve_meta.append({
            "id":         sleeve.get("id", idx + 1),
            "name":       name,
            "ticker":     ticker,
            "weight_pct": w if w > 1.5 else w * 100.0,
        })

        # Engine wants fractions internally.
        holdings[ticker] = w / 100.0 if w > 1.5 else w

    total_frac = sum(holdings.values())
    if total_frac <= 0:
        raise ValueError("Sum of weights must be > 0.")
    if abs(total_frac - 1.0) > 0.01:
        raise ValueError(
            f"Weights must sum to 100% (got {total_frac * 100:.1f}%)."
        )

    # Single synthetic portfolio in the standard catalogue shape.
    custom_portfolio = {
        "name":      portfolio_label,
        "name_en":   portfolio_label,
        "tag":       "Custom Backtest",
        "is_house":  True,
        "risk_tier": "custom",
        "holdings":  holdings,
    }

    # Fetch historical closes for the union of unique tickers.
    tickers = tuple(sorted(holdings.keys()))
    closes, errors = fetch_historical_closes(tickers, period=period)
    series_df = compute_cumulative_returns(closes, portfolios=[custom_portfolio])

    # Per-sleeve cumulative returns (for the breakdown table in the UI).
    sleeve_finals: dict[str, float | None] = {t: None for t in tickers}
    if not closes.empty:
        for t in tickers:
            if t not in closes.columns:
                continue
            ser = pd.to_numeric(closes[t], errors="coerce").dropna()
            if len(ser) < 2:
                continue
            sleeve_finals[t] = round((ser.iloc[-1] / ser.iloc[0] - 1.0) * 100.0, 4)

    for meta in sleeve_meta:
        meta["final_return_pct"] = sleeve_finals.get(meta["ticker"])

    # Top-level summary
    if not series_df.empty:
        last_row = series_df.iloc[-1]
        summary = [{
            "name":             portfolio_label,
            "name_en":          portfolio_label,
            "is_house":         True,
            "risk_tier":        "custom",
            "coverage_pct":     float(last_row["coverage_pct"]),
            "final_return_pct": float(last_row["cumulative_return_pct"]),
        }]
        as_of_date = series_df["date"].max()
        start_date = series_df["date"].min()
    else:
        summary = []
        as_of_date = None
        start_date = None

    if series_df.empty:
        data_status = "empty"
    elif errors:
        data_status = "stale"
    else:
        data_status = "ok"

    series_records = series_df[
        ["date", "portfolio_name", "cumulative_return_pct"]
    ].to_dict(orient="records") if not series_df.empty else []

    return {
        "period":         period,
        "as_of_date":     as_of_date,
        "start_date":     start_date,
        "tickers":        list(tickers),
        "portfolios":     summary,
        "sleeves":        sleeve_meta,
        "series":         series_records,
        "data_status":    data_status,
        "errors":         errors,
    }


# ---------------------------------------------------------------------------
# Custom / dynamic portfolios
# ---------------------------------------------------------------------------

def normalize_custom_portfolios(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize user-supplied portfolio configs.

    Accepts any of these shapes (field aliases are interchangeable):
        # Legacy keys:
        {"name": "...", "holdings": {"HLAL": 0.6, "SPUS": 0.4}}           # fractional
        {"name": "...", "holdings": [{"ticker": "HLAL", "weight": 60}]}   # %
        # New multi-constituent grouping keys:
        {"portfolio_name": "Abyan Ultra Growth",
         "constituents":  [{"ticker": "SPTE", "weight": 45},
                           {"ticker": "HLAL", "weight": 35},
                           {"ticker": "SPUK", "weight": 20}]}

    Normalizes weights to fractions (0-1 range) and uppercases tickers.
    Raises ValueError on malformed input.
    """
    out: list[dict[str, Any]] = []
    if not raw:
        raise ValueError("At least one portfolio is required.")

    seen_names: set[str] = set()

    for idx, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError(f"Portfolio #{idx + 1} must be an object.")

        # Accept either "name" or "portfolio_name" (multi-constituent grouping).
        name = str(p.get("portfolio_name") or p.get("name") or "").strip()
        if not name:
            raise ValueError(f"Portfolio #{idx + 1} is missing a name.")
        if name.lower() in seen_names:
            raise ValueError(f"Duplicate portfolio name: {name!r}")
        seen_names.add(name.lower())

        # Accept either "holdings" or "constituents" for the list of ETFs.
        holdings_raw = p.get("constituents")
        if holdings_raw is None:
            holdings_raw = p.get("holdings")
        if isinstance(holdings_raw, dict):
            items = [(t, w) for t, w in holdings_raw.items()]
        elif isinstance(holdings_raw, list):
            items = []
            for h in holdings_raw:
                if not isinstance(h, dict):
                    raise ValueError(f"Portfolio {name!r}: each holding must be an object.")
                ticker = h.get("ticker")
                weight = h.get("weight")
                if ticker is None or weight is None:
                    raise ValueError(f"Portfolio {name!r}: holding needs 'ticker' and 'weight'.")
                items.append((ticker, weight))
        else:
            raise ValueError(f"Portfolio {name!r}: holdings must be a dict or list.")

        if not items:
            raise ValueError(f"Portfolio {name!r} has no holdings.")

        # Clean, de-dupe (sum repeated tickers), convert to fractions.
        cleaned: dict[str, float] = {}
        for ticker, weight in items:
            t = str(ticker).strip().upper()
            if not t:
                continue
            try:
                w = float(weight)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Portfolio {name!r}: weight for {t!r} is not a number ({weight!r})."
                )
            if w < 0:
                raise ValueError(f"Portfolio {name!r}: negative weight for {t!r}.")
            # Assume anything > 1.5 is percentage-form (e.g. 60 meaning 60%)
            if w > 1.5:
                w = w / 100.0
            cleaned[t] = cleaned.get(t, 0.0) + w

        if not cleaned:
            raise ValueError(f"Portfolio {name!r}: no valid tickers after cleaning.")

        total = sum(cleaned.values())
        if total <= 0:
            raise ValueError(f"Portfolio {name!r}: weights sum to zero.")

        out.append({
            "name": name,
            "tag": str(p.get("tag", "") or ""),
            "is_house": bool(p.get("is_house", False)),
            "holdings": cleaned,
            "raw_weight_total": round(total, 4),
        })

    return out


def build_custom_payload(custom_portfolios: list[dict[str, Any]]) -> dict[str, Any]:
    """Orchestrator for user-defined portfolios.

    Same fetch + compute pipeline as build_payload(), but the portfolio configs
    come from the caller instead of the module-level PORTFOLIOS list.
    """
    normalized = normalize_custom_portfolios(custom_portfolios)

    # Collect unique tickers across all supplied portfolios.
    tickers: list[str] = sorted({t for p in normalized for t in p["holdings"]})
    closes, errors = fetch_closes(tuple(tickers))

    etf_returns = compute_etf_returns(closes)
    portfolio_returns = compute_portfolio_returns(etf_returns, portfolios=normalized)

    as_of = prev = None
    if not closes.empty:
        valid_dates = closes.dropna(how="all").index
        if len(valid_dates) >= 2:
            as_of = pd.to_datetime(valid_dates[-1]).date().isoformat()
            prev = pd.to_datetime(valid_dates[-2]).date().isoformat()
        elif len(valid_dates) == 1:
            as_of = pd.to_datetime(valid_dates[-1]).date().isoformat()

    if not etf_returns:
        data_status = "empty"
    elif len(etf_returns) < len(tickers):
        data_status = "stale"
    else:
        data_status = "ok"

    return {
        "as_of_date": as_of or date.today().isoformat(),
        "previous_date": prev,
        "portfolios": portfolio_returns,
        "etf_returns": {t: d["daily_return_pct"] for t, d in etf_returns.items()},
        "etf_details": etf_returns,
        "data_status": data_status,
        "errors": errors,
        "tickers": tickers,
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    # Bypass Streamlit cache when run as a script.
    fetch_closes.clear() if hasattr(fetch_closes, "clear") else None
    payload = build_payload()
    print(json.dumps(payload, indent=2, default=str))
