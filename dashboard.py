"""
dashboard.py — Streamlit frontend for the Saudi Daily Inflation Index.

Launch with:
    streamlit run dashboard.py

Connects to the local SQLite database (inflation_index.db) and renders:
  • Top-level KPI cards  (latest index, DoD change, items tracked, stores)
  • Interactive Plotly line chart of the index over time
  • Detailed price table for the most recent scraping date
  • Category-level weight breakdown donut chart
"""

import os
import sqlite3
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page configuration (MUST be the first Streamlit call) ────────────────────
st.set_page_config(
    page_title="Saudi Daily Inflation Index",
    page_icon="🇸🇦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "inflation_index.db")

# Color palette — Saudi-themed greens with a warm accent.
COLOR_PRIMARY   = "#006C35"   # Saudi green
COLOR_SECONDARY = "#00A651"   # lighter green
COLOR_ACCENT    = "#D4AF37"   # gold
COLOR_DANGER    = "#E74C3C"   # red for negative change
COLOR_BG_CARD   = "#F0F2F6"   # light card background


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER — cached queries so the dashboard stays snappy
# ══════════════════════════════════════════════════════════════════════════════

def _get_connection() -> sqlite3.Connection:
    """Open a read-only SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    from db_setup import create_tables, migrate_schema  # noqa: WPS433
    create_tables(conn)
    migrate_schema(conn)
    return conn


@st.cache_data(ttl=300, show_spinner="Loading index history …")
def load_index_history() -> pd.DataFrame:
    """Return the full daily_index table sorted by date."""
    conn = _get_connection()
    df = pd.read_sql_query(
        "SELECT date, index_value FROM daily_index ORDER BY date ASC",
        conn,
        parse_dates=["date"],
    )
    conn.close()
    return df


@st.cache_data(ttl=300, show_spinner="Loading latest prices …")
def load_latest_prices() -> pd.DataFrame:
    """
    Return scraped prices for the most recent date,
    joined with item master data for display.
    """
    conn = _get_connection()
    df = pd.read_sql_query(
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
               i.weight_percentage
          FROM daily_prices dp
          JOIN items i ON i.id = dp.item_id
         WHERE dp.date = (SELECT MAX(date) FROM daily_prices)
         ORDER BY i.category, i.name, dp.store_name
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=300, show_spinner="Loading basket info …")
def load_basket_summary() -> pd.DataFrame:
    """Return the item basket with weights, grouped by category."""
    conn = _get_connection()
    df = pd.read_sql_query(
        """
        SELECT category,
               COUNT(*)              AS items,
               SUM(weight_percentage) AS total_weight
          FROM items
         GROUP BY category
         ORDER BY total_weight DESC
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_item_count_and_stores() -> tuple[int, int]:
    """Quick counts for KPI cards."""
    conn = _get_connection()
    n_items  = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    n_stores = conn.execute("SELECT COUNT(DISTINCT store_name) FROM item_urls").fetchone()[0]
    conn.close()
    return n_items, n_stores


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/Flag_of_Saudi_Arabia.svg/320px-Flag_of_Saudi_Arabia.svg.png",
        width=200,
    )
    st.markdown("### ⚙️ Controls")

    if st.button("🔄  Refresh Data", width="stretch"):
        # Clear every cached function so the next render hits the DB again.
        load_index_history.clear()
        load_latest_prices.clear()
        load_basket_summary.clear()
        load_item_count_and_stores.clear()
        st.rerun()

    st.divider()
    st.markdown(
        """
        **How it works**

        1. A Playwright scraper visits Saudi e-commerce
           stores daily and records prices.
        2. A weighted **Laspeyres Index** is calculated:
           `Σ (avg_price × weight)`
        3. This dashboard visualises the trend.

        ---
        Built with ❤️ using **Streamlit + Plotly**
        """
    )


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<h1 style='text-align:center;'>🇸🇦 Saudi Daily Inflation Index</h1>"
    "<p style='text-align:center; color:grey;'>Real-time CPI Tracker — Powered by Live Supermarket Data</p>",
    unsafe_allow_html=True,
)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ══════════════════════════════════════════════════════════════════════════════

index_df = load_index_history()

if index_df.empty:
    # ── Empty state: no data yet ─────────────────────────────────────────
    st.warning(
        "⚠️ No index data found in the database. "
        "Run `python main.py` first to scrape prices and compute the index."
    )
    st.stop()

# Latest and previous index values.
latest_value = index_df["index_value"].iloc[-1]
latest_date  = index_df["date"].iloc[-1]

# Day-over-Day change (safe even with a single data point).
if len(index_df) >= 2:
    prev_value  = index_df["index_value"].iloc[-2]
    dod_change  = latest_value - prev_value
    dod_pct     = (dod_change / prev_value) * 100 if prev_value != 0 else 0.0
else:
    prev_value  = None
    dod_change  = 0.0
    dod_pct     = 0.0

n_items, n_stores = load_item_count_and_stores()

# Four KPI columns.
k1, k2, k3, k4 = st.columns(4)

k1.metric(
    label="📅 Latest Date",
    value=latest_date.strftime("%Y-%m-%d") if hasattr(latest_date, "strftime") else str(latest_date),
)
k2.metric(
    label="📈 Index Value",
    value=f"{latest_value:.4f}",
    delta=f"{dod_pct:+.2f}% DoD" if prev_value is not None else "First day",
    delta_color="inverse",  # green = lower inflation
)
k3.metric(label="🛒 Items Tracked", value=n_items)
k4.metric(label="🏪 Stores", value=n_stores)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CHART — Index Trend Over Time
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### 📊 Index Trend Over Time")

fig_line = px.area(
    index_df,
    x="date",
    y="index_value",
    labels={"date": "Date", "index_value": "Index Value (SAR-weighted)"},
)
fig_line.update_traces(
    line_color=COLOR_PRIMARY,
    fillcolor="rgba(0,108,53,0.12)",
)
fig_line.update_layout(
    hovermode="x unified",
    xaxis_title="",
    yaxis_title="Index Value",
    margin=dict(l=20, r=20, t=10, b=20),
    height=400,
)
# Add a subtle horizontal reference line at the first recorded value (base).
fig_line.add_hline(
    y=index_df["index_value"].iloc[0],
    line_dash="dot",
    line_color=COLOR_ACCENT,
    annotation_text="Base",
    annotation_position="top left",
)

st.plotly_chart(fig_line, width="stretch")


# ══════════════════════════════════════════════════════════════════════════════
# TWO-COLUMN SECTION: Category Weights  |  Price Comparison
# ══════════════════════════════════════════════════════════════════════════════

col_left, col_right = st.columns([1, 2])

# ── Left: Donut chart of basket category weights ─────────────────────────────
with col_left:
    st.markdown("### 🧺 Basket Weights by Category")
    basket_df = load_basket_summary()

    if not basket_df.empty:
        fig_donut = px.pie(
            basket_df,
            names="category",
            values="total_weight",
            hole=0.5,
            color_discrete_sequence=px.colors.qualitative.Set3,
        )
        fig_donut.update_traces(
            textposition="inside",
            textinfo="label+percent",
        )
        fig_donut.update_layout(
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=10),
            height=370,
        )
        st.plotly_chart(fig_donut, width="stretch")

# ── Right: Latest prices per item × store ────────────────────────────────────
with col_right:
    st.markdown("### 🏷️ Latest Scraped Prices")
    prices_df = load_latest_prices()

    if prices_df.empty:
        st.info("No price data yet for the latest date.")
    else:
        scrape_date = prices_df["date"].iloc[0]
        st.caption(f"Data from: **{scrape_date}**")

        # Pivot so each store becomes a column — easier to compare.
        pivot = (
            prices_df
            .pivot_table(
                index=["item_name", "category", "weight_percentage"],
                columns="store_name",
                values="price",
                aggfunc="first",
            )
            .reset_index()
        )
        pivot.columns.name = None  # remove the multi-index name

        # Calculate average price across stores (ignoring NaN / out-of-stock).
        store_cols = [c for c in pivot.columns if c not in ("item_name", "category", "weight_percentage")]
        pivot["Avg Price (SAR)"] = pivot[store_cols].mean(axis=1).round(2)

        # Rename for display.
        pivot = pivot.rename(columns={
            "item_name":         "Item",
            "category":          "Category",
            "weight_percentage": "Weight",
        })

        # Reorder: Item, Category, Weight, stores…, Avg.
        ordered = ["Item", "Category", "Weight"] + sorted(store_cols) + ["Avg Price (SAR)"]
        pivot = pivot[ordered]

        # Format the Weight column as percentage.
        pivot["Weight"] = pivot["Weight"].apply(lambda w: f"{w:.0%}")

        # Format price columns: show "—" for missing.
        for col in sorted(store_cols) + ["Avg Price (SAR)"]:
            pivot[col] = pivot[col].apply(
                lambda v: f"{v:.2f}" if pd.notna(v) else "—"
            )

        st.dataframe(
            pivot,
            width="stretch",
            hide_index=True,
            height=400,
        )


st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# DAY-OVER-DAY CHANGE BAR CHART  (only when ≥ 2 days of data)
# ══════════════════════════════════════════════════════════════════════════════

if len(index_df) >= 2:
    st.markdown("### 📉 Day-over-Day Index Change")

    changes = index_df.copy()
    changes["change"]  = changes["index_value"].diff()
    changes["pct"]     = changes["index_value"].pct_change() * 100
    changes = changes.dropna(subset=["change"])  # first row has no diff

    # Color bars: green when index drops (deflation), red when it rises.
    changes["color"] = changes["change"].apply(
        lambda x: COLOR_PRIMARY if x <= 0 else COLOR_DANGER
    )

    fig_bar = go.Figure(
        go.Bar(
            x=changes["date"],
            y=changes["pct"],
            marker_color=changes["color"],
            hovertemplate="Date: %{x|%Y-%m-%d}<br>Change: %{y:+.2f}%<extra></extra>",
        )
    )
    fig_bar.update_layout(
        xaxis_title="",
        yaxis_title="% Change",
        margin=dict(l=20, r=20, t=10, b=20),
        height=300,
    )
    st.plotly_chart(fig_bar, width="stretch")


# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<div style='text-align:center; color:grey; padding:2rem 0 1rem;'>"
    "Saudi Daily Inflation Index · Data refreshed via automated scraping · "
    "Not financial advice"
    "</div>",
    unsafe_allow_html=True,
)
