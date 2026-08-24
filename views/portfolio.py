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

def get_nav(nav_df, fund_name, fallback_name=None):
    if nav_df.empty or not fund_name:
        if fallback_name:
            return get_nav(nav_df, fallback_name)
        return None

    fund_str = str(fund_name).strip()
    res = nav_df.loc[nav_df["scheme_name"].eq(fund_str), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]

    res = nav_df.loc[nav_df["scheme_name"].str.lower() == fund_str.lower(), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]

    if len(fund_str) >= 3:
        short_name = fund_str[:15].lower()
        res = nav_df.loc[nav_df["scheme_name"].str.lower().str.contains(short_name, na=False, regex=False), ["nav"]]
        if not res.empty:
            return res.iloc[0]["nav"]

    if fallback_name:
        return get_nav(nav_df, fallback_name)

    return None

from concurrent.futures import ThreadPoolExecutor

# -----------------------------------------------
# Stock price helper
# -----------------------------------------------
@st.cache_data(ttl=600)
def get_stock_info(symbol):
    price = None
    pe = None

    if yf and symbol:
        for suffix in [".NS", ".BO", ""]:
            try:
                ticker = yf.Ticker(symbol + suffix)
                p = ticker.fast_info.last_price
                if p and p > 0:
                    price = float(p)
                    try:
                        pe_raw = ticker.info.get("trailingPE")
                        if pe_raw is not None:
                            pe = float(pe_raw)
                    except Exception:
                        pass
                    return price, pe
            except Exception:
                continue

    return price, pe


def batch_fetch_stock_info(symbols):
    """Pre-fetch stock info for multiple symbols in parallel using ThreadPoolExecutor."""
    unique_syms = [s for s in set(symbols) if s]
    if not unique_syms or not yf:
        return
    with ThreadPoolExecutor(max_workers=min(15, len(unique_syms))) as executor:
        list(executor.map(get_stock_info, unique_syms))

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
    """Pre-fetch all FX rates from CurrencyPair table symbols in parallel."""
    rates = {}
    symbols_to_fetch = list({sym for _country, sym in cp_tuples if sym})
    if not symbols_to_fetch:
        return rates

    with ThreadPoolExecutor(max_workers=min(10, len(symbols_to_fetch))) as executor:
        fetched_rates = list(executor.map(fetch_fx_rate, symbols_to_fetch))
        for sym, rate in zip(symbols_to_fetch, fetched_rates):
            rates[sym] = rate
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

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()

# -----------------------------------------------
# Load data
# -----------------------------------------------
@st.cache_data(ttl=300)
def load_portfolio_db_data():
    return (
        db.fetch_stocks(),
        db.fetch_open_transactions(),
        db.fetch_stock_allocations(),
        db.fetch_allocations(),
        db.fetch_investment_plan(),
        db.fetch_currency_pairs()
    )

with st.spinner("Loading portfolio data and live market prices..."):
    (
        db_stocks,
        open_transactions,
        db_stock_allocations,
        db_sector_allocations,
        db_investment_plan,
        db_currency_pairs
    ) = load_portfolio_db_data()
    nav_df = load_nav_data()

    # Parallel pre-fetch all stock prices concurrently
    all_equity_symbols = [
        s.get("Symbol") for s in db_stocks
        if s.get("Symbol") and s.get("Equity", True) and s.get("Listed", True)
    ]
    batch_fetch_stock_info(all_equity_symbols)

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
# Merged Data Dialog
# -----------------------------------------------
@st.dialog("📊 Merged Portfolio Data", width="large")
def show_merged_data_dialog():
    st.caption("View consolidated portfolio data filtered by Broker / Platform and Asset Type.")

    use_usd         = st.session_state.get("view_in_usd", False)
    currency_symbol = "$" if use_usd else "₹"
    money_fmt       = f"{currency_symbol} %.2f"

    # Extract unique platforms and portfolios
    platforms  = sorted(list({str(p.get("Platform")).strip() for p in plans_list if p.get("Platform") and pd.notna(p.get("Platform"))}))
    portfolios = sorted(list({str(p.get("Portfolio")).strip() for p in plans_list if p.get("Portfolio")}))

    all_brokers = sorted(list(set(platforms + portfolios)))

    col_b1, col_b2, col_b3 = st.columns([2, 2, 1])
    with col_b1:
        selected_brokers = st.multiselect(
            "Choose Broker / Platform",
            options=all_brokers,
            default=all_brokers,
            key="merged_broker_multiselect"
        )

    with col_b2:
        selected_asset_types = st.multiselect(
            "Choose Asset Types",
            options=["Stock", "Mutual Fund"],
            default=["Stock", "Mutual Fund"],
            key="merged_asset_type_multiselect"
        )

    with col_b3:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        filter_btn = st.button("🔍 Filter", type="primary", use_container_width=True, key="merged_filter_btn")

    if not selected_brokers:
        st.warning("⚠️ Please select at least one Broker / Platform.")
        return

    if not selected_asset_types:
        st.warning("⚠️ Please select at least one asset type (Stock or Mutual Fund).")
        return

    # Filter portfolios based on selected brokers
    if set(selected_brokers) == set(all_brokers):
        target_txs = open_transactions
    else:
        matching_ports = set()
        for broker in selected_brokers:
            ports = {
                p.get("Portfolio") for p in plans_list
                if p.get("Portfolio") == broker or p.get("Platform") == broker
            }
            if ports:
                matching_ports.update(ports)
            else:
                matching_ports.add(broker)
        target_txs = [tx for tx in open_transactions if tx.get("Portfolio") in matching_ports]

    if not target_txs:
        st.info("No open transactions found for the selected broker(s) / platform(s).")
        return

    # Aggregate transactions by symbol
    tx_agg = {}
    for tx in target_txs:
        sym = tx.get("Symbol", "")
        if not sym:
            continue
        sell_avg  = tx.get("SellAvg")
        sell_date = tx.get("SellDate")
        is_open   = (
            (sell_avg  is None or sell_avg  == "" or (isinstance(sell_avg,  float) and pd.isna(sell_avg))) and
            (sell_date is None or sell_date == "" or (isinstance(sell_date, float) and pd.isna(sell_date)))
        )
        if not is_open:
            continue

        qty      = float(tx.get("Qty", 0.0))
        buy_val  = float(tx.get("BuyValue", 0) or 0)
        buy_usd  = float(tx.get("BuyValueUSD", 0) or 0)
        buy_avg  = float(tx.get("BuyAvg", 0.0))

        if sym not in tx_agg:
            tx_agg[sym] = {"Qty": 0.0, "InvestedTotal": 0.0, "InvestedTotalUSD": 0.0}

        tx_agg[sym]["Qty"] += qty
        tx_agg[sym]["InvestedTotal"]    += buy_val if buy_val > 0 else buy_avg * qty
        tx_agg[sym]["InvestedTotalUSD"] += buy_usd

    stocks_lookup = {s["Symbol"]: s for s in db_stocks if s.get("Symbol")}
    total_invested_portfolio = sum(v["InvestedTotal"] for v in tx_agg.values())

    rows = []
    for sym, agg in tx_agg.items():
        p          = stocks_lookup.get(sym, {})
        name       = p.get("Name", sym)
        is_equity  = p.get("Equity", True)
        is_listed  = p.get("Listed", True)
        country    = (p.get("Country") or "INDIA").upper()
        asset_type = "Stock" if is_equity else "Mutual Fund"

        if asset_type not in selected_asset_types:
            continue

        qty          = agg["Qty"]
        invested_inr = agg["InvestedTotal"]
        invested_usd = agg["InvestedTotalUSD"]
        invested_amt = invested_usd if use_usd else invested_inr

        if qty <= 0 and invested_amt <= 0:
            continue

        pct_alloc   = (invested_inr / total_invested_portfolio * 100) if total_invested_portfolio > 0 else 0.0

        native_price = 0.0
        pe_ratio     = None

        if is_listed:
            if is_equity:
                fetched_price, fetched_pe = get_stock_info(sym)
                native_price = fetched_price if fetched_price is not None else float(p.get("LTP") or 0.0)
                pe_ratio     = fetched_pe
            else:
                fetched_nav  = get_nav(nav_df, name, sym)
                native_price = float(fetched_nav) if (fetched_nav is not None and float(fetched_nav) > 0) else float(p.get("LTP") or 0.0)
        else:
            native_price = float(p.get("LTP") or 0.0)

        live_price    = convert_price(native_price, country, currency_pairs_map, fx_rates)
        current_value = qty * live_price
        running_pnl   = current_value - invested_amt
        running_pnl_p = (running_pnl / invested_amt * 100) if invested_amt > 0 else 0.0
        avg_buy       = convert_price((invested_inr / qty) if qty > 0 else 0.0, "INDIA", currency_pairs_map, fx_rates)

        rows.append({
            "Sector":          p.get("Sector", "Unknown"),
            "Symbol":          sym,
            "Name":            name,
            "Asset Type":      asset_type,
            "Listing":         "Listed" if is_listed else "Unlisted",
            "Country":         country,
            "PE Ratio":        pe_ratio,
            "Qty":             qty,
            "Invested Amount": invested_amt,
            "% of Allocation": pct_alloc,
            "Avg Buy":         avg_buy,
            "Live Price":      live_price,
            "Current Value":   current_value,
            "Running P&L":     running_pnl,
            "Running P&L %":   running_pnl_p,
        })

    if not rows:
        st.info("No matching assets found for the selected broker and asset type filters.")
        return

    merged_df = pd.DataFrame(rows)
    merged_df = merged_df.sort_values(by=["Sector", "Symbol"], ascending=[True, True]).reset_index(drop=True)

    # Display Metrics Summary
    m_inv   = merged_df["Invested Amount"].sum()
    m_val   = merged_df["Current Value"].sum()
    m_pnl   = m_val - m_inv
    m_pnl_p = (m_pnl / m_inv * 100) if m_inv > 0 else 0.0

    pe_df   = merged_df[
        (merged_df["PE Ratio"].notnull()) &
        (~merged_df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
    ]
    avg_pe  = pe_df["PE Ratio"].mean() if not pe_df.empty else 0.0

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("💰 Total Invested", f"{currency_symbol}{m_inv:,.2f}")
    mc2.metric("📈 Current Value", f"{currency_symbol}{m_val:,.2f}")
    mc3.metric("📊 Gain / Loss", f"{currency_symbol}{m_pnl:,.2f}", delta=f"{m_pnl_p:+.2f}%")
    mc4.metric("🎯 Average PE", f"{avg_pe:.2f}")

    st.markdown("---")

    merged_column_config = {
        "Symbol":          None,
        "Country":         None,
        "Asset Type":      st.column_config.TextColumn("Asset Type"),
        "PE Ratio":        st.column_config.NumberColumn("PE \nRatio",              format="%.2f"),
        "Qty":             st.column_config.NumberColumn("Current \nQty",            format="%.4f"),
        "Invested Amount": st.column_config.NumberColumn("Current \nInvested Amount",format=money_fmt),
        "% of Allocation": st.column_config.NumberColumn("% of \nAllocation",        format="%.2f%%"),
        "Avg Buy":         st.column_config.NumberColumn("Avg \nBuy Price",           format=money_fmt),
        "Live Price":      st.column_config.NumberColumn("Live \nPrice",              format=money_fmt),
        "Current Value":   st.column_config.NumberColumn("Current \nValue",           format=money_fmt),
        "Running P&L":     st.column_config.NumberColumn("Running \nP&L",             format="%.2f"),
        "Running P&L %":   st.column_config.NumberColumn("Running \nP&L %",           format="%.2f%%"),
    }

    st.dataframe(
        merged_df,
        use_container_width=True,
        hide_index=True,
        column_config=merged_column_config
    )

# Top Bar Action Buttons
top_col1, top_col2, top_col3 = st.columns([3, 1, 1])
with top_col1:
    st.checkbox("View in USD", value=st.session_state.view_in_usd, on_change=toggle_usd, key="portfolio_usd_cb")
with top_col2:
    if st.button("🔄 Refresh Data", use_container_width=True, help="Reload live market prices and portfolio data"):
        st.cache_data.clear()
        st.rerun()
with top_col3:
    if st.button("📊 Merged Data", use_container_width=True, help="View consolidated portfolio data filtered by Broker and Asset Type"):
        show_merged_data_dialog()

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
            "Country":         country,
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
            "Sector", "Symbol", "Name", "Asset Type", "Listing", "Country", "PE Ratio",
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

            import plotly.graph_objects as go
            import plotly.express as px

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
                        height=650,
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
                        height=650,
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

            # ---- Asset View Rendering (Conditional Tabs / Single View) ----
            stocks_df = df[df["Asset Type"] == "Stock"].reset_index(drop=True)
            mf_df     = df[df["Asset Type"] == "Mutual Fund"].reset_index(drop=True)

            s_count   = len(stocks_df)
            m_count   = len(mf_df)
            all_count = len(df)

            common_column_config = {
                "Symbol":          None,
                "Asset Type":      None,
                "Country":         None,
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

            def render_all_view():
                with st.container(border=True):
                    st.subheader(f"🌐 All Assets ({all_count})")
                    if not df.empty:
                        a_inv   = df["Invested Amount"].sum()
                        a_val   = df["Current Value"].sum()
                        a_pnl   = a_val - a_inv
                        a_pnl_p = (a_pnl / a_inv * 100) if a_inv > 0 else 0.0

                        a_pe_df = df[
                            (df["PE Ratio"].notnull()) &
                            (~df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
                        ]
                        a_avg_pe = a_pe_df["PE Ratio"].mean() if not a_pe_df.empty else 0.0

                        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                        m_col1.metric("💰 Total Invested", f"{currency_symbol}{a_inv:,.2f}")
                        m_col2.metric("📈 Current Value", f"{currency_symbol}{a_val:,.2f}")
                        m_col3.metric("📊 Gain / Loss", f"{currency_symbol}{a_pnl:,.2f}", delta=f"{a_pnl_p:+.2f}%")
                        m_col4.metric("🎯 Average PE", f"{a_avg_pe:.2f}")

                        all_column_config = dict(common_column_config)
                        all_column_config["Asset Type"] = st.column_config.TextColumn("Asset Type")
                        return st.dataframe(
                            df,
                            use_container_width=True,
                            hide_index=True,
                            on_select="rerun",
                            selection_mode="single-row",
                            key=f"data_grid_all_{port_name}",
                            column_config=all_column_config
                        )
                    else:
                        st.info("No assets found in this portfolio.")
                        return None

            def render_stocks_view():
                with st.container(border=True):
                    st.subheader(f"📈 Stocks ({s_count})")
                    if not stocks_df.empty:
                        s_inv   = stocks_df["Invested Amount"].sum()
                        s_val   = stocks_df["Current Value"].sum()
                        s_pnl   = s_val - s_inv
                        s_pnl_p = (s_pnl / s_inv * 100) if s_inv > 0 else 0.0

                        s_pe_df = stocks_df[
                            (stocks_df["PE Ratio"].notnull()) &
                            (~stocks_df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
                        ]
                        s_avg_pe = s_pe_df["PE Ratio"].mean() if not s_pe_df.empty else 0.0

                        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                        m_col1.metric("💰 Total Invested", f"{currency_symbol}{s_inv:,.2f}")
                        m_col2.metric("📈 Current Value", f"{currency_symbol}{s_val:,.2f}")
                        m_col3.metric("📊 Gain / Loss", f"{currency_symbol}{s_pnl:,.2f}", delta=f"{s_pnl_p:+.2f}%")
                        m_col4.metric("🎯 Average PE", f"{s_avg_pe:.2f}")

                        return st.dataframe(
                            stocks_df,
                            use_container_width=True,
                            hide_index=True,
                            on_select="rerun",
                            selection_mode="single-row",
                            key=f"data_grid_stocks_{port_name}",
                            column_config=common_column_config
                        )
                    else:
                        st.info("No stock assets in this portfolio.")
                        return None

            def render_mf_view():
                with st.container(border=True):
                    st.subheader(f"📊 Mutual Funds ({m_count})")
                    if not mf_df.empty:
                        m_inv   = mf_df["Invested Amount"].sum()
                        m_val   = mf_df["Current Value"].sum()
                        m_pnl   = m_val - m_inv
                        m_pnl_p = (m_pnl / m_inv * 100) if m_inv > 0 else 0.0

                        m_col1, m_col2, m_col3 = st.columns(3)
                        m_col1.metric("💰 Total Invested", f"{currency_symbol}{m_inv:,.2f}")
                        m_col2.metric("📈 Current Value", f"{currency_symbol}{m_val:,.2f}")
                        m_col3.metric("📊 Gain / Loss", f"{currency_symbol}{m_pnl:,.2f}", delta=f"{m_pnl_p:+.2f}%")

                        mf_column_config = dict(common_column_config)
                        mf_column_config["PE Ratio"] = None # Hide PE Ratio for Mutual Funds
                        return st.dataframe(
                            mf_df,
                            use_container_width=True,
                            hide_index=True,
                            on_select="rerun",
                            selection_mode="single-row",
                            key=f"data_grid_mf_{port_name}",
                            column_config=mf_column_config
                        )
                    else:
                        st.info("No mutual fund assets in this portfolio.")
                        return None

            event_all    = None
            event_stocks = None
            event_mf     = None

            if s_count > 0 and m_count > 0:
                # Both Stocks & Mutual Funds present -> Show all 3 tabs
                asset_tab_all, asset_tab_stocks, asset_tab_mf = st.tabs([
                    f"🌐 All Assets ({all_count})",
                    f"📈 Stocks ({s_count})",
                    f"📊 Mutual Funds ({m_count})"
                ])
                with asset_tab_all:
                    event_all = render_all_view()
                with asset_tab_stocks:
                    event_stocks = render_stocks_view()
                with asset_tab_mf:
                    event_mf = render_mf_view()

            elif s_count > 0:
                # Only Stocks present -> Show only Stocks section (no tabs)
                event_stocks = render_stocks_view()

            elif m_count > 0:
                # Only Mutual Funds present -> Show only Mutual Funds section (no tabs)
                event_mf = render_mf_view()

            # ---- Order history ----
            # Track active selection across the asset view tabs/section
            last_sel_type_key    = f"last_selected_type_{port_name}"
            prev_all_rows_key    = f"prev_all_rows_{port_name}"
            prev_stocks_rows_key = f"prev_stocks_rows_{port_name}"
            prev_mf_rows_key     = f"prev_mf_rows_{port_name}"

            all_rows    = event_all.selection.rows if (event_all and hasattr(event_all, "selection")) else []
            stocks_rows = event_stocks.selection.rows if (event_stocks and hasattr(event_stocks, "selection")) else []
            mf_rows     = event_mf.selection.rows if (event_mf and hasattr(event_mf, "selection")) else []

            prev_all_rows    = st.session_state.get(prev_all_rows_key, [])
            prev_stocks_rows = st.session_state.get(prev_stocks_rows_key, [])
            prev_mf_rows     = st.session_state.get(prev_mf_rows_key, [])

            if all_rows != prev_all_rows:
                st.session_state[last_sel_type_key]    = "all" if all_rows else None
                st.session_state[prev_all_rows_key]    = all_rows
            elif stocks_rows != prev_stocks_rows:
                st.session_state[last_sel_type_key]    = "stocks" if stocks_rows else None
                st.session_state[prev_stocks_rows_key] = stocks_rows
            elif mf_rows != prev_mf_rows:
                st.session_state[last_sel_type_key]    = "mf" if mf_rows else None
                st.session_state[prev_mf_rows_key]     = mf_rows

            selected_asset_row = None
            last_sel_type = st.session_state.get(last_sel_type_key)

            if last_sel_type == "all" and all_rows and all_rows[0] < len(df):
                selected_asset_row = df.iloc[all_rows[0]]
            elif last_sel_type == "stocks" and stocks_rows and stocks_rows[0] < len(stocks_df):
                selected_asset_row = stocks_df.iloc[stocks_rows[0]]
            elif last_sel_type == "mf" and mf_rows and mf_rows[0] < len(mf_df):
                selected_asset_row = mf_df.iloc[mf_rows[0]]
            elif all_rows and all_rows[0] < len(df):
                selected_asset_row = df.iloc[all_rows[0]]
            elif stocks_rows and stocks_rows[0] < len(stocks_df):
                selected_asset_row = stocks_df.iloc[stocks_rows[0]]
            elif mf_rows and mf_rows[0] < len(mf_df):
                selected_asset_row = mf_df.iloc[mf_rows[0]]

            if selected_asset_row is not None:
                selected_symbol  = selected_asset_row["Symbol"]
                selected_name    = selected_asset_row["Name"]
                selected_country = (selected_asset_row.get("Country") or "INDIA").upper()

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
                            height=600,
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