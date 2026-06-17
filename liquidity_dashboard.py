"""
liquidity_dashboard.py — Foreign Liquidity Radar Dashboard

Streamlit dashboard for analyzing daily foreign ownership data
from the Saudi Exchange (Tadawul).

Connects to liquidity_radar.db (foreign_ownership_daily table) and renders:
  - Market Breadth KPIs (inflows / outflows / unchanged)
  - Top Gainer & Loser cards
  - Top 10 inflows bar chart
  - Historical ownership trend line chart
  - 3-day accumulation streak table
  - Full searchable master data table

Launch with:
    streamlit run liquidity_dashboard.py
"""

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config (MUST be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="Foreign Liquidity Radar",
    page_icon="\U0001F6F0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ───────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "liquidity_radar.db")


# ══════════════════════════════════════════════════════════════════════════════
# DARK THEME CSS
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
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
    .stDateInput label, .stMultiSelect label, .stSelectbox label {
        color: #C9D1D9 !important;
    }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_all_data() -> pd.DataFrame:
    """Load the full foreign_ownership_daily table."""
    if not os.path.isfile(DB_PATH):
        return pd.DataFrame()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        df = pd.read_sql_query(
            "SELECT date, symbol, company_name, ownership_limit, "
            "       actual_ownership, headroom "
            "FROM foreign_ownership_daily "
            "ORDER BY date, symbol",
            conn,
        )

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def compute_delta(all_data: pd.DataFrame, target_date: date) -> pd.DataFrame | None:
    """
    Compare target_date ownership with the most recent previous date.
    Returns DataFrame with: symbol, company_name, today_pct, prev_pct,
    delta, ownership_limit, headroom — or None if no prior date exists.
    """
    dates = sorted(all_data["date"].unique())
    if target_date not in dates:
        return None

    idx = dates.index(target_date)
    if idx == 0:
        return None  # no previous date

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
    """Find stocks with n_days consecutive ownership increases ending on target_date."""
    dates = sorted(all_data["date"].unique())
    if target_date not in dates:
        return None

    idx = dates.index(target_date)
    if idx < n_days - 1:
        return None

    recent_dates = dates[idx - n_days + 1 : idx + 1]
    recent = all_data[all_data["date"].isin(recent_dates)].copy()

    pivot = recent.pivot_table(
        index="symbol", columns="date", values="actual_ownership",
    )
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


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA & GUARD
# ══════════════════════════════════════════════════════════════════════════════

all_data = load_all_data()

if all_data.empty:
    st.title("\U0001F6F0 Foreign Liquidity Radar")
    st.warning(
        "No data found in `liquidity_radar.db`.  "
        "Run `python foreign_liquidity_scraper.py` first."
    )
    st.stop()

all_dates = sorted(all_data["date"].unique())
date_min = all_dates[0]
date_max = all_dates[-1]
has_history = len(all_dates) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.markdown("## \U0001F4C5 Date Selection")

selected_date = st.sidebar.date_input(
    "Snapshot Date",
    value=date_max,
    min_value=date_min,
    max_value=date_max,
)

# Snap to nearest available date
if selected_date not in all_dates:
    nearest = min(all_dates, key=lambda d: abs((d - selected_date).days))
    selected_date = nearest
    st.sidebar.caption(f"Snapped to nearest available: {selected_date}")

st.sidebar.markdown("---")
st.sidebar.markdown("## \U0001F50D Search")

all_symbols = sorted(all_data["symbol"].unique().tolist())
all_companies = sorted(all_data["company_name"].unique().tolist())

# Combined search options: "SYMBOL — Company Name"
search_options = []
sym_name_map = (
    all_data[["symbol", "company_name"]]
    .drop_duplicates()
    .set_index("symbol")["company_name"]
    .to_dict()
)
for sym in all_symbols:
    name = sym_name_map.get(sym, "")
    search_options.append(f"{sym} — {name}")

selected_search = st.sidebar.selectbox(
    "Select a stock for trend analysis",
    options=["(All — no filter)"] + search_options,
    index=0,
)

trend_symbol = None
if selected_search != "(All — no filter)":
    trend_symbol = selected_search.split(" — ")[0].strip()

st.sidebar.markdown("---")
st.sidebar.markdown("## \U0001F4CA Database")
st.sidebar.caption(f"Dates: {len(all_dates)} | Stocks: {len(all_symbols)}")
st.sidebar.caption(f"Range: {date_min} to {date_max}")


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<h1 style='margin-bottom:0'>\U0001F6F0 Foreign Liquidity Radar</h1>"
    f"<p style='color:#8B949E;margin-top:4px'>"
    f"Snapshot: <b>{selected_date.strftime('%d %b %Y')}</b>"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{len(all_symbols)} stocks tracked"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;{len(all_dates)} trading day(s) in DB"
    f"</p>",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# KPI CARDS — Market Breadth + Top Gainer/Loser
# ══════════════════════════════════════════════════════════════════════════════

delta_df = compute_delta(all_data, selected_date) if has_history else None

if delta_df is not None and not delta_df.empty:
    inflows  = int((delta_df["delta"] > 0).sum())
    outflows = int((delta_df["delta"] < 0).sum())
    unchanged = int((delta_df["delta"] == 0).sum())

    top_gainer = delta_df.nlargest(1, "delta").iloc[0]
    top_loser  = delta_df.nsmallest(1, "delta").iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Inflows (Up)", inflows, delta=f"{inflows} stocks")
    with c2:
        st.metric("Outflows (Down)", outflows, delta=f"-{outflows} stocks")
    with c3:
        st.metric("Unchanged", unchanged)
    with c4:
        gainer_name = top_gainer["company_name"]
        if len(gainer_name) > 20:
            gainer_name = gainer_name[:18] + ".."
        st.metric(
            f"Top Gainer ({top_gainer['symbol']})",
            f"{top_gainer['today_pct']:.2f}%",
            delta=f"{top_gainer['delta']:+.4f}%",
        )
    with c5:
        loser_name = top_loser["company_name"]
        if len(loser_name) > 20:
            loser_name = loser_name[:18] + ".."
        st.metric(
            f"Top Loser ({top_loser['symbol']})",
            f"{top_loser['today_pct']:.2f}%",
            delta=f"{top_loser['delta']:+.4f}%",
            delta_color="inverse",
        )
else:
    st.info(
        "Historical comparison requires at least **2 trading days** of data.  "
        "Run the scraper again tomorrow to enable delta analysis, top movers, "
        "and accumulation detection."
    )

st.markdown("")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 1 — Top 10 Foreign Inflows (Bar Chart)
# ══════════════════════════════════════════════════════════════════════════════

if delta_df is not None and not delta_df.empty:
    st.markdown("### Top 10 Foreign Inflows")

    top10 = delta_df.nlargest(10, "delta").copy()
    top10["label"] = top10["symbol"] + " — " + top10["company_name"].str[:20]
    top10 = top10.sort_values("delta", ascending=True)  # horizontal bar: largest on top

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        y=top10["label"],
        x=top10["delta"],
        orientation="h",
        marker=dict(
            color=top10["delta"],
            colorscale=[[0, "#FF4560"], [0.5, "#FEB019"], [1, "#00E396"]],
            line=dict(width=0),
        ),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Delta: %{x:+.4f}%<br>"
            "<extra></extra>"
        ),
    ))
    fig_bar.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0E1117",
        plot_bgcolor="#0E1117",
        font=dict(color="#C9D1D9"),
        height=420,
        margin=dict(l=200, r=30, t=10, b=40),
        xaxis=dict(
            title="Ownership Change (%)",
            gridcolor="#21262D",
            tickformat="+.4f",
        ),
        yaxis=dict(gridcolor="#21262D"),
        showlegend=False,
    )
    st.plotly_chart(fig_bar, width="stretch")


# ══════════════════════════════════════════════════════════════════════════════
# CHART 2 — Historical Trend (Line Chart)
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Historical Ownership Trend")

if trend_symbol:
    trend_data = all_data[all_data["symbol"] == trend_symbol].sort_values("date").copy()
    trend_name = sym_name_map.get(trend_symbol, trend_symbol)

    if trend_data.empty or len(trend_data) < 2:
        st.info(f"Not enough historical data for {trend_symbol} to plot a trend.")
    else:
        fig_line = go.Figure()

        # Actual ownership line
        fig_line.add_trace(go.Scatter(
            x=trend_data["date"],
            y=trend_data["actual_ownership"],
            mode="lines+markers",
            name="Actual Ownership",
            line=dict(color="#00E396", width=2.5),
            marker=dict(size=5),
            hovertemplate=(
                "%{x|%d %b %Y}<br>"
                "Actual: <b>%{y:.2f}%</b>"
                "<extra></extra>"
            ),
        ))

        # Limit line (if available)
        if trend_data["ownership_limit"].notna().any():
            fig_line.add_trace(go.Scatter(
                x=trend_data["date"],
                y=trend_data["ownership_limit"],
                mode="lines",
                name="Ownership Limit",
                line=dict(color="#FF4560", width=1.5, dash="dash"),
                hovertemplate=(
                    "%{x|%d %b %Y}<br>"
                    "Limit: <b>%{y:.2f}%</b>"
                    "<extra></extra>"
                ),
            ))

        fig_line.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0E1117",
            plot_bgcolor="#0E1117",
            font=dict(color="#C9D1D9"),
            title=dict(
                text=f"{trend_symbol} — {trend_name}",
                font=dict(size=16, color="#FAFAFA"),
            ),
            height=420,
            margin=dict(l=60, r=30, t=50, b=60),
            xaxis=dict(
                gridcolor="#21262D",
                title="",
            ),
            yaxis=dict(
                gridcolor="#21262D",
                title="Ownership %",
                ticksuffix="%",
            ),
            legend=dict(
                orientation="h",
                yanchor="top", y=-0.15,
                xanchor="center", x=0.5,
            ),
            hovermode="x unified",
        )
        st.plotly_chart(fig_line, width="stretch")
else:
    st.caption("Select a stock from the sidebar to view its historical ownership trend.")


# ══════════════════════════════════════════════════════════════════════════════
# ACCUMULATION TABLE — 3-day consecutive increase
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F4C8 3-Day Accumulation Streaks", expanded=False):
    if len(all_dates) < 3:
        st.info("Accumulation detection requires at least 3 trading days of data.")
    else:
        acc_df = detect_accumulation(all_data, selected_date, n_days=3)
        if acc_df is not None and not acc_df.empty:
            st.success(f"**{len(acc_df)} stock(s)** with 3-day continuous foreign accumulation.")

            display_acc = acc_df.copy()
            display_acc.columns = ["Symbol", "Company", "Start %", "Latest %", "Total Gain"]
            display_acc["Start %"] = display_acc["Start %"].map("{:.2f}%".format)
            display_acc["Latest %"] = display_acc["Latest %"].map("{:.2f}%".format)
            display_acc["Total Gain"] = display_acc["Total Gain"].map("{:+.4f}%".format)

            st.dataframe(
                display_acc,
                width="stretch",
                hide_index=True,
                height=min(400, 40 + 35 * len(display_acc)),
            )
        else:
            st.info("No stocks show 3 consecutive days of foreign accumulation.")


# ══════════════════════════════════════════════════════════════════════════════
# MASTER TABLE — full data for selected date
# ══════════════════════════════════════════════════════════════════════════════

with st.expander("\U0001F4CB Master Data Table", expanded=True):
    day_data = all_data[all_data["date"] == selected_date].copy()

    if day_data.empty:
        st.warning(f"No data for {selected_date}.")
    else:
        # Merge delta if available
        if delta_df is not None and not delta_df.empty:
            day_display = day_data.merge(
                delta_df[["symbol", "delta"]],
                on="symbol",
                how="left",
            )
        else:
            day_display = day_data.copy()
            day_display["delta"] = None

        day_display = day_display.sort_values("actual_ownership", ascending=False)

        # Format for display
        display_master = day_display[[
            "symbol", "company_name", "ownership_limit",
            "actual_ownership", "headroom", "delta",
        ]].copy()
        display_master.columns = [
            "Symbol", "Company", "Limit %",
            "Actual %", "Headroom %", "Daily Delta",
        ]
        display_master["Limit %"] = display_master["Limit %"].apply(
            lambda v: f"{v:.2f}" if pd.notna(v) else "-"
        )
        display_master["Actual %"] = display_master["Actual %"].apply(
            lambda v: f"{v:.2f}" if pd.notna(v) else "-"
        )
        display_master["Headroom %"] = display_master["Headroom %"].apply(
            lambda v: f"{v:.2f}" if pd.notna(v) else "-"
        )
        display_master["Daily Delta"] = display_master["Daily Delta"].apply(
            lambda v: f"{v:+.4f}" if pd.notna(v) else "-"
        )

        # Search filter
        search_term = st.text_input(
            "Filter table (symbol or company name)",
            "",
            key="master_search",
        )
        if search_term:
            mask = (
                display_master["Symbol"].str.contains(search_term, case=False, na=False)
                | display_master["Company"].str.contains(search_term, case=False, na=False)
            )
            display_master = display_master[mask]

        st.caption(f"Showing {len(display_master)} of {len(day_data)} stocks.")
        st.dataframe(
            display_master,
            width="stretch",
            hide_index=True,
            height=min(600, 40 + 35 * len(display_master)),
        )


# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<hr style='border-color:#21262D'>"
    "<p style='text-align:center;color:#484F58;font-size:0.8rem'>"
    "Data source: Saudi Exchange (saudiexchange.sa) — Foreign Ownership Headroom "
    "&nbsp;|&nbsp; Scraped by foreign_liquidity_scraper.py"
    "</p>",
    unsafe_allow_html=True,
)
