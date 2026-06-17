"""
tadawul_api.py — FastAPI backend for the Foreign Liquidity Radar.

Endpoints:
    GET  /                         -> service info
    GET  /health                   -> quick health / data availability check
    GET  /api/foreign-liquidity    -> full payload (sectors + stocks) for the dashboard
    GET  /api/foreign-liquidity/sectors   -> sector roll-up only (lighter response)
    GET  /api/foreign-liquidity/stocks    -> flat stock list, filterable by status/sector
    GET  /api/foreign-liquidity/files     -> list of available data files

Run:
    python -m uvicorn tadawul_api:app --port 8601 --reload
    # or
    python tadawul_api.py
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

import tadawul_processor as tp

# Robo-Advisor module (optional — requires yfinance)
try:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from modules import mod_robo
    _ROBO_AVAILABLE = True
except Exception as _robo_exc:  # pragma: no cover
    mod_robo = None  # type: ignore[assignment]
    _ROBO_AVAILABLE = False
    _ROBO_IMPORT_ERROR = str(_robo_exc)

# Portfolio Composition module (hardcoded Arabic portfolio catalogue)
from modules import mod_portfolio_composition as mpc  # noqa: E402

app = FastAPI(
    title="Tadawul Foreign Liquidity API",
    description=(
        "Processes Saudi Exchange (Tadawul) daily foreign-ownership CSV/Excel "
        "exports and serves Net Foreign Flow data to the Terminal v1.0 dashboard. "
        "Also exposes Robo-Advisor portfolio daily returns via yfinance."
    ),
    version="1.1.0",
)

# CORS — permissive defaults so the Streamlit dashboard (localhost:8504) and
# any local browser can consume the API without setup.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Basic info
# ---------------------------------------------------------------------------

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "Tadawul Foreign Liquidity API",
        "version": app.version,
        "data_dir": tp.DATA_DIR,
        "endpoints": [
            "/health",
            "/api/foreign-liquidity",
            "/api/foreign-liquidity/sectors",
            "/api/foreign-liquidity/stocks",
            "/api/foreign-liquidity/files",
            "/api/robo-advisor/portfolios",
            "/api/robo-advisor/daily-returns",
            "/api/robo-advisor/historical",
            "POST /api/backtest",
            "POST /api/portfolio-compare",
            "/api/portfolios",
            "/api/portfolio/{name}",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    files = tp.list_files()
    return {
        "status": "ok" if files else "no_data",
        "data_dir": tp.DATA_DIR,
        "data_dir_exists": os.path.isdir(tp.DATA_DIR),
        "file_count": len(files),
        "latest_file": os.path.basename(files[0]) if files else None,
    }


@app.get("/api/foreign-liquidity/files")
def list_data_files() -> dict[str, Any]:
    files = tp.list_files()
    return {
        "data_dir": tp.DATA_DIR,
        "count": len(files),
        "files": [
            {
                "name": os.path.basename(p),
                "size_bytes": os.path.getsize(p),
                "modified_ts": os.path.getmtime(p),
            }
            for p in files
        ],
    }


# ---------------------------------------------------------------------------
# Main payload
# ---------------------------------------------------------------------------

def _load_payload(file_name: str | None) -> dict[str, Any]:
    """Resolve file_name -> absolute path, or fall back to latest."""
    if file_name:
        path = os.path.join(tp.DATA_DIR, os.path.basename(file_name))
        if not os.path.isfile(path):
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {file_name}. "
                       f"See GET /api/foreign-liquidity/files for available names.",
            )
        raw = tp.load_raw(path)
        df = tp.compute_net_flow(tp.normalize_columns(raw))
        return tp.build_payload(df, source_file=path)

    return tp.process_latest()


@app.get("/api/foreign-liquidity")
def get_foreign_liquidity(
    file: str | None = Query(
        default=None,
        description="Optional filename inside tadawul_data/. Defaults to latest.",
    ),
    min_abs_flow: float | None = Query(
        default=None,
        ge=0,
        description="Drop stocks whose |net_flow_sar| is below this threshold.",
    ),
) -> dict[str, Any]:
    """Full processed payload: sector roll-up + ranked stocks."""
    payload = _load_payload(file)

    if min_abs_flow:
        payload["stocks"] = [
            s for s in payload["stocks"]
            if abs(float(s.get("net_flow_sar") or 0)) >= min_abs_flow
        ]

    return payload


@app.get("/api/foreign-liquidity/sectors")
def get_sectors(file: str | None = Query(default=None)) -> dict[str, Any]:
    """Sector roll-up only — lighter response for quick polls."""
    payload = _load_payload(file)
    return {
        "as_of_date": payload["as_of_date"],
        "source_file": payload["source_file"],
        "totals": payload["totals"],
        "sectors": payload["sectors"],
    }


@app.get("/api/foreign-liquidity/stocks")
def get_stocks(
    file: str | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="Filter: Accumulation | Distribution | Neutral",
    ),
    sector: str | None = Query(default=None, description="Exact sector name match"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Flat stock list with optional status / sector filtering."""
    payload = _load_payload(file)
    stocks = payload["stocks"]

    if status:
        status_norm = status.strip().capitalize()
        stocks = [s for s in stocks if s.get("status") == status_norm]

    if sector:
        sector_norm = sector.strip().lower()
        stocks = [s for s in stocks if str(s.get("sector", "")).lower() == sector_norm]

    return {
        "as_of_date": payload["as_of_date"],
        "source_file": payload["source_file"],
        "count": len(stocks),
        "returned": min(limit, len(stocks)),
        "stocks": stocks[:limit],
    }


# ---------------------------------------------------------------------------
# Robo-Advisor Portfolio Performance
# ---------------------------------------------------------------------------

def _require_robo() -> None:
    if not _ROBO_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "Robo-Advisor module unavailable. Ensure yfinance is installed: "
                "`pip install yfinance`. "
                f"(Import error: {_ROBO_IMPORT_ERROR})"
            ),
        )


@app.get("/api/robo-advisor/portfolios")
def get_robo_portfolios() -> dict[str, Any]:
    """List portfolio configurations (name, tag, holdings with weights)."""
    _require_robo()
    return {
        "count": len(mod_robo.PORTFOLIOS),
        "unique_tickers": mod_robo.unique_tickers(),
        "portfolios": [
            {
                "name": p["name"],
                "tag": p.get("tag", ""),
                "is_house": p.get("is_house", False),
                "holdings": [
                    {"ticker": t, "weight": w}
                    for t, w in p["holdings"].items()
                ],
            }
            for p in mod_robo.PORTFOLIOS
        ],
    }


@app.get("/api/robo-advisor/daily-returns")
def get_robo_daily_returns(
    refresh: bool = Query(
        default=False,
        description="If true, bypass the in-process price cache and re-fetch.",
    ),
) -> dict[str, Any]:
    """Daily return for each portfolio, plus per-ETF breakdown.

    Data source: yfinance (last ~5 trading days, takes the last two valid closes).
    """
    _require_robo()

    if refresh and hasattr(mod_robo.fetch_closes, "clear"):
        try:
            mod_robo.fetch_closes.clear()
        except Exception:
            pass  # cache clear is best-effort

    return mod_robo.build_payload()


@app.get("/api/robo-advisor/historical")
def get_robo_historical(
    period: str = Query(
        default="6mo",
        description="Lookback period. One of: 1mo, 3mo, 6mo, ytd, 1y, 2y, 5y.",
    ),
    refresh: bool = Query(
        default=False,
        description="If true, bypass the in-process price cache and re-fetch.",
    ),
) -> dict[str, Any]:
    """Historical daily cumulative weighted return for each predefined portfolio.

    Returns a tidy time-series payload suitable for a Plotly line chart:

        {
          "period": "6mo",
          "as_of_date": "2026-04-21",
          "start_date": "2025-10-22",
          "tickers": [...],
          "portfolios": [
            {"name": "أبيان النمو الفائق", "is_house": true, "risk_tier": "ultra_growth",
             "coverage_pct": 100.0, "final_return_pct": 12.34, ...},
            ...
          ],
          "series": [
            {"date": "2025-10-22", "portfolio_name": "أبيان النمو الفائق",
             "cumulative_return_pct": 0.0},
            ...
          ],
          "data_status": "ok",
          "errors": []
        }
    """
    _require_robo()

    if period not in mod_robo.ALLOWED_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported period {period!r}. "
                   f"Allowed: {', '.join(mod_robo.ALLOWED_PERIODS)}.",
        )

    if refresh and hasattr(mod_robo.fetch_historical_closes, "clear"):
        try:
            mod_robo.fetch_historical_closes.clear()
        except Exception:
            pass

    return mod_robo.build_historical_payload(period=period)


# ---------------------------------------------------------------------------
# Dynamic backtest — POST /api/backtest
# Receives a list of user-defined sleeves from the React Dashboard.jsx UI
# and runs the historical cumulative-return pipeline against them.
# ---------------------------------------------------------------------------

class BacktestSleeveIn(BaseModel):
    """One row in the Portfolio Construction CRUD table."""
    id:     int | str | None = None
    name:   str = Field(..., min_length=1, max_length=80)
    ticker: str = Field(..., min_length=1, max_length=12)
    weight: float = Field(..., ge=0)

    @field_validator("ticker")
    @classmethod
    def _upper_strip_ticker(cls, v: str) -> str:
        out = v.strip().upper()
        if not out:
            raise ValueError("ticker cannot be blank")
        return out

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        out = v.strip()
        if not out:
            raise ValueError("name cannot be blank")
        return out


class BacktestRequest(BaseModel):
    """Payload from Dashboard.jsx → POST /api/backtest."""
    period: str = Field(default="6mo")
    portfolio_label: str = Field(default="Custom Portfolio", max_length=80)
    portfolios: list[BacktestSleeveIn] = Field(..., min_length=1, max_length=30)

    @field_validator("period")
    @classmethod
    def _validate_period(cls, v: str) -> str:
        if v not in mod_robo.ALLOWED_PERIODS:
            raise ValueError(
                f"period must be one of {', '.join(mod_robo.ALLOWED_PERIODS)}"
            )
        return v

    @field_validator("portfolios")
    @classmethod
    def _check_weights_sum(cls, v: list[BacktestSleeveIn]) -> list[BacktestSleeveIn]:
        # Auto-detect % vs fraction (matches engine behaviour).
        total = sum(s.weight for s in v)
        if total <= 0:
            raise ValueError("sum of weights must be > 0")

        # If sums look like percentages (>1.5) require ~100; if fractions require ~1.
        if total > 1.5:
            if abs(total - 100.0) > 1.0:
                raise ValueError(
                    f"weights must sum to 100% (got {total:.1f}%)"
                )
        else:
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"weights must sum to 1.00 (got {total:.4f})"
                )

        # Reject duplicate tickers up-front.
        seen: set[str] = set()
        for s in v:
            if s.ticker in seen:
                raise ValueError(f"duplicate ticker in portfolios: {s.ticker}")
            seen.add(s.ticker)

        return v


@app.post("/api/backtest")
def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    """Run a historical cumulative-return backtest on a dynamic portfolio.

    Request body (matches Dashboard.jsx state shape):
        {
          "period": "6mo",
          "portfolio_label": "My Custom Portfolio",
          "portfolios": [
            {"id": 1, "name": "Core",   "ticker": "SPY", "weight": 40},
            {"id": 2, "name": "Growth", "ticker": "QQQ", "weight": 30},
            {"id": 3, "name": "Bonds",  "ticker": "AGG", "weight": 30}
          ]
        }

    Returns the same payload shape as `/api/robo-advisor/historical` plus a
    `sleeves` array with per-sleeve cumulative returns for the breakdown table.
    """
    _require_robo()

    sleeves = [s.model_dump() for s in req.portfolios]
    try:
        return mod_robo.run_dynamic_backtest(
            sleeves=sleeves,
            period=req.period,
            portfolio_label=req.portfolio_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Dynamic portfolio comparison — POST /api/portfolio-compare
# ---------------------------------------------------------------------------

class HoldingIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    weight: float = Field(..., ge=0)

    @field_validator("ticker")
    @classmethod
    def _upper_strip(cls, v: str) -> str:
        out = v.strip().upper()
        if not out:
            raise ValueError("ticker cannot be blank")
        return out


class CustomPortfolioIn(BaseModel):
    # Accept both legacy (`name`, `holdings`) and the new multi-constituent
    # grouping keys (`portfolio_name`, `constituents`). Clients can pass either;
    # internally the model stores them as `name` and `holdings`.
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=80,
        validation_alias=AliasChoices("name", "portfolio_name"),
    )
    tag: str | None = Field(default=None, max_length=40)
    is_house: bool = False
    holdings: list[HoldingIn] = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("holdings", "constituents"),
    )

    @field_validator("holdings")
    @classmethod
    def _dedupe_and_check(cls, v: list[HoldingIn]) -> list[HoldingIn]:
        seen: set[str] = set()
        for h in v:
            if h.ticker in seen:
                raise ValueError(f"duplicate ticker in holdings: {h.ticker}")
            seen.add(h.ticker)
        total = sum(h.weight for h in v)
        if total <= 0:
            raise ValueError("sum of weights must be > 0")
        return v


class CompareRequest(BaseModel):
    portfolios: list[CustomPortfolioIn] = Field(..., min_length=1, max_length=10)

    @field_validator("portfolios")
    @classmethod
    def _unique_names(cls, v: list[CustomPortfolioIn]) -> list[CustomPortfolioIn]:
        seen: set[str] = set()
        for p in v:
            key = p.name.strip().lower()
            if key in seen:
                raise ValueError(f"duplicate portfolio name: {p.name!r}")
            seen.add(key)
        return v


@app.post("/api/portfolio-compare")
def compare_portfolios(req: CompareRequest) -> dict[str, Any]:
    """Accept user-defined portfolios and return weighted daily returns.

    Request body (preferred multi-constituent shape):
        {
          "portfolios": [
            {
              "portfolio_name": "Abyan Ultra Growth",
              "tag": "Aggressive Growth",
              "is_house": true,
              "constituents": [
                {"ticker": "SPTE", "weight": 45},
                {"ticker": "HLAL", "weight": 35},
                {"ticker": "SPUK", "weight": 20}
              ]
            },
            ...
          ]
        }

    Legacy keys (`name`, `holdings`) are still accepted for backward
    compatibility. Weights may be specified as percentages (0-100) or
    fractions (0-1); the processor auto-detects and normalizes.
    """
    _require_robo()

    as_dicts = [
        {
            "name": p.name,
            "tag": p.tag or "",
            "is_house": p.is_house,
            "holdings": [{"ticker": h.ticker, "weight": h.weight} for h in p.holdings],
        }
        for p in req.portfolios
    ]

    try:
        return mod_robo.build_custom_payload(as_dicts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Portfolio Composition Drill-down
# ---------------------------------------------------------------------------

@app.get("/api/portfolios")
def list_portfolios() -> dict[str, Any]:
    """List all pre-built portfolios with summary info (Arabic + English names)."""
    summaries = mpc.list_portfolios_summary()
    return {
        "count":      len(summaries),
        "portfolios": summaries,
    }


@app.get("/api/portfolio/{name}")
def get_portfolio(name: str) -> dict[str, Any]:
    """Return full composition of a portfolio.

    The `{name}` path parameter accepts either the Arabic name
    (e.g., `أبيان نمو فائق`) or the English name (`Abyan Ultra Growth`).
    Arabic names are URL-percent-encoded by the client and FastAPI decodes
    them automatically before lookup.
    """
    composition = mpc.get_portfolio_composition(name)
    if composition is None:
        available = mpc.list_portfolio_names()
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Portfolio not found: {name!r}",
                "available_portfolios": available,
            },
        )
    return composition


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "tadawul_api:app",
        host="127.0.0.1",
        port=8601,
        reload=False,
        log_level="info",
    )
