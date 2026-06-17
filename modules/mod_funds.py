"""
mod_funds.py — Data layer for Saudi Mutual Funds NAV Tracker.

Reads from mutual_funds.db (table: nav_history).
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import db  # unified data-access layer (SQLite locally, Postgres on the cloud)

_DB = "funds"

FUND_COLORS = [
    "#69f6b8",  # secondary green
    "#85adff",  # primary blue
    "#ffb148",  # tertiary gold
    "#ff716c",  # error red
    "#699cff",  # primary-dim
    "#58e7ab",  # secondary-dim
]

TIMEFRAMES = ["1M", "3M", "6M", "YTD", "1Y", "All"]


@st.cache_data(ttl=300)
def load_nav_data() -> pd.DataFrame:
    """Full nav_history table."""
    if not db.ping(_DB):
        return pd.DataFrame(columns=["date", "fund_name", "nav_price"])

    df = db.read_sql(
        "SELECT date, fund_name, nav_price FROM nav_history ORDER BY date, fund_name",
        db=_DB,
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def compute_pct_change(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize NAV to % change relative to each fund's first price."""
    if df.empty:
        return df
    base = df.groupby("fund_name")["nav_price"].first().rename("base_price")
    df = df.merge(base, on="fund_name", how="left")
    df["pct_change"] = (
        ((df["nav_price"] - df["base_price"]) / df["base_price"]) * 100
    ).round(2)
    df["nav_price"] = df["nav_price"].round(4)
    df["base_price"] = df["base_price"].round(4)
    return df


def resolve_timeframe(preset: str, data_min: date, data_max: date) -> tuple[date, date]:
    end = data_max
    if preset == "1M":
        start = max(data_min, end - timedelta(days=30))
    elif preset == "3M":
        start = max(data_min, end - timedelta(days=90))
    elif preset == "6M":
        start = max(data_min, end - timedelta(days=180))
    elif preset == "YTD":
        start = max(data_min, date(end.year, 1, 1))
    elif preset == "1Y":
        start = max(data_min, end - timedelta(days=365))
    else:
        start = data_min
    return start, end


def get_top_fund_kpi() -> dict:
    """Return dict with keys: fund_name, ytd_pct — for the Overview KPI."""
    df = load_nav_data()
    if df.empty:
        return {"fund_name": "N/A", "ytd_pct": 0.0}

    data_max = df["date"].max()
    ytd_start = date(data_max.year, 1, 1)

    ytd = df[df["date"] >= ytd_start].copy()
    if ytd.empty:
        ytd = df.copy()

    ytd = compute_pct_change(ytd)
    latest = ytd.sort_values("date").groupby("fund_name").last().reset_index()

    if latest.empty:
        return {"fund_name": "N/A", "ytd_pct": 0.0}

    best = latest.loc[latest["pct_change"].idxmax()]
    return {
        "fund_name": best["fund_name"],
        "ytd_pct": best["pct_change"],
    }


def db_available() -> bool:
    return db.ping(_DB)
