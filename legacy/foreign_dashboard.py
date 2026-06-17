"""
foreign_dashboard.py — Smart Money Radar: Tadawul Foreign Ownership Tracker.

Launch with:
    streamlit run foreign_dashboard.py

Connects to foreign_flows.db (daily_ownership table) and renders:
  - Market Heat: Top 10 Accumulation / Distribution bar charts
  - Company Deep Dive: Single-company ownership trend line chart
"""

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Smart Money Radar",
    page_icon="\U0001F6F0",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "foreign_flows.db")

# ══════════════════════════════════════════════════════════════════════════════
# DARK THEME
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
    .stApp, [data-testid="stAppViewContainer"] {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    [data-testid="stSidebar"] { background-color: #161B22; }
    [data-testid="stHeader"]  { background-color: rgba(14,17,23,0.95); }

    [data-testid="stMetric"] {
        background: #161B22;
        border: 1px solid #30363D;
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetricLabel"] { color: #8B949E !important; }
    [data-testid="stMetricValue"] { color: #FAFAFA !important; font-size: 1.5rem !important; }

    /* Section headers */
    .section-hdr {
        color: #C9D1D9;
        border-bottom: 1px solid #21262D;
        padding-bottom: 6px;
        margin: 24px 0 12px 0;
        font-size: 1.1rem;
    }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

PLOTLY_DARK = dict(
    template="plotly_dark",
    paper_bgcolor="#0E1117",
    plot_bgcolor="#0E1117",
    font=dict(color="#C9D1D9"),
)


@st.cache_data(ttl=300)
def load_all_data() -> pd.DataFrame:
    if not os.path.isfile(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    df = pd.read_sql_query(
        "SELECT date, symbol, company_name, foreign_pct "
        "FROM daily_ownership ORDER BY date",
        conn,
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


@st.cache_data(ttl=300)
def get_available_dates() -> list[date]:
    if not os.path.isfile(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_ownership ORDER BY date"
    ).fetchall()
    conn.close()
    return [date.fromisoformat(r[0]) for r in rows]


def snap_to_nearest(target: date, available: list[date], direction: str = "back") -> date:
    """Find the nearest available date to `target`. 'back' snaps earlier, 'forward' snaps later."""
    if target in available:
        return target
    if direction == "back":
        earlier = [d for d in available if d <= target]
        return earlier[-1] if earlier else available[0]
    else:
        later = [d for d in available if d >= target]
        return later[0] if later else available[-1]


def compute_deltas(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """
    Compute ownership delta between two dates for all companies.
    Returns DataFrame with: symbol, company_name, start_pct, end_pct, delta
    """
    df_start = df[df["date"] == start][["symbol", "company_name", "foreign_pct"]].rename(
        columns={"foreign_pct": "start_pct"}
    )
    df_end = df[df["date"] == end][["symbol", "company_name", "foreign_pct"]].rename(
        columns={"foreign_pct": "end_pct", "company_name": "company_name_end"}
    )

    merged = df_start.merge(df_end, on="symbol", how="inner")
    merged["delta"] = (merged["end_pct"] - merged["start_pct"]).round(4)
    merged = merged.drop(columns=["company_name_end"])
    return merged.sort_values("delta", ascending=False)


# ══════════════════════════════════════════════════════════════════════════════
# EARLY EXIT — empty DB
# ══════════════════════════════════════════════════════════════════════════════

all_data = load_all_data()
avail_dates = get_available_dates()

if all_data.empty or len(avail_dates) == 0:
    st.title("\U0001F6F0 Smart Money Radar")
    st.warning(
        "No data in `foreign_flows.db`.  "
        "Run `python foreign_scraper.py` daily to populate."
    )
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.markdown("## \U0001F4C5 Date Range")

date_min, date_max = avail_dates[0], avail_dates[-1]

# Default: last 7 calendar days (snapped to available)
default_end = date_max
default_start = snap_to_nearest(date_max - timedelta(days=7), avail_dates, "forward")

col_s, col_e = st.sidebar.columns(2)
with col_s:
    start_input = st.date_input("Start", value=default_start, min_value=date_min, max_value=date_max)
with col_e:
    end_input = st.date_input("End", value=default_end, min_value=date_min, max_value=date_max)

if start_input > end_input:
    st.sidebar.error("Start must be before End.")
    st.stop()

# Snap to nearest available dates
start_date = snap_to_nearest(start_input, avail_dates, "forward")
end_date = snap_to_nearest(end_input, avail_dates, "back")

if start_date >= end_date:
    st.sidebar.warning("Start and End snap to the same date. Widen the range.")
    st.stop()

st.sidebar.caption(f"Snapped: **{start_date}** \u2192 **{end_date}**")

# ── Company selector ────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("## \U0001F50D Company Deep Dive")

companies = (
    all_data[["symbol", "company_name"]]
    .drop_duplicates()
    .sort_values("symbol")
)
company_options = [f"{r.symbol} - {r.company_name}" for _, r in companies.iterrows()]

# Default to Saudi Aramco (2222) if present
default_idx = 0
for i, opt in enumerate(company_options):
    if opt.startswith("2222"):
        default_idx = i
        break

selected_company = st.sidebar.selectbox("Select company", company_options, index=default_idx)
selected_symbol = selected_company.split(" - ")[0].strip()
selected_name = selected_company.split(" - ", 1)[1].strip()

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<h1 style='margin-bottom:0'>\U0001F6F0 Smart Money Radar</h1>"
    f"<p style='color:#8B949E;margin-top:4px'>"
    f"Foreign Ownership Tracker &nbsp;|&nbsp; "
    f"{start_date.strftime('%d %b %Y')} \u2192 {end_date.strftime('%d %b %Y')}"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{len(companies)} companies"
    f"</p>",
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE DELTAS
# ══════════════════════════════════════════════════════════════════════════════

deltas = compute_deltas(all_data, start_date, end_date)

if deltas.empty:
    st.info("No overlapping companies between the two dates.")
    st.stop()

# Summary metrics
positive = (deltas["delta"] > 0).sum()
negative = (deltas["delta"] < 0).sum()
unchanged = (deltas["delta"] == 0).sum()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Companies", len(deltas))
m2.metric("Accumulating", f"{positive}", delta=f"{positive} \u2191" if positive else "0")
m3.metric("Distributing", f"{negative}", delta=f"{negative} \u2193" if negative else "0", delta_color="inverse")
m4.metric("Unchanged", f"{unchanged}")

# ══════════════════════════════════════════════════════════════════════════════
# VIZ 1 — MARKET HEAT: ACCUMULATION vs DISTRIBUTION
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-hdr">\U0001F525 Market Heat: Accumulation vs Distribution</div>', unsafe_allow_html=True)

top_acc = deltas[deltas["delta"] > 0].head(10).copy()
top_dist = deltas[deltas["delta"] < 0].tail(10).sort_values("delta").copy()

# Labels: "SYMBOL — Company Name"
top_acc["label"] = top_acc["symbol"] + " \u2014 " + top_acc["company_name"]
top_dist["label"] = top_dist["symbol"] + " \u2014 " + top_dist["company_name"]

col_left, col_right = st.columns(2)

# ── Accumulation (green) ─────────────────────────────────────────────────
with col_left:
    if top_acc.empty:
        st.info("No accumulation detected in this period.")
    else:
        fig_acc = px.bar(
            top_acc.iloc[::-1],  # reverse so largest at top
            x="delta",
            y="label",
            orientation="h",
            text=top_acc.iloc[::-1]["delta"].apply(lambda v: f"+{v:.2f}pp"),
            color_discrete_sequence=["#00E396"],
            labels={"delta": "Delta (pp)", "label": ""},
        )
        fig_acc.update_traces(
            textposition="outside",
            textfont=dict(color="#00E396", size=12),
            hovertemplate="<b>%{y}</b><br>Delta: +%{x:.3f}pp<extra></extra>",
        )
        fig_acc.update_layout(
            **PLOTLY_DARK,
            title=dict(text="\u2B06 Top 10 Accumulation", font=dict(size=15, color="#00E396")),
            xaxis=dict(gridcolor="#21262D", title="", showticklabels=False),
            yaxis=dict(gridcolor="#21262D", title=""),
            height=420,
            margin=dict(l=10, r=80, t=40, b=20),
            showlegend=False,
        )
        st.plotly_chart(fig_acc, width="stretch")

# ── Distribution (red) ──────────────────────────────────────────────────
with col_right:
    if top_dist.empty:
        st.info("No distribution detected in this period.")
    else:
        fig_dist = px.bar(
            top_dist.iloc[::-1],  # most negative at bottom
            x="delta",
            y="label",
            orientation="h",
            text=top_dist.iloc[::-1]["delta"].apply(lambda v: f"{v:.2f}pp"),
            color_discrete_sequence=["#FF4560"],
            labels={"delta": "Delta (pp)", "label": ""},
        )
        fig_dist.update_traces(
            textposition="outside",
            textfont=dict(color="#FF4560", size=12),
            hovertemplate="<b>%{y}</b><br>Delta: %{x:.3f}pp<extra></extra>",
        )
        fig_dist.update_layout(
            **PLOTLY_DARK,
            title=dict(text="\u2B07 Top 10 Distribution", font=dict(size=15, color="#FF4560")),
            xaxis=dict(gridcolor="#21262D", title="", showticklabels=False),
            yaxis=dict(gridcolor="#21262D", title="", side="right"),
            height=420,
            margin=dict(l=80, r=10, t=40, b=20),
            showlegend=False,
        )
        st.plotly_chart(fig_dist, width="stretch")

# ══════════════════════════════════════════════════════════════════════════════
# VIZ 2 — COMPANY DEEP DIVE
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    f'<div class="section-hdr">\U0001F3AF Company Deep Dive: {selected_symbol} \u2014 {selected_name}</div>',
    unsafe_allow_html=True,
)

company_data = all_data[
    (all_data["symbol"] == selected_symbol)
    & (all_data["date"] >= start_date)
    & (all_data["date"] <= end_date)
].sort_values("date").copy()

if company_data.empty:
    st.warning(f"No data for {selected_symbol} in the selected range.")
else:
    # Metrics
    latest_pct = company_data["foreign_pct"].iloc[-1]
    earliest_pct = company_data["foreign_pct"].iloc[0]
    company_delta = round(latest_pct - earliest_pct, 4)

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Latest Foreign %", f"{latest_pct:.2f}%")
    mc2.metric("Period Start %", f"{earliest_pct:.2f}%")
    mc3.metric(
        "Delta",
        f"{company_delta:+.3f}pp",
        delta=f"{company_delta:+.3f}pp",
    )
    mc4.metric("Data Points", len(company_data))

    # Line chart
    fig_deep = px.line(
        company_data,
        x="date",
        y="foreign_pct",
        markers=True,
        labels={"date": "", "foreign_pct": "Foreign Ownership %"},
    )
    fig_deep.update_traces(
        line=dict(color="#008FFB", width=2.5),
        marker=dict(size=7, color="#008FFB"),
        hovertemplate=(
            f"<b>{selected_symbol} \u2014 {selected_name}</b><br>"
            "Date: %{x|%d %b %Y}<br>"
            "Foreign: %{y:.3f}%"
            "<extra></extra>"
        ),
    )

    # Fill area under the line
    fig_deep.update_traces(fill="tozeroy", fillcolor="rgba(0,143,251,0.08)")

    fig_deep.update_layout(
        **PLOTLY_DARK,
        title=dict(
            text=f"Foreign Ownership Trend — {selected_symbol}",
            font=dict(size=16, color="#FAFAFA"),
        ),
        xaxis=dict(gridcolor="#21262D", showgrid=True),
        yaxis=dict(gridcolor="#21262D", showgrid=True, ticksuffix="%"),
        hovermode="x unified",
        height=380,
        margin=dict(l=50, r=30, t=50, b=40),
    )
    st.plotly_chart(fig_deep, width="stretch")

# ── Footer ──────────────────────────────────────────────────────────────────
st.markdown(
    "<hr style='border-color:#21262D'>"
    "<p style='text-align:center;color:#484F58;font-size:0.8rem'>"
    "Data source: Tadawul (saudiexchange.sa) Foreign Ownership Report &nbsp;|&nbsp; "
    "Scraped by foreign_scraper.py"
    "</p>",
    unsafe_allow_html=True,
)
