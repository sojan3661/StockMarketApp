import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

try:
    import yfinance as yf
except ImportError:
    yf = None

# -----------------------------------------------
# NAV helpers
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
            sep=";",
            header=None,
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
        return None

    res = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]

    res = nav_df.loc[nav_df["scheme_name"].str.lower() == fund_name.lower(), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]

    short_name = fund_name[:15].lower()
    res = nav_df.loc[nav_df["scheme_name"].str.lower().str.contains(short_name, na=False, regex=False), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]

    return None

# -----------------------------------------------
# Stock price helper
# -----------------------------------------------
def get_stock_info(symbol):
    price = None
    pe = None

    if yf:
        for suffix in [".NS", ".BO", ""]:
            try:
                ticker = yf.Ticker(symbol + suffix)
                p = ticker.fast_info.last_price
                if p and p > 0:
                    price = float(p)
                    pe_raw = ticker.info.get("trailingPE")
                    if pe_raw is not None:
                        pe = float(pe_raw)
                    return price, pe
            except Exception:
                continue

    return price, pe

# -----------------------------------------------
# FX helpers (same logic as Dashboard.py)
# -----------------------------------------------
@st.cache_data(ttl=300)
def fetch_fx_rate(pair_symbol):
    """Fetch live FX rate for a Yahoo Finance pair symbol e.g. 'USDINR=X'."""
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


@st.cache_data(ttl=300)
def build_fx_cache(cp_tuples):
    """Pre-fetch all FX rates from CurrencyPair table symbols."""
    rates = {}
    for _country, sym in cp_tuples:
        if sym and sym not in rates:
            rates[sym] = fetch_fx_rate(sym)
    return rates


def _inr_per_unit(country, currency_pairs_map, fx_rates):
    """
    How many INR = 1 unit of country's base currency.
    INDIA -> 1.0. Others -> CurrencyPair table, then built-in fallback.
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

    st.warning(f"⚠️ No FX rate found for country '{country}'. Live Price shown in native currency.")
    return 1.0


def _usdinr(currency_pairs_map, fx_rates):
    """How many INR = 1 USD."""
    for country, cp in currency_pairs_map.items():
        sym = (cp.get("Symbol") or "").upper()
        if "USDINR" in sym:
            rate = fx_rates.get(cp["Symbol"], 0.0)
            if rate > 0:
                return rate
    return fetch_fx_rate("USDINR=X") or 84.0


def convert_price(native_price, country, currency_pairs_map, fx_rates):
    """
    Convert native_price to display currency (INR or USD).

    INR mode:
      INDIA  -> as-is
      Other  -> native × <BaseCurrency>INR

    USD mode:
      INDIA  -> native ÷ USDINR
      Other  -> (native × <BaseCurrency>INR) ÷ USDINR
    """
    use_usd = st.session_state.get("view_in_usd", False)
    country = (country or "INDIA").upper()
    inr_price = native_price * _inr_per_unit(country, currency_pairs_map, fx_rates)

    if not use_usd:
        return inr_price
    else:
        usd_inr = _usdinr(currency_pairs_map, fx_rates)
        if usd_inr <= 0:
            usd_inr = 84.0
        return inr_price / usd_inr

# -----------------------------------------------
# Page setup
# -----------------------------------------------
st.title("Portfolio Overview")

if "view_in_usd" not in st.session_state:
    st.session_state.view_in_usd = False

def toggle_usd():
    st.session_state.view_in_usd = not st.session_state.view_in_usd

st.checkbox("View in USD", value=st.session_state.view_in_usd, on_change=toggle_usd, key="portfolio_usd_cb")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()

# -----------------------------------------------
# Load data
# -----------------------------------------------
with st.spinner("Loading portfolio data and live market prices..."):
    db_stocks            = db.fetch_stocks()
    open_transactions    = db.fetch_open_transactions()
    nav_df               = load_nav_data()
    db_stock_allocations = db.fetch_stock_allocations()
    db_sector_allocations= db.fetch_allocations()
    db_investment_plan   = db.fetch_investment_plan()
    db_currency_pairs    = db.fetch_currency_pairs()

# Build currency maps
currency_pairs_map = {
    cp["Country"].upper(): cp
    for cp in (db_currency_pairs if isinstance(db_currency_pairs, list) else [])
    if cp.get("Country")
}
_cp_tuples = tuple(sorted(
    (c, cp.get("Symbol", "")) for c, cp in currency_pairs_map.items()
))
fx_rates = build_fx_cache(_cp_tuples)

# Stocks lookup
stocks_map = {s["Symbol"]: s for s in db_stocks}

plans_list      = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

# -----------------------------------------------
# Portfolio display builder
# -----------------------------------------------
def get_portfolio_display_data(port_stocks, port_open_transactions, nav_df,
                                port_stock_allocations_tuple,
                                currency_pairs_map, fx_rates):
    use_usd = st.session_state.get("view_in_usd", False)
    port_stock_allocations = dict(port_stock_allocations_tuple)

    tx_agg = {}
    for tx in port_open_transactions:
        sym = tx.get("Symbol", "")
        if not sym:
            continue

        # Only include open (unsold) transactions for invested amount
        sell_avg  = tx.get("SellAvg")
        sell_date = tx.get("SellDate")
        is_open   = (
            (sell_avg  is None or sell_avg  == "" or (isinstance(sell_avg,  float) and pd.isna(sell_avg)))  and
            (sell_date is None or sell_date == "" or (isinstance(sell_date, float) and pd.isna(sell_date)))
        )

        qty      = float(tx.get("Qty", 0.0))
        buy_val  = float(tx.get("BuyValue", 0) or 0)    # INR total value for this transaction
        buy_usd  = float(tx.get("BuyValueUSD", 0) or 0) # USD total value for this transaction
        buy_avg  = float(tx.get("BuyAvg", 0.0))

        if sym not in tx_agg:
            tx_agg[sym] = {"Qty": 0.0, "InvestedTotal": 0.0, "InvestedTotalUSD": 0.0}

        tx_agg[sym]["Qty"] += qty
        if is_open:
            # Use BuyValue directly if available, else fall back to qty * BuyAvg
            tx_agg[sym]["InvestedTotal"]    += buy_val if buy_val > 0 else buy_avg * qty
            tx_agg[sym]["InvestedTotalUSD"] += buy_usd

    # Total invested for % of allocation (always INR)
    total_invested_portfolio = sum(v["InvestedTotal"] for v in tx_agg.values())

    display_data = []

    for p in port_stocks:
        sym        = p.get("Symbol", "Unknown")
        name       = p.get("Name", "Unknown")
        is_equity  = p.get("Equity", False)
        is_listed  = p.get("Listed", True)
        country    = (p.get("Country") or "INDIA").upper()

        alloc       = float(port_stock_allocations.get(sym, 0.0))
        agg         = tx_agg.get(sym, {"Qty": 0.0, "InvestedTotal": 0.0, "InvestedTotalUSD": 0.0})
        qty         = agg["Qty"]
        invested_inr = agg["InvestedTotal"]
        invested_usd = agg["InvestedTotalUSD"]
        invested_amt = invested_usd if use_usd else invested_inr

        avg_buy_inr  = (invested_inr / qty) if qty > 0 else 0.0
        pct_alloc    = (invested_inr / total_invested_portfolio * 100) if total_invested_portfolio > 0 else 0.0

        # Live price: fetch native, then convert
        native_price = 0.0
        pe_ratio     = None

        if is_listed:
            if is_equity:
                fetched_price, fetched_pe = get_stock_info(sym)
                native_price = fetched_price if fetched_price is not None else 0.0
                pe_ratio     = fetched_pe
            else:
                fetched_nav = get_nav(nav_df, sym)
                native_price = float(fetched_nav) if fetched_nav is not None else 0.0
                if native_price == 0.0:
                    fetched_price, _ = get_stock_info(sym)
                    if fetched_price is not None and fetched_price > 0:
                        native_price = fetched_price
        else:
            native_price = float(p.get("LTP") or 0.0)

        # Convert native price to display currency
        live_price    = convert_price(native_price, country, currency_pairs_map, fx_rates)
        current_value = qty * live_price
        running_pnl   = current_value - invested_amt
        running_pnl_pct = (running_pnl / invested_amt * 100) if invested_amt else 0.0

        # Avg Buy: convert from INR to display currency
        avg_buy = convert_price(avg_buy_inr, "INDIA", currency_pairs_map, fx_rates)

        display_data.append({
            "Sector":          p.get("Sector", "Unknown"),
            "Symbol":          sym,
            "Name":            name,
            "Asset Type":      "Stock" if is_equity else "Mutual Fund",
            "Listing":         "Listed" if is_listed else "Unlisted",
            "PE Ratio":        pe_ratio,
            "Qty":             qty,
            "Invested Amount": invested_amt,
            "% of Allocation": pct_alloc,
            "Avg Buy":         avg_buy,
            "Live Price":      live_price,
            "Current Value":   current_value,
            "Running P&L":     running_pnl,
            "Running P&L %":   running_pnl_pct,
        })

    if not display_data:
        return pd.DataFrame(columns=[
            "Sector", "Symbol", "Name", "Asset Type", "Listing", "PE Ratio",
            "Qty", "Invested Amount", "% of Allocation", "Avg Buy",
            "Live Price", "Current Value", "Running P&L", "Running P&L %"
        ])

    df = pd.DataFrame(display_data)
    df = df.sort_values(by=["Sector", "Symbol"], ascending=[True, True])
    df = df.reset_index(drop=True)
    return df

# -----------------------------------------------
# Tabs
# -----------------------------------------------
if not db_stocks:
    st.info("No assets found in your portfolio yet. Go to 'Stock Management' to start adding them.")
elif not portfolio_names:
    st.info("No investment plans found. Create a portfolio in the Build Portfolio page first.")
else:
    use_usd         = st.session_state.get("view_in_usd", False)
    currency_symbol = "$" if use_usd else "₹"
    money_fmt       = f"{currency_symbol} %.2f"

    tab_names = ["Overall Portfolio"] + portfolio_names
    tabs = st.tabs(tab_names)

    for i, port_name in enumerate(tab_names):
        with tabs[i]:
            st.divider()
            st.subheader(f"Assets for {port_name}")

            if port_name == "Overall Portfolio":
                port_stock_allocations  = {}
                port_sector_allocations = {}
                symbol_to_sector = {s.get("Symbol"): s.get("Sector") for s in db_stocks if s.get("Symbol")}
                tx_symbols  = {tx.get("Symbol") for tx in open_transactions}
                port_stocks = [s for s in db_stocks if s.get("Symbol") in tx_symbols]
                port_open_transactions = open_transactions
            else:
                port_stock_allocations = {
                    a["Symbol"]: a["Allocation"]
                    for a in db_stock_allocations
                    if a.get("Portfolio") == port_name
                    and a.get("Symbol")
                    and (a.get("Allocation") or 0) > 0
                }
                port_sector_allocations = {
                    a["Sector"]: a["Allocation"]
                    for a in db_sector_allocations
                    if a.get("Portfolio") == port_name
                    and a.get("Sector")
                    and (a.get("Allocation") or 0) > 0
                }
                symbol_to_sector = {s.get("Symbol"): s.get("Sector") for s in db_stocks if s.get("Symbol")}

                tx_symbols    = {tx.get("Symbol") for tx in open_transactions if tx.get("Portfolio") == port_name}
                valid_symbols = tx_symbols

                port_stocks = [s for s in db_stocks if s.get("Symbol") in valid_symbols]
                port_open_transactions = [
                    tx for tx in open_transactions if tx.get("Portfolio") == port_name
                ]

            port_alloc_tuple = tuple(port_stock_allocations.items())

            with st.spinner(f"Calculating live valuations for {port_name}..."):
                df = get_portfolio_display_data(
                    port_stocks, port_open_transactions, nav_df,
                    port_alloc_tuple, currency_pairs_map, fx_rates
                )

            if not df.empty:
                df = df[(df["Qty"] > 0) | (df["Invested Amount"] > 0) | (df["Current Value"] > 0)].reset_index(drop=True)

            if df.empty:
                st.info(f"No assets with open value found for {port_name}.")
                continue

            # ---- Summary metrics ----
            total_invested    = df["Invested Amount"].sum()
            total_curr_val    = df["Current Value"].sum()
            gain_loss         = total_curr_val - total_invested
            gain_loss_pct     = (gain_loss / total_invested * 100) if total_invested > 0 else 0.0

            pe_df  = df[
                (df["PE Ratio"].notnull()) &
                (~df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
            ]
            avg_pe = pe_df["PE Ratio"].mean() if not pe_df.empty else 0.0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("💰 Total Invested",  f"{currency_symbol}{total_invested:,.2f}")
            m2.metric("📈 Current Value",   f"{currency_symbol}{total_curr_val:,.2f}")
            m3.metric("📊 Gain / Loss",     f"{currency_symbol}{gain_loss:,.2f}", delta=f"{gain_loss_pct:.2f}%")
            m4.metric("🎯 Average PE",      f"{avg_pe:.2f}")

            import plotly.graph_objects as go
            import plotly.express as px

            st.divider()

            # ---- Pie charts ----
            pie_col1, pie_col2 = st.columns(2)

            with pie_col1:
                if not df.empty and df["Invested Amount"].sum() > 0:
                    st.subheader("Current Asset Allocation")
                    pie_df = df[df["Invested Amount"] > 0]
                    fig_pie = go.Figure(go.Pie(
                        labels=pie_df["Name"].tolist(),
                        values=pie_df["Invested Amount"].tolist(),
                        hole=0.4,
                        marker_colors=px.colors.qualitative.Pastel,
                        textinfo="label+percent",
                        textposition="inside",
                    ))
                    fig_pie.update_layout(
                        showlegend=False,
                        margin=dict(t=20, b=20, l=0, r=0),
                        height=400,
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#E2E8F0")
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

            with pie_col2:
                proj_data = []
                for sym, alloc in port_stock_allocations.items():
                    sector   = symbol_to_sector.get(sym)
                    sec_alloc= port_sector_allocations.get(sector, 0)
                    proj_pct = (alloc * sec_alloc) / 100
                    if proj_pct > 0:
                        name = next((s.get("Name") for s in db_stocks if s.get("Symbol") == sym), sym)
                        proj_data.append({"Name": name, "Projected %": proj_pct})

                if proj_data:
                    st.subheader("Projected Target Allocation")
                    proj_df = pd.DataFrame(proj_data)
                    fig_proj = go.Figure(go.Pie(
                        labels=proj_df["Name"].tolist(),
                        values=proj_df["Projected %"].tolist(),
                        marker_colors=px.colors.qualitative.Pastel,
                        textinfo="label+percent",
                        textposition="inside",
                    ))
                    fig_proj.update_layout(
                        showlegend=False,
                        margin=dict(t=20, b=20, l=0, r=0),
                        height=400,
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#E2E8F0")
                    )
                    st.plotly_chart(fig_proj, use_container_width=True)

            st.divider()

            # ---- Download button ----
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label=f"📥 Download {port_name} Portfolio as CSV",
                data=csv,
                file_name=f"{port_name.replace(' ', '_')}_portfolio.csv",
                mime="text/csv",
            )

            # ---- Data table ----
            event = st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key=f"data_grid_{port_name}",
                column_config={
                    "Symbol":          None,
                    "Asset Type":      None,
                    "PE Ratio":        st.column_config.NumberColumn("PE \nRatio",              format="%.2f"),
                    "Qty":             st.column_config.NumberColumn("Current \nQty",            format="%.4f"),
                    "Invested Amount": st.column_config.NumberColumn("Current \nInvested Amount",format=money_fmt),
                    "% of Allocation": st.column_config.NumberColumn("% of \nAllocation",        format="%.2f%%"),
                    "Avg Buy":         st.column_config.NumberColumn("Avg \nBuy Price",           format=money_fmt),
                    "Live Price":      st.column_config.NumberColumn("Live \nPrice",              format=money_fmt),
                    "Current Value":   st.column_config.NumberColumn("Current \nValue",           format=money_fmt),
                    "Running P&L":     st.column_config.NumberColumn("Running \nP&L",             format=money_fmt),
                    "Running P&L %":   st.column_config.NumberColumn("Running \nP&L %",           format="%.2f%%"),
                }
            )

            # ---- Order history ----
            selected_rows = event.selection.rows
            if selected_rows:
                selected_index   = selected_rows[0]
                selected_symbol  = df.iloc[selected_index]["Symbol"]
                selected_name    = df.iloc[selected_index]["Name"]
                selected_country = (df.iloc[selected_index].get("Country") or "INDIA").upper()

                # Resolve the native currency symbol for this stock's country
                # e.g. INDIA -> "₹", USA -> "$", UK -> "£"
                _currency_fmt_map = {
                    "INR": "₹", "USD": "$", "GBP": "£", "EUR": "€",
                    "HKD": "HK$", "JPY": "¥", "AUD": "A$", "CAD": "C$", "CHF": "Fr",
                }
                _cp           = currency_pairs_map.get(selected_country, {})
                _base_currency= _cp.get("BaseCurrency", "INR")
                _native_sym   = _currency_fmt_map.get(_base_currency.upper(), _base_currency + " ")
                _native_fmt   = f"{_native_sym} %.2f"

                st.divider()

                with st.spinner(f"Loading transaction history for {selected_symbol}..."):
                    if port_name == "Overall Portfolio":
                        history = db.fetch_transactions_by_symbol(selected_symbol)
                    else:
                        history = db.fetch_transactions_by_symbol(selected_symbol, portfolio=port_name)

                if not history:
                    st.info("No transaction history found for this asset.")
                else:
                    hist_df = pd.DataFrame(history)

                    if "SellDate" not in hist_df.columns:
                        hist_df["SellDate"] = None
                    if "SellAvg" not in hist_df.columns:
                        hist_df["SellAvg"] = None

                    # ---- Line Chart for Open Buy Transactions ----
                    chart_df = hist_df[hist_df["SellAvg"].isna() | (hist_df["SellAvg"].isnull()) | (hist_df["SellAvg"] == "")]
                    if not chart_df.empty:
                        chart_df = chart_df.copy()
                        chart_df["BuyDate"] = pd.to_datetime(chart_df["BuyDate"])
                        
                        chart_df["Qty"] = pd.to_numeric(chart_df["Qty"], errors="coerce").fillna(0.0)
                        chart_df["BuyAvg"] = pd.to_numeric(chart_df["BuyAvg"], errors="coerce").fillna(0.0)
                        
                        if "BuyValue" not in chart_df.columns:
                            chart_df["BuyValue"] = chart_df["Qty"] * chart_df["BuyAvg"]
                        else:
                            chart_df["BuyValue"] = pd.to_numeric(chart_df["BuyValue"], errors="coerce").fillna(chart_df["Qty"] * chart_df["BuyAvg"])
                        
                        # Group by BuyDate to handle multiple buys on the same day
                        grouped_chart = chart_df.groupby("BuyDate").agg({
                            "Qty": "sum",
                            "BuyValue": "sum"
                        }).reset_index()
                        
                        grouped_chart["BuyPrice"] = grouped_chart.apply(
                            lambda r: r["BuyValue"] / r["Qty"] if r["Qty"] > 0 else 0.0, axis=1
                        )
                        grouped_chart = grouped_chart.sort_values("BuyDate")
                        
                        st.subheader(f"Buy Price Trend: {selected_name} ({selected_symbol})")
                        
                        import plotly.graph_objects as go
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=grouped_chart["BuyDate"],
                            y=grouped_chart["BuyPrice"],
                            mode="lines+markers",
                            line=dict(color="#10B981", width=3),
                            marker=dict(size=8, color="#10B981", symbol="circle"),
                            hovertemplate=(
                                "<b>Date</b>: %{x|%d-%b-%Y}<br>" +
                                "<b>Buy Price</b>: " + _native_sym + "%{y:,.2f}<br>" +
                                "<b>Quantity</b>: %{customdata[0]:,.4f}<br>" +
                                "<b>Buy Value</b>: " + _native_sym + "%{customdata[1]:,.2f}<extra></extra>"
                            ),
                            customdata=grouped_chart[["Qty", "BuyValue"]].values
                        ))
                        fig.update_layout(
                            xaxis_title="Buy Date",
                            yaxis_title=f"Buy Price ({_base_currency})",
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            font=dict(color="#E2E8F0"),
                            margin=dict(t=10, b=20, l=10, r=10),
                            height=300,
                            xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)", tickformat=",.2f")
                        )
                        st.plotly_chart(fig, use_container_width=True)
                        st.divider()

                    # ---- Order History Table ----
                    st.subheader(f"Order History: {selected_name} ({selected_symbol})")
                    display_hist = hist_df[["BuyDate", "Qty", "BuyAvg", "SellDate", "SellAvg"]].copy()

                    for date_col in ["BuyDate", "SellDate"]:
                        display_hist[date_col] = pd.to_datetime(
                            display_hist[date_col], errors="coerce"
                        ).dt.strftime("%d-%b-%Y")

                    event_hist = st.dataframe(
                        display_hist,
                        use_container_width=True,
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="multi-row",
                        key=f"history_grid_{port_name}",
                        column_config={
                            "BuyDate":  "Buy Date",
                            "Qty":      st.column_config.NumberColumn("Quantity",    format="%.4f"),
                            "BuyAvg":   st.column_config.NumberColumn("Buy Price",   format=_native_fmt),
                            "SellDate": "Sell Date",
                            "SellAvg":  st.column_config.NumberColumn("Sell Price",  format=_native_fmt),
                        }
                    )

                    selected_hist_rows = event_hist.selection.rows
                    if selected_hist_rows:
                        if len(selected_hist_rows) == 1:
                            selected_hist_index = selected_hist_rows[0]
                            tx_id    = hist_df.iloc[selected_hist_index].get("id")
                            sell_date= hist_df.iloc[selected_hist_index].get("SellDate")

                            if tx_id:
                                st.write("")
                                action_col1, action_col2 = st.columns([1, 2])

                                with action_col1:
                                    if pd.isna(sell_date) or sell_date is None:
                                        action_label = "🗑️ Delete Buy Transaction"
                                        help_text    = "This will permanently delete this buy transaction."
                                    else:
                                        action_label = "🗑️ Delete Sell Details"
                                        help_text    = "This will remove the sell date and sell rate, converting it back to an open buy transaction."

                                    if st.button(action_label, type="primary", help=help_text, key=f"del_tx_{tx_id}_{port_name}"):
                                        with st.spinner("Processing..."):
                                            if pd.isna(sell_date) or sell_date is None:
                                                success = db.delete_transaction(tx_id)
                                            else:
                                                success = db.revert_sell_transaction(tx_id)

                                            if success:
                                                st.success("Transaction updated successfully!")
                                                st.rerun()

                                with action_col2:
                                    with st.expander("✏️ Edit Transaction"):
                                        row_data = hist_df.iloc[selected_hist_index]
                                        with st.form(key=f"edit_form_{tx_id}_{port_name}"):
                                            try:
                                                b_date_val = pd.to_datetime(row_data.get("BuyDate")).date()
                                            except Exception:
                                                b_date_val = None

                                            s_date_val = None
                                            if not pd.isna(row_data.get("SellDate")) and row_data.get("SellDate") is not None:
                                                try:
                                                    s_date_val = pd.to_datetime(row_data.get("SellDate")).date()
                                                except Exception:
                                                    s_date_val = None

                                            new_qty      = st.number_input("Quantity",  value=float(row_data.get("Qty", 0.0)),    format="%.4f")
                                            new_buy_avg  = st.number_input(f"Buy Price ({_base_currency})", value=float(row_data.get("BuyAvg", 0.0)), format="%.2f")
                                            new_buy_date = st.date_input("Buy Date", value=b_date_val)

                                            has_sell     = not pd.isna(row_data.get("SellAvg")) and row_data.get("SellAvg") is not None
                                            new_sell_avg = None
                                            new_sell_date= None

                                            if has_sell:
                                                new_sell_avg  = st.number_input(f"Sell Price ({_base_currency})", value=float(row_data.get("SellAvg", 0.0)), format="%.2f")
                                                new_sell_date = st.date_input("Sell Date", value=s_date_val)

                                            submit_edit = st.form_submit_button("Update Transaction")
                                            if submit_edit:
                                                sell_d_str = new_sell_date.strftime("%Y-%m-%d") if new_sell_date else None
                                                buy_d_str  = new_buy_date.strftime("%Y-%m-%d")  if new_buy_date else None

                                                success = db.update_transaction(
                                                    tx_id=tx_id,
                                                    qty=new_qty,
                                                    buy_avg=new_buy_avg,
                                                    buy_date=buy_d_str,
                                                    sell_date=sell_d_str,
                                                    sell_avg=new_sell_avg
                                                )
                                                if success:
                                                    st.success("Transaction updated successfully!")
                                                    st.rerun()
                        else:
                            # Multiple transactions selected
                            tx_ids = [hist_df.iloc[idx].get("id") for idx in selected_hist_rows if hist_df.iloc[idx].get("id") is not None]
                            if tx_ids:
                                st.write("")
                                if st.button(f"🗑️ Delete Selected Transactions ({len(tx_ids)})", type="primary", key=f"del_multiple_{port_name}"):
                                    with st.spinner("Deleting selected transactions..."):
                                        success = db.delete_transactions(tx_ids)
                                        if success:
                                            st.success("Selected transactions deleted successfully!")
                                            st.rerun()