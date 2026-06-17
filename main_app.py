"""
main_app.py — Terminal v1.0: Unified Sovereign Intelligence Dashboard

Consolidates three projects into one Streamlit application:
  1. Inflation Tracker   (inflation_index.db)
  2. Fund Performance    (mutual_funds.db)
  3. Foreign Liquidity   (liquidity_radar.db)

Launch:
    streamlit run main_app.py
"""

import os
from datetime import date, timedelta
from html import escape as html_escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config (MUST be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Saudi Command",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load custom CSS ───────────────────────────────────────────────────────────
CSS_PATH = os.path.join(os.path.dirname(__file__), "styles.css")
if os.path.isfile(CSS_PATH):
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── Authentication gate ───────────────────────────────────────────────────────
# Open locally (no secrets); requires login on Streamlit Cloud (where the
# [auth] secrets section is set). Runs before any data is loaded or rendered.
import auth  # noqa: E402
auth.require_login()

# ── Import analytical modules ─────────────────────────────────────────────────
import db  # noqa: E402  (unified data layer; used to hide local-scrape actions on the cloud)
from modules import mod_inflation, mod_funds, mod_liquidity, mod_robo  # noqa: E402
from modules import mod_real_estate  # noqa: E402
from modules import mod_portfolio_composition as mpc  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# PLOTLY DEFAULTS — transparent so charts blend into the dashboard surface
# ══════════════════════════════════════════════════════════════════════════════

PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",   # transparent — chart container provides bg
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Tajawal, sans-serif", color="#ecf1f8", size=12),
    xaxis=dict(
        gridcolor="rgba(148,163,184,0.08)",
        zerolinecolor="rgba(148,163,184,0.12)",
        zeroline=False,
        linecolor="rgba(148,163,184,0.14)",
    ),
    yaxis=dict(
        gridcolor="rgba(148,163,184,0.08)",
        zerolinecolor="rgba(148,163,184,0.12)",
        zeroline=False,
        linecolor="rgba(148,163,184,0.14)",
    ),
    margin=dict(l=50, r=30, t=50, b=50),
    hoverlabel=dict(
        bgcolor="#101820",
        bordercolor="rgba(148,163,184,0.28)",
        font=dict(family="JetBrains Mono, monospace", color="#ecf1f8", size=12),
    ),
    colorway=["#5cc8ff", "#50e3a4", "#f6b44b", "#9f8cff", "#ff6b7a", "#2dd4bf"],
)

# Fintech accent palette (mirrors styles.css tokens)
CLR_PRIMARY   = "#5cc8ff"   # accent / primary KPI
CLR_SECONDARY = "#50e3a4"   # positive
CLR_TERTIARY  = "#f6b44b"   # warning / neutral-2
CLR_ERROR     = "#ff6b7a"   # negative
CLR_DIM       = "#a8aebf"
CLR_VIOLET    = "#9f8cff"

NAV_OPTIONS = [
    "Overview",
    "Macro & Inflation",
    "الإيجارات",
    "العقار",
    "Portfolio Workspace",
    "Foreign Liquidity Radar",
]


def _html_escape(value) -> str:
    """Escape dynamic text before embedding it in manual Streamlit HTML."""
    return html_escape("" if value is None else str(value), quote=True)


# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS — premium card primitives (replace st.metric)
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_card(
    label: str,
    value: str,
    delta: str | None = None,
    delta_color: str = "normal",   # "normal" | "inverse" | "off"
    accent: str = "primary",       # "primary" | "positive" | "negative" | "warn" | "violet"
    text_value: bool = False,      # render value with UI font (for non-numeric like fund names)
) -> None:
    """Render a premium KPI card. Drop-in for st.metric.

    delta_color semantics:
        normal  -> +x is green, -x is red (default)
        inverse -> +x is red,   -x is green (e.g. inflation)
        off     -> always grey
    """
    # Decide delta tone from sign + delta_color mode.
    delta_class = "neutral"
    arrow = ""
    if delta:
        starts_minus = delta.lstrip().startswith("-")
        starts_plus  = delta.lstrip().startswith("+")
        if delta_color == "off" or (not starts_plus and not starts_minus):
            delta_class = "neutral"
        else:
            is_negative = starts_minus
            if delta_color == "inverse":
                is_negative = not is_negative
            delta_class = "negative" if is_negative else "positive"
            arrow = "▼" if starts_minus else ("▲" if starts_plus else "")

    accent_cls = {
        "primary":  "",
        "positive": "accent-positive",
        "negative": "accent-negative",
        "warn":     "accent-warn",
        "violet":   "accent-violet",
    }.get(accent, "")

    value_cls = "kpi-value-text" if text_value else "kpi-value"

    delta_html = (
        f"<div class='kpi-delta {delta_class}'>"
        f"<span class='arrow'>{arrow}</span> {delta}</div>"
    ) if delta else ""

    # Escape minimal HTML in user-provided strings.
    def _esc(s: str) -> str:
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))

    st.markdown(
        f"<div class='kpi-card {accent_cls}'>"
        f"<div class='kpi-label'><span class='kpi-icon'></span>{_esc(label)}</div>"
        f"<div class='{value_cls}'>{_esc(value)}</div>"
        f"{delta_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _short_text(value, max_len: int = 28) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= max_len else text[: max_len - 2] + ".."


def _fmt_num(value, digits: int = 2, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.{digits}f}{suffix}"


def _fmt_pct(value, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):+.2f}%{suffix}"


def _add_visible_local_rent_index(
    df: pd.DataFrame,
    value_col: str = "avg_annual_rent",
    group_col: str = "region",
    out_col: str = "rent_index_visible_local",
) -> pd.DataFrame:
    """Rebase each region to 100 at the first visible row in the current filter."""
    if df.empty or value_col not in df.columns or group_col not in df.columns:
        return df

    df = df.copy()
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    def _local_scale(values: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(values, errors="coerce")
        first_valid = numeric.dropna()
        if first_valid.empty:
            return pd.Series(pd.NA, index=values.index, dtype="Float64")
        base = float(first_valid.iloc[0])
        if base == 0:
            return pd.Series(pd.NA, index=values.index, dtype="Float64")
        return numeric / base * 100.0

    df[out_col] = df.groupby(group_col, group_keys=False)[value_col].transform(_local_scale)
    return df


def _is_meaningful_delta(delta_df: pd.DataFrame | None, threshold: float = 0.00005) -> bool:
    if delta_df is None or delta_df.empty or "delta" not in delta_df.columns:
        return False
    return bool(delta_df["delta"].abs().max() > threshold)


def _mini_trend(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    name: str,
    color: str,
    height: int = 138,
) -> None:
    """Small decision-card sparkline used on the Overview page."""
    if df is None or df.empty or x_col not in df.columns or y_col not in df.columns:
        st.info("No trend data yet.")
        return

    plot_df = df[[x_col, y_col]].dropna().copy()
    if plot_df.empty:
        st.info("No usable trend data yet.")
        return

    fill_rgb = "108,142,255"
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        try:
            fill_rgb = ",".join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))
        except ValueError:
            fill_rgb = "108,142,255"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=plot_df[x_col],
        y=plot_df[y_col],
        mode="lines",
        name=name,
        line=dict(color=color, width=2.2, shape="spline", smoothing=0.8),
        fill="tozeroy",
        fillcolor=f"rgba({fill_rgb},0.10)",
        hovertemplate="<b>%{x}</b><br>%{y:.2f}<extra></extra>",
    ))
    mini_layout = {**PLOTLY_LAYOUT}
    mini_layout["margin"] = dict(l=12, r=12, t=8, b=18)
    mini_layout["xaxis"] = dict(visible=False)
    mini_layout["yaxis"] = dict(visible=False)
    fig.update_layout(
        **mini_layout,
        height=height,
        showlegend=False,
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _decision_note(title: str, body: str, tone: str = "neutral") -> None:
    tone_class = {
        "positive": "decision-note positive",
        "negative": "decision-note negative",
        "warn": "decision-note warn",
    }.get(tone, "decision-note")
    st.markdown(
        f"<div class='{tone_class}'>"
        f"<b>{_html_escape(title)}</b><span>{_html_escape(body)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — Navigation + Branding
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    # Branded wordmark.
    st.markdown(
        "<div class='brand-card'>"
        "<div class='brand-mark'>SC</div>"
        "<div>"
        "<h1>Saudi Command</h1>"
        "<p>Market Intelligence</p>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    page = st.radio(
        "COMMAND CENTER",
        options=NAV_OPTIONS,
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("---")

    # Sync status pill (animated)
    st.markdown(
        '<div class="sync-badge"><span class="sync-dot"></span>Data Synced</div>',
        unsafe_allow_html=True,
    )
    st.caption(f"Last UI refresh: {date.today().strftime('%Y-%m-%d')}")
    st.markdown("")

    # DB availability — premium status-list styling
    inf_ok = mod_inflation.db_available()
    fun_ok = mod_funds.db_available()
    liq_ok = mod_liquidity.db_available()

    def _src_row(ok: bool, name: str) -> str:
        status = "ok" if ok else "bad"
        return (
            f"<div class='source-row {status}'>"
            f"<span></span>{name}"
            f"</div>"
        )

    st.markdown(
        "<p style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
        "letter-spacing:0.18em; color:var(--text-muted); text-transform:uppercase; "
        "margin:6px 0 8px;'>Data Sources</p>"
        + _src_row(inf_ok, "inflation_index.db")
        + _src_row(fun_ok, "mutual_funds.db")
        + _src_row(liq_ok, "liquidity_radar.db"),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — top bar with page title and date
# ══════════════════════════════════════════════════════════════════════════════

def _page_header(title: str, subtitle: str = ""):
    """Premium page header with gradient title + monospace date stamp."""
    today_str = date.today().strftime("%b %d, %Y").upper()
    sub_html = f"<p class='subtitle'>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"<div class='page-header'>"
        f"<div><h2>{title}</h2>{sub_html}</div>"
        f"<span class='date-stamp'>{today_str}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

def page_overview():
    _page_header("Saudi Command Center", "Decision signals across prices, rent, property, portfolios, and flows")

    inf_df = mod_inflation.load_index_history(mod_inflation.db_fingerprint())
    rent_df = mod_real_estate.load_rent_index_history(mod_real_estate.db_fingerprint())
    repi_df, repi_error = mod_real_estate.load_real_estate_price_index_history()
    fund_df = mod_funds.load_nav_data()
    liq_df = mod_liquidity.load_all_data()

    latest_inf = None if inf_df.empty else float(inf_df["index_value"].iloc[-1])
    inf_delta = None
    if len(inf_df) >= 2:
        prev_inf = float(inf_df["index_value"].iloc[-2])
        inf_delta = ((latest_inf - prev_inf) / prev_inf * 100.0) if prev_inf else None

    latest_rent = None if rent_df.empty else rent_df.iloc[-1]

    def _latest_repi(sector: str):
        if repi_df.empty:
            return None
        rows = repi_df[repi_df["sector_display"].eq(sector)].sort_values("period_date")
        return None if rows.empty else rows.iloc[-1]

    general_repi = _latest_repi("General index")

    fund_latest = pd.DataFrame()
    fund_avg = None
    fund_alpha = None
    top_fund_name = "N/A"
    fund_trend = pd.DataFrame()
    if not fund_df.empty:
        data_max = fund_df["date"].max()
        ytd_start = date(data_max.year, 1, 1)
        ytd = fund_df[fund_df["date"] >= ytd_start].copy()
        if ytd.empty:
            ytd = fund_df.copy()
        fund_calc = mod_funds.compute_pct_change(ytd)
        fund_latest = fund_calc.sort_values("date").groupby("fund_name").last().reset_index()
        if not fund_latest.empty:
            top_row = fund_latest.loc[fund_latest["pct_change"].idxmax()]
            fund_avg = float(fund_latest["pct_change"].mean())
            fund_alpha = float(top_row["pct_change"] - fund_avg)
            top_fund_name = str(top_row["fund_name"])
        fund_trend = (
            fund_calc.groupby("date", as_index=False)["pct_change"].mean()
                     .rename(columns={"pct_change": "avg_return"})
        )

    flow_trend_rows = []
    delta_df = None
    meaningful_flow = False
    liq_dates = sorted(liq_df["date"].unique()) if not liq_df.empty else []
    if len(liq_dates) >= 2:
        for flow_date in liq_dates[-60:]:
            day_delta = mod_liquidity.compute_delta(liq_df, flow_date)
            if day_delta is not None and not day_delta.empty:
                flow_trend_rows.append({
                    "date": flow_date,
                    "net_stocks": int((day_delta["delta"] > 0).sum() - (day_delta["delta"] < 0).sum()),
                    "active": int((day_delta["delta"].abs() > 0.00005).sum()),
                })
        delta_df = mod_liquidity.compute_delta(liq_df, liq_dates[-1])
        meaningful_flow = _is_meaningful_delta(delta_df)
    flow_trend = pd.DataFrame(flow_trend_rows)

    st.markdown("<p class='section-title'>Decision Signals</p>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        _kpi_card(
            "Inflation Nowcast",
            _fmt_num(latest_inf, 2),
            delta=_fmt_pct(inf_delta, " DoD") if inf_delta is not None else "Awaiting baseline",
            delta_color="inverse",
            accent="warn",
        )
        _mini_trend(inf_df.tail(45), "date", "index_value", "Inflation", CLR_TERTIARY)
    with c2:
        _kpi_card(
            "Rent Pressure",
            _fmt_num(None if latest_rent is None else latest_rent["rent_index"], 2),
            delta=_fmt_pct(None if latest_rent is None else latest_rent["mom_pct"], " MoM"),
            delta_color="inverse",
            accent="primary",
        )
        _mini_trend(rent_df.tail(36), "date", "rent_index", "Rent CPI", CLR_PRIMARY)
    with c3:
        _kpi_card(
            "Property Price Signal",
            _fmt_num(None if general_repi is None else general_repi["value"], 2),
            delta=_fmt_pct(None if general_repi is None else general_repi["qoq_pct"], " QoQ"),
            accent="violet",
        )
        if not repi_df.empty:
            repi_trend = repi_df[repi_df["sector_display"].eq("General index")].tail(16)
            _mini_trend(repi_trend, "period_date", "value", "REPI", CLR_VIOLET)
        else:
            st.info("No REPI data yet.")
    st.markdown("")
    c4, c5 = st.columns(2)
    with c4:
        _kpi_card(
            "Portfolio Alpha",
            _fmt_pct(fund_alpha, ""),
            delta=f"Top vs peer avg · {_short_text(top_fund_name, 18)}",
            delta_color="off",
            accent="positive" if (fund_alpha or 0) >= 0 else "negative",
        )
        _mini_trend(fund_trend.tail(60), "date", "avg_return", "Fund average", CLR_SECONDARY)
    with c5:
        if delta_df is not None and not delta_df.empty:
            inflows = int((delta_df["delta"] > 0).sum())
            outflows = int((delta_df["delta"] < 0).sum())
            net_stocks = inflows - outflows
            active = int((delta_df["delta"].abs() > 0.00005).sum())
        else:
            inflows = outflows = net_stocks = active = 0
        _kpi_card(
            "Foreign Flow Signal",
            f"{net_stocks:+d} stocks",
            delta=f"{active} active · {inflows} in / {outflows} out",
            delta_color="off",
            accent="positive" if net_stocks > 0 else ("negative" if net_stocks < 0 else "primary"),
        )
        _mini_trend(flow_trend, "date", "net_stocks", "Net stocks", CLR_SECONDARY)

    st.markdown("")
    note_cols = st.columns(3)
    with note_cols[0]:
        _decision_note(
            "Macro pressure",
            "Inflation and rent are shown as separate indexed signals so daily price noise does not hide the rent trend.",
            "warn" if (inf_delta or 0) > 0 or (latest_rent is not None and (latest_rent.get("mom_pct") or 0) > 0) else "neutral",
        )
    with note_cols[1]:
        _decision_note(
            "Portfolio context",
            "Alpha is measured as the best tracked fund versus the current peer average, not against inflation.",
            "positive" if (fund_alpha or 0) > 0 else "neutral",
        )
    with note_cols[2]:
        flow_text = (
            "Latest liquidity deltas contain real movers."
            if meaningful_flow else
            "Latest liquidity snapshot has no meaningful daily ownership movement."
        )
        _decision_note("Flow quality", flow_text, "positive" if meaningful_flow else "warn")

    col_left, col_right = st.columns([5, 5])
    with col_left:
        st.markdown("<p class='section-title'>Macro Pressure Board</p>", unsafe_allow_html=True)
        macro_traces = []
        if not inf_df.empty:
            tmp = inf_df[["date", "index_value"]].copy()
            tmp["Signal"] = "Daily inflation basket"
            tmp = tmp.rename(columns={"index_value": "Index"})
            macro_traces.append(tmp)
        if not rent_df.empty:
            tmp = rent_df[["date", "rent_index"]].copy()
            tmp["Signal"] = "Official rent CPI"
            tmp = tmp.rename(columns={"rent_index": "Index"})
            macro_traces.append(tmp)
        if macro_traces:
            macro_df = pd.concat(macro_traces, ignore_index=True)
            fig_macro = px.line(
                macro_df,
                x="date",
                y="Index",
                color="Signal",
                markers=True,
                color_discrete_sequence=[CLR_TERTIARY, CLR_PRIMARY],
            )
            fig_macro.update_traces(line=dict(width=2.4), marker=dict(size=5))
            fig_macro.update_layout(
                **PLOTLY_LAYOUT,
                height=360,
                hovermode="x unified",
                legend_title_text="",
            )
            fig_macro.update_yaxes(title=dict(text="Index level"))
            fig_macro.update_xaxes(title=dict(text=""))
            st.plotly_chart(fig_macro, width="stretch")
            st.caption("Signals stay separate: each line keeps its own official/app index level instead of being forced onto a shared return axis.")
        else:
            st.info("No macro trend data is available yet.")

    with col_right:
        st.markdown("<p class='section-title'>Top Stocks by Foreign Inflow</p>", unsafe_allow_html=True)
        if meaningful_flow and delta_df is not None:
            top5 = delta_df.nlargest(5, "delta").copy()
            top5["label"] = top5["symbol"] + " - " + top5["company_name"].astype(str).str[:20]
            top5 = top5.sort_values("delta", ascending=True)
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                y=top5["label"],
                x=top5["delta"],
                orientation="h",
                marker=dict(color=CLR_SECONDARY),
                hovertemplate="<b>%{y}</b><br>Ownership delta: %{x:+.4f}pp<extra></extra>",
            ))
            _bar_layout = {**PLOTLY_LAYOUT}
            _bar_layout["xaxis"] = {
                **PLOTLY_LAYOUT["xaxis"],
                "title": "Ownership change (percentage points)",
                "tickformat": "+.4f",
            }
            _bar_layout["margin"] = dict(l=160, r=30, t=10, b=50)
            fig_bar.update_layout(**_bar_layout, height=360, showlegend=False)
            st.plotly_chart(fig_bar, width="stretch")
        elif delta_df is not None and not delta_df.empty:
            st.info("No meaningful flow: latest daily foreign-ownership deltas are all flat.")
        else:
            st.info("Need 2+ trading days for foreign-flow ranking.")

    st.markdown("<p class='section-title'>Data Health</p>", unsafe_allow_html=True)
    health_rows = [
        {
            "Domain": "Inflation",
            "Freshness": "OK" if latest_inf is not None else "No index",
            "Coverage": f"{len(inf_df):,} daily index rows",
            "Decision Use": "Nowcast price pressure",
        },
        {
            "Domain": "Rents",
            "Freshness": "OK" if latest_rent is not None else "No rent CPI",
            "Coverage": f"{len(rent_df):,} rent CPI rows",
            "Decision Use": "Rent pressure",
        },
        {
            "Domain": "Real Estate",
            "Freshness": "OK" if general_repi is not None else "No REPI",
            "Coverage": f"{len(repi_df):,} REPI rows",
            "Decision Use": "Property-price cycle",
        },
        {
            "Domain": "Portfolio",
            "Freshness": "OK" if not fund_latest.empty else "No NAV",
            "Coverage": f"{len(fund_latest):,} latest funds",
            "Decision Use": "Peer-relative performance",
        },
        {
            "Domain": "Foreign Liquidity",
            "Freshness": "Active" if meaningful_flow else "Flat / no signal",
            "Coverage": f"{len(liq_dates):,} trading days",
            "Decision Use": "Accumulation and breadth",
        },
    ]
    if repi_error:
        health_rows[2]["Freshness"] = "REPI source warning"
    st.dataframe(pd.DataFrame(health_rows), width="stretch", hide_index=True, height=180)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — MACRO & INFLATION
# ══════════════════════════════════════════════════════════════════════════════

def page_macro():
    _page_header(
        "Macro & Inflation",
        "Saudi Daily CPI Tracker — Supermarket + Official GASTAT Price and CPI Data",
    )

    if not mod_inflation.db_available():
        st.warning("inflation_index.db not found. Run `python main.py` to scrape price data.")
        return

    db_key = mod_inflation.db_fingerprint()
    index_df = mod_inflation.load_index_history(db_key)
    n_items, n_sources = mod_inflation.load_item_count_and_stores(db_key)
    price_context = mod_inflation.get_latest_price_context(db_key)

    latest_index_date = price_context.get("latest_index_date")
    latest_raw_date = price_context.get("latest_raw_date")
    display_price_date = price_context.get("display_date")
    latest_raw = price_context.get("latest_raw") or {}
    latest_run = price_context.get("latest_run") or {}
    latest_raw_ok = int(latest_raw.get("ok_items") or 0)
    latest_raw_expected = int(latest_raw.get("expected_items") or n_items or 0)
    latest_raw_coverage = float(latest_raw.get("coverage_pct") or 0.0)
    show_partial = False

    if price_context.get("raw_is_partial"):
        show_partial = st.toggle(
            "Show latest partial scrape",
            value=False,
            help="Audit the newest raw scrape without replacing the last computed index day.",
            key="macro_show_partial_scrape",
        )
        if show_partial:
            display_price_date = latest_raw_date

    prices_df = mod_inflation.load_latest_prices(db_key, selected_date=display_price_date)

    def _latest_source_label(row) -> str:
        store_name = str(row.get("store_name", ""))
        source_type = str(row.get("source_type", "supermarket"))
        source_name = str(row.get("source_name", "Supermarket scraper"))
        if store_name == "Noon":
            return "Noon marketplace"
        if store_name == "Amazon":
            return "Amazon marketplace"
        if store_name.startswith("GASTAT"):
            return store_name
        if store_name == "External CPI Proxy":
            return "External CPI Proxy - pending real source"
        if source_type != "supermarket":
            return store_name or source_name
        if source_name == "External CPI Proxy":
            return "External CPI Proxy - pending real source"
        return f"{store_name} product page"

    if not prices_df.empty:
        for col, fallback in (
            ("source_type", "supermarket"),
            ("source_name", "Supermarket scraper"),
            ("store_name", ""),
        ):
            if col not in prices_df.columns:
                prices_df[col] = fallback
        prices_df["data_source"] = prices_df.apply(_latest_source_label, axis=1)

    latest_value = None if index_df.empty else index_df["index_value"].iloc[-1]

    if not prices_df.empty:
        ok_prices = prices_df[
            (prices_df["scrape_status"] == "ok") & prices_df["price"].notna()
        ]
        covered_items = ok_prices["item_name"].nunique()
        coverage_pct = (covered_items / n_items * 100.0) if n_items else 0.0
    else:
        covered_items = 0
        coverage_pct = 0.0

    if len(index_df) >= 2:
        prev_value = index_df["index_value"].iloc[-2]
        dod_pct = ((latest_value - prev_value) / prev_value) * 100 if prev_value != 0 else 0.0
    else:
        prev_value = None
        dod_pct = 0.0

    # ── KPI Row ──────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if latest_value is None:
            _kpi_card("Headline CPI Index", "N/A", delta="coverage gate active", accent="warn", text_value=True)
        else:
            _kpi_card(
                "Headline CPI Index",
                f"{latest_value:.4f}",
                delta=f"{dod_pct:+.2f}% DoD" if prev_value is not None else "First day",
                delta_color="inverse",
                accent="warn",
            )
    with c2:
        _kpi_card(
            "Displayed Coverage",
            f"{covered_items:,}/{n_items:,}",
            delta=f"{coverage_pct:.1f}% · {display_price_date or 'N/A'}",
            accent="positive" if coverage_pct >= 80 else "warn",
        )
    with c3:
        _kpi_card(
            "Last Computed Index Date",
            str(latest_index_date or "N/A"),
            delta="daily_index anchor",
            delta_color="off",
            accent="violet",
            text_value=True,
        )
    with c4:
        raw_delta = (
            f"{latest_raw_ok:,}/{latest_raw_expected:,} raw · {latest_raw_coverage:.1f}%"
            if latest_raw_date else
            f"{n_sources:,} sources monitored"
        )
        _kpi_card(
            "Latest Raw Scrape Date",
            str(latest_raw_date or "N/A"),
            delta=raw_delta,
            delta_color="off",
            accent="primary" if latest_raw_coverage >= 80 else "warn",
            text_value=True,
        )

    st.markdown("")

    if price_context.get("raw_is_partial") and not show_partial:
        status_suffix = ""
        if latest_run:
            run_status = str(latest_run.get("status") or "unknown")
            run_stage = str(latest_run.get("stage") or "unknown")
            status_suffix = f" Run status: {run_status} · {run_stage}."
        _decision_note(
            "Displayed index uses last complete day",
            (
                f"{latest_index_date}. Latest partial scrape: {latest_raw_date} · "
                f"{latest_raw_ok}/{latest_raw_expected} · waiting for official/source completion."
                f"{status_suffix}"
            ),
            "warn",
        )
    elif price_context.get("raw_is_partial") and show_partial:
        _decision_note(
            "Partial scrape audit mode",
            (
                f"Showing raw observations from {latest_raw_date}. The headline KPI still uses "
                f"the last computed index date: {latest_index_date}."
            ),
            "warn",
        )

    if index_df.empty:
        if prices_df.empty:
            st.warning("No price observation rows found yet. Run the scraper and/or `python external_sources.py`.")
        else:
            st.warning(
                f"Index not computed yet: latest basket coverage is {coverage_pct:.1f}% "
                f"({covered_items}/{n_items} items), below the 80% quality threshold. "
                "Latest price observation rows are shown below."
            )

    if price_context.get("raw_is_partial"):
        st.markdown("<p class='section-title'>Latest Partial Source Coverage</p>", unsafe_allow_html=True)
        source_health = mod_inflation.load_source_health_for_date(db_key, selected_date=latest_raw_date)
        if not source_health.empty:
            priority_sources = {
                "Panda": 0,
                "Ninja": 1,
                "Danube": 2,
                "Tamimi": 3,
                "Noon": 4,
                "Amazon": 5,
                "GASTAT Average Prices": 6,
                "GASTAT CPI Category Index": 7,
            }
            partial_focus = source_health.copy()
            partial_focus["_order"] = partial_focus["Source"].map(priority_sources).fillna(99)
            partial_focus = partial_focus[
                (partial_focus["Rows"] > 0) | (partial_focus["_order"] < 99)
            ].sort_values(["_order", "Rows"], ascending=[True, False])
            st.dataframe(
                partial_focus[
                    ["Source", "Status", "Expected Items", "Observed Items", "OK Items", "Rows", "OK Rows"]
                ],
                width="stretch",
                hide_index=True,
                height=min(330, 40 + 32 * len(partial_focus)),
            )

    # ── Main Chart: Historical Inflation ─────────────────────────────────
    st.markdown("<p class='section-title'>Historical Inflation Index Trend</p>", unsafe_allow_html=True)

    if index_df.empty:
        st.info("No daily index series is available until a scrape reaches the required basket coverage.")
    else:
    # The Laspeyres CPI lives in a narrow band around 100 (e.g. 100.0-101.6
    # in the current data set).  The legacy px.area chart used fill='tozeroy'
    # which forced the y-axis to start at 0 — every fractional daily move was
    # crushed into a flat green line.  Build the chart as a go.Scatter with
    # the fill anchored to an INVISIBLE baseline JUST BELOW the data minimum
    # so Plotly's autorange frames the active spectrum (~99-103) cleanly.
        y_lo_data = float(index_df["index_value"].min())
        y_hi_data = float(index_df["index_value"].max())
        y_span    = max(y_hi_data - y_lo_data, 1.0)
        y_pad     = max(y_span * 0.4, 0.5)   # ≥ 0.5 unit breathing room
        y_floor   = y_lo_data - y_pad         # invisible band floor

        fig = go.Figure()
        # Invisible baseline trace: re-anchors the area fill to y_floor so the
        # subsequent fill='tonexty' draws the green band between the data line
        # and the floor (not all the way down to zero).
        fig.add_trace(go.Scatter(
            x=index_df["date"],
            y=[y_floor] * len(index_df),
            mode="lines",
            line=dict(color="rgba(0,0,0,0)", width=0),
            showlegend=False,
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=index_df["date"],
            y=index_df["index_value"],
            mode="lines",
            name="Inflation Index",
            line=dict(color=CLR_SECONDARY, width=2.5, shape="spline", smoothing=1.0),
            fill="tonexty",
            fillcolor="rgba(105,246,184,0.10)",
            hovertemplate="<b>%{x|%d %b %Y}</b><br>Index: <b>%{y:.4f}</b><extra></extra>",
        ))

        fig.update_layout(
            **PLOTLY_LAYOUT,
            height=400,
            hovermode="x unified",
            showlegend=False,
        )

        # Auto-range on the bounded fill — Plotly now frames the active data
        # spectrum (e.g. 99.5-101.6) instead of starting at zero.  fixedrange
        # stays False so the user can still drag/zoom interactively.
        fig.update_yaxes(
            autorange=True,
            fixedrange=False,
            title=dict(text="Index Value (Base = 100)"),
            tickformat=".2f",
        )
        fig.update_xaxes(title=dict(text=""))

        # Reference line at the canonical Laspeyres base value.
        fig.add_hline(
            y=100.0,
            line_dash="dot", line_color=CLR_TERTIARY, line_width=1,
            annotation_text="Base = 100",
            annotation_position="top left",
            annotation_font_color=CLR_TERTIARY,
        )
        st.plotly_chart(fig, width="stretch")

    # ── Basket Composition Drawer (institutional transparency) ───────────
    # Surfaces the app's expanded proxy basket + its normalised statistical
    # weights directly under the index chart. Data comes from basket_config
    # (the single source of truth), not the database, so a stale `items`
    # table cannot misrepresent the app methodology.
    with st.expander(
        "📋 سلة التطبيق الموسعة والأوزان الإحصائية (Expanded App Basket & Weights)",
        expanded=False,
    ):
        from basket_config import normalized_basket   # noqa: WPS433

        basket = normalized_basket()
        usable_source_rows = prices_df[
            (prices_df["scrape_status"] == "ok") & prices_df["price"].notna()
        ].copy() if not prices_df.empty else pd.DataFrame()
        if not usable_source_rows.empty and "data_source" not in usable_source_rows.columns:
            usable_source_rows["data_source"] = usable_source_rows.apply(_latest_source_label, axis=1)

        def _source_priority(value: str) -> tuple[int, str]:
            if value.endswith("product page") or value in {"Noon marketplace", "Amazon marketplace"}:
                return (0, value)
            if value and not value.startswith("External CPI Proxy") and not value.startswith("GASTAT"):
                return (1, value)
            if value == "GASTAT Average Prices":
                return (2, value)
            if value.startswith("GASTAT"):
                return (3, value)
            if value.startswith("External CPI Proxy"):
                return (9, value)
            return (5, value)

        source_lookup: dict[str, str] = {}
        primary_source_rows: list[dict[str, str]] = []
        if not usable_source_rows.empty:
            for item_name, group in usable_source_rows.groupby("item_name"):
                sources = sorted(set(group["data_source"].dropna()), key=_source_priority)
                if sources:
                    source_lookup[item_name] = ", ".join(sources)
                    primary_source_rows.append({
                        "Source": sources[0],
                        "Item": item_name,
                    })

        if primary_source_rows:
            source_mix = (
                pd.DataFrame(primary_source_rows)
                  .groupby("Source", as_index=False)
                  .agg(Items=("Item", "nunique"))
                  .sort_values("Items", ascending=False)
            )
            st.dataframe(source_mix, width="stretch", hide_index=True, height=150)

        basket_df = pd.DataFrame(
            [
                {
                    "اسم المنتج (Item Name)":      item["name"],
                    "التصنيف (Category)":          item["category"],
                    "مصدر السعر الحالي (Current Price Source)": source_lookup.get(
                        item["name"],
                        item.get("source", {}).get("name", "No latest price observation"),
                    ),
                    "الوزن الإحصائي (Weight %)":   item["weight"],
                }
                for item in basket
            ]
        )
        # Sort by weight descending — heaviest contributors surface first.
        basket_df = (
            basket_df.sort_values("الوزن الإحصائي (Weight %)", ascending=False)
                     .reset_index(drop=True)
        )
        # Format weight as a percentage with 3 decimals (e.g. 2.500 %).
        basket_df["الوزن الإحصائي (Weight %)"] = (
            basket_df["الوزن الإحصائي (Weight %)"].apply(lambda w: f"{w * 100:.3f}%")
        )

        st.dataframe(basket_df, width="stretch", hide_index=True)

    # ── Bottom Row: Category Weights + Heatmap ──────────────────────────
    col_left, col_right = st.columns([4, 6])

    with col_left:
        st.markdown("<p class='section-title'>Top Basket Weights by Category</p>", unsafe_allow_html=True)
        basket_df = mod_inflation.load_basket_summary(db_key)
        if not basket_df.empty:
            weight_scale = 100.0 if float(basket_df["total_weight"].sum()) <= 1.5 else 1.0
            chart_weights = basket_df.copy()
            chart_weights["Weight %"] = chart_weights["total_weight"] * weight_scale
            chart_weights = chart_weights.sort_values("Weight %", ascending=False).head(14)
            chart_weights = chart_weights.sort_values("Weight %", ascending=True)
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                y=chart_weights["category"],
                x=chart_weights["Weight %"],
                orientation="h",
                marker=dict(color=CLR_PRIMARY),
                text=chart_weights["Weight %"].map(lambda value: f"{value:.1f}%"),
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>Weight: %{x:.2f}%<extra></extra>",
            ))
            _cat_layout = {**PLOTLY_LAYOUT}
            _cat_layout["xaxis"] = {
                **PLOTLY_LAYOUT["xaxis"],
                "title": "Basket weight",
                "ticksuffix": "%",
            }
            _cat_layout["margin"] = dict(l=125, r=45, t=10, b=45)
            fig_bar.update_layout(**_cat_layout, height=360, showlegend=False)
            st.plotly_chart(fig_bar, width="stretch")
            with st.expander("Full category weight table", expanded=False):
                full_weights = basket_df.copy()
                full_weights["Weight %"] = full_weights["total_weight"] * weight_scale
                full_weights = full_weights.sort_values("Weight %", ascending=False)
                st.dataframe(
                    full_weights[["category", "items", "Weight %"]].rename(
                        columns={"category": "Category", "items": "Items"}
                    ),
                    width="stretch",
                    hide_index=True,
                )

    with col_right:
        st.markdown(
            f"<p class='section-title'>Price Observations · {_html_escape(display_price_date or 'N/A')}</p>",
            unsafe_allow_html=True,
        )
        if not prices_df.empty:
            if "match_tier" not in prices_df.columns:
                prices_df["match_tier"] = "exact"
            if "observed_title" not in prices_df.columns:
                prices_df["observed_title"] = None

            accepted_prices = prices_df[
                (prices_df["scrape_status"] == "ok") & prices_df["price"].notna()
            ].copy()
            if not accepted_prices.empty:
                match_counts = (
                    accepted_prices["match_tier"].fillna("exact").value_counts()
                                   .rename_axis("Match Tier").reset_index(name="Rows")
                )
                st.dataframe(match_counts, width="stretch", hide_index=True, height=115)

            pivot = (
                prices_df.pivot_table(
                    index=["item_name", "category", "weight_percentage"],
                    columns="store_name", values="price", aggfunc="first",
                ).reset_index()
            )
            pivot.columns.name = None
            usable_source_rows = prices_df[
                (prices_df["scrape_status"] == "ok") & prices_df["price"].notna()
            ]
            source_lookup = (
                usable_source_rows.groupby(["item_name", "category", "weight_percentage"])["data_source"]
                         .apply(lambda values: ", ".join(sorted(set(values.dropna()))))
                         .reset_index(name="Data Source")
            )
            pivot = pivot.merge(
                source_lookup,
                on=["item_name", "category", "weight_percentage"],
                how="left",
            )
            store_cols = [
                c for c in pivot.columns
                if c not in ("item_name", "category", "weight_percentage", "Data Source")
            ]
            pivot["Avg / Index"] = pivot[store_cols].mean(axis=1).round(2)
            pivot = pivot.rename(columns={"item_name": "Item", "category": "Category", "weight_percentage": "Weight"})
            display_cols = ["Item", "Category", "Data Source", "Weight"] + store_cols + ["Avg / Index"]
            st.dataframe(
                pivot[display_cols],
                width="stretch", hide_index=True,
                height=min(400, 40 + 35 * len(pivot)),
            )
            if not accepted_prices.empty:
                audit_df = accepted_prices.rename(
                    columns={
                        "item_name": "Item",
                        "store_name": "Store",
                        "price": "Price",
                        "match_tier": "Match",
                        "observed_title": "Observed Product",
                        "data_source": "Data Source",
                    }
                )
                audit_df["Observed Product"] = audit_df["Observed Product"].fillna(audit_df["Item"])
                audit_df["Price"] = audit_df["Price"].round(2)
                st.dataframe(
                    audit_df[["Item", "Store", "Data Source", "Price", "Match", "Observed Product"]],
                    width="stretch",
                    hide_index=True,
                    height=min(320, 40 + 32 * len(audit_df)),
                )
        else:
            st.info("No price data available yet.")

    st.markdown("")
    st.markdown("<p class='section-title'>Price Data Quality Diagnostics</p>", unsafe_allow_html=True)
    if prices_df.empty:
        st.info("Quality diagnostics will appear after the first price observation batch.")
    else:
        quality_left, quality_mid, quality_right = st.columns([2, 2, 2])
        with quality_left:
            status_counts = (
                prices_df["scrape_status"].fillna("unknown").value_counts()
                         .rename_axis("Status").reset_index(name="Rows")
            )
            st.dataframe(status_counts, width="stretch", hide_index=True, height=160)
        with quality_mid:
            source_mix = (
                prices_df.assign(
                    source_bucket=prices_df["data_source"].fillna("Unknown")
                )
                .groupby("source_bucket", as_index=False)
                .agg(Items=("item_name", "nunique"), Rows=("item_name", "size"))
                .sort_values(["Items", "Rows"], ascending=False)
                .rename(columns={"source_bucket": "Current Source"})
            )
            st.dataframe(source_mix, width="stretch", hide_index=True, height=160)
        with quality_right:
            ok_prices = prices_df[
                (prices_df["scrape_status"] == "ok") & prices_df["price"].notna()
            ].copy()
            proxy_rows = ok_prices[
                ok_prices["data_source"].fillna("").str.contains("External CPI Proxy", case=False, na=False)
            ]
            proxy_share = (proxy_rows["item_name"].nunique() / covered_items * 100.0) if covered_items else 0.0
            missing_items = max(n_items - covered_items, 0)
            _decision_note(
                "Coverage gate",
                (
                    f"Displayed date {display_price_date or 'N/A'}: {covered_items}/{n_items} items priced · "
                    f"{missing_items} missing · {proxy_share:.1f}% proxy among priced items."
                ),
                "positive" if coverage_pct >= 80 and proxy_share < 25 else "warn",
            )
            if proxy_share > 0:
                _decision_note(
                    "Proxy watch",
                    "External CPI proxy rows are useful placeholders, but real daily sources should replace them over time.",
                    "warn",
                )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — RENTS / REAL ESTATE
# ══════════════════════════════════════════════════════════════════════════════

def page_real_estate(mode: str = "all"):
    show_rents = mode in {"all", "rents"}
    show_property = mode in {"all", "property"}
    page_title = "الإيجارات" if mode == "rents" else ("العقار" if mode == "property" else "الإيجارات والعقار")
    page_subtitle = (
        "Saudi Ejar contract rents and official rent CPI"
        if mode == "rents"
        else (
            "Saudi real estate price indices by region, sector, and type"
            if mode == "property"
            else "Saudi Rent CPI and Real Estate Price Index Monitor"
        )
    )
    _page_header(
        page_title,
        page_subtitle,
    )

    rent_df = (
        mod_real_estate.load_rent_index_history(mod_real_estate.db_fingerprint())
        if show_rents else pd.DataFrame()
    )
    repi_df, repi_error = (
        mod_real_estate.load_real_estate_price_index_history()
        if show_property else (pd.DataFrame(), None)
    )
    regional_repi_df, regional_repi_error = (
        mod_real_estate.load_regional_real_estate_price_index_history()
        if show_property else (pd.DataFrame(), None)
    )
    legacy_regional_sector_repi_df, legacy_regional_sector_repi_error = (
        mod_real_estate.load_legacy_region_sector_real_estate_price_index_history()
        if show_property else (pd.DataFrame(), None)
    )

    def _pct(value, suffix: str) -> str:
        if value is None or pd.isna(value):
            return "N/A"
        return f"{float(value):+.2f}% {suffix}"

    def _num(value, digits: int = 2) -> str:
        if value is None or pd.isna(value):
            return "N/A"
        return f"{float(value):.{digits}f}"

    def _latest_sector(sector_display: str):
        if repi_df.empty:
            return None
        sector_rows = repi_df[repi_df["sector_display"] == sector_display].sort_values("period_date")
        if sector_rows.empty:
            return None
        return sector_rows.iloc[-1]

    rent_latest = None if rent_df.empty else rent_df.iloc[-1]
    general_latest = _latest_sector("General index")
    residential_latest = _latest_sector("Residential total")
    commercial_latest = _latest_sector("Commercial total")

    if show_rents:
        c1, c2 = st.columns(2)
        with c1:
            _kpi_card(
                "Rent CPI Index",
                _num(None if rent_latest is None else rent_latest["rent_index"]),
                delta=_pct(None if rent_latest is None else rent_latest["mom_pct"], "MoM"),
                delta_color="inverse",
                accent="warn",
            )
        with c2:
            _kpi_card(
                "Rent YoY",
                _pct(None if rent_latest is None else rent_latest["yoy_pct"], ""),
                delta=(
                    rent_latest["date"].strftime("%Y-%m")
                    if rent_latest is not None and hasattr(rent_latest["date"], "strftime")
                    else "N/A"
                ),
                delta_color="off",
                accent="primary",
                text_value=True,
            )

    if show_property:
        c1, c2, c3 = st.columns(3)
        with c1:
            _kpi_card(
                "Real Estate General",
                _num(None if general_latest is None else general_latest["value"]),
                delta=_pct(None if general_latest is None else general_latest["qoq_pct"], "QoQ"),
                delta_color="normal",
                accent="violet",
            )
        with c2:
            _kpi_card(
                "Residential REPI",
                _num(None if residential_latest is None else residential_latest["value"]),
                delta=_pct(None if residential_latest is None else residential_latest["qoq_pct"], "QoQ"),
                delta_color="normal",
                accent="positive",
            )
        with c3:
            _kpi_card(
                "Commercial REPI",
                _num(None if commercial_latest is None else commercial_latest["value"]),
                delta=_pct(None if commercial_latest is None else commercial_latest["qoq_pct"], "QoQ"),
                delta_color="normal",
                accent="warn",
            )

    if show_property and repi_error:
        st.warning(f"Real estate price source unavailable: {repi_error}")
    if show_property and regional_repi_error:
        st.warning(f"Regional real estate price source unavailable: {regional_repi_error}")
    if show_property and legacy_regional_sector_repi_error:
        st.warning(f"Legacy regional-sector price source warning: {legacy_regional_sector_repi_error}")

    st.markdown("")

    st.markdown("<p class='section-title'>Regional Rent Index · Ejar Contracts</p>", unsafe_allow_html=True)
    ejar_history_options = {
        "1Y": 1,
        "3Y": 3,
        "5Y": 5,
        "All": mod_real_estate.MAX_EJAR_HISTORY_YEARS,
    }
    toolbar_cols = st.columns([1.1, 1.9, 1.45, 1.05])
    with toolbar_cols[0]:
        ejar_history_label = st.segmented_control(
            "Range",
            options=list(ejar_history_options),
            default="5Y",
            key="real_estate_ejar_range",
        )
    ejar_history_years = ejar_history_options.get(ejar_history_label or "5Y", 5)
    ejar_series_df, _ejar_latest_df, ejar_error = mod_real_estate.load_ejar_regional_rent_index(ejar_history_years)
    if ejar_error:
        st.warning(f"Ejar regional rent source warning: {ejar_error}")

    if ejar_series_df.empty:
        st.info("Regional Ejar rent data is not available yet.")
    else:
        ejar_series_df = ejar_series_df.copy()
        ejar_series_df["date"] = pd.to_datetime(ejar_series_df["date"], errors="coerce")
        ejar_series_df = ejar_series_df.dropna(subset=["date"])
        loaded_min_date = ejar_series_df["date"].min().date()
        loaded_max_date = ejar_series_df["date"].max().date()

        with toolbar_cols[1]:
            date_range_value = st.date_input(
                "Date range",
                value=(loaded_min_date, loaded_max_date),
                min_value=loaded_min_date,
                max_value=loaded_max_date,
                key=f"real_estate_ejar_date_range_{ejar_history_label or '5Y'}",
            )
        with toolbar_cols[2]:
            ejar_view_mode = st.segmented_control(
                "Rent view",
                options=["Unified Index", "Local Index", "Annual Rent SAR"],
                default="Unified Index",
                key="real_estate_ejar_view",
            )
        with toolbar_cols[3]:
            compare_regions = st.toggle(
                "Compare regions",
                value=False,
                key="real_estate_ejar_compare",
            )

        if isinstance(date_range_value, tuple) and len(date_range_value) == 2:
            selected_start_date, selected_end_date = date_range_value
        else:
            selected_start_date, selected_end_date = loaded_min_date, loaded_max_date
        if selected_start_date is None or selected_end_date is None:
            selected_start_date, selected_end_date = loaded_min_date, loaded_max_date
        if selected_start_date > selected_end_date:
            selected_start_date, selected_end_date = selected_end_date, selected_start_date

        preferred_regions = ["الوسطى", "الشرقية", "الغربية", "الجنوب", "الشمال"]
        available_regions = [
            region for region in preferred_regions
            if region in set(ejar_series_df["region"].dropna())
        ]
        available_regions += [
            region for region in sorted(ejar_series_df["region"].dropna().unique())
            if region not in available_regions
        ]
        default_region = "الوسطى" if "الوسطى" in available_regions else available_regions[0]

        region_cols = st.columns([1.15, 2.85])
        with region_cols[0]:
            selected_region = st.selectbox(
                "Region",
                options=available_regions,
                index=available_regions.index(default_region),
                key="real_estate_ejar_focus_region",
            )
        if compare_regions:
            with region_cols[1]:
                selected_regions = st.multiselect(
                    "Regions to compare",
                    options=available_regions,
                    default=[selected_region],
                    key="real_estate_ejar_compare_regions",
                )
            if not selected_regions:
                selected_regions = [selected_region]
        else:
            selected_regions = [selected_region]

        if ejar_view_mode == "Annual Rent SAR":
            ejar_y_field = "avg_annual_rent"
            ejar_y_title = "Avg Annual Rent (SAR)"
            ejar_value_title = "Latest Annual Rent"
        elif ejar_view_mode == "Local Index":
            ejar_y_field = "rent_index_visible_local"
            ejar_y_title = "Local Index (selected period start = 100)"
            ejar_value_title = "Latest Local Index"
        else:
            ejar_y_field = "rent_index_common"
            ejar_y_title = "Unified Index (equal-region baseline = 100)"
            ejar_value_title = "Latest Unified Index"

        start_ts = pd.Timestamp(selected_start_date)
        end_ts = pd.Timestamp(selected_end_date)
        ejar_filtered_df = ejar_series_df[
            (ejar_series_df["date"] >= start_ts)
            & (ejar_series_df["date"] <= end_ts)
            & (ejar_series_df["region"].isin(selected_regions))
        ].copy()
        ejar_filtered_df = _add_visible_local_rent_index(ejar_filtered_df)
        ejar_chart_df = ejar_filtered_df.copy()
        ejar_chart_df[ejar_y_field] = pd.to_numeric(ejar_chart_df[ejar_y_field], errors="coerce")
        ejar_chart_df = ejar_chart_df.dropna(subset=[ejar_y_field])

        focus_df = ejar_filtered_df[
            ejar_filtered_df["region"] == selected_region
        ].sort_values("date").copy()
        focus_chart_df = focus_df.dropna(subset=[ejar_y_field])
        if not focus_chart_df.empty:
            latest_focus = focus_chart_df.iloc[-1]
            first_focus = focus_chart_df.iloc[0]
            latest_metric = float(latest_focus[ejar_y_field])
            first_metric = float(first_focus[ejar_y_field])
            period_change = (
                (latest_metric - first_metric) / first_metric * 100.0
                if first_metric else None
            )
            latest_metric_text = (
                f"{latest_metric:,.0f} SAR"
                if ejar_view_mode == "Annual Rent SAR"
                else f"{latest_metric:.2f}"
            )
            metric_cols = st.columns(5)
            with metric_cols[0]:
                _kpi_card(ejar_value_title, latest_metric_text, delta=selected_region, delta_color="off", accent="primary", text_value=ejar_view_mode == "Annual Rent SAR")
            with metric_cols[1]:
                _kpi_card("MoM", _pct(latest_focus["mom_pct"], ""), delta=latest_focus["date"].strftime("%Y-%m"), delta_color="inverse", accent="warn")
            with metric_cols[2]:
                _kpi_card("Period Change", _pct(period_change, ""), delta=f"from {first_focus['date']:%Y-%m}", delta_color="inverse", accent="violet")
            with metric_cols[3]:
                _kpi_card("Contracts", f"{float(latest_focus['contracts']):,.0f}", delta="latest period", delta_color="off", accent="positive")
            with metric_cols[4]:
                coverage_text = f"{int(latest_focus['cities_observed'])}/{int(latest_focus['cities_expected'])}"
                _kpi_card("Coverage", coverage_text, delta="cities", delta_color="off", accent="primary", text_value=True)

        if ejar_chart_df.empty:
            st.info(
                "Unified regional rent index needs at least two regions in one shared baseline month. "
                "Switch to Local Index or Annual Rent SAR to view the cached rent levels."
            )
        else:
            hover_template = (
                "<b>%{x|%b %Y}</b><br>"
                "Region: <b>%{customdata[0]}</b><br>"
                "Unified index: <b>%{customdata[1]:.2f}</b><br>"
                "Local index: <b>%{customdata[2]:.2f}</b><br>"
                "Avg annual rent: <b>%{customdata[3]:,.0f} SAR</b><br>"
                "Contracts: <b>%{customdata[4]:,.0f}</b><br>"
                "Coverage: %{customdata[5]:.0f}/%{customdata[6]:.0f} cities"
                "<extra></extra>"
            )
            custom_cols = [
                "region",
                "rent_index_common",
                "rent_index_visible_local",
                "avg_annual_rent",
                "contracts",
                "cities_observed",
                "cities_expected",
            ]
            if compare_regions:
                fig_ejar = px.line(
                    ejar_chart_df,
                    x="date",
                    y=ejar_y_field,
                    color="region",
                    markers=True,
                    custom_data=custom_cols,
                    color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                )
                fig_ejar.update_traces(
                    line=dict(width=2.4),
                    marker=dict(size=5),
                    hovertemplate=hover_template,
                )
            else:
                fig_ejar = go.Figure()
                fig_ejar.add_trace(go.Scatter(
                    x=ejar_chart_df["date"],
                    y=ejar_chart_df[ejar_y_field],
                    mode="lines+markers",
                    name=selected_region,
                    customdata=ejar_chart_df[custom_cols].to_numpy(),
                    line=dict(color=CLR_PRIMARY, width=3.0, shape="spline", smoothing=0.8),
                    marker=dict(size=5, color=CLR_PRIMARY),
                    hovertemplate=hover_template,
                ))
                latest_point = ejar_chart_df.sort_values("date").iloc[-1]
                fig_ejar.add_trace(go.Scatter(
                    x=[latest_point["date"]],
                    y=[latest_point[ejar_y_field]],
                    mode="markers",
                    name="Latest",
                    customdata=latest_point[custom_cols].to_frame().T.to_numpy(),
                    marker=dict(size=13, color=CLR_TERTIARY, line=dict(color="#0b1022", width=2)),
                    hovertemplate=hover_template,
                    showlegend=False,
                ))
            ejar_layout = {**PLOTLY_LAYOUT}
            ejar_layout["margin"] = dict(l=82, r=28, t=36, b=72)
            fig_ejar.update_layout(
                **ejar_layout,
                height=430,
                hovermode="x unified",
                legend_title_text="",
                showlegend=compare_regions,
            )
            fig_ejar.update_yaxes(
                title=dict(text=ejar_y_title),
                tickformat=",.0f" if ejar_view_mode == "Annual Rent SAR" else ".1f",
            )
            fig_ejar.update_xaxes(
                title=dict(text=""),
                rangeslider_visible=True,
                rangeslider_thickness=0.055,
            )
            if ejar_view_mode in {"Unified Index", "Local Index"}:
                fig_ejar.add_hline(
                    y=100.0,
                    line_dash="dot",
                    line_color=CLR_TERTIARY,
                    line_width=1,
                    annotation_text="Local baseline = 100" if ejar_view_mode == "Local Index" else "Common baseline = 100",
                    annotation_position="top left",
                    annotation_font_color=CLR_TERTIARY,
                )
            st.plotly_chart(fig_ejar, width="stretch")
            st.caption(
                f"Source: [{mod_real_estate.EJAR_SOURCE_NAME}]"
                f"({mod_real_estate.EJAR_SOURCE_URL}) · "
                "authenticated residential rental contracts aggregated by city and region."
            )

        ejar_table_source = (
            ejar_filtered_df.sort_values("date")
                            .groupby("region", as_index=False)
                            .tail(1)
                            .copy()
        )
        city_names = {
            region: "، ".join(city["city_ar"] for city in cities)
            for region, cities in mod_real_estate.REGIONAL_RENT_CITY_GROUPS.items()
        }
        ejar_table_columns = [
            "Region",
            "Cities",
            "Latest Period",
            "Unified Index",
            "Local Index",
            "Avg Annual Rent (SAR)",
            "Contracts",
            "MoM",
            "Coverage",
        ]
        ejar_table = pd.DataFrame(columns=ejar_table_columns)
        if not ejar_table_source.empty:
            ejar_table_source["Region"] = ejar_table_source["region"]
            ejar_table_source["Cities"] = ejar_table_source["region"].map(city_names)
            ejar_table_source["Latest Period"] = ejar_table_source["date"].dt.strftime("%Y-%m")
            ejar_table_source["Unified Index"] = ejar_table_source["rent_index_common"]
            ejar_table_source["Local Index"] = ejar_table_source["rent_index_visible_local"]
            ejar_table_source["Avg Annual Rent (SAR)"] = ejar_table_source["avg_annual_rent"]
            ejar_table_source["Contracts"] = ejar_table_source["contracts"]
            ejar_table_source["MoM"] = ejar_table_source["mom_pct"]
            ejar_table_source["Coverage"] = (
                ejar_table_source["cities_observed"].astype(int).astype(str)
                + "/"
                + ejar_table_source["cities_expected"].astype(int).astype(str)
                + " cities"
            )
            ejar_table = ejar_table_source[ejar_table_columns].sort_values(
                {
                    "Annual Rent SAR": "Avg Annual Rent (SAR)",
                    "Local Index": "Local Index",
                }.get(ejar_view_mode, "Unified Index"),
                ascending=False,
            )
        ejar_table["Unified Index"] = ejar_table["Unified Index"].map(
            lambda value: "N/A" if pd.isna(value) else f"{float(value):.2f}"
        )
        ejar_table["Local Index"] = ejar_table["Local Index"].map(
            lambda value: "N/A" if pd.isna(value) else f"{float(value):.2f}"
        )
        ejar_table["Avg Annual Rent (SAR)"] = ejar_table["Avg Annual Rent (SAR)"].map(
            lambda value: f"{float(value):,.0f}"
        )
        ejar_table["Contracts"] = ejar_table["Contracts"].map(lambda value: f"{float(value):,.0f}")
        ejar_table["MoM"] = ejar_table["MoM"].map(
            lambda value: "N/A" if pd.isna(value) else f"{float(value):+.2f}%"
        )
        if ejar_table.empty:
            st.info("No regional rent rows match the selected date range and region filter.")
        else:
            st.dataframe(
                ejar_table[ejar_table_columns],
                width="stretch",
                hide_index=True,
                height=min(280, 40 + 35 * len(ejar_table)),
            )
        st.caption(
            "Ejar regional rent uses documented residential rental contracts; "
            "values are average annual rent, not CPI. Unified Index uses one equal-region baseline, "
            "while Local Index rebases every visible region to 100 at the selected period start, "
            "and the date picker filters the visible chart and table without changing cached source data."
        )

    st.markdown("<p class='section-title'>Regional Property Price Index · Official REPI</p>", unsafe_allow_html=True)
    if regional_repi_df.empty and legacy_regional_sector_repi_df.empty:
        st.info("Regional real estate price index rows are not available yet.")
    else:
        region_labels_ar = {
            "Riyadh": "الرياض",
            "Eastern Province": "الشرقية",
            "Makkah": "مكة",
            "Madinah": "المدينة",
            "Al Qaseem": "القصيم",
            "Aseer": "عسير",
            "Tabouk": "تبوك",
            "Hail": "حائل",
            "Northern Borders": "الحدود الشمالية",
            "Jazan": "جازان",
            "Najran": "نجران",
            "Al Baha": "الباحة",
            "Al Jouf": "الجوف",
            "Saudi Arabia": "السعودية",
        }

        def _region_display(region: str) -> str:
            label = region_labels_ar.get(str(region))
            return f"{label} · {region}" if label else str(region)

        basis_options = []
        if not regional_repi_df.empty:
            basis_options.append("Current regions (2023=100)")
        if not legacy_regional_sector_repi_df.empty:
            basis_options.append("Region x sector (2014=100 legacy)")
        property_basis_label = st.segmented_control(
            "Data basis",
            options=basis_options,
            default=basis_options[0],
            key="real_estate_repi_data_basis",
        )
        use_legacy_region_sector = property_basis_label == "Region x sector (2014=100 legacy)"
        property_source_df = (
            legacy_regional_sector_repi_df.copy()
            if use_legacy_region_sector
            else regional_repi_df.copy()
        )
        property_base_label = "2014=100" if use_legacy_region_sector else "2023=100"
        property_source_name = (
            mod_real_estate.LEGACY_REGIONAL_SECTOR_REPI_SOURCE_NAME
            if use_legacy_region_sector
            else mod_real_estate.REGIONAL_REPI_SOURCE_NAME
        )
        property_source_url = (
            mod_real_estate.KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_PAGE_URL
            if use_legacy_region_sector
            else mod_real_estate.KAPSARC_REGIONAL_REPI_PAGE_URL
        )
        if not use_legacy_region_sector and not legacy_regional_sector_repi_df.empty:
            st.caption(
                "Current regional source is General index only. "
                "Switch Data basis to Region x sector (2014=100 legacy) to change Sector/type."
            )

        property_source_df["period_date"] = pd.to_datetime(
            property_source_df["period_date"],
            errors="coerce",
        )
        property_source_df = property_source_df.dropna(subset=["period_date"])
        regional_min_date = property_source_df["period_date"].min().date()
        regional_max_date = property_source_df["period_date"].max().date()

        preferred_property_regions = [
            "Riyadh",
            "Eastern Province",
            "Makkah",
            "Madinah",
            "Al Qaseem",
            "Aseer",
            "Tabouk",
            "Hail",
            "Northern Borders",
            "Jazan",
            "Najran",
            "Al Baha",
            "Al Jouf",
        ]
        source_regions = set(property_source_df["region"].dropna())
        property_regions = [region for region in preferred_property_regions if region in source_regions]
        property_regions += [
            region for region in sorted(source_regions)
            if region not in property_regions and region != "Saudi Arabia"
        ]
        if not property_regions:
            property_regions = sorted(source_regions)
        default_property_region = "Riyadh" if "Riyadh" in property_regions else property_regions[0]
        property_sectors = sorted(property_source_df["sector_display"].dropna().unique())

        property_toolbar = st.columns([1.35, 1.35, 1.45, 1.0])
        with property_toolbar[0]:
            property_region = st.selectbox(
                "Region",
                options=property_regions,
                index=property_regions.index(default_property_region),
                format_func=_region_display,
                key=f"real_estate_repi_region_{property_base_label}",
            )
        with property_toolbar[1]:
            property_sector = st.selectbox(
                "Sector/type",
                options=property_sectors,
                index=property_sectors.index("General index") if "General index" in property_sectors else 0,
                disabled=len(property_sectors) <= 1,
                key=f"real_estate_repi_sector_{property_base_label}",
            )
        with property_toolbar[2]:
            property_date_range = st.date_input(
                "Date range",
                value=(regional_min_date, regional_max_date),
                min_value=regional_min_date,
                max_value=regional_max_date,
                key=f"real_estate_repi_date_range_{property_base_label}",
            )
        with property_toolbar[3]:
            property_compare = st.toggle(
                "Compare regions",
                value=False,
                key="real_estate_repi_compare_regions_toggle",
            )

        if isinstance(property_date_range, tuple) and len(property_date_range) == 2:
            property_start_date, property_end_date = property_date_range
        else:
            property_start_date, property_end_date = regional_min_date, regional_max_date
        if property_start_date is None or property_end_date is None:
            property_start_date, property_end_date = regional_min_date, regional_max_date
        if property_start_date > property_end_date:
            property_start_date, property_end_date = property_end_date, property_start_date

        if property_compare:
            compare_default = [property_region]
            if property_region != "Eastern Province" and "Eastern Province" in property_regions:
                compare_default.append("Eastern Province")
            property_compare_regions = st.multiselect(
                "Regions to compare",
                options=property_regions,
                default=compare_default,
                format_func=_region_display,
                key="real_estate_repi_compare_regions",
            )
            if not property_compare_regions:
                property_compare_regions = [property_region]
        else:
            property_compare_regions = [property_region]

        property_start_ts = pd.Timestamp(property_start_date)
        property_end_ts = pd.Timestamp(property_end_date)
        property_chart_df = property_source_df[
            (property_source_df["period_date"] >= property_start_ts)
            & (property_source_df["period_date"] <= property_end_ts)
            & (property_source_df["region"].isin(property_compare_regions))
            & (property_source_df["sector_display"] == property_sector)
        ].copy()
        property_chart_df["value"] = pd.to_numeric(property_chart_df["value"], errors="coerce")
        property_chart_df = property_chart_df.dropna(subset=["value"]).sort_values(["region", "period_date"])

        focus_property_df = property_chart_df[property_chart_df["region"] == property_region].sort_values("period_date")
        if not focus_property_df.empty:
            property_latest = focus_property_df.iloc[-1]
            property_first = focus_property_df.iloc[0]
            period_move = (
                (float(property_latest["value"]) - float(property_first["value"]))
                / float(property_first["value"])
                * 100.0
                if float(property_first["value"]) else None
            )
            national_same_period = property_source_df[
                (property_source_df["region"] == "Saudi Arabia")
                & (property_source_df["period_date"] == property_latest["period_date"])
                & (property_source_df["sector_display"] == property_sector)
            ]
            spread_vs_national = None
            if not national_same_period.empty:
                spread_vs_national = float(property_latest["value"]) - float(national_same_period.iloc[-1]["value"])

            prop_cols = st.columns(4)
            with prop_cols[0]:
                _kpi_card(
                    "Regional REPI",
                    _num(property_latest["value"]),
                    delta=_region_display(property_region),
                    delta_color="off",
                    accent="violet",
                    text_value=False,
                )
            with prop_cols[1]:
                _kpi_card(
                    "QoQ",
                    _pct(property_latest["qoq_pct"], ""),
                    delta=property_latest["period_date"].strftime("%Y Q") + str(((property_latest["period_date"].month - 1) // 3) + 1),
                    delta_color="normal",
                    accent="primary",
                    text_value=True,
                )
            with prop_cols[2]:
                _kpi_card(
                    "YoY",
                    _pct(property_latest["yoy_pct"], ""),
                    delta="same quarter last year",
                    delta_color="normal",
                    accent="positive",
                    text_value=True,
                )
            with prop_cols[3]:
                spread_text = "N/A" if spread_vs_national is None else f"{spread_vs_national:+.2f} pts"
                _kpi_card(
                    "Vs Saudi",
                    spread_text,
                    delta=_pct(period_move, "from range start"),
                    delta_color="normal",
                    accent="warn",
                    text_value=True,
                )

        if property_chart_df.empty:
            st.info("No regional property index rows match the selected filters.")
        else:
            regional_custom_cols = [
                "region",
                "sector_display",
                "value",
                "qoq_pct",
                "yoy_pct",
            ]
            regional_hover = (
                "<b>%{x|%b %Y}</b><br>"
                "Region: <b>%{customdata[0]}</b><br>"
                "Sector: %{customdata[1]}<br>"
                "Index: <b>%{customdata[2]:.2f}</b><br>"
                "QoQ: <b>%{customdata[3]:+.2f}%</b><br>"
                "YoY: <b>%{customdata[4]:+.2f}%</b>"
                "<extra></extra>"
            )
            if property_compare:
                fig_property_region = px.line(
                    property_chart_df,
                    x="period_date",
                    y="value",
                    color="region",
                    markers=True,
                    custom_data=regional_custom_cols,
                    color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                )
                fig_property_region.update_traces(
                    line=dict(width=2.4),
                    marker=dict(size=6),
                    hovertemplate=regional_hover,
                )
            else:
                fig_property_region = go.Figure()
                fig_property_region.add_trace(go.Scatter(
                    x=property_chart_df["period_date"],
                    y=property_chart_df["value"],
                    mode="lines+markers",
                    name=_region_display(property_region),
                    customdata=property_chart_df[regional_custom_cols].to_numpy(),
                    line=dict(color=CLR_VIOLET, width=3.0, shape="spline", smoothing=0.75),
                    marker=dict(size=6, color=CLR_VIOLET),
                    hovertemplate=regional_hover,
                ))
                latest_property_point = property_chart_df.sort_values("period_date").iloc[-1]
                fig_property_region.add_trace(go.Scatter(
                    x=[latest_property_point["period_date"]],
                    y=[latest_property_point["value"]],
                    mode="markers",
                    name="Latest",
                    customdata=latest_property_point[regional_custom_cols].to_frame().T.to_numpy(),
                    marker=dict(size=13, color=CLR_TERTIARY, line=dict(color="#0b1022", width=2)),
                    hovertemplate=regional_hover,
                    showlegend=False,
                ))
            property_layout = {**PLOTLY_LAYOUT}
            property_layout["margin"] = dict(l=72, r=28, t=28, b=70)
            fig_property_region.update_layout(
                **property_layout,
                height=420,
                hovermode="x unified",
                legend_title_text="",
                showlegend=property_compare,
            )
            fig_property_region.update_yaxes(title=dict(text=f"Regional REPI ({property_base_label})"))
            fig_property_region.update_xaxes(
                title=dict(text=""),
                rangeslider_visible=True,
                rangeslider_thickness=0.055,
            )
            fig_property_region.add_hline(
                y=100.0,
                line_dash="dot",
                line_color=CLR_TERTIARY,
                line_width=1,
                annotation_text=f"{property_base_label} base",
                annotation_position="top left",
                annotation_font_color=CLR_TERTIARY,
            )
            st.plotly_chart(fig_property_region, width="stretch")
            property_caption_note = (
                "quarterly official general real estate price index by administrative region. "
                "The current regional source does not publish a sector-by-region split, so the regional chart uses General index."
                if not use_legacy_region_sector
                else (
                    "official legacy Excel tables parsed from the regional legacy workbooks; this view supports region x sector/type "
                    "but uses the older 2014=100 base and the available export history."
                )
            )
            st.caption(
                f"Source: [{property_source_name}]"
                f"({property_source_url}) · "
                f"{property_caption_note}"
            )

        latest_property_table = (
            property_chart_df.sort_values("period_date")
                             .groupby("region", as_index=False)
                             .tail(1)
                             .copy()
        )
        if latest_property_table.empty:
            st.info("No latest regional property rows to show for the selected filters.")
        else:
            latest_property_table["Region"] = latest_property_table["region"].map(_region_display)
            latest_property_table["Period"] = (
                latest_property_table["year"].astype(str)
                + " "
                + latest_property_table["quarter"].astype(str)
            )
            latest_property_table["Index"] = latest_property_table["value"].map(lambda value: _num(value, 2))
            latest_property_table["QoQ"] = latest_property_table["qoq_pct"].map(lambda value: _pct(value, ""))
            latest_property_table["YoY"] = latest_property_table["yoy_pct"].map(lambda value: _pct(value, ""))
            latest_property_table["Source"] = "KAPSARC/GASTAT"
            st.dataframe(
                latest_property_table[["Region", "Period", "Index", "QoQ", "YoY", "Source"]],
                width="stretch",
                hide_index=True,
                height=min(280, 40 + 35 * len(latest_property_table)),
            )

    col_left, col_right = st.columns([5, 5])

    with col_left:
        st.markdown("<p class='section-title'>Rent CPI Index Trend</p>", unsafe_allow_html=True)
        if rent_df.empty:
            st.info("No rent CPI rows are available yet.")
        else:
            fig_rent = go.Figure()
            fig_rent.add_trace(go.Scatter(
                x=rent_df["date"],
                y=rent_df["rent_index"],
                mode="lines",
                name="Rent CPI",
                line=dict(color=CLR_TERTIARY, width=2.5, shape="spline", smoothing=1.0),
                hovertemplate="<b>%{x|%b %Y}</b><br>Rent CPI: <b>%{y:.2f}</b><extra></extra>",
            ))
            fig_rent.update_layout(
                **PLOTLY_LAYOUT,
                height=360,
                hovermode="x unified",
                showlegend=False,
            )
            fig_rent.update_yaxes(title=dict(text="Rent CPI Index"))
            fig_rent.update_xaxes(title=dict(text=""))
            st.plotly_chart(fig_rent, width="stretch")
            st.caption(
                "Source: [GASTAT Consumer Price Index]"
                "(https://www.stats.gov.sa/en/w/consumer-price-index-december-2025-1) · "
                "rent CPI category index for actual rentals paid by tenants."
            )

    with col_right:
        st.markdown("<p class='section-title'>Real Estate Price Index Trend</p>", unsafe_allow_html=True)
        if repi_df.empty:
            st.info("No real estate price index rows are available yet.")
        else:
            core_sectors = [
                "General index",
                "Residential total",
                "Commercial total",
                "Agricultural total",
            ]
            chart_df = repi_df[repi_df["sector_display"].isin(core_sectors)]
            if chart_df.empty:
                st.info("No core real estate sector rows are available yet.")
            else:
                fig_repi = px.line(
                    chart_df,
                    x="period_date",
                    y="value",
                    color="sector_display",
                    markers=True,
                    color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                )
                fig_repi.update_traces(
                    line=dict(width=2.3),
                    marker=dict(size=6),
                    hovertemplate=(
                        "<b>%{x|%b %Y}</b><br>"
                        "%{fullData.name}: <b>%{y:.2f}</b><extra></extra>"
                    ),
                )
                fig_repi.update_layout(
                    **PLOTLY_LAYOUT,
                    height=360,
                    hovermode="x unified",
                    legend_title_text="",
                )
                fig_repi.update_yaxes(title=dict(text="Index (2023=100)"))
                fig_repi.update_xaxes(title=dict(text=""))
                st.plotly_chart(fig_repi, width="stretch")
                st.caption(
                    f"Source: [{mod_real_estate.REPI_SOURCE_NAME}]"
                    f"({mod_real_estate.KAPSARC_REPI_PAGE_URL}) · "
                    "quarterly official real estate price index by sector."
                )

    st.markdown("<p class='section-title'>Latest Real Estate Price Index by Sector</p>", unsafe_allow_html=True)
    if repi_df.empty:
        st.info("Real estate sector table will appear once the source is reachable.")
    else:
        latest_sector_df = (
            repi_df.sort_values("period_date")
                   .groupby("sector_display", as_index=False)
                   .tail(1)
                   .sort_values("value", ascending=False)
                   .copy()
        )
        latest_sector_df["Period"] = (
            latest_sector_df["year"].astype(str) + " " + latest_sector_df["quarter"].astype(str)
        )
        latest_sector_df["Index"] = latest_sector_df["value"].map(lambda x: _num(x, 2))
        latest_sector_df["QoQ"] = latest_sector_df["qoq_pct"].map(lambda x: _pct(x, ""))
        latest_sector_df["YoY"] = latest_sector_df["yoy_pct"].map(lambda x: _pct(x, ""))
        latest_sector_df["Source"] = "KAPSARC/GASTAT"
        latest_sector_df = latest_sector_df.rename(columns={"sector_display": "Sector"})
        st.dataframe(
            latest_sector_df[["Sector", "Period", "Index", "QoQ", "YoY", "Source"]],
            width="stretch",
            hide_index=True,
            height=min(460, 40 + 34 * len(latest_sector_df)),
        )

    source_rows = []
    if rent_latest is not None:
        source_rows.append({
            "Indicator": "Rent CPI Index",
            "What It Measures": "Official CPI rent component for actual rentals paid by tenants",
            "Frequency": "Monthly",
            "Latest Period": rent_latest["date"].strftime("%Y-%m"),
            "Source": rent_latest["source_name"],
            "Source Link": "https://www.stats.gov.sa/en/w/consumer-price-index-december-2025-1",
            "App Transformation": "Plotted as the official index level; no regional split.",
        })
    if general_latest is not None:
        source_rows.append({
            "Indicator": "Real Estate Price Index",
            "What It Measures": "Official real estate price index by sector, base 2023=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{general_latest['year']} {general_latest['quarter']}",
            "Source": general_latest["source_name"],
            "Source Link": general_latest["source_url"],
            "App Transformation": "Filtered to core sectors and plotted by sector.",
        })
    if not regional_repi_df.empty:
        regional_source_latest = regional_repi_df.sort_values("period_date").iloc[-1]
        source_rows.append({
            "Indicator": "Regional Real Estate Price Index",
            "What It Measures": "Official general real estate price index by administrative region, base 2023=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{regional_source_latest['year']} {regional_source_latest['quarter']}",
            "Source": regional_source_latest["source_name"],
            "Source Link": regional_source_latest["source_url"],
            "App Transformation": "Filtered by administrative region; current regional source is General index only.",
        })
    if not legacy_regional_sector_repi_df.empty:
        legacy_source_latest = legacy_regional_sector_repi_df.sort_values("period_date").iloc[-1]
        source_rows.append({
            "Indicator": "Legacy Regional-Sector REPI",
            "What It Measures": "Official real estate price index by administrative region and sector/type, base 2014=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{legacy_source_latest['year']} {legacy_source_latest['quarter']}",
            "Source": legacy_source_latest["source_name"],
            "Source Link": legacy_source_latest["source_url"],
            "App Transformation": "Parsed original Region_I Excel tables; use separately from the 2023=100 current series.",
        })
    if not _ejar_latest_df.empty:
        source_rows.append({
            "Indicator": "Regional Ejar Rent Index",
            "What It Measures": "Average annual rent from authenticated residential rental contracts",
            "Frequency": "Monthly",
            "Latest Period": str(_ejar_latest_df["Latest Period"].iloc[0]),
            "Source": mod_real_estate.EJAR_SOURCE_NAME,
            "Source Link": mod_real_estate.EJAR_SOURCE_URL,
            "App Transformation": "Aggregated to regions; Unified Index uses one equal-region baseline.",
        })
    if source_rows:
        with st.expander("Data Source Receipts", expanded=False):
            st.dataframe(pd.DataFrame(source_rows), width="stretch", hide_index=True)
        st.caption(
            "Use this table as the audit trail: it states the public source, what the series measures, "
            "and what transformation the dashboard applies before plotting."
        )
    else:
        st.info("No source metadata is available yet.")


def page_rents():
    _page_header("الإيجارات", "Ejar contract rents and official rent CPI")

    rent_df = mod_real_estate.load_rent_index_history(mod_real_estate.db_fingerprint())
    rent_latest = None if rent_df.empty else rent_df.iloc[-1]

    c1, c2 = st.columns(2)
    with c1:
        _kpi_card(
            "Rent CPI Index",
            _fmt_num(None if rent_latest is None else rent_latest["rent_index"], 2),
            delta=_fmt_pct(None if rent_latest is None else rent_latest["mom_pct"], " MoM"),
            delta_color="inverse",
            accent="warn",
        )
    with c2:
        _kpi_card(
            "Rent YoY",
            _fmt_pct(None if rent_latest is None else rent_latest["yoy_pct"], ""),
            delta=(
                rent_latest["date"].strftime("%Y-%m")
                if rent_latest is not None and hasattr(rent_latest["date"], "strftime")
                else "N/A"
            ),
            delta_color="off",
            accent="primary",
            text_value=True,
        )

    st.markdown("<p class='section-title'>Rental Asking Price Nowcast · Public Listings</p>", unsafe_allow_html=True)
    nowcast_actions = st.columns([1.05, 3.0])
    with nowcast_actions[0]:
        if db.IS_POSTGRES:
            # Cloud is a read-only mirror: live scraping runs on the local
            # pipeline and syncs into Postgres, so don't offer it here.
            st.caption("Updates automatically from the local pipeline.")
        elif st.button("Refresh asking rents", width="stretch", key="rental_nowcast_refresh"):
            with st.spinner("Refreshing public rental listing pages..."):
                summary = mod_real_estate.refresh_rental_listing_nowcast()
            mod_real_estate.load_rental_listing_nowcast.clear()
            if summary.get("status") == "failed":
                st.warning(
                    "Rental nowcast refresh failed: "
                    + " | ".join(summary.get("errors") or ["no rows returned"])
                )
            else:
                st.success(
                    "Rental nowcast refreshed: "
                    f"{summary.get('usable_rows', 0):,} usable listings from "
                    f"{summary.get('cities_ok', 0)}/{summary.get('cities_requested', 0)} cities."
                )

    nowcast_series_df, nowcast_latest_df, nowcast_source_mix, nowcast_error = (
        mod_real_estate.load_rental_listing_nowcast(mod_real_estate.db_fingerprint())
    )
    if nowcast_error:
        st.warning(nowcast_error)

    if nowcast_series_df.empty:
        st.info(
            "No asking-rent nowcast snapshot yet. Use Refresh asking rents or run "
            "`python rental_nowcast.py --refresh`."
        )
    else:
        nowcast_series_df = nowcast_series_df.copy()
        nowcast_series_df["observed_date"] = pd.to_datetime(
            nowcast_series_df["observed_date"],
            errors="coerce",
        )
        nowcast_series_df = nowcast_series_df.dropna(subset=["observed_date"])

        preferred_regions = ["الوسطى", "الشرقية", "الغربية", "الجنوب", "الشمال"]
        nowcast_regions = [
            region for region in preferred_regions
            if region in set(nowcast_series_df["region"].dropna())
        ]
        nowcast_regions += [
            region for region in sorted(nowcast_series_df["region"].dropna().unique())
            if region not in nowcast_regions
        ]

        nowcast_toolbar = st.columns([1.2, 1.0, 1.8])
        default_nowcast_region = "الوسطى" if "الوسطى" in nowcast_regions else nowcast_regions[0]
        with nowcast_toolbar[0]:
            nowcast_region = st.selectbox(
                "Nowcast region",
                options=nowcast_regions,
                index=nowcast_regions.index(default_nowcast_region),
                key="rental_nowcast_region",
            )
        with nowcast_toolbar[1]:
            nowcast_compare = st.toggle("Compare regions", value=True, key="rental_nowcast_compare")
        if nowcast_compare:
            with nowcast_toolbar[2]:
                nowcast_selected_regions = st.multiselect(
                    "Nowcast regions to compare",
                    options=nowcast_regions,
                    default=nowcast_regions,
                    key="rental_nowcast_regions",
                )
            if not nowcast_selected_regions:
                nowcast_selected_regions = [nowcast_region]
        else:
            nowcast_selected_regions = [nowcast_region]

        nowcast_chart_df = nowcast_series_df[
            nowcast_series_df["region"].isin(nowcast_selected_regions)
        ].copy()
        nowcast_chart_df["asking_index_common"] = pd.to_numeric(
            nowcast_chart_df["asking_index_common"],
            errors="coerce",
        )
        nowcast_chart_df["median_annual_rent"] = pd.to_numeric(
            nowcast_chart_df["median_annual_rent"],
            errors="coerce",
        )
        nowcast_chart_df = nowcast_chart_df.dropna(subset=["median_annual_rent"])

        focus_nowcast = nowcast_series_df[nowcast_series_df["region"] == nowcast_region].sort_values("observed_date")
        if not focus_nowcast.empty:
            latest_nowcast = focus_nowcast.iloc[-1]
            first_nowcast = focus_nowcast.iloc[0]
            latest_median = float(latest_nowcast["median_annual_rent"])
            first_median = float(first_nowcast["median_annual_rent"])
            period_change = ((latest_median - first_median) / first_median * 100.0) if first_median else None
            n1, n2, n3, n4 = st.columns(4)
            with n1:
                _kpi_card(
                    "Median Asking Rent",
                    f"{latest_median:,.0f} SAR",
                    delta=nowcast_region,
                    delta_color="off",
                    accent="primary",
                    text_value=True,
                )
            with n2:
                _kpi_card(
                    "Asking Index",
                    _fmt_num(latest_nowcast["asking_index_common"], 2),
                    delta="common listing baseline",
                    delta_color="off",
                    accent="violet",
                )
            with n3:
                _kpi_card(
                    "Listings",
                    f"{float(latest_nowcast['listing_count']):,.0f}",
                    delta=f"{int(latest_nowcast['cities_observed'])}/{int(latest_nowcast['cities_expected'])} cities",
                    delta_color="off",
                    accent="positive",
                )
            with n4:
                _kpi_card(
                    "Period Move",
                    _fmt_pct(period_change, ""),
                    delta=f"from {first_nowcast['observed_date']:%Y-%m-%d}",
                    delta_color="normal",
                    accent="warn",
                )

        if nowcast_chart_df.empty:
            st.info("No asking-rent nowcast rows match the selected regions.")
        else:
            custom_cols = [
                "region",
                "median_annual_rent",
                "listing_count",
                "cities_observed",
                "cities_expected",
            ]
            hover = (
                "<b>%{x|%Y-%m-%d}</b><br>"
                "Region: <b>%{customdata[0]}</b><br>"
                "Asking index: <b>%{y:.2f}</b><br>"
                "Median asking rent: <b>%{customdata[1]:,.0f} SAR</b><br>"
                "Listings: <b>%{customdata[2]:,.0f}</b><br>"
                "Cities: %{customdata[3]:.0f}/%{customdata[4]:.0f}"
                "<extra></extra>"
            )
            plot_nowcast = nowcast_chart_df.dropna(subset=["asking_index_common"]).copy()
            if plot_nowcast.empty:
                st.info("Asking-rent index baseline is not available yet, but latest medians are shown below.")
            else:
                if nowcast_compare:
                    fig_nowcast = px.line(
                        plot_nowcast,
                        x="observed_date",
                        y="asking_index_common",
                        color="region",
                        markers=True,
                        custom_data=custom_cols,
                        color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                    )
                    fig_nowcast.update_traces(line=dict(width=2.4), marker=dict(size=7), hovertemplate=hover)
                else:
                    fig_nowcast = go.Figure()
                    fig_nowcast.add_trace(go.Scatter(
                        x=plot_nowcast["observed_date"],
                        y=plot_nowcast["asking_index_common"],
                        mode="lines+markers",
                        name=nowcast_region,
                        customdata=plot_nowcast[custom_cols].to_numpy(),
                        line=dict(color=CLR_SECONDARY, width=3.0, shape="spline", smoothing=0.75),
                        marker=dict(size=7, color=CLR_SECONDARY),
                        hovertemplate=hover,
                    ))
                    latest_point = plot_nowcast.sort_values("observed_date").iloc[-1]
                    fig_nowcast.add_trace(go.Scatter(
                        x=[latest_point["observed_date"]],
                        y=[latest_point["asking_index_common"]],
                        mode="markers",
                        customdata=latest_point[custom_cols].to_frame().T.to_numpy(),
                        marker=dict(size=13, color=CLR_TERTIARY, line=dict(color="#0b1022", width=2)),
                        hovertemplate=hover,
                        showlegend=False,
                    ))
                nowcast_layout = {**PLOTLY_LAYOUT}
                nowcast_layout["margin"] = dict(l=78, r=28, t=30, b=58)
                fig_nowcast.update_layout(
                    **nowcast_layout,
                    height=340,
                    hovermode="x unified",
                    legend_title_text="",
                    showlegend=nowcast_compare,
                )
                fig_nowcast.update_yaxes(title=dict(text="Asking Rent Index (first common snapshot = 100)"))
                fig_nowcast.update_xaxes(title=dict(text=""))
                if plot_nowcast["observed_date"].nunique() == 1:
                    only_date = pd.Timestamp(plot_nowcast["observed_date"].iloc[0])
                    fig_nowcast.update_xaxes(
                        range=[
                            only_date - pd.Timedelta(days=3),
                            only_date + pd.Timedelta(days=3),
                        ],
                        dtick=24 * 60 * 60 * 1000,
                        tickformat="%b %d",
                    )
                fig_nowcast.add_hline(
                    y=100.0,
                    line_dash="dot",
                    line_color=CLR_TERTIARY,
                    line_width=1,
                    annotation_text="Baseline = 100",
                    annotation_position="top left",
                    annotation_font_color=CLR_TERTIARY,
                )
                st.plotly_chart(fig_nowcast, width="stretch")
            st.caption(
                f"Source: [{mod_real_estate.RENTAL_NOWCAST_SOURCE_NAME}]"
                f"({mod_real_estate.RENTAL_NOWCAST_SOURCE_URL}) · asking rents from public listings; "
                "this is a faster market nowcast, not authenticated Ejar contract rent."
            )

        if not nowcast_latest_df.empty:
            display_nowcast_latest = nowcast_latest_df.copy()
            display_nowcast_latest["Asking Index"] = display_nowcast_latest["Asking Index"].map(
                lambda value: "N/A" if pd.isna(value) else f"{float(value):.2f}"
            )
            display_nowcast_latest["Median Annual Asking Rent (SAR)"] = display_nowcast_latest[
                "Median Annual Asking Rent (SAR)"
            ].map(lambda value: f"{float(value):,.0f}")
            display_nowcast_latest["Listings"] = display_nowcast_latest["Listings"].map(
                lambda value: f"{float(value):,.0f}"
            )
            display_nowcast_latest["MoM"] = display_nowcast_latest["MoM"].map(
                lambda value: "N/A" if pd.isna(value) else f"{float(value):+.2f}%"
            )
            with st.expander("Latest asking rent table", expanded=False):
                st.dataframe(display_nowcast_latest, width="stretch", hide_index=True, height=235)

    st.markdown("<p class='section-title'>Regional Rent Index · Ejar Contracts</p>", unsafe_allow_html=True)
    ejar_history_options = {
        "1Y": 1,
        "3Y": 3,
        "5Y": 5,
        "All": mod_real_estate.MAX_EJAR_HISTORY_YEARS,
    }
    rent_toolbar = st.columns([1.0, 1.7, 1.35, 1.1])
    with rent_toolbar[0]:
        ejar_history_label = st.segmented_control(
            "Range",
            options=list(ejar_history_options),
            default="5Y",
            key="rents_page_range",
        )
    ejar_series_df, _ejar_latest_df, ejar_error = mod_real_estate.load_ejar_regional_rent_index(
        ejar_history_options.get(ejar_history_label or "5Y", 5)
    )
    if ejar_error:
        st.warning(f"Ejar regional rent source warning: {ejar_error}")

    if ejar_series_df.empty:
        st.info("Regional Ejar rent data is not available yet.")
    else:
        ejar_series_df = ejar_series_df.copy()
        ejar_series_df["date"] = pd.to_datetime(ejar_series_df["date"], errors="coerce")
        ejar_series_df = ejar_series_df.dropna(subset=["date"])
        loaded_min_date = ejar_series_df["date"].min().date()
        loaded_max_date = ejar_series_df["date"].max().date()

        with rent_toolbar[1]:
            ejar_date_range = st.date_input(
                "Date range",
                value=(loaded_min_date, loaded_max_date),
                min_value=loaded_min_date,
                max_value=loaded_max_date,
                key=f"rents_page_date_range_{ejar_history_label or '5Y'}",
            )
        with rent_toolbar[2]:
            rent_view_mode = st.segmented_control(
                "Rent view",
                options=["Unified Index", "Local Index", "Annual Rent SAR"],
                default="Unified Index",
                key="rents_page_view",
            )
        with rent_toolbar[3]:
            compare_regions = st.toggle("Compare", value=False, key="rents_page_compare")

        preferred_regions = ["الوسطى", "الشرقية", "الغربية", "الجنوب", "الشمال"]
        available_regions = [
            region for region in preferred_regions
            if region in set(ejar_series_df["region"].dropna())
        ]
        available_regions += [
            region for region in sorted(ejar_series_df["region"].dropna().unique())
            if region not in available_regions
        ]
        default_region = "الوسطى" if "الوسطى" in available_regions else available_regions[0]

        region_cols = st.columns([1.2, 2.8])
        with region_cols[0]:
            selected_region = st.selectbox(
                "Region",
                options=available_regions,
                index=available_regions.index(default_region),
                key="rents_page_region",
            )
        if compare_regions:
            with region_cols[1]:
                selected_regions = st.multiselect(
                    "Regions to compare",
                    options=available_regions,
                    default=[selected_region],
                    key="rents_page_compare_regions",
                )
            if not selected_regions:
                selected_regions = [selected_region]
        else:
            selected_regions = [selected_region]

        if isinstance(ejar_date_range, tuple) and len(ejar_date_range) == 2:
            selected_start_date, selected_end_date = ejar_date_range
        else:
            selected_start_date, selected_end_date = loaded_min_date, loaded_max_date
        if selected_start_date is None or selected_end_date is None:
            selected_start_date, selected_end_date = loaded_min_date, loaded_max_date
        if selected_start_date > selected_end_date:
            selected_start_date, selected_end_date = selected_end_date, selected_start_date

        if rent_view_mode == "Annual Rent SAR":
            y_field = "avg_annual_rent"
            y_title = "Avg Annual Rent (SAR)"
        elif rent_view_mode == "Local Index":
            y_field = "rent_index_visible_local"
            y_title = "Local Index (selected period start = 100)"
        else:
            y_field = "rent_index_common"
            y_title = "Unified Index (equal-region baseline = 100)"
        start_ts = pd.Timestamp(selected_start_date)
        end_ts = pd.Timestamp(selected_end_date)
        chart_df = ejar_series_df[
            (ejar_series_df["date"] >= start_ts)
            & (ejar_series_df["date"] <= end_ts)
            & (ejar_series_df["region"].isin(selected_regions))
        ].copy()
        chart_df = _add_visible_local_rent_index(chart_df)
        chart_df[y_field] = pd.to_numeric(chart_df[y_field], errors="coerce")
        chart_df = chart_df.dropna(subset=[y_field]).sort_values(["region", "date"])

        focus_df = chart_df[chart_df["region"] == selected_region].sort_values("date")
        if not focus_df.empty:
            latest_focus = focus_df.iloc[-1]
            first_focus = focus_df.iloc[0]
            latest_metric = float(latest_focus[y_field])
            first_metric = float(first_focus[y_field])
            period_change = ((latest_metric - first_metric) / first_metric * 100.0) if first_metric else None
            metric_cols = st.columns(4)
            with metric_cols[0]:
                _kpi_card(
                    "Latest",
                    f"{latest_metric:,.0f} SAR" if y_field == "avg_annual_rent" else f"{latest_metric:.2f}",
                    delta=selected_region,
                    delta_color="off",
                    accent="primary",
                    text_value=y_field == "avg_annual_rent",
                )
            with metric_cols[1]:
                _kpi_card("MoM", _fmt_pct(latest_focus["mom_pct"], ""), delta=latest_focus["date"].strftime("%Y-%m"), delta_color="inverse", accent="warn")
            with metric_cols[2]:
                _kpi_card("Period Change", _fmt_pct(period_change, ""), delta=f"from {first_focus['date']:%Y-%m}", delta_color="inverse", accent="violet")
            with metric_cols[3]:
                _kpi_card("Contracts", f"{float(latest_focus['contracts']):,.0f}", delta="latest period", delta_color="off", accent="positive")

        if chart_df.empty:
            st.info("No regional rent rows match the selected filters.")
        else:
            custom_cols = [
                "region",
                "rent_index_common",
                "rent_index_visible_local",
                "avg_annual_rent",
                "contracts",
                "cities_observed",
                "cities_expected",
            ]
            hover_template = (
                "<b>%{x|%b %Y}</b><br>"
                "Region: <b>%{customdata[0]}</b><br>"
                "Unified index: <b>%{customdata[1]:.2f}</b><br>"
                "Local index: <b>%{customdata[2]:.2f}</b><br>"
                "Avg annual rent: <b>%{customdata[3]:,.0f} SAR</b><br>"
                "Contracts: <b>%{customdata[4]:,.0f}</b><br>"
                "Coverage: %{customdata[5]:.0f}/%{customdata[6]:.0f} cities"
                "<extra></extra>"
            )
            if compare_regions:
                fig_ejar = px.line(
                    chart_df,
                    x="date",
                    y=y_field,
                    color="region",
                    markers=True,
                    custom_data=custom_cols,
                    color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                )
                fig_ejar.update_traces(line=dict(width=2.4), marker=dict(size=5), hovertemplate=hover_template)
            else:
                fig_ejar = go.Figure()
                fig_ejar.add_trace(go.Scatter(
                    x=chart_df["date"],
                    y=chart_df[y_field],
                    mode="lines+markers",
                    name=selected_region,
                    customdata=chart_df[custom_cols].to_numpy(),
                    line=dict(color=CLR_PRIMARY, width=3.0, shape="spline", smoothing=0.8),
                    marker=dict(size=5, color=CLR_PRIMARY),
                    hovertemplate=hover_template,
                ))
                latest_point = chart_df.sort_values("date").iloc[-1]
                fig_ejar.add_trace(go.Scatter(
                    x=[latest_point["date"]],
                    y=[latest_point[y_field]],
                    mode="markers",
                    customdata=latest_point[custom_cols].to_frame().T.to_numpy(),
                    marker=dict(size=13, color=CLR_TERTIARY, line=dict(color="#0b1022", width=2)),
                    hovertemplate=hover_template,
                    showlegend=False,
                ))
            ejar_layout = {**PLOTLY_LAYOUT}
            ejar_layout["margin"] = dict(l=82, r=28, t=36, b=72)
            fig_ejar.update_layout(**ejar_layout, height=430, hovermode="x unified", legend_title_text="", showlegend=compare_regions)
            fig_ejar.update_yaxes(title=dict(text=y_title), tickformat=",.0f" if y_field == "avg_annual_rent" else ".1f")
            fig_ejar.update_xaxes(title=dict(text=""), rangeslider_visible=True, rangeslider_thickness=0.055)
            if y_field in {"rent_index_common", "rent_index_visible_local"}:
                fig_ejar.add_hline(
                    y=100.0,
                    line_dash="dot",
                    line_color=CLR_TERTIARY,
                    line_width=1,
                    annotation_text="Local baseline = 100" if y_field == "rent_index_visible_local" else "Common baseline = 100",
                    annotation_position="top left",
                    annotation_font_color=CLR_TERTIARY,
                )
            st.plotly_chart(fig_ejar, width="stretch")
            st.caption(
                f"Source: [{mod_real_estate.EJAR_SOURCE_NAME}]"
                f"({mod_real_estate.EJAR_SOURCE_URL}) · authenticated residential rental contracts aggregated by city and region. "
                "Unified Index preserves regional level differences; Local Index rebases each visible region to 100 at the selected period start."
            )

        latest_rent_rows = (
            chart_df.sort_values("date")
                    .groupby("region", as_index=False)
                    .tail(1)
                    .copy()
        )
        if not latest_rent_rows.empty:
            latest_rent_rows["Latest Period"] = latest_rent_rows["date"].dt.strftime("%Y-%m")
            latest_rent_rows["Unified Index"] = latest_rent_rows["rent_index_common"].map(lambda value: "N/A" if pd.isna(value) else f"{float(value):.2f}")
            latest_rent_rows["Local Index"] = latest_rent_rows["rent_index_visible_local"].map(lambda value: "N/A" if pd.isna(value) else f"{float(value):.2f}")
            latest_rent_rows["Avg Annual Rent (SAR)"] = latest_rent_rows["avg_annual_rent"].map(lambda value: f"{float(value):,.0f}")
            latest_rent_rows["Contracts"] = latest_rent_rows["contracts"].map(lambda value: f"{float(value):,.0f}")
            latest_rent_rows["MoM"] = latest_rent_rows["mom_pct"].map(lambda value: "N/A" if pd.isna(value) else f"{float(value):+.2f}%")
            latest_rent_rows = latest_rent_rows.rename(columns={"region": "Region"})
            st.dataframe(
                latest_rent_rows[["Region", "Latest Period", "Unified Index", "Local Index", "Avg Annual Rent (SAR)", "Contracts", "MoM"]],
                width="stretch",
                hide_index=True,
                height=min(260, 40 + 35 * len(latest_rent_rows)),
            )

    st.markdown("<p class='section-title'>Rent CPI Index Trend</p>", unsafe_allow_html=True)
    if rent_df.empty:
        st.info("No rent CPI rows are available yet.")
    else:
        fig_rent = go.Figure()
        fig_rent.add_trace(go.Scatter(
            x=rent_df["date"],
            y=rent_df["rent_index"],
            mode="lines",
            name="Rent CPI",
            line=dict(color=CLR_TERTIARY, width=2.5, shape="spline", smoothing=1.0),
            hovertemplate="<b>%{x|%b %Y}</b><br>Rent CPI: <b>%{y:.2f}</b><extra></extra>",
        ))
        fig_rent.update_layout(**PLOTLY_LAYOUT, height=360, hovermode="x unified", showlegend=False)
        fig_rent.update_yaxes(title=dict(text="Rent CPI Index"))
        fig_rent.update_xaxes(title=dict(text=""))
        st.plotly_chart(fig_rent, width="stretch")
        st.caption(
            "Source: [GASTAT Consumer Price Index]"
            "(https://www.stats.gov.sa/en/w/consumer-price-index-december-2025-1) · rent CPI category index for actual rentals paid by tenants."
        )

    source_rows = []
    if rent_latest is not None:
        source_rows.append({
            "Indicator": "Rent CPI Index",
            "What It Measures": "Official CPI rent component for actual rentals paid by tenants",
            "Frequency": "Monthly",
            "Latest Period": rent_latest["date"].strftime("%Y-%m"),
            "Source": rent_latest["source_name"],
            "Source Link": "https://www.stats.gov.sa/en/w/consumer-price-index-december-2025-1",
            "App Transformation": "Plotted as the official index level; no regional split.",
        })
    if "_ejar_latest_df" in locals() and not _ejar_latest_df.empty:
        source_rows.append({
            "Indicator": "Regional Ejar Rent Index",
            "What It Measures": "Average annual rent from authenticated residential rental contracts",
            "Frequency": "Monthly",
            "Latest Period": str(_ejar_latest_df["Latest Period"].iloc[0]),
            "Source": mod_real_estate.EJAR_SOURCE_NAME,
            "Source Link": mod_real_estate.EJAR_SOURCE_URL,
            "App Transformation": "Aggregated to regions; Unified Index uses one equal-region baseline.",
        })
    if "nowcast_latest_df" in locals() and not nowcast_latest_df.empty:
        source_rows.append({
            "Indicator": "Rental Asking Price Nowcast",
            "What It Measures": "Median annual asking rent from public apartment rental listings",
            "Frequency": "Daily snapshot when refresh runs",
            "Latest Period": str(nowcast_latest_df["Latest Snapshot"].iloc[0]),
            "Source": mod_real_estate.RENTAL_NOWCAST_SOURCE_NAME,
            "Source Link": mod_real_estate.RENTAL_NOWCAST_SOURCE_URL,
            "App Transformation": "Filtered to usable annual asking rents; city medians are aggregated to regional medians, then indexed from the first common snapshot.",
        })
    if source_rows:
        with st.expander("Data Source Receipts", expanded=False):
            st.dataframe(pd.DataFrame(source_rows), width="stretch", hide_index=True)


def page_property():
    _page_header("العقار", "Official real estate price indices by region, sector, and type")

    repi_df, repi_error = mod_real_estate.load_real_estate_price_index_history()
    regional_repi_df, regional_repi_error = mod_real_estate.load_regional_real_estate_price_index_history()
    legacy_repi_df, legacy_error = mod_real_estate.load_legacy_region_sector_real_estate_price_index_history()
    spliced_repi_df, spliced_error = mod_real_estate.load_spliced_regional_real_estate_price_index_history()

    def _latest_sector(sector_display: str):
        if repi_df.empty:
            return None
        rows = repi_df[repi_df["sector_display"] == sector_display].sort_values("period_date")
        return None if rows.empty else rows.iloc[-1]

    general_latest = _latest_sector("General index")
    residential_latest = _latest_sector("Residential total")
    commercial_latest = _latest_sector("Commercial total")

    c1, c2, c3 = st.columns(3)
    with c1:
        _kpi_card("Real Estate General", _fmt_num(None if general_latest is None else general_latest["value"], 2), delta=_fmt_pct(None if general_latest is None else general_latest["qoq_pct"], " QoQ"), accent="violet")
    with c2:
        _kpi_card("Residential REPI", _fmt_num(None if residential_latest is None else residential_latest["value"], 2), delta=_fmt_pct(None if residential_latest is None else residential_latest["qoq_pct"], " QoQ"), accent="positive")
    with c3:
        _kpi_card("Commercial REPI", _fmt_num(None if commercial_latest is None else commercial_latest["value"], 2), delta=_fmt_pct(None if commercial_latest is None else commercial_latest["qoq_pct"], " QoQ"), accent="warn")

    if repi_error:
        st.warning(f"Real estate price source unavailable: {repi_error}")
    if regional_repi_error:
        st.warning(f"Regional real estate price source unavailable: {regional_repi_error}")
    if legacy_error:
        st.warning(f"Legacy regional-sector price source warning: {legacy_error}")
    if spliced_error:
        st.warning(f"Continuous regional price source warning: {spliced_error}")

    st.markdown("<p class='section-title'>Regional Property Price Index · Official REPI</p>", unsafe_allow_html=True)
    if regional_repi_df.empty and legacy_repi_df.empty and spliced_repi_df.empty:
        st.info("Regional real estate price index rows are not available yet.")
    else:
        region_labels_ar = {
            "Riyadh": "الرياض",
            "Eastern Province": "الشرقية",
            "Makkah": "مكة",
            "Madinah": "المدينة",
            "Al Qaseem": "القصيم",
            "Aseer": "عسير",
            "Tabouk": "تبوك",
            "Hail": "حائل",
            "Northern Borders": "الحدود الشمالية",
            "Jazan": "جازان",
            "Najran": "نجران",
            "Al Baha": "الباحة",
            "Al Jouf": "الجوف",
            "Saudi Arabia": "السعودية",
        }

        def _region_display(region: str) -> str:
            label = region_labels_ar.get(str(region))
            return f"{label} · {region}" if label else str(region)

        basis_options = []
        if not spliced_repi_df.empty:
            basis_options.append("Continuous regions (2019-current)")
        if not regional_repi_df.empty:
            basis_options.append("Current regions (2023=100)")
        if not legacy_repi_df.empty:
            basis_options.append("Region x sector (2014=100 legacy)")
        basis_label = st.segmented_control(
            "Data basis",
            options=basis_options,
            default=basis_options[0],
            key="property_page_basis_v2",
        )
        use_spliced = basis_label == "Continuous regions (2019-current)"
        use_legacy = basis_label == "Region x sector (2014=100 legacy)"
        if use_spliced:
            source_df = spliced_repi_df.copy()
            base_label = "linked 2014-scale"
            source_name = mod_real_estate.SPLICED_REGIONAL_REPI_SOURCE_NAME
            source_url = mod_real_estate.SPLICED_REGIONAL_REPI_SOURCE_URL
        elif use_legacy:
            source_df = legacy_repi_df.copy()
            base_label = "2014=100"
            source_name = mod_real_estate.LEGACY_REGIONAL_SECTOR_REPI_SOURCE_NAME
            source_url = mod_real_estate.KAPSARC_LEGACY_REGIONAL_SECTOR_REPI_PAGE_URL
        else:
            source_df = regional_repi_df.copy()
            base_label = "2023=100"
            source_name = mod_real_estate.REGIONAL_REPI_SOURCE_NAME
            source_url = mod_real_estate.KAPSARC_REGIONAL_REPI_PAGE_URL

        if use_spliced:
            st.caption(
                "Continuous view links the legacy 2014-base regional series with the current 2023-base regional series on their overlap; "
                "it is available for General index only."
            )
        elif not use_legacy and not legacy_repi_df.empty:
            st.caption("Current regional source is General index only. Switch to Region x sector (2014=100 legacy) to change Sector/type.")

        scale_options = ["Range start = 100", "Linked level"] if use_spliced else ["Range start = 100", "Official base"]
        scale_mode = st.segmented_control(
            "Index scale",
            options=scale_options,
            default="Range start = 100",
            key=f"property_page_scale_{base_label}",
        )

        source_df["period_date"] = pd.to_datetime(source_df["period_date"], errors="coerce")
        source_df = source_df.dropna(subset=["period_date"])
        min_date = source_df["period_date"].min().date()
        max_date = source_df["period_date"].max().date()

        preferred_regions = ["Riyadh", "Eastern Province", "Makkah", "Madinah", "Al Qaseem", "Aseer", "Tabouk", "Hail", "Northern Borders", "Jazan", "Najran", "Al Baha", "Al Jouf"]
        source_regions = set(source_df["region"].dropna())
        property_regions = [region for region in preferred_regions if region in source_regions]
        property_regions += [region for region in sorted(source_regions) if region not in property_regions and region != "Saudi Arabia"]
        if not property_regions:
            property_regions = sorted(source_regions)
        default_region = "Riyadh" if "Riyadh" in property_regions else property_regions[0]
        sectors = sorted(source_df["sector_display"].dropna().unique())

        toolbar = st.columns([1.3, 1.4, 1.6, 1.0])
        with toolbar[0]:
            selected_region = st.selectbox(
                "Region",
                options=property_regions,
                index=property_regions.index(default_region),
                format_func=_region_display,
                key=f"property_page_region_{base_label}",
            )
        with toolbar[1]:
            selected_sector = st.selectbox(
                "Sector/type",
                options=sectors,
                index=sectors.index("General index") if "General index" in sectors else 0,
                disabled=len(sectors) <= 1,
                key=f"property_page_sector_{base_label}",
            )
        with toolbar[2]:
            date_range = st.date_input(
                "Date range",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key=f"property_page_date_range_{base_label}",
            )
        with toolbar[3]:
            compare_regions = st.toggle("Compare", value=False, key="property_page_compare")

        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = min_date, max_date
        if start_date is None or end_date is None:
            start_date, end_date = min_date, max_date
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        if compare_regions:
            compare_default = [selected_region]
            if selected_region != "Eastern Province" and "Eastern Province" in property_regions:
                compare_default.append("Eastern Province")
            selected_regions = st.multiselect(
                "Regions to compare",
                options=property_regions,
                default=compare_default,
                format_func=_region_display,
                key="property_page_compare_regions",
            )
            if not selected_regions:
                selected_regions = [selected_region]
        else:
            selected_regions = [selected_region]

        chart_df = source_df[
            (source_df["period_date"] >= pd.Timestamp(start_date))
            & (source_df["period_date"] <= pd.Timestamp(end_date))
            & (source_df["region"].isin(selected_regions))
            & (source_df["sector_display"] == selected_sector)
        ].copy()
        chart_df["value"] = pd.to_numeric(chart_df["value"], errors="coerce")
        chart_df = chart_df.dropna(subset=["value"]).sort_values(["region", "period_date"])
        chart_df["official_value"] = chart_df["value"]
        display_value_field = "value"
        display_axis_title = f"Regional REPI ({base_label})"
        baseline_annotation = f"{base_label} base"
        source_value_label = f"Official index ({base_label})"
        scale_caption = "Displayed values use the official source base."
        if use_spliced:
            display_axis_title = "Regional REPI (linked continuous level)"
            baseline_annotation = "Linked 2014-scale reference"
            source_value_label = "Linked index"
            scale_caption = (
                "Displayed values link the legacy 2014-base series to the current 2023-base series using their overlapping quarters; "
                "this preserves one continuous regional line from 2019 to the latest official quarter."
            )
        if scale_mode == "Range start = 100":
            chart_df["range_index"] = chart_df.groupby("region")["official_value"].transform(
                lambda values: values / values.iloc[0] * 100.0
                if len(values) and values.iloc[0] else pd.NA
            )
            chart_df = chart_df.dropna(subset=["range_index"])
            display_value_field = "range_index"
            display_axis_title = "Index (range start = 100)"
            baseline_annotation = "Range start = 100"
            scale_caption = (
                "Displayed values are rebased so the first visible point for each selected region equals 100; "
                "official source values remain in the hover and table."
            )
            if use_spliced:
                scale_caption = (
                    "Displayed values are rebased to the first visible point, while the underlying regional line remains the "
                    "linked 2019-to-latest continuous series."
                )

        focus_df = chart_df[chart_df["region"] == selected_region].sort_values("period_date")
        if not focus_df.empty:
            latest = focus_df.iloc[-1]
            first = focus_df.iloc[0]
            period_move = ((float(latest["value"]) - float(first["value"])) / float(first["value"]) * 100.0) if float(first["value"]) else None
            national_range = source_df[
                (source_df["region"] == "Saudi Arabia")
                & (source_df["period_date"] >= pd.Timestamp(start_date))
                & (source_df["period_date"] <= pd.Timestamp(end_date))
                & (source_df["sector_display"] == selected_sector)
            ].copy()
            national_range["value"] = pd.to_numeric(national_range["value"], errors="coerce")
            national_range = national_range.dropna(subset=["value"]).sort_values("period_date")
            spread = None
            if not national_range.empty:
                if scale_mode == "Range start = 100":
                    national_first = float(national_range["value"].iloc[0])
                    national_latest = float(national_range["value"].iloc[-1])
                    if national_first:
                        spread = float(latest[display_value_field]) - (national_latest / national_first * 100.0)
                else:
                    same_period = national_range[national_range["period_date"] == latest["period_date"]]
                    if not same_period.empty:
                        spread = float(latest["value"]) - float(same_period.iloc[-1]["value"])
            prop_cols = st.columns(4)
            with prop_cols[0]:
                _kpi_card(
                    "Range Index" if scale_mode == "Range start = 100" else "Regional REPI",
                    _fmt_num(latest[display_value_field], 2),
                    delta=_region_display(selected_region),
                    delta_color="off",
                    accent="violet",
                )
            with prop_cols[1]:
                _kpi_card("QoQ", _fmt_pct(latest["qoq_pct"], ""), delta=latest["period_date"].strftime("%Y Q") + str(((latest["period_date"].month - 1) // 3) + 1), accent="primary", text_value=True)
            with prop_cols[2]:
                _kpi_card("YoY", _fmt_pct(latest["yoy_pct"], ""), delta="same quarter last year", accent="positive", text_value=True)
            with prop_cols[3]:
                _kpi_card("Vs Saudi", "N/A" if spread is None else f"{spread:+.2f} pts", delta=_fmt_pct(period_move, " from range start"), accent="warn", text_value=True)

        if chart_df.empty:
            st.info("No regional property index rows match the selected filters.")
        else:
            custom_cols = ["region", "sector_display", display_value_field, "official_value", "qoq_pct", "yoy_pct"]
            hover = (
                "<b>%{x|%b %Y}</b><br>"
                "Region: <b>%{customdata[0]}</b><br>"
                "Sector: %{customdata[1]}<br>"
                "Displayed index: <b>%{customdata[2]:.2f}</b><br>"
                f"{source_value_label}: <b>%{{customdata[3]:.2f}}</b><br>"
                "QoQ: <b>%{customdata[4]:+.2f}%</b><br>"
                "YoY: <b>%{customdata[5]:+.2f}%</b>"
                "<extra></extra>"
            )
            if compare_regions:
                fig_region = px.line(
                    chart_df,
                    x="period_date",
                    y=display_value_field,
                    color="region",
                    markers=True,
                    custom_data=custom_cols,
                    color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
                )
                fig_region.update_traces(line=dict(width=2.4), marker=dict(size=6), hovertemplate=hover)
            else:
                fig_region = go.Figure()
                fig_region.add_trace(go.Scatter(
                    x=chart_df["period_date"],
                    y=chart_df[display_value_field],
                    mode="lines+markers",
                    name=_region_display(selected_region),
                    customdata=chart_df[custom_cols].to_numpy(),
                    line=dict(color=CLR_VIOLET, width=3.0, shape="spline", smoothing=0.75),
                    marker=dict(size=6, color=CLR_VIOLET),
                    hovertemplate=hover,
                ))
                latest_point = chart_df.sort_values("period_date").iloc[-1]
                fig_region.add_trace(go.Scatter(
                    x=[latest_point["period_date"]],
                    y=[latest_point[display_value_field]],
                    mode="markers",
                    customdata=latest_point[custom_cols].to_frame().T.to_numpy(),
                    marker=dict(size=13, color=CLR_TERTIARY, line=dict(color="#0b1022", width=2)),
                    hovertemplate=hover,
                    showlegend=False,
                ))
            region_layout = {**PLOTLY_LAYOUT}
            region_layout["margin"] = dict(l=72, r=28, t=28, b=70)
            fig_region.update_layout(**region_layout, height=420, hovermode="x unified", legend_title_text="", showlegend=compare_regions)
            fig_region.update_yaxes(title=dict(text=display_axis_title))
            fig_region.update_xaxes(title=dict(text=""), rangeslider_visible=True, rangeslider_thickness=0.055)
            fig_region.add_hline(
                y=100.0,
                line_dash="dot",
                line_color=CLR_TERTIARY,
                line_width=1,
                annotation_text=baseline_annotation,
                annotation_position="top left",
                annotation_font_color=CLR_TERTIARY,
            )
            st.plotly_chart(fig_region, width="stretch")
            if use_spliced:
                note = (
                    "linked continuous regional General index. Legacy values cover the early period, and current values are scaled "
                    "through the overlap so the line continues to the latest official quarter."
                )
            elif use_legacy:
                note = "official legacy Excel tables parsed from the regional legacy workbooks; supports region x sector/type with the older base."
            else:
                note = "quarterly official general real estate price index by administrative region."
            st.caption(f"Source: [{source_name}]({source_url}) · {note}")
            st.caption(scale_caption)

        latest_rows = (
            chart_df.sort_values("period_date")
                    .groupby("region", as_index=False)
                    .tail(1)
                    .copy()
        )
        if not latest_rows.empty:
            latest_rows["Region"] = latest_rows["region"].map(_region_display)
            latest_rows["Period"] = latest_rows["year"].astype(str) + " " + latest_rows["quarter"].astype(str)
            latest_rows["Displayed Index"] = latest_rows[display_value_field].map(lambda value: _fmt_num(value, 2))
            source_index_column = "Linked Index" if use_spliced else "Official Index"
            latest_rows[source_index_column] = latest_rows["value"].map(lambda value: _fmt_num(value, 2))
            latest_rows["QoQ"] = latest_rows["qoq_pct"].map(lambda value: _fmt_pct(value, ""))
            latest_rows["YoY"] = latest_rows["yoy_pct"].map(lambda value: _fmt_pct(value, ""))
            latest_rows["Source"] = "KAPSARC/GASTAT"
            st.dataframe(
                latest_rows[["Region", "Period", "Displayed Index", source_index_column, "QoQ", "YoY", "Source"]],
                width="stretch",
                hide_index=True,
                height=min(280, 40 + 35 * len(latest_rows)),
            )

    st.markdown("<p class='section-title'>National REPI by Sector</p>", unsafe_allow_html=True)
    if repi_df.empty:
        st.info("No real estate price index rows are available yet.")
    else:
        core_sectors = ["General index", "Residential total", "Commercial total", "Agricultural total"]
        chart_df = repi_df[repi_df["sector_display"].isin(core_sectors)]
        if chart_df.empty:
            st.info("No core real estate sector rows are available yet.")
        else:
            fig_repi = px.line(
                chart_df,
                x="period_date",
                y="value",
                color="sector_display",
                markers=True,
                color_discrete_sequence=PLOTLY_LAYOUT["colorway"],
            )
            fig_repi.update_traces(line=dict(width=2.3), marker=dict(size=6), hovertemplate="<b>%{x|%b %Y}</b><br>%{fullData.name}: <b>%{y:.2f}</b><extra></extra>")
            fig_repi.update_layout(**PLOTLY_LAYOUT, height=360, hovermode="x unified", legend_title_text="")
            fig_repi.update_yaxes(title=dict(text="Index (2023=100)"))
            fig_repi.update_xaxes(title=dict(text=""))
            st.plotly_chart(fig_repi, width="stretch")
            st.caption(
                f"Source: [{mod_real_estate.REPI_SOURCE_NAME}]"
                f"({mod_real_estate.KAPSARC_REPI_PAGE_URL}) · quarterly official real estate price index by sector."
            )

        latest_sector_df = (
            repi_df.sort_values("period_date")
                   .groupby("sector_display", as_index=False)
                   .tail(1)
                   .sort_values("value", ascending=False)
                   .copy()
        )
        latest_sector_df["Period"] = latest_sector_df["year"].astype(str) + " " + latest_sector_df["quarter"].astype(str)
        latest_sector_df["Index"] = latest_sector_df["value"].map(lambda value: _fmt_num(value, 2))
        latest_sector_df["QoQ"] = latest_sector_df["qoq_pct"].map(lambda value: _fmt_pct(value, ""))
        latest_sector_df["YoY"] = latest_sector_df["yoy_pct"].map(lambda value: _fmt_pct(value, ""))
        latest_sector_df["Source"] = "KAPSARC/GASTAT"
        latest_sector_df = latest_sector_df.rename(columns={"sector_display": "Sector"})
        st.dataframe(latest_sector_df[["Sector", "Period", "Index", "QoQ", "YoY", "Source"]], width="stretch", hide_index=True, height=min(460, 40 + 34 * len(latest_sector_df)))

    st.markdown("<p class='section-title'>Data Source Receipts</p>", unsafe_allow_html=True)
    source_rows = []
    if general_latest is not None:
        source_rows.append({
            "Indicator": "Real Estate Price Index",
            "What It Measures": "Official real estate price index by sector, base 2023=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{general_latest['year']} {general_latest['quarter']}",
            "Source": general_latest["source_name"],
            "Source Link": general_latest["source_url"],
            "App Transformation": "Filtered to core sectors and plotted by sector.",
        })
    if not regional_repi_df.empty:
        latest = regional_repi_df.sort_values("period_date").iloc[-1]
        source_rows.append({
            "Indicator": "Regional Real Estate Price Index",
            "What It Measures": "Official general real estate price index by administrative region, base 2023=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{latest['year']} {latest['quarter']}",
            "Source": latest["source_name"],
            "Source Link": latest["source_url"],
            "App Transformation": "Filtered by administrative region; current regional source is General index only.",
        })
    if not spliced_repi_df.empty:
        latest = spliced_repi_df.sort_values("period_date").iloc[-1]
        source_rows.append({
            "Indicator": "Continuous Regional REPI",
            "What It Measures": "Linked regional General index from legacy 2014-base and current 2023-base official REPI sources",
            "Frequency": "Quarterly",
            "Latest Period": f"{latest['year']} {latest['quarter']}",
            "Source": latest["source_name"],
            "Source Link": latest["source_url"],
            "App Transformation": "Spliced on overlapping quarters so one regional line runs from 2019 to the latest official quarter.",
        })
    if not legacy_repi_df.empty:
        latest = legacy_repi_df.sort_values("period_date").iloc[-1]
        source_rows.append({
            "Indicator": "Legacy Regional-Sector REPI",
            "What It Measures": "Official real estate price index by administrative region and sector/type, base 2014=100",
            "Frequency": "Quarterly",
            "Latest Period": f"{latest['year']} {latest['quarter']}",
            "Source": latest["source_name"],
            "Source Link": latest["source_url"],
            "App Transformation": "Parsed original regional legacy Excel workbooks; use separately from the 2023=100 current series.",
        })
    if source_rows:
        st.dataframe(pd.DataFrame(source_rows), width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — FUND PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════

def page_funds(embedded: bool = False):
    if embedded:
        st.markdown("<p class='section-title'>Fund Performance</p>", unsafe_allow_html=True)
        st.caption("Saudi mutual funds NAV analytics inside the unified Portfolio Workspace.")
    else:
        _page_header("Fund Performance", "Saudi Mutual Funds NAV Analytics")

    if not mod_funds.db_available():
        st.warning("mutual_funds.db not found. Run `python funds_scraper.py` first.")
        return

    all_data = mod_funds.load_nav_data()
    if all_data.empty:
        st.warning("No NAV data found. Run the funds scraper or backfill first.")
        return

    date_min = all_data["date"].min()
    date_max = all_data["date"].max()

    st.markdown("<div class='page-toolbar-label'>Fund filters</div>", unsafe_allow_html=True)
    filter_left, filter_mid, filter_right = st.columns([1.2, 2.6, 1.2])
    with filter_left:
        tf = st.pills("Timeframe", options=mod_funds.TIMEFRAMES, default="6M", key="fund_tf")
        start_date, end_date = mod_funds.resolve_timeframe(tf or "6M", date_min, date_max)
    with filter_mid:
        all_funds = sorted(all_data["fund_name"].unique().tolist())
        selected_funds = st.multiselect(
            "Funds",
            options=all_funds,
            default=all_funds,
            key="fund_sel",
        )
    with filter_right:
        chart_mode = st.radio(
            "Y-axis",
            ["% Change", "Absolute NAV"],
            horizontal=True,
            key="fund_mode",
        )

    if not selected_funds:
        st.info("Select at least one fund from the page filters.")
        return

    filtered = all_data[
        (all_data["date"] >= start_date) &
        (all_data["date"] <= end_date) &
        (all_data["fund_name"].isin(selected_funds))
    ].copy()

    if filtered.empty:
        st.info("No data for the selected range and funds.")
        return

    df = mod_funds.compute_pct_change(filtered)

    # ── KPI Cards ────────────────────────────────────────────────────────
    latest = df.sort_values("date").groupby("fund_name").last().reset_index()

    # Best and worst
    best_row = latest.loc[latest["pct_change"].idxmax()]
    worst_row = latest.loc[latest["pct_change"].idxmin()]

    avg_ret = float(latest["pct_change"].mean())
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _kpi_card("Avg YTD Return", f"{avg_ret:+.2f}%",
                  accent="positive" if avg_ret >= 0 else "negative",
                  delta=f"{len(latest)} funds aggregated", delta_color="off")
    with c2:
        _kpi_card("Funds Tracked", f"{len(selected_funds):,}", accent="primary")
    with c3:
        bname = best_row["fund_name"]
        if len(bname) > 22:
            bname = bname[:20] + ".."
        _kpi_card(f"Top · {bname}", f"{best_row['nav_price']:.4f}",
                  delta=f"{best_row['pct_change']:+.2f}%", accent="positive")
    with c4:
        wname = worst_row["fund_name"]
        if len(wname) > 22:
            wname = wname[:20] + ".."
        _kpi_card(f"Bottom · {wname}", f"{worst_row['nav_price']:.4f}",
                  delta=f"{worst_row['pct_change']:+.2f}%", accent="negative")

    st.markdown("")

    # ── Main Performance Chart ───────────────────────────────────────────
    color_map = {f: mod_funds.FUND_COLORS[i % len(mod_funds.FUND_COLORS)] for i, f in enumerate(all_funds)}

    use_pct = chart_mode == "% Change"
    y_col = "pct_change" if use_pct else "nav_price"
    y_label = "Change (%)" if use_pct else "NAV Price (SAR)"

    st.markdown(
        f"<p class='section-title'>{'Relative Performance (% Change from Period Start)' if use_pct else 'Absolute NAV Price'}</p>",
        unsafe_allow_html=True,
    )

    fig = px.line(
        df, x="date", y=y_col, color="fund_name",
        color_discrete_map=color_map,
        custom_data=["fund_name", "nav_price", "pct_change"],
    )

    if use_pct:
        fig.update_traces(
            hovertemplate="<b>%{customdata[0]}</b><br>%{x|%d %b %Y}<br>Change: <b>%{y:+.2f}%</b><br>NAV: %{customdata[1]:.4f}<extra></extra>",
            line=dict(width=2.5),
        )
        fig.add_hline(y=0, line_dash="dot", line_color="rgba(66,72,89,0.3)", line_width=1)
    else:
        fig.update_traces(
            hovertemplate="<b>%{customdata[0]}</b><br>%{x|%d %b %Y}<br>NAV: <b>%{y:.4f}</b><br>Change: %{customdata[2]:+.2f}%<extra></extra>",
            line=dict(width=2.5),
        )

    fig.update_layout(
        **PLOTLY_LAYOUT,
        height=480,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, font=dict(size=11)),
        yaxis_ticksuffix="%" if use_pct else "",
        yaxis_hoverformat="+.2f" if use_pct else ".4f",
        hovermode="x unified" if use_pct else "closest",
    )
    fig.update_xaxes(rangeslider_visible=True, rangeslider_thickness=0.06)
    st.plotly_chart(fig, width="stretch")

    # ── Constituent Performance Table ────────────────────────────────────
    with st.expander("Constituent Performance Matrix", expanded=True):
        table_df = latest[["fund_name", "nav_price", "pct_change"]].copy()
        table_df.columns = ["Fund Name", "NAV (SAR)", "Period Return %"]
        table_df["NAV (SAR)"] = table_df["NAV (SAR)"].map("{:.4f}".format)
        table_df["Period Return %"] = table_df["Period Return %"].map("{:+.2f}%".format)
        st.dataframe(table_df, width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — FOREIGN LIQUIDITY RADAR
# ══════════════════════════════════════════════════════════════════════════════

def _render_liquidity_velocity_chart(
    symbol: str,
    name: str,
    history_df: pd.DataFrame,
) -> None:
    """Render the Liquidity Velocity panel from REAL historical data.

    Layout:
      1. A large KPI card pinned above the chart showing the current foreign
         ownership %, with the *daily delta* (today − prior trading day) as
         the colored delta. Accent and arrow direction are driven by the
         delta sign — green for inflow, red for outflow, dim for first-day.
      2. A premium Plotly Spline Area Chart (``mode="lines+markers"`` +
         ``shape="spline"``) with ``fill='tonexty'`` against an INVISIBLE
         baseline trace placed 30 % below the data minimum.  This lets
         ``update_yaxes(autorange=True, fixedrange=False)`` snap the y-axis
         onto the active spectrum (e.g. Aramco's 0.73 % → 0.75 % swings)
         instead of collapsing them by anchoring to zero.

    The chart dynamically scales to whatever history is available:
        * 0 rows  → an info banner explaining the symbol has no scraped
                    data yet.
        * 1 row   → KPI only, plus a caption that the trend chart unlocks
                    once a second day is recorded.
        * 2+ rows → full spline area chart, y-axis auto-zoomed onto the
                    data band so even sub-percent movement is prominent.
    """
    # ── Empty-state guards ──────────────────────────────────────────────
    if history_df is None or history_df.empty:
        st.info(f"No historical foreign-ownership data yet for {symbol}.")
        return

    df = history_df.sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["actual_ownership"]).reset_index(drop=True)
    if df.empty:
        st.info(f"No usable ownership readings for {symbol}.")
        return

    # ── Headline figures ────────────────────────────────────────────────
    current_pct = float(df["actual_ownership"].iloc[-1])
    latest_date = df["date"].iloc[-1]

    daily_delta: float | None = None
    prev_date = None
    if len(df) >= 2:
        prev_pct = float(df["actual_ownership"].iloc[-2])
        prev_date = df["date"].iloc[-2]
        daily_delta = round(current_pct - prev_pct, 4)

    # Latest available regulatory limit (may be NaN for some symbols).
    lim_series = df["ownership_limit"].dropna() if "ownership_limit" in df.columns else pd.Series(dtype=float)
    ownership_limit = float(lim_series.iloc[-1]) if not lim_series.empty else None

    # ── KPI card · Current ownership % + daily delta ────────────────────
    if daily_delta is None:
        # First day in the data set — no delta computable.
        delta_text = f"first reading · {latest_date}"
        accent = "primary"
        delta_color = "off"
    else:
        sign = "+" if daily_delta >= 0 else ""
        delta_text = f"{sign}{daily_delta:+.4f}pp · vs {prev_date}"
        # Plotly delta arrow comes from the leading +/- character; trim the
        # synthesized double-sign before passing to _kpi_card.
        if delta_text.startswith("++"):
            delta_text = delta_text[1:]
        if daily_delta > 0:
            accent = "positive"
        elif daily_delta < 0:
            accent = "negative"
        else:
            accent = "primary"
        delta_color = "normal"

    _kpi_card(
        f"Current Foreign Ownership · {symbol}",
        f"{current_pct:.2f}%",
        delta=delta_text,
        delta_color=delta_color,
        accent=accent,
    )

    # ── 1-day edge case: KPI is enough, skip the chart ──────────────────
    if len(df) < 2:
        st.caption(
            f"📈 The historical trend chart will appear here once a second "
            f"trading day is recorded for {symbol}. (Currently 1 data point — "
            f"{latest_date}.)"
        )
        return

    # ── Data-centered y-axis via invisible baseline + autorange ─────────
    # Anchor the area fill to an INVISIBLE baseline trace placed 30 % below
    # the data minimum (with a 0.4 pp floor so very stable stocks still get
    # breathing room).  Plotly's autorange then frames the active spectrum
    # — e.g. Aramco 0.73 % → 0.75 % becomes a prominent ribbon instead of a
    # flat line crushed against the zero baseline.
    data_min = float(df["actual_ownership"].min())
    data_max = float(df["actual_ownership"].max())
    data_range = max(data_max - data_min, 0.01)
    padding = max(data_range * 0.3, 0.4)   # ≥ 30 % of the data range
    y_floor = max(0.0, data_min - padding)

    # When the regulatory cap is close enough to the data to be visually
    # informative we also stretch the autorange upward to include it via
    # a second invisible mirror trace.
    show_limit_on_chart = (
        ownership_limit is not None
        and not pd.isna(ownership_limit)
        and float(ownership_limit) <= data_max + data_range * 4
    )
    y_ceil_for_limit = (
        float(ownership_limit) * 1.04 if show_limit_on_chart else None
    )

    fig = go.Figure()

    # (1) Invisible floor — re-anchors fill='tonexty' to y_floor (not zero)
    #     so autorange snaps onto the data band.
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=[y_floor] * len(df),
        mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        showlegend=False,
        hoverinfo="skip",
    ))

    # (2) Visible spline area trace — the real foreign-ownership series.
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["actual_ownership"],
        mode="lines+markers",
        name="Foreign Ownership",
        line=dict(color="#4ade8c", width=2.5, shape="spline", smoothing=1.0),
        marker=dict(size=5, color="#4ade8c", line=dict(width=0)),
        fill="tonexty",
        fillcolor="rgba(74, 222, 140, 0.18)",
        hovertemplate="<b>%{x|%d %b %Y}</b><br>Ownership: <b>%{y:.4f}%</b><extra></extra>",
    ))

    # (3) Optional regulatory ceiling — dashed red overlay + invisible
    #     mirror trace so autorange extends upward to include it.
    if show_limit_on_chart:
        fig.add_hline(
            y=float(ownership_limit),
            line_color=CLR_ERROR,
            line_width=1.4,
            line_dash="dash",
            annotation=dict(
                text=f"Limit · {float(ownership_limit):.0f}%",
                font=dict(family="JetBrains Mono, monospace", size=10, color=CLR_ERROR),
                bgcolor="rgba(255,107,107,0.10)",
                borderpad=4,
            ),
            annotation_position="top right",
        )
        fig.add_trace(go.Scatter(
            x=df["date"],
            y=[y_ceil_for_limit] * len(df),
            mode="lines",
            line=dict(color="rgba(0,0,0,0)", width=0),
            showlegend=False,
            hoverinfo="skip",
        ))

    # ── Layout: transparent backgrounds + monospaced hover label ─────────
    _layout = {**PLOTLY_LAYOUT}
    _layout["xaxis"] = {
        **PLOTLY_LAYOUT["xaxis"],
        "tickformat": "%b %d",
    }
    _layout["margin"] = dict(l=50, r=30, t=50, b=40)

    fig.update_layout(
        **_layout,
        height=380,
        showlegend=False,
        title=dict(
            text=f"{symbol} — {name[:40]}",
            font=dict(family="Inter, sans-serif", size=14, color="#e8eaf3"),
            x=0.01,
            xanchor="left",
        ),
        hovermode="x unified",
    )

    # Hadi-requested auto-zoom: with the invisible baseline already anchored
    # 30 % below the data minimum, autorange snaps the y-axis onto the
    # active spectrum.  fixedrange stays False so drag-zoom remains live.
    fig.update_yaxes(
        autorange=True,
        fixedrange=False,
        title=dict(text="Foreign Ownership", font=dict(size=11, color=CLR_DIM)),
        ticksuffix="%",
        tickformat=".2f",
    )

    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # Footer caption — surfaces sample size + headroom context.
    headroom_note = ""
    if ownership_limit is not None and not pd.isna(ownership_limit):
        headroom_note = (
            f" · regulatory headroom "
            f"{(float(ownership_limit) - current_pct):+.2f}pp "
            f"vs {float(ownership_limit):.0f}% cap"
        )
    st.caption(
        f"📊 {len(df)} real trading days · {df['date'].iloc[0]} → {latest_date}"
        f"{headroom_note}"
    )


def page_liquidity():
    _page_header("Foreign Liquidity Radar", "Tadawul Foreign Ownership Headroom Analysis")

    if not mod_liquidity.db_available():
        st.warning("liquidity_radar.db not found. Run `python foreign_liquidity_scraper.py` first.")
        return

    all_data = mod_liquidity.load_all_data()
    if all_data.empty:
        st.warning("No foreign ownership data. Run the scraper first.")
        return

    all_dates = sorted(all_data["date"].unique())
    date_max = all_dates[-1]
    has_history = len(all_dates) >= 2
    all_symbols = sorted(all_data["symbol"].unique().tolist())
    sym_name_map = (
        all_data[["symbol", "company_name"]].drop_duplicates()
        .set_index("symbol")["company_name"].to_dict()
    )

    st.markdown("<div class='page-toolbar-label'>Liquidity filters</div>", unsafe_allow_html=True)
    ctrl_date, ctrl_stock, ctrl_meta = st.columns([1.2, 2.4, 1.2])
    with ctrl_date:
        selected_date = st.date_input(
            "Snapshot date",
            value=date_max,
            min_value=all_dates[0],
            max_value=date_max,
            key="liq_date",
        )
        if selected_date not in all_dates:
            selected_date = min(all_dates, key=lambda d: abs((d - selected_date).days))
            st.caption(f"Snapped to: {selected_date}")
    with ctrl_stock:
        search_options = [f"{s} - {sym_name_map.get(s, '')}" for s in all_symbols]
        selected_search = st.selectbox(
            "Trend analysis stock",
            options=["(All - no filter)"] + search_options,
            index=0,
            key="liq_search",
        )
        trend_symbol = None
        if selected_search != "(All - no filter)":
            trend_symbol = selected_search.split(" - ")[0].strip()
    with ctrl_meta:
        _decision_note(
            "Universe",
            f"{len(all_dates)} trading days · {len(all_symbols)} stocks tracked.",
            "positive" if has_history else "warn",
        )

    # ── KPI Cards ────────────────────────────────────────────────────────
    delta_df = mod_liquidity.compute_delta(all_data, selected_date) if has_history else None

    if delta_df is not None and not delta_df.empty:
        meaningful_delta = _is_meaningful_delta(delta_df)
        inflows = int((delta_df["delta"] > 0).sum())
        outflows = int((delta_df["delta"] < 0).sum())
        unchanged = int((delta_df["delta"] == 0).sum())
        top_gainer = delta_df.nlargest(1, "delta").iloc[0] if meaningful_delta else None
        top_loser = delta_df.nsmallest(1, "delta").iloc[0]

        net_flow = inflows - outflows
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sign = "+" if net_flow >= 0 else ""
            _kpi_card(
                "Net Foreign Inflow",
                f"{sign}{net_flow} stocks",
                delta=f"{inflows} in / {outflows} out",
                delta_color="off",
                accent="positive" if net_flow >= 0 else "negative",
            )
        with c2:
            breadth_pct = inflows / len(delta_df) * 100
            _kpi_card(
                "Accumulation Breadth",
                f"{inflows} stocks",
                delta=f"{breadth_pct:.0f}% of universe",
                delta_color="off",
                accent="primary",
            )
        with c3:
            if top_gainer is None:
                _kpi_card(
                    "Flow Quality",
                    "No signal",
                    delta=f"{unchanged} unchanged stocks",
                    delta_color="off",
                    accent="warn",
                    text_value=True,
                )
            else:
                gname = _short_text(top_gainer["company_name"], 20)
                _kpi_card(
                    f"Top Inflow · {top_gainer['symbol']}",
                    f"{top_gainer['today_pct']:.2f}%",
                    delta=f"{top_gainer['delta']:+.4f}pp · {gname}",
                    accent="positive",
                )
        with c4:
            _kpi_card(
                "Tracked Universe",
                f"{len(all_symbols)} stocks",
                delta=f"{len(all_dates)} trading days",
                delta_color="off",
                accent="violet",
            )
    else:
        st.info("Need 2+ trading days for delta analysis. Run the scraper again tomorrow.")

    st.markdown("")

    # ── Charts Row ───────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    # Left: Top 10 Inflows bar chart
    with col_left:
        st.markdown("<p class='section-title'>Top 10 Foreign Inflows</p>", unsafe_allow_html=True)

        if delta_df is not None and not delta_df.empty and _is_meaningful_delta(delta_df):
            top10 = delta_df.nlargest(10, "delta").copy()
            top10["label"] = top10["symbol"] + " — " + top10["company_name"].str[:18]
            top10 = top10.sort_values("delta", ascending=True)

            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                y=top10["label"], x=top10["delta"], orientation="h",
                marker=dict(
                    color=top10["delta"],
                    colorscale=[[0, CLR_ERROR], [0.5, CLR_TERTIARY], [1, CLR_SECONDARY]],
                ),
                hovertemplate="<b>%{y}</b><br>Delta: %{x:+.4f}%<extra></extra>",
            ))
            # Merge PLOTLY_LAYOUT first so per-chart xaxis/margin overrides don't
            # collide with the keys already inside the unpacked global.
            _bar_layout = {**PLOTLY_LAYOUT}
            _bar_layout["xaxis"] = {
                **PLOTLY_LAYOUT["xaxis"],
                "title": "Ownership Change (%)",
                "tickformat": "+.4f",
            }
            _bar_layout["margin"] = dict(l=180, r=30, t=10, b=50)
            fig_bar.update_layout(
                **_bar_layout, height=420, showlegend=False,
            )
            st.plotly_chart(fig_bar, width="stretch")
        elif delta_df is not None and not delta_df.empty:
            st.info("No meaningful flow: all latest foreign-ownership deltas are flat.")
        else:
            st.info("No delta data available.")

    # Right: Liquidity Velocity (KPI + historical trend with mock backfill)
    with col_right:
        st.markdown(
            "<p class='section-title'>Liquidity Velocity (Historical Trend)</p>",
            unsafe_allow_html=True,
        )

        if not all_symbols:
            st.info("No stock data available in the database yet.")
        else:
            # ── In-panel stock picker (the user can switch stocks right here,
            #    no need to go back to the sidebar) ─────────────────────────
            picker_options = [f"{s} — {sym_name_map.get(s, '')}" for s in all_symbols]

            # Default selection: whatever the sidebar already picked, otherwise
            # Saudi Aramco (2222), otherwise the first stock in the list.
            default_symbol = trend_symbol
            if not default_symbol:
                default_symbol = "2222" if "2222" in all_symbols else all_symbols[0]
            default_idx = (
                all_symbols.index(default_symbol)
                if default_symbol in all_symbols else 0
            )

            st.markdown(
                "<div style='font-family:JetBrains Mono,monospace; font-size:0.62rem; "
                "font-weight:700; letter-spacing:0.18em; color:#a8aebf; "
                "margin:4px 0 4px;'>اختر شركة لعرض رسمها البياني</div>",
                unsafe_allow_html=True,
            )
            picker_choice = st.selectbox(
                "اختر شركة",
                options=picker_options,
                index=default_idx,
                key="liq_velocity_stock_picker",
                label_visibility="collapsed",
            )
            chosen_symbol = picker_choice.split(" — ")[0].strip()

            # ── Query the real historical ownership series for this symbol
            #    straight from liquidity_radar.db (cached for 5 minutes).
            history_df = mod_liquidity.get_symbol_history(chosen_symbol)
            name = sym_name_map.get(chosen_symbol, chosen_symbol)
            _render_liquidity_velocity_chart(chosen_symbol, name, history_df)

    # ── Hot Stocks Radar Table ───────────────────────────────────────────
    st.markdown("<p class='section-title'>Hot Stocks Radar</p>", unsafe_allow_html=True)

    if delta_df is not None and not delta_df.empty and _is_meaningful_delta(delta_df):
        # Build HTML table with green/red badges
        display_df = delta_df.sort_values("delta", ascending=False).head(20).copy()
        rows_html = ""
        for _, row in display_df.iterrows():
            d = row["delta"]
            badge_cls = "badge-green" if d > 0 else ("badge-red" if d < 0 else "badge-neutral")
            delta_str = f"{d:+.4f}%"

            prev_str = f"{row['prev_pct']:.2f}" if pd.notna(row.get('prev_pct')) else "-"
            curr_str = f"{row['today_pct']:.2f}" if pd.notna(row.get('today_pct')) else "-"
            name = str(row["company_name"])
            if len(name) > 32:
                name = name[:30] + ".."
            symbol_html = _html_escape(row["symbol"])
            name_html = _html_escape(name)
            prev_html = _html_escape(prev_str)
            curr_html = _html_escape(curr_str)
            delta_html = _html_escape(delta_str)

            rows_html += f"""
            <tr style="border-bottom:1px solid rgba(66,72,89,0.05); transition:background 0.15s;">
                <td style="padding:10px 20px; font-family:Space Grotesk,monospace; font-size:0.85rem; color:#85adff; font-weight:700;">{symbol_html}</td>
                <td style="padding:10px 20px; font-size:0.85rem; font-weight:500; opacity:0.85;">{name_html}</td>
                <td style="padding:10px 20px; font-family:Space Grotesk,monospace; font-size:0.85rem; text-align:right; opacity:0.5;">{prev_html}</td>
                <td style="padding:10px 20px; font-family:Space Grotesk,monospace; font-size:0.85rem; text-align:right;">{curr_html}</td>
                <td style="padding:10px 20px; text-align:right;"><span class="{badge_cls}">{delta_html}</span></td>
            </tr>"""

        table_html = f"""
        <div style="background:#0c1324; border:1px solid rgba(66,72,89,0.08); border-radius:2px; overflow:hidden;">
            <div style="padding:16px 20px; background:rgba(0,0,0,0.2); border-bottom:1px solid rgba(66,72,89,0.1); display:flex; justify-content:space-between; align-items:center;">
                <span style="font-family:Space Grotesk,monospace; font-size:0.6rem; color:#6f7588; letter-spacing:0.1em; text-transform:uppercase;">Top 20 by Delta  |  {selected_date}</span>
            </div>
            <table style="width:100%; border-collapse:collapse; text-align:left;">
                <thead>
                    <tr style="background:rgba(0,0,0,0.3);">
                        <th style="padding:14px 20px; font-family:Space Grotesk,monospace; font-size:0.6rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6f7588;">Symbol</th>
                        <th style="padding:14px 20px; font-family:Space Grotesk,monospace; font-size:0.6rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6f7588;">Company Name</th>
                        <th style="padding:14px 20px; font-family:Space Grotesk,monospace; font-size:0.6rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6f7588; text-align:right;">Prev %</th>
                        <th style="padding:14px 20px; font-family:Space Grotesk,monospace; font-size:0.6rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6f7588; text-align:right;">Current %</th>
                        <th style="padding:14px 20px; font-family:Space Grotesk,monospace; font-size:0.6rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6f7588; text-align:right;">Delta</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>"""
        st.markdown(table_html, unsafe_allow_html=True)
    elif delta_df is not None and not delta_df.empty:
        st.info("No meaningful flow: the latest daily ownership snapshot is unchanged across the tracked universe.")

    # ── Accumulation Streaks ─────────────────────────────────────────────
    st.markdown("")
    with st.expander("3-Day Accumulation Streaks", expanded=False):
        if len(all_dates) < 3:
            st.info("Need 3+ trading days for accumulation detection.")
        else:
            acc_df = mod_liquidity.detect_accumulation(all_data, selected_date, n_days=3)
            if acc_df is not None and not acc_df.empty:
                st.success(f"**{len(acc_df)} stock(s)** with 3-day continuous foreign accumulation.")
                disp = acc_df.copy()
                disp.columns = ["Symbol", "Company", "Start %", "Latest %", "Total Gain"]
                disp["Start %"] = disp["Start %"].map("{:.2f}%".format)
                disp["Latest %"] = disp["Latest %"].map("{:.2f}%".format)
                disp["Total Gain"] = disp["Total Gain"].map("{:+.4f}%".format)
                st.dataframe(disp, width="stretch", hide_index=True)
            else:
                st.info("No 3-day accumulation streaks detected.")

    # ── Full Master Table ────────────────────────────────────────────────
    with st.expander("Master Data Table", expanded=False):
        day_data = all_data[all_data["date"] == selected_date].copy()
        if day_data.empty:
            st.warning(f"No data for {selected_date}.")
        else:
            if delta_df is not None and not delta_df.empty:
                day_display = day_data.merge(delta_df[["symbol", "delta"]], on="symbol", how="left")
            else:
                day_display = day_data.copy()
                day_display["delta"] = None

            day_display = day_display.sort_values("actual_ownership", ascending=False)
            disp = day_display[["symbol", "company_name", "ownership_limit", "actual_ownership", "headroom", "delta"]].copy()
            disp.columns = ["Symbol", "Company", "Limit %", "Actual %", "Headroom %", "Delta"]
            disp["Limit %"] = disp["Limit %"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "-")
            disp["Actual %"] = disp["Actual %"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "-")
            disp["Headroom %"] = disp["Headroom %"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "-")
            disp["Delta"] = disp["Delta"].apply(lambda v: f"{v:+.4f}" if pd.notna(v) else "-")

            search_term = st.text_input("Filter (symbol or company)", "", key="liq_master_search")
            if search_term:
                mask = (
                    disp["Symbol"].str.contains(search_term, case=False, na=False) |
                    disp["Company"].str.contains(search_term, case=False, na=False)
                )
                disp = disp[mask]

            st.caption(f"Showing {len(disp)} of {len(day_data)} stocks.")
            st.dataframe(disp, width="stretch", hide_index=True,
                         height=min(600, 40 + 35 * len(disp)))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — ROBO-ADVISOR PORTFOLIO PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════

def page_robo(embedded: bool = False):
    """Robo-Advisor — Arabic-localized Primary vs Benchmark cumulative return.

    Two selectboxes (Primary / Benchmark), Arabic timeframe pills, and an
    area chart showing how the chosen Abyan portfolio compares to the
    universal SPUS benchmark. All KPI labels are in Arabic per spec.
    """
    if embedded:
        st.markdown("<p class='section-title'>Robo-Advisor</p>", unsafe_allow_html=True)
        st.caption("Abyan portfolios compared against selected benchmarks.")
    else:
        _page_header(
            "Robo-Advisor Portfolio Performance",
            "Cumulative-return tracking · historical Yahoo Finance closes",
        )

    if not mod_robo.is_available():
        st.error("**yfinance not installed.**  Run `pip install yfinance` and restart.")
        return

    # ── Arabic timeframe pills (default → 3 أشهر) ────────────────────────
    timeframe_options = [
        ("1mo", "شهر واحد"),
        ("3mo", "3 أشهر"),
        ("ytd", "منذ بداية العام"),
        ("1y",  "سنة كاملة"),
    ]
    tf_labels = [lbl for _, lbl in timeframe_options]
    tf_codes  = [code for code, _ in timeframe_options]

    st.markdown("<div class='page-toolbar-label'>Robo filters</div>", unsafe_allow_html=True)
    pcol, refresh_col = st.columns([2.6, 1])
    with pcol:
        tf_lbl = st.radio(
            "الفترة الزمنية",
            options=tf_labels,
            index=1,                       # default → 3 أشهر
            horizontal=True,
            key="robo_timeframe",
        )
    with refresh_col:
        if st.button("Refresh prices", width="stretch", key="robo_refresh"):
            for cache_attr in ("fetch_closes", "fetch_historical_closes"):
                fn = getattr(mod_robo, cache_attr, None)
                if fn is not None and hasattr(fn, "clear"):
                    try:
                        fn.clear()
                    except Exception:
                        pass
            st.rerun()
    selected_period = tf_codes[tf_labels.index(tf_lbl)]

    # ── Two dropdowns: Primary Portfolio + Benchmark ─────────────────────
    house_names = [p["name"] for p in mod_robo.PORTFOLIOS if p.get("is_house")]
    bench_names = [p["name"] for p in mod_robo.PORTFOLIOS if not p.get("is_house")]
    all_names   = [p["name"] for p in mod_robo.PORTFOLIOS]

    primary_default = "أبيان النمو الفائق"
    primary_idx = house_names.index(primary_default) if primary_default in house_names else 0

    # Multiselect default: just SPUS — user can add tier-specific competitors.
    bench_default_list = ["المعيار: مؤشر SPUS"]
    bench_default_list = [n for n in bench_default_list if n in bench_names]

    sel_col1, sel_col2 = st.columns([1, 1.4])
    with sel_col1:
        st.markdown(
            "<div style='font-family:JetBrains Mono,monospace; font-size:0.62rem; "
            "font-weight:700; letter-spacing:0.18em; color:#4ade8c; margin-bottom:4px;'>"
            "★ محفظة أبيان</div>",
            unsafe_allow_html=True,
        )
        primary_name = st.selectbox(
            "اختر المحفظة الأساسية",
            options=house_names if house_names else all_names,
            index=primary_idx,
            key="robo_primary",
            label_visibility="collapsed",
        )

    with sel_col2:
        st.markdown(
            "<div style='font-family:JetBrains Mono,monospace; font-size:0.62rem; "
            "font-weight:700; letter-spacing:0.18em; color:#b794f6; margin-bottom:4px;'>"
            "المعايير والمنافسون</div>",
            unsafe_allow_html=True,
        )
        bench_name_list = st.multiselect(
            "اختر المعايير أو المنافسين للمقارنة",
            options=bench_names if bench_names else all_names,
            default=bench_default_list,
            key="robo_benchmarks",
            label_visibility="collapsed",
            help="يمكنك اختيار محفظة معيار واحدة أو أكثر لرسمها على نفس الرسم البياني",
        )

    # The chart should plot Primary + every benchmark in the multiselect.
    # De-dup if the user picks a benchmark that happens to match the primary.
    selected_names = [primary_name] + [n for n in bench_name_list if n != primary_name]

    if not bench_name_list:
        st.info("اختر معياراً واحداً على الأقل لمقارنته بالمحفظة الأساسية.")

    # ── Fetch (API → in-process fallback) ────────────────────────────────
    with st.spinner(f"جارٍ جلب أسعار آخر {tf_lbl} من Yahoo Finance..."):
        try:
            hist_payload, hist_source = _fetch_historical_payload(selected_period)
        except Exception as exc:
            st.error(f"تعذّر جلب البيانات التاريخية: {exc}")
            return

    for err in hist_payload.get("errors", []):
        st.warning(err)

    series_records = hist_payload.get("series", [])
    if not series_records:
        st.error("لا توجد بيانات تاريخية للفترة المختارة.")
        return

    # ── Status caption (Arabic) ──────────────────────────────────────────
    status = hist_payload.get("data_status", "?")
    status_color = {
        "ok":    CLR_SECONDARY,
        "stale": CLR_TERTIARY,
        "empty": CLR_ERROR,
    }.get(status, CLR_DIM)
    status_ar = {"ok": "متاح", "stale": "جزئي", "empty": "فارغ"}.get(status, status)
    st.markdown(
        f"<div style='display:flex; gap:18px; flex-wrap:wrap; align-items:center; "
        f"margin:-2px 0 16px; font-family:JetBrains Mono,monospace; font-size:0.7rem; "
        f"color:#a8aebf;'>"
        f"<span><span style='color:{status_color};'>●</span> "
        f"الحالة: <b style='color:{status_color};'>{status_ar}</b></span>"
        f"<span>الفترة: <b style='color:#e8eaf3;'>{hist_payload.get('start_date')}</b> "
        f"→ <b style='color:#e8eaf3;'>{hist_payload.get('as_of_date')}</b></span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── KPI cards (Arabic labels) ────────────────────────────────────────
    # Per spec, the Benchmark and Delta cards compare against the FIRST item
    # in the multiselect. The chart still plots ALL selections.
    summary_by_name = {p["name"]: p for p in hist_payload.get("portfolios", [])}
    primary_res = summary_by_name.get(primary_name)

    first_bench_name = bench_name_list[0] if bench_name_list else None
    first_bench_res  = summary_by_name.get(first_bench_name) if first_bench_name else None

    primary_ret    = primary_res.get("final_return_pct")    if primary_res    else None
    first_bench_ret = first_bench_res.get("final_return_pct") if first_bench_res else None
    spread = (primary_ret - first_bench_ret) if (primary_ret is not None and first_bench_ret is not None) else None

    k1, k2, k3 = st.columns([1, 1, 0.85])
    with k1:
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_SECONDARY}; "
            f"margin-bottom:6px;'>★ محفظة أبيان</div>",
            unsafe_allow_html=True,
        )
        if primary_ret is None:
            _kpi_card(primary_name, "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                primary_name,
                f"{primary_ret:+.2f}%",
                delta=f"العائد التراكمي ({tf_lbl})",
                delta_color="off",
                accent="positive" if primary_ret >= 0 else "negative",
            )

    with k2:
        # Card label hints there are more benchmarks than the one being shown.
        extra_count = max(0, len(bench_name_list) - 1)
        more_hint = f" · +{extra_count} على الرسم" if extra_count > 0 else ""
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_VIOLET}; "
            f"margin-bottom:6px;'>المعيار الأول{more_hint}</div>",
            unsafe_allow_html=True,
        )
        if not first_bench_name:
            _kpi_card("المعيار", "—", accent="primary", text_value=True)
        elif first_bench_ret is None:
            _kpi_card(first_bench_name, "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                first_bench_name,
                f"{first_bench_ret:+.2f}%",
                delta=f"العائد التراكمي ({tf_lbl})",
                delta_color="off",
                accent="violet",
            )

    with k3:
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_PRIMARY}; "
            f"margin-bottom:6px;'>الفارق (الأساسية − المعيار الأول)</div>",
            unsafe_allow_html=True,
        )
        if spread is None:
            _kpi_card("الأداء النسبي", "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                "الأداء النسبي",
                f"{spread:+.3f}pp",
                delta=("تفوّقت محفظة أبيان" if spread > 0
                       else "تفوّق المعيار" if spread < 0
                       else "متطابق"),
                delta_color="off",
                accent="positive" if spread > 0 else ("negative" if spread < 0 else "primary"),
            )

    st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)

    # ── Area chart (px.line + fill='tozeroy') ────────────────────────────
    fig = _historical_area_chart(series_records, selected_names, tf_lbl)
    if fig is None:
        st.info("لا توجد بيانات متداخلة بين المحفظتين في هذه الفترة.")
        return
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # ── Constituent breakdown (Arabic header) ────────────────────────────
    st.markdown(
        "<h3 style='font-family:Inter,Tajawal,sans-serif; font-size:0.95rem; "
        "font-weight:600; color:#e8eaf3; margin-top:28px; margin-bottom:12px; "
        "direction:rtl; text-align:right;'>تفصيل مكونات المحفظة</h3>",
        unsafe_allow_html=True,
    )

    # Table covers everything currently on the chart: primary + all selected benchmarks.
    rows = []
    for nm in selected_names:
        cfg = mod_robo.get_portfolio_by_name(nm)
        if cfg is None:
            continue
        for ticker, weight in cfg["holdings"].items():
            rows.append({
                "المحفظة": nm,
                "الرمز":    ticker,
                "الوزن":    f"{weight * 100:.0f}%",
                "النوع":    "أبيان" if cfg.get("is_house") else "معيار",
            })

    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            height=min(500, 40 + 35 * len(rows)),
        )
    else:
        st.info("لا توجد بيانات للمكونات.")




# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — STRATEGIC BENCHMARKS  (hardcoded Abyan vs Standard, paired by tier)
# ══════════════════════════════════════════════════════════════════════════════

API_URL = "http://127.0.0.1:8601"  # local FastAPI backend (optional)


def _holdings_table_html(holdings, constituent_lookup):
    """Render a compact constituent breakdown table for one portfolio."""
    rows_html = ""
    for h in holdings:
        ticker = h["ticker"]
        weight_pct = float(h.get("weight", 0)) * 100
        daily_pct = h.get("daily_return_pct")
        contrib = h.get("contribution_pct")
        missing = h.get("missing", False)

        if missing:
            ret_html = "<span style='color:#ff6b6b;'>—</span>"
            contrib_html = "<span style='color:#ff6b6b; font-style:italic;'>missing</span>"
        else:
            ret_color = "#4ade8c" if (daily_pct or 0) >= 0 else "#ff6b6b"
            ret_html = (f"<span style='color:{ret_color};'>{daily_pct:+.3f}%</span>"
                        if daily_pct is not None else "—")
            contrib_html = (f"<span style='color:{ret_color};'>{contrib:+.3f}%</span>"
                            if contrib is not None else "—")

        info = constituent_lookup.get(ticker.upper(), {})
        name_en = info.get("name_en", ticker)
        ticker_html = _html_escape(ticker)
        name_html = _html_escape(name_en)

        rows_html += (
            "<tr>"
            f"<td style='font-weight:700; color:#e8eaf3; padding:8px 12px;'>{ticker_html}</td>"
            f"<td style='color:#a8aebf; padding:8px 12px;'>{name_html}</td>"
            f"<td style='text-align:right; padding:8px 12px;'>{weight_pct:.1f}%</td>"
            f"<td style='text-align:right; padding:8px 12px;'>{ret_html}</td>"
            f"<td style='text-align:right; padding:8px 12px;'>{contrib_html}</td>"
            "</tr>"
        )

    return (
        "<table style='width:100%; border-collapse:collapse; "
        "font-family:JetBrains Mono,monospace; font-size:0.78rem;'>"
        "<thead><tr style='border-bottom:1px solid rgba(140,152,188,0.18);'>"
        "<th style='text-align:left; padding:10px 12px; font-size:0.62rem; "
        "letter-spacing:0.16em; color:#6b7290; font-weight:700;'>TICKER</th>"
        "<th style='text-align:left; padding:10px 12px; font-size:0.62rem; "
        "letter-spacing:0.16em; color:#6b7290; font-weight:700;'>NAME</th>"
        "<th style='text-align:right; padding:10px 12px; font-size:0.62rem; "
        "letter-spacing:0.16em; color:#6b7290; font-weight:700;'>WEIGHT</th>"
        "<th style='text-align:right; padding:10px 12px; font-size:0.62rem; "
        "letter-spacing:0.16em; color:#6b7290; font-weight:700;'>DAILY</th>"
        "<th style='text-align:right; padding:10px 12px; font-size:0.62rem; "
        "letter-spacing:0.16em; color:#6b7290; font-weight:700;'>CONTRIBUTION</th>"
        "</tr></thead><tbody>"
        + rows_html
        + "</tbody></table>"
    )


def _fetch_historical_payload(period: str) -> tuple[dict, str]:
    """Try the FastAPI backend first; fall back to in-process call.

    Returns (payload, source) where source is either 'api' or 'local'.
    """
    try:
        import requests  # type: ignore
        resp = requests.get(
            f"{API_URL}/api/robo-advisor/historical",
            params={"period": period},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json(), "api"
    except Exception:
        pass
    # API unreachable or non-200 — compute in-process.
    return mod_robo.build_historical_payload(period=period), "local"


def _historical_line_chart(series_records: list[dict], selected_names: list[str]):
    """Build a Plotly spline line chart of cumulative returns by portfolio.

    Pure presentation: takes server-side records (date · portfolio_name ·
    cumulative_return_pct) and the user's multiselect choices.
    """
    if not series_records or not selected_names:
        return None

    df = pd.DataFrame(series_records)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["portfolio_name"].isin(selected_names)].copy()
    df = df.sort_values(["portfolio_name", "date"])

    if df.empty:
        return None

    # Stable color mapping so the same portfolio keeps the same hue across
    # selection changes (otherwise px.line reassigns colors when the input set shrinks).
    color_pool = [CLR_SECONDARY, CLR_PRIMARY, CLR_TERTIARY, CLR_VIOLET,
                  CLR_ERROR, "#7be0d4", "#ffd166", "#a5b4fc"]
    sorted_names = sorted(df["portfolio_name"].unique())
    color_map = {name: color_pool[i % len(color_pool)] for i, name in enumerate(sorted_names)}

    fig = px.line(
        df,
        x="date",
        y="cumulative_return_pct",
        color="portfolio_name",
        line_shape="spline",
        color_discrete_map=color_map,
        labels={
            "date": "Date",
            "cumulative_return_pct": "Cumulative Return (%)",
            "portfolio_name": "Portfolio",
        },
        render_mode="svg",
    )

    # Smooth, semi-transparent fill to ground each line.
    for tr in fig.data:
        tr.update(line=dict(width=2.5, smoothing=1.0))
        tr.update(hovertemplate=(
            "<b>%{fullData.name}</b><br>"
            "%{x|%b %d, %Y}<br>"
            "Cumulative: <b>%{y:+.2f}%</b><extra></extra>"
        ))

    fig.add_hline(y=0, line_color="rgba(140,152,188,0.35)",
                  line_width=1, line_dash="dot")

    # Build layout in the merge-then-spread pattern to avoid duplicate kwargs.
    _layout = {**PLOTLY_LAYOUT}
    _layout["paper_bgcolor"] = "rgba(0,0,0,0)"
    _layout["plot_bgcolor"]  = "rgba(0,0,0,0)"
    _layout["yaxis"] = {
        **PLOTLY_LAYOUT["yaxis"],
        "ticksuffix": "%",
        "title": dict(text="Cumulative Return", font=dict(size=11, color=CLR_DIM)),
        "zeroline": True,
        "zerolinecolor": "rgba(140,152,188,0.20)",
        "zerolinewidth": 1,
    }
    _layout["xaxis"] = {
        **PLOTLY_LAYOUT["xaxis"],
        "title": dict(text="", font=dict(size=11, color=CLR_DIM)),
        "tickformat": "%b %d",
    }
    fig.update_layout(
        **_layout,
        height=440,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.16,
            xanchor="center", x=0.5,
            bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, Tajawal, sans-serif", size=11, color="#e8eaf3"),
            title=dict(text=""),
        ),
    )
    return fig


def _historical_area_chart(series_records, selected_names, period_label_ar=""):
    """Arabic-localized AREA chart of cumulative returns.

    Same shape as _historical_line_chart() but with fill='tozeroy' so each
    line becomes a filled area for that modern fintech look. Y-axis label
    is "العائد التراكمي (%)" per spec.
    """
    if not series_records or not selected_names:
        return None

    df = pd.DataFrame(series_records)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["portfolio_name"].isin(selected_names)].copy()
    df = df.sort_values(["portfolio_name", "date"])

    if df.empty:
        return None

    # Stable color mapping — preserves hue when user toggles the dropdowns.
    color_pool = [CLR_SECONDARY, CLR_VIOLET, CLR_PRIMARY, CLR_TERTIARY, CLR_ERROR,
                  "#7be0d4", "#ffd166", "#a5b4fc"]
    sorted_names = sorted(df["portfolio_name"].unique())
    color_map = {name: color_pool[i % len(color_pool)] for i, name in enumerate(sorted_names)}

    fig = px.line(
        df,
        x="date",
        y="cumulative_return_pct",
        color="portfolio_name",
        line_shape="spline",
        color_discrete_map=color_map,
        labels={
            "date": "التاريخ",
            "cumulative_return_pct": "العائد التراكمي (%)",
            "portfolio_name": "المحفظة",
        },
        render_mode="svg",
    )

    # Convert each line trace into a filled area trace.
    fig.update_traces(fill='tozeroy', line=dict(width=2.5, smoothing=1.0))

    # Per-trace hover template + slight opacity on the fill so overlapping
    # areas stay readable.
    for tr in fig.data:
        # Pull the line color, build a translucent matching fill color.
        line_hex = (tr.line.color or "#6c8eff").lstrip("#")
        if len(line_hex) == 6:
            r, g, b = (int(line_hex[i:i+2], 16) for i in (0, 2, 4))
            tr.update(fillcolor=f"rgba({r}, {g}, {b}, 0.18)")
        tr.update(hovertemplate=(
            "<b>%{fullData.name}</b><br>"
            "%{x|%b %d, %Y}<br>"
            "العائد التراكمي: <b>%{y:+.2f}%</b><extra></extra>"
        ))

    fig.add_hline(y=0, line_color="rgba(140,152,188,0.35)",
                  line_width=1, line_dash="dot")

    # Layout — transparent backgrounds + Arabic axis title.
    _layout = {**PLOTLY_LAYOUT}
    _layout["paper_bgcolor"] = "rgba(0,0,0,0)"
    _layout["plot_bgcolor"]  = "rgba(0,0,0,0)"
    _layout["yaxis"] = {
        **PLOTLY_LAYOUT["yaxis"],
        "ticksuffix": "%",
        "title": dict(
            text="العائد التراكمي (%)",
            font=dict(family="Inter, Tajawal, sans-serif", size=12, color=CLR_DIM),
        ),
        "zeroline": True,
        "zerolinecolor": "rgba(140,152,188,0.20)",
        "zerolinewidth": 1,
    }
    _layout["xaxis"] = {
        **PLOTLY_LAYOUT["xaxis"],
        "title": dict(text="", font=dict(size=11, color=CLR_DIM)),
        "tickformat": "%b %d",
    }

    fig.update_layout(
        **_layout,
        height=460,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.14,
            xanchor="center", x=0.5,
            bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, Tajawal, sans-serif", size=12, color="#e8eaf3"),
            title=dict(text=""),
        ),
    )

    # Surface the period label in the chart title for context.
    if period_label_ar:
        fig.update_layout(
            title=dict(
                text=f"العائد التراكمي · {period_label_ar}",
                font=dict(family="Inter, Tajawal, sans-serif", size=14, color="#e8eaf3"),
                x=0.5, xanchor="center",
            ),
        )

    return fig


def _composition_donut(holdings, color_seed="house"):
    """Donut chart of portfolio composition by weight."""
    labels = [h["ticker"] for h in holdings]
    values = [float(h.get("weight", 0)) * 100 for h in holdings]

    palette_house = ["#6c8eff", "#4ade8c", "#b794f6", "#7be0d4", "#ffb547", "#a5b4fc", "#86efac"]
    palette_bench = ["#94a3b8", "#cbd5e1", "#a8aebf", "#94a3b8", "#cbd5e1", "#94a3b8", "#a8aebf"]
    colors = palette_house if color_seed == "house" else palette_bench

    fig = go.Figure(data=[go.Pie(
        labels=labels, values=values, hole=0.62,
        marker=dict(colors=colors[:len(labels)], line=dict(color="rgba(0,0,0,0)", width=0)),
        textfont=dict(family="JetBrains Mono, monospace", size=11),
        hovertemplate="<b>%{label}</b><br>Weight: %{value:.1f}%<extra></extra>",
        textinfo="label+percent",
    )])
    # Merge first to avoid duplicate-keyword crash on `margin`.
    _donut_layout = {**PLOTLY_LAYOUT}
    _donut_layout["margin"] = dict(l=10, r=10, t=20, b=10)
    fig.update_layout(
        **_donut_layout,
        height=280,
        showlegend=False,
    )
    return fig


def page_custom_robo(embedded: bool = False):
    """Strategic Benchmark Dashboard — Abyan vs Standard, paired by risk tier."""
    if embedded:
        st.markdown("<p class='section-title'>Strategic Benchmarks</p>", unsafe_allow_html=True)
        st.caption("Abyan house portfolios versus standard competitors by risk tier.")
    else:
        _page_header(
            "Strategic Benchmarks",
            "Abyan house portfolios vs industry standard, by risk tier",
        )

    if not mod_robo.is_available():
        st.error("**yfinance not installed.**  Run `pip install yfinance` and restart.")
        return

    # Tier selector
    tier_options = [
        f"{mod_robo.TIER_LABELS_AR[t]}  ·  {mod_robo.TIER_LABELS_EN[t]}"
        for t in mod_robo.TIER_ORDER
    ]
    sel_idx = st.radio(
        "RISK TIER",
        options=list(range(len(tier_options))),
        format_func=lambda i: tier_options[i],
        index=0,
        horizontal=True,
        key="strat_tier_idx",
    )
    selected_tier = mod_robo.TIER_ORDER[sel_idx]
    house_cfg, bench_cfg = mod_robo.get_tier_pair(selected_tier)

    if house_cfg is None or bench_cfg is None:
        st.error(f"Tier `{selected_tier}` is missing a house or benchmark portfolio.")
        return

    # Fetch payload covering all 8 portfolios
    with st.spinner("Fetching latest closes from Yahoo Finance..."):
        try:
            payload = mod_robo.build_payload()
        except Exception as exc:
            st.error(f"Could not fetch portfolio data: {exc}")
            return

    by_name = {p["name"]: p for p in payload.get("portfolios", [])}
    house_res = by_name.get(house_cfg["name"])
    bench_res = by_name.get(bench_cfg["name"])

    if not house_res or not bench_res:
        st.error("Backend returned an unexpected payload — house or benchmark missing.")
        return

    # Status caption
    status = payload.get("data_status", "?")
    status_color = {"ok": CLR_SECONDARY, "stale": CLR_TERTIARY, "empty": CLR_ERROR}.get(status, CLR_DIM)
    st.markdown(
        f"<div style='display:flex; gap:18px; align-items:center; margin:-6px 0 18px; "
        f"font-family:JetBrains Mono,monospace; font-size:0.7rem; color:#a8aebf;'>"
        f"<span><span style='color:{status_color};'>●</span> "
        f"Status: <b style='color:{status_color}; text-transform:uppercase;'>{status}</b></span>"
        f"<span>As of <b style='color:#e8eaf3;'>{payload.get('as_of_date')}</b> "
        f"vs <b style='color:#e8eaf3;'>{payload.get('previous_date')}</b></span>"
        f"<span>{len(mod_robo.PORTFOLIOS)} portfolios · "
        f"{len(mod_robo.unique_tickers())} tickers</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    for err in payload.get("errors", []):
        st.warning(err)

    # Head-to-head KPI cards
    house_ret = house_res.get("weighted_return_pct")
    bench_ret = bench_res.get("weighted_return_pct")
    house_cov = house_res.get("coverage_pct", 100)
    bench_cov = bench_res.get("coverage_pct", 100)

    spread = None
    if house_ret is not None and bench_ret is not None:
        spread = house_ret - bench_ret

    k1, k2, k3 = st.columns([1, 1, 0.85])
    with k1:
        partial = f" · partial {house_cov}%" if house_cov < 100 else ""
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_SECONDARY}; "
            f"margin-bottom:6px;'>★ HOUSE · {house_cfg['tag']}{partial}</div>",
            unsafe_allow_html=True,
        )
        if house_ret is None:
            _kpi_card(house_cfg["name"], "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                house_cfg["name"],
                f"{house_ret:+.2f}%",
                delta=f"{house_cfg['name_en']} · daily",
                delta_color="off",
                accent="positive" if house_ret >= 0 else "negative",
            )

    with k2:
        partial = f" · partial {bench_cov}%" if bench_cov < 100 else ""
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_DIM}; "
            f"margin-bottom:6px;'>BENCHMARK · {bench_cfg['tag']}{partial}</div>",
            unsafe_allow_html=True,
        )
        if bench_ret is None:
            _kpi_card(bench_cfg["name"], "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                bench_cfg["name"],
                f"{bench_ret:+.2f}%",
                delta=f"{bench_cfg['name_en']} · daily",
                delta_color="off",
                accent="violet",
            )

    with k3:
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace; font-size:0.6rem; "
            f"font-weight:700; letter-spacing:0.20em; color:{CLR_PRIMARY}; "
            f"margin-bottom:6px;'>SPREAD (HOUSE − BENCH)</div>",
            unsafe_allow_html=True,
        )
        if spread is None:
            _kpi_card("Excess Return", "N/A", accent="primary", text_value=True)
        else:
            _kpi_card(
                "Excess Return",
                f"{spread:+.3f}pp",
                delta=("house outperformed" if spread > 0
                       else "house underperformed" if spread < 0
                       else "matched benchmark"),
                delta_color="off",
                accent="positive" if spread > 0 else ("negative" if spread < 0 else "primary"),
            )

    st.markdown("<div style='margin-top:24px;'></div>", unsafe_allow_html=True)

    # Composition donuts
    st.markdown("<p class='section-title'>Composition</p>", unsafe_allow_html=True)
    d1, d2 = st.columns(2)
    with d1:
        st.markdown(
            f"<div style='font-family:Inter,sans-serif; font-size:0.78rem; "
            f"font-weight:600; color:#e8eaf3; margin-bottom:4px;'>"
            f"★ {house_cfg['name']}</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            _composition_donut(house_res["holdings"], color_seed="house"),
            width="stretch", config={"displayModeBar": False},
        )
    with d2:
        st.markdown(
            f"<div style='font-family:Inter,sans-serif; font-size:0.78rem; "
            f"font-weight:600; color:#e8eaf3; margin-bottom:4px;'>"
            f"{bench_cfg['name']}</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            _composition_donut(bench_res["holdings"], color_seed="bench"),
            width="stretch", config={"displayModeBar": False},
        )

    # Constituent breakdown tables
    st.markdown("<p class='section-title' style='margin-top:18px;'>"
                "Constituent Performance</p>", unsafe_allow_html=True)

    constituent_lookup = {}
    try:
        constituent_lookup = mpc.ETF_DICTIONARY  # type: ignore[attr-defined]
    except AttributeError:
        pass

    t1, t2 = st.columns(2)
    with t1:
        st.markdown(
            "<div style='background:linear-gradient(180deg,#151b33,#0f1428); "
            "border:1px solid rgba(140,152,188,0.10); border-radius:14px; "
            "padding:14px 18px; box-shadow:0 1px 0 rgba(255,255,255,0.03) inset, "
            "0 8px 24px -12px rgba(0,0,0,0.55);'>"
            + _holdings_table_html(house_res["holdings"], constituent_lookup)
            + "</div>",
            unsafe_allow_html=True,
        )
    with t2:
        st.markdown(
            "<div style='background:linear-gradient(180deg,#151b33,#0f1428); "
            "border:1px solid rgba(140,152,188,0.10); border-radius:14px; "
            "padding:14px 18px; box-shadow:0 1px 0 rgba(255,255,255,0.03) inset, "
            "0 8px 24px -12px rgba(0,0,0,0.55);'>"
            + _holdings_table_html(bench_res["holdings"], constituent_lookup)
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── Historical cumulative-return line chart ──────────────────────────
    st.markdown("<p class='section-title' style='margin-top:32px;'>"
                "Historical Performance · Cumulative Return</p>",
                unsafe_allow_html=True)

    # Period selector — small segmented control above the multiselect
    period_options = [
        ("1mo", "1M"), ("3mo", "3M"), ("6mo", "6M"),
        ("ytd", "YTD"), ("1y", "1Y"),
    ]
    period_labels = [lbl for _, lbl in period_options]
    period_codes  = [code for code, _ in period_options]

    pcol, _ = st.columns([1, 3])
    with pcol:
        period_lbl = st.radio(
            "PERIOD",
            options=period_labels,
            index=2,                       # default → 6M
            horizontal=True,
            key="strat_hist_period",
        )
    selected_period = period_codes[period_labels.index(period_lbl)]

    # Multiselect — Arabic label, defaults to the Ultra Growth pair
    all_portfolio_names = [cfg["name"] for cfg in mod_robo.PORTFOLIOS]
    default_selection = [
        n for n in ["أبيان النمو الفائق", "محفظة تمرة: النمو الفائق"]
        if n in all_portfolio_names
    ]

    selected_names = st.multiselect(
        "اختر المحافظ للمقارنة",
        options=all_portfolio_names,
        default=default_selection,
        key="strat_hist_multiselect",
        help="اختر محفظة واحدة أو أكثر لرسم العائد التراكمي عبر الزمن",
    )

    if not selected_names:
        st.info("اختر محفظة واحدة على الأقل لعرض الرسم البياني.")
        return

    # Fetch (API → in-process fallback). The 6mo / 8 portfolios payload is
    # cached for an hour at the backend, so toggling the multiselect is cheap.
    with st.spinner(f"Fetching {selected_period} historical closes..."):
        try:
            hist_payload, hist_source = _fetch_historical_payload(selected_period)
        except Exception as exc:
            st.error(f"Could not fetch historical data: {exc}")
            return

    for err in hist_payload.get("errors", []):
        st.warning(err)

    series_records = hist_payload.get("series", [])
    if not series_records:
        st.error("No historical data available for the selected period.")
        return

    # Status caption — date range + source
    src_label = "FastAPI" if hist_source == "api" else "in-process (API offline)"
    st.markdown(
        f"<div style='font-family:JetBrains Mono,monospace; font-size:0.65rem; "
        f"color:#6b7290; letter-spacing:0.06em; margin:-4px 0 8px;'>"
        f"Range: <b style='color:#a8aebf;'>{hist_payload.get('start_date')}</b> → "
        f"<b style='color:#a8aebf;'>{hist_payload.get('as_of_date')}</b>  ·  "
        f"Source: {src_label}  ·  "
        f"{len(selected_names)} of {len(all_portfolio_names)} portfolios shown"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Build & render the spline line chart
    fig = _historical_line_chart(series_records, selected_names)
    if fig is None:
        st.info("Selected portfolios have no overlapping data in this period.")
        return
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # Per-portfolio summary cards below the chart
    summary_by_name = {p["name"]: p for p in hist_payload.get("portfolios", [])}
    summary_rows = [summary_by_name[n] for n in selected_names if n in summary_by_name]
    if summary_rows:
        cols = st.columns(min(len(summary_rows), 4))
        for col, p in zip(cols, summary_rows):
            with col:
                final = p.get("final_return_pct")
                tier_label = mod_robo.TIER_LABELS_EN.get(p.get("risk_tier", ""), "")
                tag_color = CLR_SECONDARY if p.get("is_house") else CLR_VIOLET
                tag_text = "★ HOUSE" if p.get("is_house") else "BENCHMARK"
                st.markdown(
                    f"<div style='font-family:JetBrains Mono,monospace; font-size:0.58rem; "
                    f"font-weight:700; letter-spacing:0.18em; color:{tag_color}; "
                    f"margin-bottom:6px;'>{tag_text} · {tier_label}</div>",
                    unsafe_allow_html=True,
                )
                _kpi_card(
                    p["name"],
                    f"{final:+.2f}%" if final is not None else "N/A",
                    delta=f"{selected_period.upper()} cumulative",
                    delta_color="off",
                    accent="positive" if (final or 0) > 0 else ("negative" if (final or 0) < 0 else "primary"),
                )




# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — تحليل المحافظ (Arabic Portfolio Composition drill-down)
# ══════════════════════════════════════════════════════════════════════════════

# Terminal palette mapped to donut slices (reused between pies).
_DONUT_COLORS = ["#69f6b8", "#85adff", "#ffb148", "#c8a2ff", "#58e7ab", "#699cff", "#ff9bb0"]


def _fetch_composition_via_api(name: str) -> dict | None:
    """Try the FastAPI backend; return None if unreachable or 4xx/5xx."""
    try:
        import urllib.parse as _up
        import requests  # type: ignore
        encoded = _up.quote(name, safe="")
        resp = requests.get(f"{API_URL}/api/portfolio/{encoded}", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def page_portfolio_composition(embedded: bool = False):
    # RTL scope for this page only
    st.markdown(
        "<style>"
        "div[data-testid='stAppViewContainer'] div.block-container "
        "[data-rtl-scope='portfolio-drilldown'] { direction: rtl; text-align: right; }"
        "[data-rtl-scope='portfolio-drilldown'] .stMetric { text-align: right; }"
        "[data-rtl-scope='portfolio-drilldown'] .stMetric label { direction: rtl; }"
        "</style>",
        unsafe_allow_html=True,
    )
    st.markdown("<div data-rtl-scope='portfolio-drilldown'>", unsafe_allow_html=True)

    # Header (RTL)
    if embedded:
        st.markdown(
            "<h3 style='margin:0 0 6px; font-size:1.05rem; direction:rtl; text-align:right;'>"
            "تحليل المحافظ</h3>"
            "<p style='font-family:JetBrains Mono,monospace; font-size:0.65rem; "
            "color:#6b7290; direction:rtl; text-align:right; margin:0 0 16px;'>"
            "تفصيل مكونات المحافظ داخل مساحة Portfolio Workspace</p>",
            unsafe_allow_html=True,
        )
    else:
        today_str = date.today().strftime("%b %d, %Y").upper()
        st.markdown(
            f"<div style='display:flex; justify-content:space-between; align-items:center; "
            f"margin-bottom:24px; direction:rtl;'>"
            f"<div>"
            f"<h2 style='margin:0; font-size:1.25rem; font-weight:700; "
            f"letter-spacing:-0.02em;'>تحليل المحافظ</h2>"
            f"<p style='font-family:Space Grotesk,monospace; font-size:0.6rem; "
            f"color:#6f7588; letter-spacing:0.1em; text-transform:uppercase; "
            f"margin:2px 0 0;'>PORTFOLIO COMPOSITION DRILL-DOWN</p>"
            f"</div>"
            f"<span style='font-family:Space Grotesk,monospace; font-size:0.6rem; "
            f"color:#6f7588; letter-spacing:0.05em;'>{today_str}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Selector ──────────────────────────────────────────────────────────
    names = mpc.list_portfolio_names()
    if not names:
        st.error("لا توجد محافظ معرّفة في قاعدة البيانات.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    selected_name = st.selectbox(
        "اختر محفظة لعرض مكوناتها",
        options=names,
        index=0,
        key="arabic_portfolio_select",
    )

    # Prefer the HTTP endpoint; fall back to in-process lookup if the API is offline.
    composition = _fetch_composition_via_api(selected_name)
    source = "FastAPI"
    if composition is None:
        composition = mpc.get_portfolio_composition(selected_name)
        source = "مباشر (API غير متاح)"

    if composition is None:
        st.error(f"لم يتم العثور على المحفظة: {selected_name}")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── Meta row ──────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-family:Space Grotesk,monospace; font-size:0.65rem; "
        f"letter-spacing:0.08em; color:#6f7588; margin-bottom:14px;'>"
        f"<span style='color:{CLR_SECONDARY};'>●</span> &nbsp;"
        f"{composition.get('risk_ar','')}  &middot;  {composition.get('tag','')}  &middot;  "
        f"المصدر: {source}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Metrics ───────────────────────────────────────────────────────────
    total_weight = composition["total_weight_pct"]
    num_funds    = composition["num_funds"]
    is_valid     = composition["is_valid"]

    weight_color = CLR_SECONDARY if is_valid else CLR_ERROR
    weight_icon  = "✓" if is_valid else "⚠"

    m1, m2 = st.columns(2)

    with m1:
        st.markdown(
            f"<div style='font-family:Space Grotesk,monospace; font-size:0.55rem; "
            f"letter-spacing:0.2em; color:{weight_color}; margin-bottom:4px; "
            f"direction:rtl;'>{weight_icon} إجمالي الوزن</div>"
            f"<div style='font-family:Space Grotesk,monospace; font-size:2rem; "
            f"font-weight:700; color:{weight_color}; line-height:1; direction:ltr; "
            f"text-align:right;'>{total_weight:.1f}%</div>"
            f"<div style='font-family:Inter,sans-serif; font-size:0.75rem; "
            f"color:#a5aabf; margin-top:4px; direction:rtl;'>"
            f"إجمالي الوزن (%)"
            f"</div>",
            unsafe_allow_html=True,
        )

    with m2:
        st.markdown(
            f"<div style='font-family:Space Grotesk,monospace; font-size:0.55rem; "
            f"letter-spacing:0.2em; color:{CLR_PRIMARY}; margin-bottom:4px; "
            f"direction:rtl;'>FUNDS</div>"
            f"<div style='font-family:Space Grotesk,monospace; font-size:2rem; "
            f"font-weight:700; color:{CLR_PRIMARY}; line-height:1; direction:ltr; "
            f"text-align:right;'>{num_funds}</div>"
            f"<div style='font-family:Inter,sans-serif; font-size:0.75rem; "
            f"color:#a5aabf; margin-top:4px; direction:rtl;'>"
            f"عدد الصناديق"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Donut chart ───────────────────────────────────────────────────────
    holdings = composition["holdings"]
    donut_df = pd.DataFrame([
        {
            "الرمز": h["ticker"],
            "اسم الأداة": h["name_ar"],
            "الوزن": h["weight_pct"],
            "label": f"{h['ticker']} ({h['weight_pct']:.0f}%)",
        }
        for h in holdings
    ])

    donut = px.pie(
        donut_df,
        names="label",
        values="الوزن",
        hole=0.45,
        color_discrete_sequence=_DONUT_COLORS[:len(holdings)],
    )
    donut.update_traces(
        textposition="outside",
        textinfo="label",
        textfont=dict(family="Space Grotesk, monospace", size=13, color="#e0e5fb"),
        marker=dict(line=dict(color="#080e1d", width=2)),
        hovertemplate="<b>%{label}</b><br>الوزن: %{value:.1f}%<extra></extra>",
        pull=[0.02] * len(holdings),
    )
    donut.update_layout(
        **{k: v for k, v in PLOTLY_LAYOUT.items() if k not in ("xaxis", "yaxis")},
        height=420,
        showlegend=False,
        title=dict(
            text="توزيع الأصول (%)",
            font=dict(family="Inter, sans-serif", size=15, color="#e0e5fb"),
            x=0.98, xanchor="right",
        ),
        annotations=[dict(
            text=f"<b>{num_funds}</b><br><span style='font-size:10px; color:#a5aabf;'>صناديق</span>",
            x=0.5, y=0.5, font_size=22, font_color="#e0e5fb", showarrow=False,
            font=dict(family="Space Grotesk, monospace"),
        )],
    )
    st.plotly_chart(donut, width="stretch", config={"displayModeBar": False})

    # ── Details table ─────────────────────────────────────────────────────
    st.markdown(
        "<h3 style='font-family:Inter; font-size:0.95rem; font-weight:600; "
        "color:#e0e5fb; margin-top:24px; margin-bottom:12px; direction:rtl; "
        "text-align:right;'>تفاصيل المكونات</h3>",
        unsafe_allow_html=True,
    )

    details_df = pd.DataFrame([
        {
            "الرمز":               h["ticker"],
            "اسم الأداة الاستثمارية": h["name_ar"],
            "الوزن (%)":           f"{h['weight_pct']:.1f}%",
        }
        for h in holdings
    ])
    st.dataframe(
        details_df,
        width="stretch",
        hide_index=True,
        height=min(320, 40 + 35 * len(details_df)),
    )

    st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — PORTFOLIO WORKSPACE
# ══════════════════════════════════════════════════════════════════════════════

def page_portfolio_workspace():
    _page_header(
        "Portfolio Workspace",
        "Funds, robo portfolios, strategic benchmarks, and composition analysis",
    )

    fund_df = mod_funds.load_nav_data()
    fund_count = 0 if fund_df.empty else fund_df["fund_name"].nunique()
    robo_count = len(mod_robo.PORTFOLIOS)
    ticker_count = len(mod_robo.unique_tickers())
    composition_count = len(mpc.list_portfolio_names())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi_card("Tracked Funds", f"{fund_count:,}", delta="NAV history universe", delta_color="off", accent="primary")
    with k2:
        _kpi_card("Robo Portfolios", f"{robo_count:,}", delta=f"{ticker_count} ETF / equity tickers", delta_color="off", accent="positive")
    with k3:
        _kpi_card("Risk Tiers", f"{len(mod_robo.TIER_ORDER):,}", delta="Strategic benchmark ladder", delta_color="off", accent="violet")
    with k4:
        _kpi_card("Composition Models", f"{composition_count:,}", delta="Arabic holdings drill-down", delta_color="off", accent="warn")

    st.markdown("")
    _decision_note(
        "Workspace logic",
        "Performance, robo comparison, strategic benchmarks, and holdings now live together so portfolio decisions stay in one flow.",
        "positive",
    )

    workspace_view = st.pills(
        "Portfolio view",
        options=[
            "Funds",
            "Robo Comparison",
            "Strategic Benchmarks",
            "Composition",
        ],
        default="Funds",
        key="portfolio_workspace_view",
    )
    workspace_view = workspace_view or "Funds"

    if workspace_view == "Funds":
        page_funds(embedded=True)
    elif workspace_view == "Robo Comparison":
        page_robo(embedded=True)
    elif workspace_view == "Strategic Benchmarks":
        page_custom_robo(embedded=True)
    elif workspace_view == "Composition":
        page_portfolio_composition(embedded=True)


if page == "Overview":
    page_overview()
elif page == "Macro & Inflation":
    page_macro()
elif page == "الإيجارات":
    page_rents()
elif page == "العقار":
    page_property()
elif page == "Portfolio Workspace":
    page_portfolio_workspace()
elif page == "Foreign Liquidity Radar":
    page_liquidity()


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    "<hr style='border-color:rgba(66,72,89,0.12); margin-top:40px;'>"
    "<p style='text-align:center; font-family:Space Grotesk,monospace; font-size:0.55rem; "
    "color:#424859; letter-spacing:0.1em; text-transform:uppercase;'>"
    "Terminal v1.0 &nbsp;&mdash;&nbsp; Sovereign Intelligence Platform "
    "&nbsp;&mdash;&nbsp; Data Sources: inflation_index.db &middot; mutual_funds.db &middot; liquidity_radar.db"
    "</p>",
    unsafe_allow_html=True,
)
