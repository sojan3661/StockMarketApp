import streamlit as st
import pandas as pd
import sys
import os
import math
import yfinance as yf

# Add root path for Config import
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from Config.supabase_client import db


st.title("Portfolio Rebalancing")

# -----------------------------
# Supabase Check
# -----------------------------
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

if st.button("🔄 Refresh Data", help="Reload live prices and allocations"):
    for k in list(st.session_state.keys()):
        if k.startswith("port_stock_allocations_") or k.startswith("editor_") or k.startswith("symbols_"):
            del st.session_state[k]
    st.cache_data.clear()
    st.rerun()



# -----------------------------
# Load Mutual Fund NAV (cached)
# -----------------------------
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


from concurrent.futures import ThreadPoolExecutor

# -----------------------------
# Cache NSE / Yahoo Price
# -----------------------------
@st.cache_data(ttl=600)
def get_stock_price(symbol):
    """Returns native price (in the stock's home currency)."""
    price = 0.0

    if yf and symbol:
        for suffix in [".NS", ".BO", ""]:
            try:
                ticker = yf.Ticker(symbol + suffix)
                p = ticker.fast_info.last_price
                if p and p > 0:
                    return float(p)
            except Exception:
                continue

    return price


def batch_fetch_stock_prices(symbols):
    """Pre-fetch stock prices for multiple symbols in parallel using ThreadPoolExecutor."""
    unique_syms = [s for s in set(symbols) if s]
    if not unique_syms or not yf:
        return
    with ThreadPoolExecutor(max_workers=min(15, len(unique_syms))) as executor:
        list(executor.map(get_stock_price, unique_syms))


# -----------------------------
# FX helpers
# -----------------------------
@st.cache_data(ttl=300)
def fetch_fx_rate(pair_symbol):
    """
    Fetch live FX rate for a Yahoo Finance pair symbol e.g. 'USDINR=X'.
    Returns rate as float or 0.0 on failure.
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


def inr_per_unit(country, currency_pairs_map, fx_rates):
    """
    Returns how many INR = 1 unit of the country's base currency.
    INDIA -> 1.0
    Others -> looks up CurrencyPair table Symbol (e.g. GBPINR=X),
              then falls back to built-in map.
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

    st.warning(f"⚠️ No FX rate found for country '{country}'. LTP shown in native currency.")
    return 1.0


def to_inr(native_price, country, currency_pairs_map, fx_rates):
    """Convert a native price to INR using the country's FX rate."""
    return native_price * inr_per_unit(country, currency_pairs_map, fx_rates)


# -----------------------------
# Get MF NAV
# -----------------------------
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


# -----------------------------
# Load Database Data
# -----------------------------
@st.cache_data(ttl=600)
def load_portfolio_data():
    with st.spinner("Loading data from database..."):
        db_sectors           = db.fetch_sectors()
        db_allocations       = db.fetch_allocations()
        raw_stocks           = db.fetch_stocks()
        db_stocks            = sorted(
            [s for s in raw_stocks if s.get("ACTIVE", True) is not False],
            key=lambda x: x.get("Symbol", "")
        )
        db_stock_allocations = db.fetch_stock_allocations()
        open_transactions    = db.fetch_open_transactions()
        db_investment_plan   = db.fetch_investment_plan()
        db_currency_pairs    = db.fetch_currency_pairs()
        return (
            db_sectors,
            db_allocations,
            db_stocks,
            db_stock_allocations,
            open_transactions,
            db_investment_plan,
            db_currency_pairs
        )

(
    db_sectors,
    db_allocations,
    db_stocks,
    db_stock_allocations,
    open_transactions,
    db_investment_plan,
    db_currency_pairs
) = load_portfolio_data()

nav_df = load_nav_data()

# Parallel pre-fetch stock prices for all listed stocks
all_equity_symbols = [
    s.get("Symbol") for s in db_stocks
    if s.get("Symbol") and s.get("Equity", True) and s.get("Listed", True)
]
batch_fetch_stock_prices(all_equity_symbols)

# Build currency pair lookup: country (uppercase) -> CurrencyPair row
currency_pairs_map = {
    cp["Country"].upper(): cp
    for cp in (db_currency_pairs if isinstance(db_currency_pairs, list) else [])
    if cp.get("Country")
}

# Pre-fetch all FX rates
_cp_tuples = tuple(sorted(
    (c, cp.get("Symbol", "")) for c, cp in currency_pairs_map.items()
))
fx_rates = build_fx_cache(_cp_tuples)

# Build stock country lookup: symbol -> country
stock_country_map = {
    s["Symbol"]: (s.get("Country") or "INDIA").upper()
    for s in db_stocks
    if s.get("Symbol")
}

# -----------------------------
# Aggregate Transactions
# -----------------------------
tx_df = pd.DataFrame(open_transactions)

if not tx_df.empty:
    if "Portfolio" not in tx_df.columns:
        tx_df["Portfolio"] = ""
    tx_agg = (
        tx_df.groupby(["Portfolio", "Symbol"])
        .agg({"Qty": "sum", "BuyValue": "sum"})
        .rename(columns={"BuyValue": "InvestedTotal"})
        .to_dict("index")
    )
else:
    tx_agg = {}


# -----------------------------
# Expected Investment
# -----------------------------
if not db_investment_plan:
    st.info("No investment plans found!")
    st.stop()

plans_list      = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

if not portfolio_names:
    st.info("No valid portfolios found.")
    st.stop()

# -----------------------------
# Tabs Generation
# -----------------------------
tabs = st.tabs(portfolio_names)

with open("debug_editor.txt", "a") as f:
    f.write(f"--- RERUN ---\n")
    for k, v in st.session_state.items():
        if "editor" in k:
            f.write(f"Key: {k}, Value: {v}\n")

for i, port_name in enumerate(portfolio_names):

    with tabs[i]:

        # 1. Filter allocations for this specific portfolio
        port_allocations = [a for a in db_allocations if a.get("Portfolio") == port_name]
        sector_alloc_dict = {
            alloc["Sector"]: alloc["Allocation"]
            for alloc in port_allocations
            if alloc.get("Sector")
        }

        # 1b. Filter stock targets for this specific portfolio
        alloc_state_key = f"port_stock_allocations_{port_name}"
        if alloc_state_key not in st.session_state:
            # Get existing allocations
            existing_allocs = {
                a["Symbol"]: a["Allocation"]
                for a in db_stock_allocations
                if a.get("Portfolio") == port_name and a.get("Symbol")
            }
            # Initialize with all stocks (defaulting to 0.0 if not present)
            st.session_state[alloc_state_key] = {
                s["Symbol"]: existing_allocs.get(s["Symbol"], 0.0)
                for s in db_stocks
                if s.get("Symbol")
            }
        port_stock_allocations = st.session_state[alloc_state_key]

        # Update port_stock_allocations with any edits currently in session state
        for sector_row in db_sectors:
            sec_name = sector_row.get("Sector")
            if not sec_name:
                continue
            editor_key = f"editor_{port_name}_{sec_name}"
            symbols_key = f"symbols_{port_name}_{sec_name}"
            if editor_key in st.session_state and symbols_key in st.session_state:
                edits = st.session_state[editor_key].get("edited_rows", {})
                displayed_symbols = st.session_state[symbols_key]
                for idx_str, changes in edits.items():
                    try:
                        idx = int(idx_str)
                        if 0 <= idx < len(displayed_symbols):
                            sym = displayed_symbols[idx]
                            if sym and "Allocation %" in changes:
                                port_stock_allocations[sym] = float(changes["Allocation %"])
                    except ValueError:
                        pass

        # 2. Calculate Expected Investment for this specific portfolio
        plan_details    = next((p for p in plans_list if p.get("Portfolio") == port_name), {})
        monthly_sip     = plan_details.get("Monthly SIP") or 0
        months          = plan_details.get("Number of Months") or 0

        # Current invested = sum of BuyValue for open transactions in this portfolio
        port_open_tx = [tx for tx in open_transactions if tx.get("Portfolio") == port_name]
        current_invested = sum(
            float(tx.get("BuyValue", 0))
            for tx in port_open_tx
        )

        total_expected = plan_details.get("Current Invested Amount", 0) + (monthly_sip * months)

        # 3. Header
        st.subheader(f"Asset Allocation for {port_name}")

        sip_months_key = f"sip_months_{port_name}"
        if sip_months_key not in st.session_state:
            st.session_state[sip_months_key] = 12

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("💰 Current Invested", f"₹{current_invested:,.2f}")

        expected_metric_placeholder = col2.empty()
        inflow_metric_placeholder   = col3.empty()

        with col4:
            sip_total_placeholder = st.empty()
            with st.popover("Change SIP Months"):
                st.session_state[sip_months_key] = st.number_input(
                    "SIP Months", min_value=1,
                    value=st.session_state[sip_months_key],
                    key=f"input_{sip_months_key}"
                )

        show_only_inflow = st.checkbox("Show only Inflow Instruments", key=f"show_inflow_{port_name}")

        total_inflow = 0
        total_sip    = 0

        if not db_sectors:
            st.info("No sectors found!")
            continue

        # Calculate sum of expected across all assets in the portfolio
        portfolio_expected_sum = 0.0
        for sector_row in db_sectors:
            sec_name  = sector_row.get("Sector")
            tar_alloc = sector_alloc_dict.get(sec_name, 0)
            if not tar_alloc or tar_alloc <= 0:
                continue
            sec_stocks = [s for s in db_stocks if s.get("Sector") == sec_name]
            for p in sec_stocks:
                sym   = p.get("Symbol")
                alloc = float(port_stock_allocations.get(sym, 0.0))
                portfolio_expected_sum += total_expected * (tar_alloc / 100) * (alloc / 100)

        master_updates = []

        for sector_row in db_sectors:

            sector_name  = sector_row.get("Sector")
            target_alloc = sector_alloc_dict.get(sector_name, 0)

            # Skip sectors with no allocation assigned for this portfolio
            if not target_alloc or target_alloc <= 0:
                continue

            sector_expected = total_expected * (target_alloc / 100)
            sector_stocks = [s for s in db_stocks if s.get("Sector") == sector_name]

            rows = []

            for p in sector_stocks:
                sym     = p.get("Symbol")
                name    = p.get("Name")
                country = (p.get("Country") or "INDIA").upper()

                alloc = float(port_stock_allocations.get(sym, 0.0))

                agg      = tx_agg.get((port_name, sym), {"Qty": 0, "InvestedTotal": 0})
                qty      = agg["Qty"]
                invested = agg["InvestedTotal"]

                # Fetch native price, then convert to INR for non-India stocks
                if p.get("Equity", True):
                    if p.get("Listed", True):
                        native_price = get_stock_price(sym)
                    else:
                        native_price = float(p.get("LTP") or 0.0)
                else:
                    native_price = get_nav(nav_df, sym)

                # Convert to INR — no-op (×1.0) for Indian stocks
                price_inr = to_inr(native_price, country, currency_pairs_map, fx_rates)

                # Asset target expected = Total × Sector % × Asset %
                expected = total_expected * (target_alloc / 100) * (alloc / 100)
                inflow   = max(0, expected - invested)

                total_inflow += inflow

                buy = math.ceil(inflow / price_inr) if price_inr > 0 else 0

                sip = inflow / st.session_state[sip_months_key] if inflow > 0 else 0
                total_sip += sip

                project_alloc_pct = (expected / portfolio_expected_sum * 100) if portfolio_expected_sum > 0 else 0.0

                rows.append({
                    "Symbol":       sym,
                    "Name":         name,
                    "Country":      country,
                    "LTP (INR)":    price_inr,
                    "Qty":          qty,
                    "Invested":     invested,
                    "Allocation %": alloc,
                    "Project Allocation %": project_alloc_pct,
                    "Expected":     expected,
                    "Inflow":       inflow,
                    "SIP":          sip,
                    "Buy":          buy,
                })

            df = pd.DataFrame(rows)
            display_df = df[df["Inflow"] > 0] if show_only_inflow else df

            # If show_only_inflow is active and no stock in this sector has inflow, hide the entire sector
            if show_only_inflow and display_df.empty:
                if not df.empty:
                    updates = df[["Symbol", "Name", "Allocation %"]].rename(
                        columns={"Allocation %": "Allocation"}
                    )
                    master_updates.extend(updates.to_dict("records"))
                continue

            with st.expander(
                f"📁 {sector_name} (Target Sector Allocation: {target_alloc}%) - Expected ₹{sector_expected:,.2f}",
                expanded=False
            ):
                if not sector_stocks:
                    st.info("No assets in this sector")
                    continue

                if display_df.empty:
                    st.session_state[f"symbols_{port_name}_{sector_name}"] = []
                    sector_sum = df["Allocation %"].sum()
                    if sector_sum > 100:
                        st.warning(f"⚠ Allocation exceeds 100% ({sector_sum:.2f}%)")
                    else:
                        st.caption(f"Sector total: {sector_sum:.2f}% / 100%")

                    updates = df[["Symbol", "Name", "Allocation %"]].rename(
                        columns={"Allocation %": "Allocation"}
                    )
                    master_updates.extend(updates.to_dict("records"))
                    continue

                # Save the displayed symbols list in session state so we can map edits on the next run
                st.session_state[f"symbols_{port_name}_{sector_name}"] = display_df["Symbol"].tolist()

                edited_df = st.data_editor(
                    display_df,
                    key=f"editor_{port_name}_{sector_name}",
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Name":         None,   # Hidden — used internally
                        "Country":      None,   # Hidden — used for FX only
                        "Allocation %": st.column_config.NumberColumn(
                            "Allocation %",
                            min_value=0.0,
                            max_value=100.0,
                            step=0.5
                        ),
                        "Project Allocation %": st.column_config.NumberColumn("Project Allocation %", format="%.2f%%"),
                        "LTP (INR)":    st.column_config.NumberColumn("LTP (INR)",   format="₹%.2f"),
                        "Invested":     st.column_config.NumberColumn("Invested",    format="₹%.2f"),
                        "Expected":     st.column_config.NumberColumn("Expected",    format="₹%.2f"),
                        "Inflow":       st.column_config.NumberColumn("Inflow",      format="₹%.2f"),
                        "SIP":          st.column_config.NumberColumn("SIP",         format="₹%.2f"),
                    },
                    disabled=[
                        "Symbol", "Name", "Country", "LTP (INR)",
                        "Qty", "Invested", "Project Allocation %", "Expected", "Inflow", "SIP", "Buy"
                    ]
                )

                df.update(edited_df)

                # Allocation validation
                sector_sum = df["Allocation %"].sum()
                if sector_sum > 100:
                    st.warning(f"⚠ Allocation exceeds 100% ({sector_sum:.2f}%)")
                else:
                    st.caption(f"Sector total: {sector_sum:.2f}% / 100%")

                updates = df[["Symbol", "Name", "Allocation %"]].rename(
                    columns={"Allocation %": "Allocation"}
                )
                master_updates.extend(updates.to_dict("records"))

        displayed_expected_investment = current_invested + total_inflow
        expected_metric_placeholder.metric("🎯 Expected Investment", f"₹{displayed_expected_investment:,.2f}")
        inflow_metric_placeholder.metric("💵 Total Inflow",          f"₹{total_inflow:,.2f}")
        sip_total_placeholder.metric("📊 Total SIP",                f"₹{total_sip:,.2f}")

        st.divider()
        submitted = st.button(
            f"💾 Save {port_name} Asset Allocations",
            type="primary",
            use_container_width=True,
            key=f"save_btn_{port_name}"
        )

        if submitted:
            with st.spinner("Saving allocations..."):
                success = db.upsert_stock_allocations(master_updates, port_name)

            if success:
                st.success("🎉 Allocations saved successfully!")
                # Clear session state edits, allocations state, and symbols list for this portfolio
                if alloc_state_key in st.session_state:
                    del st.session_state[alloc_state_key]
                for sector_row in db_sectors:
                    sec_name = sector_row.get("Sector")
                    if sec_name:
                        editor_key = f"editor_{port_name}_{sec_name}"
                        symbols_key = f"symbols_{port_name}_{sec_name}"
                        if editor_key in st.session_state:
                            del st.session_state[editor_key]
                        if symbols_key in st.session_state:
                            del st.session_state[symbols_key]
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Error saving allocations")