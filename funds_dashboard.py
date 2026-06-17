"""
funds_dashboard.py — Streamlit dashboard for Saudi Mutual Funds NAV Tracker.

Launch with:
    streamlit run funds_dashboard.py

Connects to mutual_funds.db (nav_history table) and renders:
  - Quick timeframe pills (1M / 3M / 6M / YTD / 1Y / All)
  - Sidebar date-range pickers with smart defaults from actual data bounds
  - Normalized % Change multi-line chart (Plotly) with rich tooltips
  - Per-fund delta metric cards for the selected timeframe
  - Absolute NAV chart toggle
  - Collapsible raw data table and data inspector
"""

import os
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config (MUST be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="Saudi Mutual Funds Tracker",
    page_icon="\U0001F4C8",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ───────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "mutual_funds.db")

FUND_COLORS = [
    "#00E396",  # green
    "#008FFB",  # blue
    "#FEB019",  # amber
    "#FF4560",  # red
    "#775DD0",  # purple
    "#00D9E9",  # cyan
    "#FF66C3",  # pink
    "#26A69A",  # teal
]

# Quick-select timeframe presets
TIMEFRAMES = ["1M", "3M", "6M", "YTD", "1Y", "All"]


# ══════════════════════════════════════════════════════════════════════════════
# DARK THEME CSS
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
    /* ── Dark background ─────────────────────────────────────────── */
    .stApp, [data-testid="stAppViewContainer"] {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    [data-testid="stSidebar"] {
        background-color: #161B22;
    }
    [data-testid="stHeader"] {
        background-color: rgba(14,17,23,0.95);
    }

    /* ── Metric cards ────────────────────────────────────────────── */
    [data-testid="stMetric"] {
        background: #161B22;
        border: 1px solid #30363D;
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetricLabel"] {
        color: #8B949E !important;
        font-size: 0.85rem !important;
    }
    [data-testid="stMetricValue"] {
        color: #FAFAFA !important;
        font-size: 1.6rem !important;
    }

    /* ── Sidebar tweaks ──────────────────────────────────────────── */
    .stDateInput label, .stMultiSelect label {
        color: #C9D1D9 !important;
    }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_nav_data() -> pd.DataFrame:
    """Load the full nav_history table into a DataFrame."""
    if not os.path.isfile(DB_PATH):
        return pd.DataFrame(columns=["date", "fund_name", "nav_price"])

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    df = pd.read_sql_query(
        "SELECT date, fund_name, nav_price FROM nav_history ORDER BY date, fund_name",
        conn,
    )
    conn.close()

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def compute_pct_change(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize NAV prices to % change relative to each fund's first
    available price in the filtered DataFrame.

    Adds columns: base_price, pct_change

    IMPORTANT: pct_change and nav_price are rounded to financial display
    precision (2dp and 4dp respectively) so that Plotly tooltips never
    leak raw floats like "0.8728179551122222%".  The rounding happens
    here — at the source — so every downstream consumer (charts, tables,
    metric cards) automatically inherits clean numbers.
    """
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
    """Convert a timeframe preset label into a (start_date, end_date) tuple."""
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
    else:  # "All"
        start = data_min
    return start, end


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA & GUARD
# ══════════════════════════════════════════════════════════════════════════════

all_data = load_nav_data()

if all_data.empty:
    st.title("\U0001F4C8 Saudi Mutual Funds Tracker")
    st.warning(
        "No NAV data found in `mutual_funds.db`.  "
        "Run `python funds_scraper.py` or `python funds_backfill.py` first."
    )
    st.stop()

# ── Data bounds ─────────────────────────────────────────────────────────────
date_min: date = all_data["date"].min()
date_max: date = all_data["date"].max()


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE — single source of truth for date range
# ══════════════════════════════════════════════════════════════════════════════
#
# Streamlit rule: once a widget with key="X" renders, subsequent reruns
# read from st.session_state["X"] and IGNORE the value= parameter.
# So we must write to session_state BEFORE widgets render, and never
# pass value= together with key= for programmatically-controlled widgets.
#
# Flow:
#   1. Pills click  → on_change callback writes new dates to session_state
#                      BEFORE the date_input widgets render
#   2. Date manual  → on_change callback detects user override, clears pill
#   3. Both widgets always read their current value from session_state

if "tf_pill" not in st.session_state:
    # First load: default to 6M
    st.session_state["tf_pill"] = "6M"
    s, e = resolve_timeframe("6M", date_min, date_max)
    st.session_state["ds"] = s
    st.session_state["de"] = e


def _on_pill_change():
    """Callback: user clicked a quick-select pill."""
    pill = st.session_state["tf_pill"]
    if pill:
        s, e = resolve_timeframe(pill, date_min, date_max)
        st.session_state["ds"] = s
        st.session_state["de"] = e


def _on_date_change():
    """Callback: user manually changed a date picker → clear the pill."""
    # Check if the current dates still match the selected pill
    pill = st.session_state.get("tf_pill")
    if pill:
        expected_s, expected_e = resolve_timeframe(pill, date_min, date_max)
        if (st.session_state["ds"] != expected_s
                or st.session_state["de"] != expected_e):
            st.session_state["tf_pill"] = None


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — TIMEFRAME CONTROLS
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.markdown("## \U0001F4C5 Timeframe")

# ── Quick-filter pills ─────────────────────────────────────────────────────
st.sidebar.pills(
    "Quick select",
    options=TIMEFRAMES,
    key="tf_pill",
    on_change=_on_pill_change,
    help="Pick a preset or use the date pickers below for a custom range",
)

# ── Date pickers (read/write via session_state keys "ds" and "de") ─────────
st.sidebar.caption("Or pick a custom range:")
col_s, col_e = st.sidebar.columns(2)
with col_s:
    st.date_input(
        "Start",
        min_value=date_min,
        max_value=date_max,
        key="ds",
        on_change=_on_date_change,
    )
with col_e:
    st.date_input(
        "End",
        min_value=date_min,
        max_value=date_max,
        key="de",
        on_change=_on_date_change,
    )

# ── Read final values from the single source of truth ──────────────────────
start_date: date = st.session_state["ds"]
end_date: date = st.session_state["de"]

# Guard: start must be before end
if start_date >= end_date:
    start_date = max(date_min, end_date - timedelta(days=30))
    st.session_state["ds"] = start_date
    if start_date >= end_date:
        st.sidebar.error("Not enough data for the selected range.")
        st.stop()

# ── Fund selector ────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("## \U0001F3E6 Funds")

all_funds = sorted(all_data["fund_name"].unique().tolist())
selected_funds = st.sidebar.multiselect(
    "Select funds",
    options=all_funds,
    default=all_funds,
)

if not selected_funds:
    st.sidebar.warning("Select at least one fund.")
    st.stop()

# ── Chart mode toggle ───────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("## \U0001F4CA Chart Mode")
chart_mode = st.sidebar.radio(
    "Y-axis",
    ["% Change (normalized)", "Absolute NAV"],
    horizontal=True,
    index=0,
)


# ══════════════════════════════════════════════════════════════════════════════
# FILTER & COMPUTE
# ══════════════════════════════════════════════════════════════════════════════

filtered = all_data[
    (all_data["date"] >= start_date)
    & (all_data["date"] <= end_date)
    & (all_data["fund_name"].isin(selected_funds))
].copy()

if filtered.empty:
    st.title("\U0001F4C8 Saudi Mutual Funds Tracker")
    st.info("No data in the selected range. Try a wider date window or check fund selection.")
    st.stop()

df = compute_pct_change(filtered)


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

# Count actual data days
data_days = df["date"].nunique()
tf_label = st.session_state.get("tf_pill") or "Custom"

st.markdown(
    "<h1 style='margin-bottom:0'>\U0001F4C8 Saudi Mutual Funds Tracker</h1>"
    f"<p style='color:#8B949E;margin-top:4px'>"
    f"{start_date.strftime('%d %b %Y')} — {end_date.strftime('%d %b %Y')}"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{tf_label}"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{len(selected_funds)} fund(s)"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{data_days:,} trading days"
    f"</p>",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# METRIC CARDS — final % return per fund
# ══════════════════════════════════════════════════════════════════════════════

# Get the last row per fund in the filtered range
latest = df.sort_values("date").groupby("fund_name").last().reset_index()

cols = st.columns(min(len(selected_funds), 5))
for i, fund in enumerate(selected_funds):
    row = latest[latest["fund_name"] == fund]
    if row.empty:
        continue
    pct = row["pct_change"].iloc[0]
    nav = row["nav_price"].iloc[0]
    col_idx = i % len(cols)
    with cols[col_idx]:
        short_name = fund if len(fund) <= 30 else fund[:28] + "..."
        st.metric(
            label=short_name,
            value=f"{nav:.4f}",
            delta=f"{pct:+.2f}%",
        )

st.markdown("")  # spacer


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CHART
# ══════════════════════════════════════════════════════════════════════════════

# Consistent color map (same color for a fund regardless of selection)
color_map = {
    fund: FUND_COLORS[i % len(FUND_COLORS)]
    for i, fund in enumerate(all_funds)
}

use_pct = chart_mode == "% Change (normalized)"
y_col = "pct_change" if use_pct else "nav_price"
y_label = "Change (%)" if use_pct else "NAV Price (SAR)"
chart_title = (
    "Normalized Performance (% Change from Period Start)"
    if use_pct
    else "Absolute NAV Price"
)

fig = px.line(
    df,
    x="date",
    y=y_col,
    color="fund_name",
    color_discrete_map=color_map,
    custom_data=["fund_name", "nav_price", "pct_change"],
    labels={
        "date": "",
        y_col: y_label,
        "fund_name": "Fund",
    },
)

# ── Tooltips ─────────────────────────────────────────────────────────────────
if use_pct:
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "%{x|%d %b %Y}<br>"
            "Change: <b>%{y:+.2f}%</b><br>"
            "NAV: %{customdata[1]:.4f}"
            "<extra></extra>"
        ),
        line=dict(width=2.5),
    )
else:
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "%{x|%d %b %Y}<br>"
            "NAV: <b>%{y:.4f}</b><br>"
            "Change: %{customdata[2]:+.2f}%"
            "<extra></extra>"
        ),
        line=dict(width=2.5),
    )

# ── Zero reference line (% change mode only) ────────────────────────────────
if use_pct:
    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color="#30363D",
        line_width=1,
        annotation_text="Base (0%)",
        annotation_position="bottom left",
        annotation_font_color="#8B949E",
    )

# ── Dark layout ──────────────────────────────────────────────────────────────
fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#0E1117",
    plot_bgcolor="#0E1117",
    font=dict(color="#C9D1D9"),
    title=dict(
        text=chart_title,
        font=dict(size=18, color="#FAFAFA"),
    ),
    xaxis=dict(
        gridcolor="#21262D",
        showgrid=True,
        title="",
        tickformat="%b %Y" if data_days > 90 else "%d %b",
        dtick="M3" if data_days > 365 else ("M1" if data_days > 90 else None),
    ),
    yaxis=dict(
        gridcolor="#21262D",
        showgrid=True,
        ticksuffix="%" if use_pct else "",
        tickprefix="" if use_pct else "",
        hoverformat="+.2f" if use_pct else ".4f",
        title="",
    ),
    legend=dict(
        orientation="h",
        yanchor="top",
        y=-0.12,
        xanchor="center",
        x=0.5,
        font=dict(size=12),
    ),
    hovermode="x unified" if use_pct else "closest",
    height=540,
    margin=dict(l=60, r=30, t=60, b=80),
)

# Range slider for zooming
fig.update_xaxes(
    rangeslider=dict(visible=True, thickness=0.05),
)

st.plotly_chart(fig, width="stretch")


# ══════════════════════════════════════════════════════════════════════════════
# PER-FUND SPARKLINES  (mini cards below the main chart)
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F4C9 Per-Fund Sparklines", expanded=False):
    spark_cols = st.columns(min(len(selected_funds), 3))
    for i, fund in enumerate(selected_funds):
        fund_df = df[df["fund_name"] == fund].sort_values("date")
        if fund_df.empty or len(fund_df) < 2:
            continue

        col_idx = i % len(spark_cols)
        with spark_cols[col_idx]:
            spark_fig = go.Figure()
            spark_fig.add_trace(go.Scatter(
                x=fund_df["date"],
                y=fund_df["nav_price"],
                mode="lines",
                line=dict(color=color_map.get(fund, "#00E396"), width=2),
                fill="tozeroy",
                fillcolor=color_map.get(fund, "#00E396").replace(")", ",0.1)").replace("rgb", "rgba")
                if color_map.get(fund, "").startswith("rgb") else None,
                hovertemplate="%{x|%d %b %Y}<br>NAV: %{y:.4f}<extra></extra>",
            ))
            spark_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0E1117",
                plot_bgcolor="#0E1117",
                title=dict(text=fund[:35], font=dict(size=13, color="#C9D1D9")),
                height=200,
                margin=dict(l=40, r=10, t=35, b=25),
                xaxis=dict(showgrid=False, showticklabels=True, tickformat="%b"),
                yaxis=dict(showgrid=False, showticklabels=True),
                showlegend=False,
            )
            st.plotly_chart(spark_fig, width="stretch", key=f"spark_{fund}")


# ══════════════════════════════════════════════════════════════════════════════
# RAW DATA TABLE (collapsed by default)
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F4CB Raw NAV Data"):
    display_df = df[["date", "fund_name", "nav_price", "pct_change"]].copy()
    display_df.columns = ["Date", "Fund", "NAV Price", "% Change"]
    display_df["% Change"] = display_df["% Change"].map("{:+.2f}%".format)
    display_df["NAV Price"] = display_df["NAV Price"].map("{:.4f}".format)
    st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        height=400,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DATA INSPECTOR — spot anomalies in the chart DataFrame
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F50D Data Inspector"):
    st.caption("Sorted by **|% Change|** descending — largest swings first to spot anomalies.")

    inspect_df = df[["date", "fund_name", "base_price", "nav_price", "pct_change"]].copy()

    # Day-over-day % change per fund (key anomaly signal)
    inspect_df = inspect_df.sort_values(["fund_name", "date"])
    inspect_df["dod_change_%"] = (
        inspect_df.groupby("fund_name")["nav_price"]
        .pct_change()
        .mul(100)
    )

    # Sort by absolute pct_change descending — biggest outliers float to top
    inspect_df["_abs_pct"] = inspect_df["pct_change"].abs()
    inspect_df = inspect_df.sort_values("_abs_pct", ascending=False).drop(columns="_abs_pct")

    # Format for display
    display_inspect = inspect_df.copy()
    display_inspect.columns = ["Date", "Fund", "Base Price", "NAV Price", "% Change", "DoD Change %"]
    display_inspect["% Change"] = inspect_df["pct_change"].map("{:+.2f}%".format)
    display_inspect["DoD Change %"] = inspect_df["dod_change_%"].apply(
        lambda v: f"{v:+.2f}%" if pd.notna(v) else "-"
    )
    display_inspect["Base Price"] = inspect_df["base_price"].map("{:.4f}".format)
    display_inspect["NAV Price"] = inspect_df["nav_price"].map("{:.4f}".format)

    # Count anomalies
    anomaly_count = (inspect_df["dod_change_%"].abs() > 5).sum()
    if anomaly_count > 0:
        st.warning(
            f"**{anomaly_count} anomalous row(s) detected** "
            f"(day-over-day change > 5%).  "
            f"Run `python funds_doctor.py` to auto-sanitize."
        )
    else:
        st.success("No day-over-day anomalies detected (all DoD changes within 5%).")

    st.dataframe(
        display_inspect,
        width="stretch",
        hide_index=True,
        height=400,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE STATS
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F4BE Database Stats"):
    total_rows = len(all_data)
    fund_stats = all_data.groupby("fund_name").agg(
        Records=("date", "count"),
        First=("date", "min"),
        Last=("date", "max"),
    ).reset_index()
    fund_stats.columns = ["Fund", "Records", "First Date", "Last Date"]
    fund_stats = fund_stats.sort_values("Records", ascending=False)

    st.caption(f"Total records in database: **{total_rows:,}**")
    st.dataframe(fund_stats, width="stretch", hide_index=True)


# ── Footer ──────────────────────────────────────────────────────────────────
st.markdown(
    "<hr style='border-color:#21262D'>"
    "<p style='text-align:center;color:#484F58;font-size:0.8rem'>"
    "Data source: Tadawul (saudiexchange.sa) &amp; Riyad Capital &nbsp;|&nbsp; "
    "Scraped by funds_scraper.py &nbsp;|&nbsp; "
    "Backfilled by funds_backfill.py"
    "</p>",
    unsafe_allow_html=True,
)
