"""
tadawul_processor.py — Foreign Ownership ingestion & Net Flow calculation.

Reads daily Tadawul foreign-ownership files (CSV or Excel) dropped into
./tadawul_data/ and computes estimated Net Foreign Flow in SAR.

Pipeline:
    find_latest_file()  ->  load_raw(path)  ->  normalize_columns(df)
    ->  compute_net_flow(df)  ->  build_payload(df)

Formula:
    Net Flow (SAR) = (Pct_Today - Pct_Yesterday) / 100
                     * Total_Shares
                     * Daily_Close_Price

    Positive  -> Accumulation (foreign buying)
    Negative  -> Distribution (foreign selling)
    Zero/NaN  -> Neutral

Safe by construction: missing numerics coerce to NaN, rows with any missing
input are dropped from the flow calc, sector names are filled with "Unclassified".
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), "tadawul_data")
SUPPORTED_EXT = (".csv", ".xlsx", ".xls")

# Tadawul exports vary column names & language. Canonical names used internally:
CANONICAL = {
    "ticker": "ticker",
    "sector": "sector",
    "pct_today": "pct_today",
    "pct_yesterday": "pct_yesterday",
    "total_shares": "total_shares",
    "close_price": "close_price",
    "company_name": "company_name",
}

# Accepted header aliases (case-insensitive, whitespace/underscore-insensitive).
ALIASES: dict[str, list[str]] = {
    "ticker": [
        "ticker", "symbol", "code", "stockcode", "stock_code", "tadawulcode",
        "tadawul_code", "tickersymbol",
    ],
    "sector": [
        "sector", "industry", "sectorname", "sector_name", "gics_sector",
        "tadawulsector",
    ],
    "pct_today": [
        "foreign_ownership_pct_today", "foreignownership_today",
        "foreign_pct_today", "pct_today", "today_pct", "foreign_today",
        "foreignownershippcttoday", "todays_foreign_pct",
    ],
    "pct_yesterday": [
        "foreign_ownership_pct_yesterday", "foreignownership_yesterday",
        "foreign_pct_yesterday", "pct_yesterday", "yesterday_pct",
        "foreign_yesterday", "foreignownershippctyesterday",
        "prior_foreign_pct", "prev_foreign_pct",
    ],
    "total_shares": [
        "total_shares", "shares_outstanding", "outstanding_shares",
        "shares_total", "total_outstanding_shares", "issued_shares",
    ],
    "close_price": [
        "daily_close_price", "close_price", "close", "price", "last_price",
        "closing_price",
    ],
    "company_name": [
        "company_name", "name", "company", "companyname", "issuer",
        "issuer_name",
    ],
}


# ---------------------------------------------------------------------------
# 1) File discovery
# ---------------------------------------------------------------------------

def list_files(data_dir: str = DATA_DIR) -> list[str]:
    """Absolute paths of all supported data files, newest mtime first."""
    if not os.path.isdir(data_dir):
        return []
    paths = [
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.lower().endswith(SUPPORTED_EXT) and not f.startswith("~$")
    ]
    paths.sort(key=os.path.getmtime, reverse=True)
    return paths


def find_latest_file(data_dir: str = DATA_DIR) -> str | None:
    """Return the most recently-modified supported file, or None."""
    files = list_files(data_dir)
    return files[0] if files else None


# ---------------------------------------------------------------------------
# 2) Load raw
# ---------------------------------------------------------------------------

def load_raw(path: str) -> pd.DataFrame:
    """Read a .csv / .xlsx / .xls file into a DataFrame. Empty DF on failure."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".csv":
            # Try utf-8 first, fall back to cp1256 (common for Arabic dumps).
            try:
                df = pd.read_csv(path)
            except UnicodeDecodeError:
                df = pd.read_csv(path, encoding="cp1256")
        else:
            df = pd.read_excel(path)
    except Exception as exc:  # pragma: no cover
        print(f"[tadawul_processor] failed to read {path}: {exc}")
        return pd.DataFrame()
    return df


# ---------------------------------------------------------------------------
# 3) Normalize columns
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Lowercase alphanumeric key for alias matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns using ALIASES -> canonical names. Unknown columns kept as-is."""
    if df.empty:
        return df

    slug_to_canonical: dict[str, str] = {}
    for canon, aliases in ALIASES.items():
        for alias in aliases:
            slug_to_canonical[_slug(alias)] = canon

    rename_map: dict[str, str] = {}
    for col in df.columns:
        canon = slug_to_canonical.get(_slug(col))
        if canon:
            rename_map[col] = canon

    return df.rename(columns=rename_map)


# ---------------------------------------------------------------------------
# 4) Compute Net Flow
# ---------------------------------------------------------------------------

REQUIRED_COLS = ("ticker", "pct_today", "pct_yesterday", "total_shares", "close_price")


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """Force a series to numeric, stripping common junk (%, commas, spaces)."""
    if series.dtype.kind in "iuf":
        return series
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def compute_net_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Add `delta_pct`, `net_flow_sar`, `status` columns. Drops malformed rows."""
    if df.empty:
        return df

    # Ensure every required canonical column exists (NaN-fill missing ones).
    for col in REQUIRED_COLS:
        if col not in df.columns:
            df[col] = pd.NA

    if "sector" not in df.columns:
        df["sector"] = "Unclassified"
    if "company_name" not in df.columns:
        df["company_name"] = ""

    out = df.copy()

    # Coerce numerics safely.
    for col in ("pct_today", "pct_yesterday", "total_shares", "close_price"):
        out[col] = _coerce_numeric(out[col])

    # Ticker as clean string.
    out["ticker"] = out["ticker"].astype(str).str.strip()
    out = out[out["ticker"].str.len() > 0]
    out = out[out["ticker"].str.lower() != "nan"]

    # Sector fallback.
    out["sector"] = out["sector"].fillna("Unclassified").astype(str).str.strip()
    out.loc[out["sector"] == "", "sector"] = "Unclassified"

    # Company name fallback.
    out["company_name"] = out["company_name"].fillna("").astype(str).str.strip()

    # Delta & Net Flow — both vectorized. NaN propagates safely.
    out["delta_pct"] = (out["pct_today"] - out["pct_yesterday"]).round(4)
    out["net_flow_sar"] = (
        (out["delta_pct"] / 100.0) * out["total_shares"] * out["close_price"]
    ).round(2)

    # Drop rows where net flow couldn't be computed.
    out = out.dropna(subset=["net_flow_sar"])

    # Classify.
    out["status"] = out["net_flow_sar"].apply(_classify_status)

    return out.reset_index(drop=True)


def _classify_status(net_flow: float) -> str:
    if pd.isna(net_flow) or net_flow == 0:
        return "Neutral"
    return "Accumulation" if net_flow > 0 else "Distribution"


# ---------------------------------------------------------------------------
# 5) Build API payload
# ---------------------------------------------------------------------------

def build_payload(df: pd.DataFrame, source_file: str | None = None) -> dict[str, Any]:
    """Produce the JSON-ready dict for GET /api/foreign-liquidity."""
    as_of = _infer_as_of(source_file)

    if df.empty:
        return {
            "as_of_date": as_of,
            "source_file": os.path.basename(source_file) if source_file else None,
            "row_count": 0,
            "totals": {
                "total_net_flow_sar": 0.0,
                "total_accumulation_sar": 0.0,
                "total_distribution_sar": 0.0,
                "accumulation_count": 0,
                "distribution_count": 0,
                "neutral_count": 0,
            },
            "sectors": [],
            "stocks": [],
        }

    stocks = (
        df.sort_values("net_flow_sar", ascending=False)
          .loc[:, [
              "ticker", "company_name", "sector",
              "pct_today", "pct_yesterday", "delta_pct",
              "total_shares", "close_price",
              "net_flow_sar", "status",
          ]]
          .to_dict(orient="records")
    )

    # Sector roll-up.
    sector_groups = (
        df.groupby("sector", dropna=False)
          .agg(
              net_flow_sar=("net_flow_sar", "sum"),
              stock_count=("ticker", "count"),
              accumulation_count=("status", lambda s: (s == "Accumulation").sum()),
              distribution_count=("status", lambda s: (s == "Distribution").sum()),
          )
          .reset_index()
          .sort_values("net_flow_sar", ascending=False)
    )

    sectors_payload = []
    for _, row in sector_groups.iterrows():
        sector_name = row["sector"]
        top_stocks = (
            df[df["sector"] == sector_name]
              .sort_values("net_flow_sar", ascending=False)
              .head(5)
              .loc[:, ["ticker", "company_name", "net_flow_sar", "status", "delta_pct"]]
              .to_dict(orient="records")
        )
        sectors_payload.append({
            "sector": sector_name,
            "net_flow_sar": round(float(row["net_flow_sar"]), 2),
            "stock_count": int(row["stock_count"]),
            "accumulation_count": int(row["accumulation_count"]),
            "distribution_count": int(row["distribution_count"]),
            "top_stocks": top_stocks,
        })

    total_net = float(df["net_flow_sar"].sum())
    accum = df[df["net_flow_sar"] > 0]
    distrib = df[df["net_flow_sar"] < 0]

    return {
        "as_of_date": as_of,
        "source_file": os.path.basename(source_file) if source_file else None,
        "row_count": int(len(df)),
        "totals": {
            "total_net_flow_sar": round(total_net, 2),
            "total_accumulation_sar": round(float(accum["net_flow_sar"].sum()), 2),
            "total_distribution_sar": round(float(distrib["net_flow_sar"].sum()), 2),
            "accumulation_count": int(len(accum)),
            "distribution_count": int(len(distrib)),
            "neutral_count": int((df["net_flow_sar"] == 0).sum()),
        },
        "sectors": sectors_payload,
        "stocks": stocks,
    }


def _infer_as_of(path: str | None) -> str:
    """Try to pull a YYYY-MM-DD from the filename; else use file mtime; else today."""
    today = datetime.now().strftime("%Y-%m-%d")
    if not path:
        return today
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", os.path.basename(path))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        return today


# ---------------------------------------------------------------------------
# 6) One-shot entry point
# ---------------------------------------------------------------------------

def process_latest(data_dir: str = DATA_DIR) -> dict[str, Any]:
    """Convenience: find latest file -> load -> normalize -> compute -> payload."""
    path = find_latest_file(data_dir)
    if path is None:
        return build_payload(pd.DataFrame(), source_file=None)

    raw = load_raw(path)
    if raw.empty:
        return build_payload(pd.DataFrame(), source_file=path)

    df = normalize_columns(raw)
    df = compute_net_flow(df)
    return build_payload(df, source_file=path)


# ---------------------------------------------------------------------------
# 7) CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target:
        raw = load_raw(target)
        df = compute_net_flow(normalize_columns(raw))
        payload = build_payload(df, source_file=target)
    else:
        payload = process_latest()

    # Trim the stocks list for console readability.
    preview = dict(payload)
    preview["stocks"] = payload["stocks"][:10]
    print(json.dumps(preview, indent=2, ensure_ascii=False, default=str))
