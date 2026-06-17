"""
mod_liquidity.py — Data layer for Foreign Liquidity Radar.

Reads from liquidity_radar.db (table: foreign_ownership_daily).
"""

from datetime import date

import pandas as pd
import streamlit as st

import db  # unified data-access layer (SQLite locally, Postgres on the cloud)

_DB = "liquidity"


@st.cache_data(ttl=300)
def load_all_data() -> pd.DataFrame:
    """Full foreign_ownership_daily table."""
    if not db.ping(_DB):
        return pd.DataFrame()

    df = db.read_sql(
        "SELECT date, symbol, company_name, ownership_limit, "
        "       actual_ownership, headroom "
        "FROM foreign_ownership_daily "
        "ORDER BY date, symbol",
        db=_DB,
    )

    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


@st.cache_data(ttl=300)
def get_symbol_history(symbol: str) -> pd.DataFrame:
    """Return the full historical foreign-ownership series for one symbol.

    Queries `foreign_ownership_daily` in liquidity_radar.db directly so the
    Liquidity Velocity chart always reflects the real accumulated history,
    not a cached pivot. Sorted ascending by date.

    Columns: date · company_name · actual_ownership · ownership_limit · headroom

    Returns an empty DataFrame when the DB is missing or the symbol has no
    rows yet — callers should `.empty`-check before plotting.
    """
    if not symbol or not db.ping(_DB):
        return pd.DataFrame()

    df = db.read_sql(
        "SELECT date, company_name, actual_ownership, "
        "       ownership_limit, headroom "
        "FROM foreign_ownership_daily "
        "WHERE symbol = ? "
        "ORDER BY date ASC",
        params=(symbol,),
        db=_DB,
    )

    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def compute_delta(all_data: pd.DataFrame, target_date: date) -> pd.DataFrame | None:
    """Compare target_date with previous date. Returns df with delta column."""
    dates = sorted(all_data["date"].unique())
    if target_date not in dates:
        return None
    idx = dates.index(target_date)
    if idx == 0:
        return None

    prev_date = dates[idx - 1]

    today_df = all_data[all_data["date"] == target_date][
        ["symbol", "company_name", "actual_ownership", "ownership_limit", "headroom"]
    ].copy()
    today_df = today_df.rename(columns={"actual_ownership": "today_pct"})

    prev_df = all_data[all_data["date"] == prev_date][
        ["symbol", "actual_ownership"]
    ].copy()
    prev_df = prev_df.rename(columns={"actual_ownership": "prev_pct"})

    merged = today_df.merge(prev_df, on="symbol", how="inner")
    merged["delta"] = (merged["today_pct"] - merged["prev_pct"]).round(4)
    merged["prev_date"] = prev_date
    return merged


def detect_accumulation(all_data: pd.DataFrame, target_date: date, n_days: int = 3) -> pd.DataFrame | None:
    """Find stocks with n_days consecutive ownership increases."""
    dates = sorted(all_data["date"].unique())
    if target_date not in dates:
        return None
    idx = dates.index(target_date)
    if idx < n_days - 1:
        return None

    recent_dates = dates[idx - n_days + 1: idx + 1]
    recent = all_data[all_data["date"].isin(recent_dates)].copy()

    pivot = recent.pivot_table(index="symbol", columns="date", values="actual_ownership")
    pivot = pivot.dropna()
    if pivot.empty:
        return None

    pivot = pivot[sorted(pivot.columns)]
    diffs = pivot.diff(axis=1).iloc[:, 1:]
    accumulating = (diffs > 0).all(axis=1)

    if not accumulating.any():
        return None

    acc_symbols = pivot.index[accumulating].tolist()
    names = all_data[["symbol", "company_name"]].drop_duplicates().set_index("symbol")

    rows = []
    for sym in acc_symbols:
        vals = pivot.loc[sym]
        rows.append({
            "symbol": sym,
            "company_name": names.loc[sym, "company_name"] if sym in names.index else "",
            "start_pct": round(vals.iloc[0], 4),
            "latest_pct": round(vals.iloc[-1], 4),
            "total_gain": round(vals.iloc[-1] - vals.iloc[0], 4),
        })

    return pd.DataFrame(rows).sort_values("total_gain", ascending=False).reset_index(drop=True)


def get_liquidity_kpi() -> dict:
    """Return dict with keys: inflows, outflows, net_stocks, latest_date — for Overview KPI."""
    df = load_all_data()
    if df.empty:
        return {"inflows": 0, "outflows": 0, "net_stocks": 0, "latest_date": "N/A"}

    dates = sorted(df["date"].unique())
    latest_date = dates[-1]

    if len(dates) < 2:
        return {"inflows": 0, "outflows": 0, "net_stocks": 0, "latest_date": str(latest_date)}

    delta_df = compute_delta(df, latest_date)
    if delta_df is None or delta_df.empty:
        return {"inflows": 0, "outflows": 0, "net_stocks": 0, "latest_date": str(latest_date)}

    inflows = int((delta_df["delta"] > 0).sum())
    outflows = int((delta_df["delta"] < 0).sum())

    return {
        "inflows": inflows,
        "outflows": outflows,
        "net_stocks": inflows - outflows,
        "latest_date": str(latest_date),
    }


def db_available() -> bool:
    return db.ping(_DB)
