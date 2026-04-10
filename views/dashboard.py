import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

try:
    from nsepython import nse_eq, index_pe_pb_div
except ImportError:
    nse_eq = None
    index_pe_pb_div = None

from datetime import datetime, timedelta

st.title("Dashboard")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

# Cache bust button
if st.button("🔄 Refresh Data", help="Reload live prices from NSE"):
    for k in list(st.session_state.keys()):
        if k.startswith("port_df_"):
            del st.session_state[k]
    st.cache_data.clear()
    st.rerun()

# -----------------------------------------------
# Cache helpers
# -----------------------------------------------
@st.cache_data(ttl=18000)
def _fetch_nav_data_cached():
    import urllib.request
    import ssl
    req = urllib.request.Request(
        "https://www.amfiindia.com/spages/NAVAll.txt",
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=context) as response:
        return pd.read_csv(
            response,
            sep=";", header=None,
            names=["scheme_code", "isin1", "isin2", "scheme_name", "nav", "date"],
            on_bad_lines="skip"
        )

def load_nav_data():
    try:
        return _fetch_nav_data_cached()
    except Exception:
        return pd.DataFrame()


def get_nav(nav_df, fund_name):
    if nav_df.empty or not fund_name:
        return 0.0
        
    res = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav"]]
    if not res.empty:
        return float(res.iloc[0]["nav"])
        
    res = nav_df.loc[nav_df["scheme_name"].str.lower() == fund_name.lower(), ["nav"]]
    if not res.empty:
        return float(res.iloc[0]["nav"])
        
    short_name = fund_name[:15].lower()
    res = nav_df.loc[nav_df["scheme_name"].str.lower().str.contains(short_name, na=False, regex=False), ["nav"]]
    if not res.empty:
        return float(res.iloc[0]["nav"])
        
    return 0.0


@st.cache_data(ttl=600)
def get_stock_info(symbol):
    """Returns (price, pe_ratio) for a stock."""
    price = None
    pe = None

    if nse_eq:
        try:
            quote = nse_eq(symbol)
            if quote and 'priceInfo' in quote and 'lastPrice' in quote['priceInfo']:
                price = float(quote["priceInfo"]["lastPrice"])
            
            if quote and 'metadata' in quote:
                md = quote['metadata']
                # Try multiple possible keys for PE
                pe_raw = md.get('pdSymbolPe') or md.get('pdSectorPe') or md.get('pe')
                if pe_raw is not None:
                    pe = float(pe_raw)
            if price and price > 0:
                return price, pe
        except Exception:
            pass

    # Yahoo Finance Fallback if NSE failed or returned no valid price
    import urllib.request
    import urllib.parse
    import json
    import ssl
    
    for suffix in [".NS", ".BO", ""]:
        try:
            encoded_sym = urllib.parse.quote(symbol)
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded_sym}{suffix}?interval=1d&range=1d"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get("chart", {}).get("result"):
                    meta = data["chart"]["result"][0]["meta"]
                    fallback_price = float(meta.get("regularMarketPrice", 0.0))
                    if fallback_price and fallback_price > 0:
                        return fallback_price, pe
        except Exception:
            continue
            
    return price or 0.0, pe


@st.cache_data(ttl=86400) # Cache for 24 hours
def get_index_pe(index_name):
    """Fetches the latest PE for an index using nsepython."""
    if not index_pe_pb_div:
        return 0.0
    try:
        # Fetch last 10 days to ensure we get a valid record
        end = datetime.now().strftime('%d-%b-%Y')
        start = (datetime.now() - timedelta(days=10)).strftime('%d-%b-%Y')
        df = index_pe_pb_div(index_name, start, end)
        if df is not None and not df.empty:
            # Column is 'pe' (lowercase) in recent nsepython versions
            latest_pe = df.iloc[0].get('pe') or df.iloc[0].get('PE')
            if latest_pe is not None:
                return float(latest_pe)
    except Exception:
        pass
    return 0.0


# -----------------------------------------------
# Load data
# -----------------------------------------------
with st.spinner("Loading dashboard data..."):
    db_stocks        = db.fetch_stocks()
    open_tx          = db.fetch_open_transactions()
    db_stock_allocs  = db.fetch_stock_allocations()
    db_sector_allocs = db.fetch_allocations()
    db_plans         = db.fetch_investment_plan()
    nav_df           = load_nav_data()

plans_list      = db_plans if isinstance(db_plans, list) else ([db_plans] if db_plans else [])
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

# Build fast lookups
stocks_map = {s["Symbol"]: s for s in db_stocks}  # Symbol -> stock info

# Aggregate open transactions by (Portfolio, Symbol)
tx_agg = {}   # (portfolio, symbol) -> {Qty, InvestedTotal}
for tx in open_tx:
    port = tx.get("Portfolio", "")
    sym  = tx.get("Symbol", "")
    if not sym:
        continue
    key = (port, sym)
    if key not in tx_agg:
        tx_agg[key] = {"Qty": 0.0, "InvestedTotal": 0.0}
    qty      = float(tx.get("Qty", 0))
    buy_avg  = float(tx.get("BuyAvg", 0))
    tx_agg[key]["Qty"]           += qty
    tx_agg[key]["InvestedTotal"] += qty * buy_avg


def live_price(stock_info):
    """Returns the live price for a stock/MF."""
    sym      = stock_info.get("Symbol", "")
    name     = stock_info.get("Name", "")
    is_eq    = stock_info.get("Equity", True)
    is_lst   = stock_info.get("Listed", True)
    if not is_lst:
        return float(stock_info.get("LTP") or 0.0), None
    if is_eq:
        return get_stock_info(sym)
    return get_nav(nav_df, sym), None


def build_portfolio_df(port_name):
    """Build a summary DataFrame for a single portfolio."""
    # Sector allocation % lookup for this portfolio
    sector_alloc_pct = {
        a["Sector"]: float(a.get("Allocation", 0) or 0)
        for a in db_sector_allocs
        if a.get("Portfolio") == port_name and a.get("Sector")
    }

    # Stocks tagged to this portfolio with allocation > 0
    alloc_map = {
        a["Symbol"]: a["Allocation"]
        for a in db_stock_allocs
        if a.get("Portfolio") == port_name
        and a.get("Symbol")
        and (a.get("Allocation") or 0) > 0
    }

    rows = []
    for sym, target_alloc in alloc_map.items():
        stock_info   = stocks_map.get(sym, {})
        sector       = stock_info.get("Sector", "Unknown")
        name         = stock_info.get("Name", sym)
        agg          = tx_agg.get((port_name, sym), {"Qty": 0.0, "InvestedTotal": 0.0})
        qty          = agg["Qty"]
        invested     = agg["InvestedTotal"]
        price, pe    = live_price(stock_info)
        curr_val     = qty * price
        s_alloc_pct  = sector_alloc_pct.get(sector, 0.0)
        rows.append({
            "Sector":           sector,
            "Symbol":           sym,
            "Name":             name,
            "Portfolio":        port_name,
            "Target Alloc %":   float(target_alloc),
            "Sector Alloc %":   s_alloc_pct,
            "Qty":              qty,
            "Invested":         invested,
            "Live Price":       price,
            "PE Ratio":         pe,
            "Current Value":    curr_val,
        })

    if not rows:
        return pd.DataFrame(columns=["Sector","Symbol","Name","Target Alloc %","Sector Alloc %",
                                     "Qty","Invested","Live Price", "PE Ratio", "Current Value"])
    return pd.DataFrame(rows).sort_values(["Sector","Symbol"]).reset_index(drop=True)


def build_investment_bar_df(port_names_filter=None):
    """Build Portfolio vs Invested vs Expected DataFrame for bar chart.
    If port_names_filter is None, include all portfolios.
    """
    ports = port_names_filter if port_names_filter else portfolio_names
    rows = []
    for port in ports:
        # Current invested = sum of Qty * BuyAvg for all open txns in this portfolio
        curr = sum(
            tx_agg.get((port, a["Symbol"]), {"InvestedTotal": 0.0})["InvestedTotal"]
            for a in db_stock_allocs
            if a.get("Portfolio") == port and a.get("Symbol")
            and (a.get("Allocation") or 0) > 0
        )
        plan = next((p for p in plans_list if p.get("Portfolio") == port), {})
        expected = (
            float(plan.get("Current Invested Amount", 0) or 0)
            + float(plan.get("Monthly SIP", 0) or 0) * float(plan.get("Number of Months", 0) or 0)
        )
        rows.append({"Portfolio": port, "Current Invested": curr, "Expected Investment": expected})
    return pd.DataFrame(rows)


def render_investment_bar(bar_df):
    """Render a grouped bar chart of Current Invested vs Expected Investment."""
    if bar_df.empty:
        return
    st.subheader("Current Invested vs Expected Investment")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Current Invested",
        x=bar_df["Portfolio"],
        y=bar_df["Current Invested"],
        marker_color="#4C78A8",
        text=bar_df["Current Invested"].apply(lambda v: f"₹{v:,.0f}"),
        textposition="outside"
    ))
    fig.add_trace(go.Bar(
        name="Expected Investment",
        x=bar_df["Portfolio"],
        y=bar_df["Expected Investment"],
        marker_color="#F58518",
        text=bar_df["Expected Investment"].apply(lambda v: f"₹{v:,.0f}"),
        textposition="outside"
    ))
    fig.update_layout(
        barmode="group",
        yaxis_title="Amount (₹)",
        xaxis_title="Portfolio",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#E2E8F0")),
        margin=dict(t=60, b=40),
        height=420,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#E2E8F0"),
        xaxis=dict(gridcolor="#2D333B"),
        yaxis=dict(gridcolor="#2D333B")
    )
    st.plotly_chart(fig, use_container_width=True)


def sector_invested_df(port_name=None):
    """Return a DataFrame of Sector vs Invested for pie chart.
    If port_name is None, aggregate across ALL portfolios.
    """
    data = {}
    ports = [port_name] if port_name else portfolio_names
    for port in ports:
        alloc_syms = {
            a["Symbol"]
            for a in db_stock_allocs
            if a.get("Portfolio") == port
            and a.get("Symbol")
            and (a.get("Allocation") or 0) > 0
        }
        for sym in alloc_syms:
            stock_info = stocks_map.get(sym, {})
            sector     = stock_info.get("Sector", "Unknown")
            agg        = tx_agg.get((port, sym), {"InvestedTotal": 0.0})
            invested   = agg["InvestedTotal"]
            data[sector] = data.get(sector, 0.0) + invested

    df = pd.DataFrame(list(data.items()), columns=["Sector", "Invested"])
    return df[df["Invested"] > 0].sort_values("Invested", ascending=False)


def render_summary_and_pie(df, sector_df, port_label, bar_df=None, metric_expected=0.0,
                           total_expected=0.0, port_expected_map=None):
    """Render metrics + bar + pie + stock bar + table for a given portfolio df."""
    # Ensure PE Ratio exists to handle stale session data
    if "PE Ratio" not in df.columns:
        df["PE Ratio"] = None

    total_invested    = df["Invested"].sum()
    total_curr_val    = df["Current Value"].sum()
    gain_loss         = total_curr_val - total_invested
    gain_loss_pct     = (gain_loss / total_invested * 100) if total_invested > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("💰 Total Invested",       f"₹{total_invested:,.2f}")
    m2.metric("🎯 Expected Investment",  f"₹{metric_expected:,.2f}")
    m3.metric("📈 Current Value",        f"₹{total_curr_val:,.2f}")
    m4.metric("📊 Gain / Loss",          f"₹{gain_loss:,.2f}", delta=f"{gain_loss_pct:.2f}%")

    # ---- PE Ratio Metrics ----
    # Calculate Average PE Ratio (Exclude DEBT and ETF/INDEX FUND)
    pe_filtered = df[
        (df["PE Ratio"].notnull()) & 
        (~df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
    ]
    avg_pe = pe_filtered["PE Ratio"].mean() if not pe_filtered.empty else 0.0

    st.markdown("### 📊 Valuation & Index Comparison")
    
    # Fetch Index PEs
    niphy_pe = get_index_pe("NIFTY 50")
    bank_pe = get_index_pe("NIFTY BANK")

    col_pe1, col_pe2 = st.columns([1, 2])
    with col_pe1:
        st.metric("🎯 Portfolio Avg PE", f"{avg_pe:.2f}")
    
    with col_pe2:
        index_data = {
            "Index": ["NIFTY 50", "NIFTY BANK"],
            "Current PE": [f"{niphy_pe:.2f}" if niphy_pe > 0 else "N/A", 
                           f"{bank_pe:.2f}" if bank_pe > 0 else "N/A"]
        }
        st.table(pd.DataFrame(index_data))

    st.divider()

    # Bar chart — Current Invested vs Expected
    if bar_df is not None and not bar_df.empty:
        render_investment_bar(bar_df)
        st.divider()

    # Pie chart — sector allocation + drilldown
    if not sector_df.empty:
        sess_key = f"drill_sector_{port_label}"
        if sess_key not in st.session_state:
            st.session_state[sess_key] = None

        selected_sector = st.session_state[sess_key]

        # ── Level 1: Sector Pie ──────────────────────────────────────────────
        if selected_sector is None:
            st.subheader("Sector Allocation (by Invested Amount)")

            chosen = st.selectbox(
                "🔍 Drill into Sector",
                options=["— Select —"] + sector_df["Sector"].tolist(),
                key=f"sel_{port_label}"
            )
            if chosen != "— Select —":
                st.session_state[sess_key] = chosen
                st.rerun()

            fig = go.Figure(go.Pie(
                labels=sector_df["Sector"].tolist(),
                values=sector_df["Invested"].tolist(),
                hole=0.4,
                marker_colors=px.colors.qualitative.Pastel,
                textinfo="label+percent",
                textposition="inside",
            ))
            fig.update_layout(
                showlegend=False,
                margin=dict(t=20, b=20, l=0, r=0),
                height=420,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#E2E8F0")
            )
            st.plotly_chart(fig, use_container_width=True)

        # ── Level 2: Stock Pie Drilldown ─────────────────────────────────────
        else:
            col_back, col_title = st.columns([1, 5])
            with col_back:
                if st.button("⬅ Back", key=f"back_{port_label}"):
                    st.session_state[sess_key] = None
                    st.rerun()
            with col_title:
                st.subheader(f"📂 {selected_sector} — Stock Breakdown")

            sector_stocks = df[(df["Sector"] == selected_sector) & (df["Invested"] > 0)]

            if sector_stocks.empty:
                st.info(f"No invested stocks in **{selected_sector}** yet.")
            else:
                fig_d = go.Figure(go.Pie(
                    labels=sector_stocks["Name"].tolist(),
                    values=sector_stocks["Invested"].tolist(),
                    hole=0.4,
                    marker_colors=px.colors.qualitative.Set2,
                    textinfo="label+percent",
                    textposition="inside",
                ))
                fig_d.update_layout(
                    showlegend=False,
                    margin=dict(t=20, b=20, l=0, r=0),
                    height=420,
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color="#E2E8F0")
                )
                st.plotly_chart(fig_d, use_container_width=True)

                # Per-stock metrics (% only, no trend delta)
                total = sector_stocks["Invested"].sum()
                mcols = st.columns(len(sector_stocks))
                for mc, row in zip(mcols, sector_stocks.itertuples()):
                    pct = row.Invested / total * 100 if total > 0 else 0
                    mc.metric(row.Name, f"{pct:.1f}%")

                # Summary table
                st.dataframe(
                    sector_stocks[["Name", "Target Alloc %", "Invested", "Current Value"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Target Alloc %": st.column_config.NumberColumn("Target %", format="%.2f%%"),
                        "Invested":       st.column_config.NumberColumn("Invested", format="₹ %.2f"),
                        "Current Value":  st.column_config.NumberColumn("Current Value", format="₹ %.2f"),
                    }
                )
    else:
        st.info("No invested data to display for the pie chart yet.")

    st.divider()

    # Asset table
    if not df.empty and df["Invested"].sum() > 0:
        st.subheader(f"Assets — {port_label}")
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Symbol":           None,
                "Portfolio":        None,
                "Target Alloc %":   None,
                "Sector Alloc %":   None,
                "Qty":              st.column_config.NumberColumn("Qty", format="%.4f"),
                "Invested":         st.column_config.NumberColumn("Invested", format="₹ %.2f"),
                "Live Price":       st.column_config.NumberColumn("Live Price", format="₹ %.2f"),
                "PE Ratio":         st.column_config.NumberColumn("PE Ratio", format="%.2f"),
                "Current Value":    st.column_config.NumberColumn("Current Value", format="₹ %.2f"),
            }
        )
    else:
        st.info(f"No asset data available for {port_label}.")

    # ---- Stock-level bar chart ----
    # Use port_expected_map for per-stock calculation (needed in Overall tab)
    # Otherwise fall back to total_expected (single portfolio tabs)
    can_show_stock_bar = (port_expected_map is not None and "Portfolio" in df.columns) or total_expected > 0
    if not df.empty and can_show_stock_bar:
        st.divider()
        st.subheader("Stock: Current Invested vs Expected")
        stock_bar = df[["Name", "Invested", "Sector Alloc %", "Target Alloc %"]].copy()

        if port_expected_map is not None and "Portfolio" in df.columns:
            # Per-stock expected using each stock's own portfolio expected
            stock_bar["Expected"] = df.apply(
                lambda row: (
                    port_expected_map.get(row["Portfolio"], 0.0)
                    * (row["Sector Alloc %"] / 100)
                    * (row["Target Alloc %"] / 100)
                ),
                axis=1
            )
        else:
            # Single portfolio tab: use total_expected directly
            stock_bar["Expected"] = (
                total_expected
                * (stock_bar["Sector Alloc %"] / 100)
                * (stock_bar["Target Alloc %"] / 100)
            )

        stock_bar = stock_bar[stock_bar["Invested"] > 0].sort_values("Invested", ascending=False)

        if not stock_bar.empty:
            fig_s = go.Figure()
            fig_s.add_trace(go.Bar(
                name="Current Invested",
                x=stock_bar["Name"],
                y=stock_bar["Invested"],
                marker_color="#4C78A8",
                text=stock_bar["Invested"].apply(lambda v: f"₹{v:,.0f}"),
                textposition="outside"
            ))
            fig_s.add_trace(go.Bar(
                name="Expected",
                x=stock_bar["Name"],
                y=stock_bar["Expected"],
                marker_color="#F58518",
                text=stock_bar["Expected"].apply(lambda v: f"₹{v:,.0f}"),
                textposition="outside"
            ))
            fig_s.update_layout(
                barmode="group",
                yaxis_title="Amount (₹)",
                xaxis_title="Stock",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#E2E8F0")),
                margin=dict(t=60, b=80),
                height=450,
                xaxis_tickangle=-35,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#E2E8F0"),
                xaxis=dict(gridcolor="#2D333B"),
                yaxis=dict(gridcolor="#2D333B")
            )
            st.plotly_chart(fig_s, use_container_width=True)


# -----------------------------------------------
# Tabs: Overall + one per portfolio
# -----------------------------------------------
if not portfolio_names:
    st.info("No investment portfolios found. Create one in the Build Portfolio page first.")
    st.stop()

# ---- Pre-load all portfolio DataFrames (cached in session_state) ----
# Live price calls are expensive; only fetch once per session (or after Refresh).
for _p in portfolio_names:
    _key = f"port_df_{_p}"
    if _key not in st.session_state:
        with st.spinner(f"Fetching prices for {_p}..."):
            st.session_state[_key] = build_portfolio_df(_p)

all_dfs    = [st.session_state[f"port_df_{p}"] for p in portfolio_names]
combined_df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

overall_bar_df   = build_investment_bar_df()
overall_expected = overall_bar_df["Expected Investment"].sum() if not overall_bar_df.empty else 0.0
port_exp_map     = {
    row["Portfolio"]: row["Expected Investment"]
    for _, row in overall_bar_df.iterrows()
} if not overall_bar_df.empty else {}

tab_labels = ["🌐 Overall Portfolio"] + portfolio_names
tabs = st.tabs(tab_labels)

# ---- Overall Tab ----
with tabs[0]:
    if combined_df.empty:
        st.info("No data across any portfolio yet.")
    else:
        overall_sector_df = sector_invested_df(port_name=None)
        render_summary_and_pie(combined_df, overall_sector_df, "All Portfolios",
                               bar_df=overall_bar_df,
                               metric_expected=overall_expected,
                               total_expected=0.0,
                               port_expected_map=port_exp_map)

# ---- Per-Portfolio Tabs ----
for i, port_name in enumerate(portfolio_names):
    with tabs[i + 1]:
        port_df      = st.session_state[f"port_df_{port_name}"]
        port_sec_df  = sector_invested_df(port_name=port_name)
        port_bar_df  = build_investment_bar_df(port_names_filter=[port_name])
        port_expected = float(port_bar_df["Expected Investment"].iloc[0]) if not port_bar_df.empty else 0.0

        if port_df.empty or port_df["Invested"].sum() == 0:
            st.info(f"No invested data found for **{port_name}**.")
        else:
            render_summary_and_pie(port_df, port_sec_df, port_name,
                                   bar_df=port_bar_df,
                                   metric_expected=port_expected,
                                   total_expected=port_expected)
