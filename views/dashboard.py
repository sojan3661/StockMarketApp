import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db
from concurrent.futures import ThreadPoolExecutor

try:
    import yfinance as yf
except ImportError:
    yf = None

from datetime import datetime, timedelta

st.title("Dashboard")

if "view_in_usd" not in st.session_state:
    st.session_state.view_in_usd = False

def toggle_usd():
    st.session_state.view_in_usd = not st.session_state.view_in_usd
    # Clear cached portfolio dfs so they rebuild with correct currency
    for k in list(st.session_state.keys()):
        if k.startswith("port_df_"):
            del st.session_state[k]

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

col1, col2 = st.columns([2, 8])
with col1:
    if st.button("🔄 Refresh Data", help="Reload live prices and allocations"):
        refresh_all_data()

with col2:
    st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
    st.checkbox("View in USD", value=st.session_state.view_in_usd, on_change=toggle_usd, key="dashboard_usd_cb")

# -----------------------------------------------
# Load data from central cache
# -----------------------------------------------
from Config.data_cache import get_global_app_data, refresh_all_data, get_stock_price, get_stock_info, get_nav, resolve_asset_ltp

app_data = get_global_app_data()
db_stocks          = app_data.get("stocks", [])
open_tx            = app_data.get("open_transactions", [])
db_stock_allocs    = app_data.get("stock_allocations", [])
db_sector_allocs   = app_data.get("allocations", [])
db_plans           = app_data.get("investment_plan", [])
db_currency_pairs  = app_data.get("currency_pairs", [])
nav_df             = app_data.get("nav_df", pd.DataFrame())


@st.cache_data(ttl=300)
def fetch_fx_rate(pair_symbol):
    """
    Fetch live FX rate for a Yahoo Finance currency pair symbol.
    e.g. 'USDINR=X', 'GBPINR=X', 'EURINR=X'
    Returns the rate as a float, or 0.0 on failure.
    """
    if not pair_symbol:
        return 0.0
    import urllib.request
    import urllib.parse
    import json
    import ssl

    try:
        encoded = urllib.parse.quote(pair_symbol)
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get("chart", {}).get("result"):
                meta = data["chart"]["result"][0]["meta"]
                rate = float(meta.get("regularMarketPrice", 0.0))
                if rate > 0:
                    return rate
    except Exception:
        pass
    return 0.0


@st.cache_data(ttl=86400)
def get_index_pe(index_name):
    """Fetches the latest PE for an index using yfinance."""
    if not yf:
        return 0.0
    try:
        symbol = "^NSEI" if index_name == "NIFTY 50" else "^NSEBANK"
        ticker = yf.Ticker(symbol)
        pe = ticker.info.get("trailingPE")
        if pe is not None:
            return float(pe)
    except Exception:
        pass
    return 0.0




plans_list      = db_plans if isinstance(db_plans, list) else ([db_plans] if db_plans else [])
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

# Build fast lookups
stocks_map = {s["Symbol"]: s for s in db_stocks}  # Symbol -> stock info

# country (uppercase) -> CurrencyPair row
# e.g. {"INDIA": {"BaseCurrency": "INR", "PairCurrency": "USD", "Symbol": "USDINR=X"}, ...}
currency_pairs_map = {
    cp["Country"].upper(): cp
    for cp in (db_currency_pairs if isinstance(db_currency_pairs, list) else [])
    if cp.get("Country")
}

# Pre-fetch all FX rates keyed by Yahoo pair symbol (e.g. "USDINR=X" -> 84.52)
@st.cache_data(ttl=300)
def build_fx_cache(cp_tuples):
    """cp_tuples: tuple of (country, symbol) pairs (hashable for caching)."""
    rates = {}
    symbols_to_fetch = list({sym for _country, sym in cp_tuples if sym})
    if not symbols_to_fetch:
        return rates

    with ThreadPoolExecutor(max_workers=min(10, len(symbols_to_fetch))) as executor:
        fetched_rates = list(executor.map(fetch_fx_rate, symbols_to_fetch))
        for sym, rate in zip(symbols_to_fetch, fetched_rates):
            rates[sym] = rate
    return rates

_cp_tuples = tuple(sorted(
    (c, cp.get("Symbol", "")) for c, cp in currency_pairs_map.items()
))
fx_rates = build_fx_cache(_cp_tuples)


def _inr_per_unit(country):
    """
    How many INR = 1 unit of country's base currency.
    For INDIA returns 1.0 (already INR).
    For others: CurrencyPair table first, then built-in fallback map.
    """
    country = (country or "INDIA").upper()
    if country == "INDIA":
        return 1.0

    # 1. CurrencyPair table lookup
    cp  = currency_pairs_map.get(country, {})
    sym = cp.get("Symbol", "")
    if sym:
        rate = fx_rates.get(sym) or fetch_fx_rate(sym)
        if rate and rate > 0:
            return rate

    # 2. Built-in country -> Yahoo FX symbol fallback
    _country_to_fx = {
        "USA": "USDINR=X", "US": "USDINR=X",
        "UK": "GBPINR=X",  "GB": "GBPINR=X",
        "EU": "EURINR=X",  "EUROPE": "EURINR=X",
        "HK": "HKDINR=X",  "HONG KONG": "HKDINR=X",
        "JP": "JPYINR=X",  "JAPAN": "JPYINR=X",
        "AU": "AUDINR=X",  "AUSTRALIA": "AUDINR=X",
        "CA": "CADINR=X",  "CANADA": "CADINR=X",
        "CH": "CHFINR=X",  "SWITZERLAND": "CHFINR=X",
    }
    fallback_sym = _country_to_fx.get(country)
    if fallback_sym:
        rate = fetch_fx_rate(fallback_sym)
        if rate and rate > 0:
            return rate

    # 3. Nothing worked - warn and show native price
    st.warning(f"⚠️ No FX rate found for country '{country}'. Live Price shown in native currency.")
    return 1.0


def _usdinr():
    """How many INR = 1 USD. Used to convert any INR amount -> USD."""
    # Look for a pair whose Symbol contains USDINR
    for country, cp in currency_pairs_map.items():
        sym = (cp.get("Symbol") or "").upper()
        if "USDINR" in sym:
            rate = fx_rates.get(cp["Symbol"], 0.0)
            if rate > 0:
                return rate
    # Direct fetch as fallback
    return fetch_fx_rate("USDINR=X") or 84.0


def convert_price(native_price, country):
    use_usd = st.session_state.get("view_in_usd", False)
    country = (country or "INDIA").upper()
    native_price = float(native_price or 0.0)

    if not use_usd:
        # INR display
        return native_price * _inr_per_unit(country)   # 1.0 for INDIA
    else:
        # USD display
        usd_inr = _usdinr()
        if usd_inr <= 0:
            usd_inr = 84.0
        inr_price = native_price * _inr_per_unit(country)
        return inr_price / usd_inr


# Aggregate open transactions by (Portfolio, Symbol)
tx_agg = {}   # (portfolio, symbol) -> {Qty, InvestedTotal, InvestedTotalUSD}
for tx in open_tx:
    port = tx.get("Portfolio", "")
    sym  = tx.get("Symbol", "")
    if not sym:
        continue
    key = (port, sym)
    if key not in tx_agg:
        tx_agg[key] = {"Qty": 0.0, "InvestedTotal": 0.0, "InvestedTotalUSD": 0.0, "BuyValueTotal": 0.0}

    # Only count invested amount for open (unsold) transactions
    sell_avg  = tx.get("SellAvg")
    sell_date = tx.get("SellDate")
    is_open   = (
        (sell_avg  is None or sell_avg  == "" or (isinstance(sell_avg,  float) and pd.isna(sell_avg)))  and
        (sell_date is None or sell_date == "" or (isinstance(sell_date, float) and pd.isna(sell_date)))
    )

    qty     = float(tx.get("Qty", 0))
    buy_val = float(tx.get("BuyValue", 0) or 0)    # INR total for this transaction
    buy_usd = float(tx.get("BuyValueUSD", 0) or 0) # USD total for this transaction
    buy_avg = float(tx.get("BuyAvg", 0))

    if is_open:
        tx_agg[key]["Qty"] += qty
        # Use BuyValue directly if available, else fall back to qty * BuyAvg
        tx_agg[key]["InvestedTotal"]    += buy_val if buy_val > 0 else qty * buy_avg
        tx_agg[key]["InvestedTotalUSD"] += buy_usd
        tx_agg[key]["BuyValueTotal"]    += buy_val


def live_price(stock_info):
    """
    Returns (display_price, pe_ratio).
    display_price is already converted to the active currency (INR or USD).
    """
    sym     = stock_info.get("Symbol", "")
    is_eq   = stock_info.get("Equity", True)
    is_lst  = stock_info.get("Listed", True)
    country = (stock_info.get("Country") or "INDIA").upper()

    if not is_lst:
        native = float(stock_info.get("LTP") or 0.0)
        return convert_price(native, country), None

    if is_eq:
        native, pe = get_stock_info(sym)
    else:
        native, pe = get_nav(nav_df, sym, stock_info.get("Name")), None

    return convert_price(native, country), pe


def build_portfolio_df(port_name):
    """Build a summary DataFrame for a single portfolio."""
    use_usd = st.session_state.get("view_in_usd", False)

    sector_alloc_pct = {
        a["Sector"]: float(a.get("Allocation", 0) or 0)
        for a in db_sector_allocs
        if a.get("Portfolio") == port_name and a.get("Sector")
    }

    alloc_map = {
        a["Symbol"]: float(a.get("Allocation", 0) or 0)
        for a in db_stock_allocs
        if a.get("Portfolio") == port_name
        and a.get("Symbol")
    }

    all_syms = set()
    for sym, target_alloc in alloc_map.items():
        if target_alloc > 0:
            all_syms.add(sym)

    for (p, sym), agg in tx_agg.items():
        if p == port_name and (agg.get("Qty", 0) > 0 or agg.get("InvestedTotal", 0) > 0):
            all_syms.add(sym)

    rows = []
    for sym in sorted(all_syms):
        target_alloc = alloc_map.get(sym, 0.0)
        stock_info  = stocks_map.get(sym, {})
        sector      = stock_info.get("Sector", "Unknown")
        name        = stock_info.get("Name", sym)
        country     = (stock_info.get("Country") or "INDIA").upper()
        agg         = tx_agg.get((port_name, sym), {"Qty": 0.0, "InvestedTotal": 0.0, "InvestedTotalUSD": 0.0})
        qty         = agg["Qty"]
        invested    = agg["InvestedTotalUSD"] if use_usd else agg["InvestedTotal"]
        price, pe   = live_price(stock_info)    # already in display currency
        curr_val    = qty * price
        s_alloc_pct = sector_alloc_pct.get(sector, 0.0)
        rows.append({
            "Sector":         sector,
            "Symbol":         sym,
            "Name":           name,
            "Country":        country,
            "Portfolio":      port_name,
            "Target Alloc %": float(target_alloc),
            "Sector Alloc %": s_alloc_pct,
            "Qty":            qty,
            "Invested":       invested,
            "Live Price":     price,
            "PE Ratio":       pe,
            "Current Value":  curr_val,
            "Running P&L":    curr_val - invested,
            "Running P&L %":  ((curr_val - invested) / invested * 100) if invested else 0.0,
        })

    if not rows:
        return pd.DataFrame(columns=["Sector", "Symbol", "Name", "Country", "Portfolio",
                                     "Target Alloc %", "Sector Alloc %",
                                     "Qty", "Invested", "Live Price", "PE Ratio", "Current Value", "Running P&L", "Running P&L %"])
    return pd.DataFrame(rows).sort_values(["Sector", "Symbol"]).reset_index(drop=True)


def build_investment_bar_df(port_names_filter=None):
    """Build Portfolio vs Invested vs Expected vs Target DataFrame for bar chart (always INR)."""
    ports = port_names_filter if port_names_filter else portfolio_names
    rows = []
    for port in ports:
        curr = sum(
            float(tx.get("BuyValue", 0) or 0)
            for tx in open_tx
            if tx.get("Portfolio") == port
        )
        plan = next((p for p in plans_list if p.get("Portfolio") == port), {})
        expected = (
            float(plan.get("Current Invested Amount", 0) or 0)
            + float(plan.get("Monthly SIP", 0) or 0) * float(plan.get("Number of Months", 0) or 0)
        )
        target = float(plan.get("Target", 0) or 0)
        rows.append({
            "Portfolio": port,
            "Current Invested": curr,
            "Expected Investment": expected,
            "Target": target
        })
    return pd.DataFrame(rows)


def render_investment_bar(bar_df):
    """Render Current Invested vs Expected Investment visualization with toggle (Bar Chart vs Portfolio Allocation Pie Chart)."""
    if bar_df.empty:
        return

    col_title, col_toggle = st.columns([6, 4])

    with col_toggle:
        st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
        show_pie = st.toggle("Portfolio Allocation", value=False, key="overall_investment_pie_toggle")

    with col_title:
        if show_pie:
            st.subheader("Portfolio Allocation")
        else:
            st.subheader("Current Invested vs Expected Investment")

    if not show_pie:
        # Bar Chart: Current Invested vs Expected Investment
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

    else:
        # Pie chart mode: show portfolio allocation
        alloc_metric = st.radio(
            "Select Metric:",
            options=["Current Invested", "Expected Investment", "Target"],
            horizontal=True,
            key="overall_pie_alloc_radio"
        )

        pie_val_col = alloc_metric
        pie_data = bar_df[bar_df[pie_val_col] > 0] if pie_val_col in bar_df.columns else pd.DataFrame()

        if pie_data.empty:
            st.info(f"No {pie_val_col} data available for portfolio pie chart.")
        else:
            fig_p = go.Figure(go.Pie(
                labels=pie_data["Portfolio"].tolist(),
                values=pie_data[pie_val_col].tolist(),
                hole=0.4,
                marker_colors=px.colors.qualitative.Pastel,
                textinfo="label+percent",
                textposition="inside",
            ))
            fig_p.update_layout(
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5, font=dict(color="#E2E8F0")),
                margin=dict(t=30, b=60, l=0, r=0),
                height=420,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#E2E8F0")
            )
            st.plotly_chart(fig_p, use_container_width=True)


def sector_invested_df(port_name=None):
    """Return a DataFrame of Sector vs Invested for pie chart."""
    use_usd      = st.session_state.get("view_in_usd", False)
    invested_key = "InvestedTotalUSD" if use_usd else "InvestedTotal"

    data = {}
    ports = [port_name] if port_name else portfolio_names
    for port in ports:
        alloc_syms = set()
        for a in db_stock_allocs:
            if a.get("Portfolio") == port and a.get("Symbol"):
                if float(a.get("Allocation", 0) or 0) > 0:
                    alloc_syms.add(a["Symbol"])
        for (p, sym), agg in tx_agg.items():
            if p == port and (agg.get("Qty", 0) > 0 or agg.get("InvestedTotal", 0) > 0):
                alloc_syms.add(sym)

        for sym in alloc_syms:
            stock_info = stocks_map.get(sym, {})
            sector     = stock_info.get("Sector", "Unknown")
            agg        = tx_agg.get((port, sym), {"InvestedTotal": 0.0, "InvestedTotalUSD": 0.0})
            invested   = agg[invested_key]
            data[sector] = data.get(sector, 0.0) + invested

    df = pd.DataFrame(list(data.items()), columns=["Sector", "Invested"])
    return df[df["Invested"] > 0].sort_values("Invested", ascending=False)


def render_summary_and_pie(df, sector_df, port_label, bar_df=None, metric_expected=0.0,
                           total_expected=0.0, port_expected_map=None):
    """Render metrics + bar + pie + stock bar + table for a given portfolio df."""
    use_usd         = st.session_state.get("view_in_usd", False)
    currency_symbol = "$" if use_usd else "₹"
    money_fmt       = f"{currency_symbol} %.2f"

    # Ensure PE Ratio exists to handle stale session data
    if "PE Ratio" not in df.columns:
        df["PE Ratio"] = None

    total_invested = df["Invested"].sum()
    total_curr_val = df["Current Value"].sum()
    gain_loss      = total_curr_val - total_invested
    gain_loss_pct  = (gain_loss / total_invested * 100) if total_invested > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("💰 Total Invested",      f"{currency_symbol}{total_invested:,.2f}")
    exp_display = (metric_expected / _usdinr()) if use_usd else metric_expected
    m2.metric("🎯 Expected Investment", f"{currency_symbol}{exp_display:,.2f}")
    m3.metric("📈 Current Value",       f"{currency_symbol}{total_curr_val:,.2f}")
    m4.metric("📊 Gain / Loss",         f"{currency_symbol}{gain_loss:,.2f}", delta=f"{gain_loss_pct:.2f}%")


    # Show live FX rates being used
    if use_usd:
        usd_inr = _usdinr()
        fx_display = [{"Currency Pair": "USD/INR", "Rate": f"₹{usd_inr:,.4f}"}]
        for country, cp in currency_pairs_map.items():
            if country == "INDIA":
                continue
            sym  = cp.get("Symbol", "")
            rate = fx_rates.get(sym, 0.0)
            base = cp.get("BaseCurrency", country)
            if rate > 0:
                fx_display.append({
                    "Currency Pair": f"{base}/INR → {base}/USD",
                    "Rate": f"₹{rate:,.4f} → ${rate / usd_inr:,.4f}"
                })
        if fx_display:
            with st.expander("💱 FX Rates in use"):
                st.table(pd.DataFrame(fx_display))
    else:
        # Show non-INR conversion rates used for non-Indian stocks
        non_india = [
            c for c in currency_pairs_map if c != "INDIA"
            # only show if any stock in df belongs to this country
            and "Country" in df.columns
            and df["Country"].str.upper().eq(c).any()
        ]
        if non_india:
            fx_display = []
            for country in non_india:
                cp   = currency_pairs_map[country]
                sym  = cp.get("Symbol", "")
                rate = fx_rates.get(sym, 0.0)
                base = cp.get("BaseCurrency", country)
                if rate > 0:
                    fx_display.append({"Currency Pair": f"{base}/INR", "Rate": f"₹{rate:,.4f}"})
            if fx_display:
                with st.expander("💱 FX Rates in use"):
                    st.table(pd.DataFrame(fx_display))

    st.divider()

    # Bar chart — Current Invested vs Expected (always INR)
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

                # Per-stock metrics
                total = sector_stocks["Invested"].sum()
                mcols = st.columns(len(sector_stocks))
                for mc, row in zip(mcols, sector_stocks.itertuples()):
                    pct = row.Invested / total * 100 if total > 0 else 0
                    mc.metric(row.Name, f"{pct:.1f}%")

                # Summary table
                st.dataframe(
                    sector_stocks[["Name", "Target Alloc %", "Invested", "Current Value", "Running P&L", "Running P&L %"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Target Alloc %": st.column_config.NumberColumn("Target %",      format="%.2f%%"),
                        "Invested":       st.column_config.NumberColumn("Invested",       format=money_fmt),
                        "Current Value":  st.column_config.NumberColumn("Current Value",  format=money_fmt),
                        "Running P&L":    st.column_config.NumberColumn("Running P&L",     format=money_fmt),
                        "Running P&L %":  st.column_config.NumberColumn("Running P&L %",   format="%.2f%%"),
                    }
                )
    else:
        st.info("No invested data to display for the pie chart yet.")



    # ---- Stock-level bar chart ----
    can_show_stock_bar = (port_expected_map is not None and "Portfolio" in df.columns) or total_expected > 0
    if not df.empty and can_show_stock_bar:
        st.divider()
        st.subheader("Stock: Current Invested vs Expected")
        stock_bar = df[["Name", "Invested", "Sector Alloc %", "Target Alloc %", "Portfolio"]].copy()

        if port_expected_map is not None and "Portfolio" in df.columns:
            stock_bar["Expected"] = df.apply(
                lambda row: (
                    port_expected_map.get(row["Portfolio"], 0.0)
                    * (row["Sector Alloc %"] / 100)
                    * (row["Target Alloc %"] / 100)
                ),
                axis=1
            )
        else:
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
                text=stock_bar["Invested"].apply(lambda v: f"{currency_symbol}{v:,.0f}"),
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
                yaxis_title=f"Amount ({currency_symbol})",
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
for _p in portfolio_names:
    _key = f"port_df_{_p}"
    if _key not in st.session_state:
        with st.spinner(f"Fetching prices for {_p}..."):
            st.session_state[_key] = build_portfolio_df(_p)

all_dfs     = [st.session_state[f"port_df_{p}"] for p in portfolio_names]
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
        port_df       = st.session_state[f"port_df_{port_name}"]
        port_sec_df   = sector_invested_df(port_name=port_name)
        port_bar_df   = build_investment_bar_df(port_names_filter=[port_name])
        port_expected = float(port_bar_df["Expected Investment"].iloc[0]) if not port_bar_df.empty else 0.0

        plan = next((p for p in plans_list if p.get("Portfolio") == port_name), {})
        platform = plan.get("Platform")
        if pd.notna(platform) and platform:
            st.markdown(f"**Platform:** {platform}")

        if port_df.empty or port_df["Invested"].sum() == 0:
            st.info(f"No invested data found for **{port_name}**.")
        else:
            render_summary_and_pie(port_df, port_sec_df, port_name,
                                   bar_df=None,
                                   metric_expected=port_expected,
                                   total_expected=port_expected)