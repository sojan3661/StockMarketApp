import streamlit as st
import pandas as pd
import numpy as np
import sys
import os
import datetime
import concurrent.futures
from dateutil.relativedelta import relativedelta

# Append parent directory to path to import Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

try:
    import yfinance as yf
except ImportError:
    yf = None

# -----------------------------------------------
# Country Currency Mapping
# -----------------------------------------------
COUNTRY_CURRENCY = {
    "INDIA": {"code": "INR", "symbol": "₹"},
    "USA": {"code": "USD", "symbol": "$"},
    "US": {"code": "USD", "symbol": "$"},
    "UK": {"code": "GBP", "symbol": "£"},
    "GB": {"code": "GBP", "symbol": "£"},
    "EU": {"code": "EUR", "symbol": "€"},
    "EUROPE": {"code": "EUR", "symbol": "€"},
    "HK": {"code": "HKD", "symbol": "HK$"},
    "HONG KONG": {"code": "HKD", "symbol": "HK$"},
    "JP": {"code": "JPY", "symbol": "¥"},
    "JAPAN": {"code": "JPY", "symbol": "¥"},
    "AU": {"code": "AUD", "symbol": "A$"},
    "AUSTRALIA": {"code": "AUD", "symbol": "A$"},
    "CA": {"code": "CAD", "symbol": "C$"},
    "CANADA": {"code": "CAD", "symbol": "C$"},
    "CH": {"code": "CHF", "symbol": "Fr"},
    "SWITZERLAND": {"code": "CHF", "symbol": "Fr"},
}

# -----------------------------------------------
# FX helpers (consistent with Portfolio page)
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
    """How many INR = 1 unit of country's base currency."""
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


def convert_price(native_price, country, currency_pairs_map, fx_rates, use_usd):
    """Convert native_price of a country's stock to display currency (INR or USD)."""
    country = (country or "INDIA").upper()
    try:
        val = float(native_price or 0.0)
    except (ValueError, TypeError):
        val = 0.0
    rate = _inr_per_unit(country, currency_pairs_map, fx_rates)
    inr_price = val * (rate if rate is not None else 1.0)

    if not use_usd:
        return inr_price
    else:
        usd_inr = _usdinr(currency_pairs_map, fx_rates)
        if usd_inr <= 0:
            usd_inr = 84.0
        return inr_price / usd_inr

# -----------------------------------------------
# yfinance Actions Fetching & Caching
# -----------------------------------------------
@st.cache_data(ttl=21600)  # Caches data for 6 hours
def fetch_ticker_corporate_actions(symbol, country):
    """Fetches dividends and splits for a symbol from yfinance."""
    if not yf:
        return {"success": False, "ticker": symbol, "error": "yfinance not installed", "dividends": {}, "splits": {}}
    
    suffixes = [""]
    if (country or "INDIA").upper() == "INDIA":
        suffixes = [".NS", ".BO"]
    
    for suffix in suffixes:
        ticker_symbol = symbol + suffix
        try:
            ticker = yf.Ticker(ticker_symbol)
            # Fetching dividends and splits (triggers network fetch)
            dividends = ticker.dividends
            splits = ticker.splits
            
            if isinstance(dividends, pd.Series):
                div_dict = {str(k.date()): float(v) for k, v in dividends.items() if v > 0}
            elif isinstance(dividends, pd.DataFrame) and not dividends.empty:
                div_dict = {str(k.date()): float(v) for k, v in dividends.iloc[:, 0].items() if v > 0}
            else:
                div_dict = {}

            # Check calendar for upcoming dividends
            calendar = ticker.calendar
            if isinstance(calendar, dict) and "Ex-Dividend Date" in calendar:
                ex_date = calendar["Ex-Dividend Date"]
                if isinstance(ex_date, datetime.date):
                    date_str = str(ex_date)
                    if date_str not in div_dict:
                        info = ticker.info
                        val = info.get("lastDividendValue") or info.get("dividendRate") or 0.0
                        if val > 0:
                            div_dict[date_str] = float(val)

            if isinstance(splits, pd.Series):
                split_dict = {str(k.date()): float(v) for k, v in splits.items() if v > 0}
            elif isinstance(splits, pd.DataFrame) and not splits.empty:
                split_dict = {str(k.date()): float(v) for k, v in splits.iloc[:, 0].items() if v > 0}
            else:
                split_dict = {}
            
            return {
                "success": True,
                "ticker": ticker_symbol,
                "dividends": div_dict,
                "splits": split_dict
            }
        except Exception:
            continue
            
    try:
        ticker = yf.Ticker(symbol)
        dividends = ticker.dividends
        splits = ticker.splits
        
        if isinstance(dividends, pd.Series):
            div_dict = {str(k.date()): float(v) for k, v in dividends.items() if v > 0}
        elif isinstance(dividends, pd.DataFrame) and not dividends.empty:
            div_dict = {str(k.date()): float(v) for k, v in dividends.iloc[:, 0].items() if v > 0}
        else:
            div_dict = {}

        # Check calendar for upcoming dividends in fallback
        calendar = ticker.calendar
        if isinstance(calendar, dict) and "Ex-Dividend Date" in calendar:
            ex_date = calendar["Ex-Dividend Date"]
            if isinstance(ex_date, datetime.date):
                date_str = str(ex_date)
                if date_str not in div_dict:
                    info = ticker.info
                    val = info.get("lastDividendValue") or info.get("dividendRate") or 0.0
                    if val > 0:
                        div_dict[date_str] = float(val)

        if isinstance(splits, pd.Series):
            split_dict = {str(k.date()): float(v) for k, v in splits.items() if v > 0}
        elif isinstance(splits, pd.DataFrame) and not splits.empty:
            split_dict = {str(k.date()): float(v) for k, v in splits.iloc[:, 0].items() if v > 0}
        else:
            split_dict = {}
            
        return {
            "success": True,
            "ticker": symbol,
            "dividends": div_dict,
            "splits": split_dict
        }
    except Exception as e:
        return {
            "success": False,
            "ticker": symbol,
            "error": str(e),
            "dividends": {},
            "splits": {}
        }


def fetch_all_actions(active_stocks):
    """Fetches corporate actions for all active stocks in parallel."""
    results = {}
    if not active_stocks:
        return results
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(active_stocks))) as executor:
        future_to_stock = {
            executor.submit(fetch_ticker_corporate_actions, s["Symbol"], s.get("Country", "INDIA")): s
            for s in active_stocks
        }
        for future in concurrent.futures.as_completed(future_to_stock):
            stock = future_to_stock[future]
            try:
                results[stock["Symbol"]] = future.result()
            except Exception as e:
                results[stock["Symbol"]] = {
                    "success": False,
                    "ticker": stock["Symbol"],
                    "error": str(e),
                    "dividends": {},
                    "splits": {}
                }
    return results

# -----------------------------------------------
# Share Holdings Calculation (Date-based FIFO)
# -----------------------------------------------
def get_shares_held_on_date(transactions, symbol, target_date, portfolio=None):
    """
    Calculates exact quantity of shares held on target_date.
    A transaction row is counted if: BuyDate < target_date AND (SellDate is None OR SellDate >= target_date).
    """
    t_date = pd.to_datetime(target_date).date()
    qty = 0.0
    for tx in transactions:
        if tx.get("Symbol") != symbol:
            continue
        if portfolio and tx.get("Portfolio") != portfolio:
            continue
            
        buy_date_str = tx.get("BuyDate")
        if not buy_date_str:
            continue
        
        try:
            buy_date = pd.to_datetime(buy_date_str).date()
        except Exception:
            continue
            
        if buy_date < t_date:
            sell_date_str = tx.get("SellDate")
            if sell_date_str:
                try:
                    sell_date = pd.to_datetime(sell_date_str).date()
                except Exception:
                    sell_date = None
                    
                if sell_date and sell_date >= t_date:
                    qty += float(tx.get("Qty", 0.0))
            else:
                qty += float(tx.get("Qty", 0.0))
    return qty

# -----------------------------------------------
# Helper to Format Split Ratio
# -----------------------------------------------
def format_split_ratio(ratio):
    """Formats splits ratio like 2.0 to 'Split 2:1', or 1.5 to 'Split 3:2'."""
    if ratio == 0:
        return "0:1"
    if ratio > 1:
        if ratio.is_integer():
            return f"Split {int(ratio)}:1"
        else:
            from fractions import Fraction
            frac = Fraction(ratio).limit_denominator(10)
            return f"Split {frac.numerator}:{frac.denominator}"
    else:
        inv = 1 / ratio
        if inv.is_integer():
            return f"Reverse Split 1:{int(inv)}"
        else:
            from fractions import Fraction
            frac = Fraction(ratio).limit_denominator(10)
            return f"Split {frac.numerator}:{frac.denominator}"



# -----------------------------------------------
# Streamlit Interface Rendering
# -----------------------------------------------
st.title("Corporate Actions 📅")

# Verify yfinance installation
if not yf:
    st.error("⚠️ The `yfinance` package is not installed. Please add it to requirements or install manually.")
    st.stop()

# USD Display State Initialization
if "view_in_usd" not in st.session_state:
    st.session_state.view_in_usd = False

def toggle_usd():
    st.session_state.view_in_usd = not st.session_state.view_in_usd

st.checkbox("View in USD", value=st.session_state.view_in_usd, on_change=toggle_usd, key="corp_actions_usd_cb")

# DB configuration check
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()

# 1. Fetch DB records from central cache
from Config.data_cache import get_global_app_data, refresh_all_data

app_data = get_global_app_data()
db_stocks          = app_data.get("stocks", [])
open_transactions  = app_data.get("open_transactions", [])
all_transactions   = app_data.get("all_transactions", [])
db_investment_plan = app_data.get("investment_plan", [])
db_currency_pairs  = app_data.get("currency_pairs", [])

# 2. Determine active equity stocks (where quantity > 0 currently)
tx_agg = {}
for tx in open_transactions:
    sym = tx.get("Symbol")
    qty = float(tx.get("Qty", 0.0))
    if sym:
        tx_agg[sym] = tx_agg.get(sym, 0.0) + qty

stocks_map = {s["Symbol"]: s for s in db_stocks}
active_stocks = []
for sym, qty in tx_agg.items():
    if qty > 0 and sym in stocks_map:
        s = stocks_map[sym]
        # Skip unlisted or non-equity assets for yfinance
        if s.get("Equity") == True and s.get("Listed", True) != False:
            active_stocks.append(s)

if not active_stocks:
    st.info("💡 You do not currently hold any active listed stock investments (invested amount > 0). Add stock transactions to view corporate actions.")
    st.stop()

# 3. Load live currency rates
currency_pairs_map = {
    cp["Country"].upper(): cp
    for cp in (db_currency_pairs if isinstance(db_currency_pairs, list) else [])
    if cp.get("Country")
}
_cp_tuples = tuple(sorted(
    (c, cp.get("Symbol", "")) for c, cp in currency_pairs_map.items()
))
fx_rates = build_fx_cache(_cp_tuples)

# 4. Fetch corporate actions from yfinance
with st.spinner(f"Fetching corporate actions for {len(active_stocks)} active stocks..."):
    actions_data = fetch_all_actions(active_stocks)

# 5. Top level layout controls
action_type_option = st.selectbox(
    "Action Type Filter",
    ["All Corporate Actions", "Dividends Only", "Stock Splits & Bonuses Only"]
)

# 6. Setup portfolio-based tabs
plans_list = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]
tab_names = ["Overall Portfolio"] + portfolio_names
tabs = st.tabs(tab_names)

# Render tab contents
for i, tab_name in enumerate(tab_names):
    with tabs[i]:
        active_portfolio = tab_name if tab_name != "Overall Portfolio" else None
        st.divider()
        st.subheader(f"Upcoming Corporate Actions (Next 60 Days) for {tab_name}")
        
        events = []
        today = datetime.date.today()
        limit_date = today + datetime.timedelta(days=60)
        
        # Loop through active stocks and compile corporate action events
        for stock in active_stocks:
            sym = stock["Symbol"]
            name = stock["Name"]
            country = stock.get("Country", "INDIA")
            
            act = actions_data.get(sym, {})
            if not act or not act.get("success"):
                continue
                
            # A. Process Dividends
            divs = act.get("dividends", {})
            for date_str, val in divs.items():
                try:
                    date_val = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                    
                if today <= date_val <= limit_date:
                    # Quantity held in this portfolio on the ex-date
                    shares = get_shares_held_on_date(all_transactions, sym, date_val, portfolio=active_portfolio)
                    if shares > 0:
                        events.append({
                            "Date": date_val,
                            "Symbol": sym,
                            "Name": name,
                            "Type": "Dividend",
                            "RawValue": val,
                            "Shares Held": shares,
                            "Country": country
                        })
                        
            # B. Process Splits (which represent splits & bonus issues)
            splits = act.get("splits", {})
            for date_str, val in splits.items():
                try:
                    date_val = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                    
                if today <= date_val <= limit_date:
                    # Quantity held in this portfolio on the split-date
                    shares = get_shares_held_on_date(all_transactions, sym, date_val, portfolio=active_portfolio)
                    if shares > 0:
                        events.append({
                            "Date": date_val,
                            "Symbol": sym,
                            "Name": name,
                            "Type": "Stock Split / Bonus",
                            "RawValue": val,
                            "Shares Held": shares,
                            "Country": country
                        })
                        
        # Filter by selected action type
        if action_type_option == "Dividends Only":
            events = [e for e in events if e["Type"] == "Dividend"]
        elif action_type_option == "Stock Splits & Bonuses Only":
            events = [e for e in events if e["Type"] == "Stock Split / Bonus"]

        if not events:
            st.info(f"No upcoming corporate actions found in the next 60 days for {tab_name}.")
            continue
            
        # Compile events DataFrame
        df_events = pd.DataFrame(events)
        df_events = df_events.sort_values(by="Date", ascending=True) # Sort chronological for upcoming actions
        
        use_usd = st.session_state.get("view_in_usd", False)
        disp_symbol = "$" if use_usd else "₹"
        
        formatted_records = []
        total_dividends_display = 0.0
        dividend_count = 0
        split_count = 0
        chart_dividends = []
        
        for _, row in df_events.iterrows():
            e_date = row["Date"]
            sym = row["Symbol"]
            name = row["Name"]
            e_type = row["Type"]
            raw_val = row["RawValue"]
            shares = row["Shares Held"]
            country = row["Country"]
            
            # Retrieve native currency symbol
            cur_info = COUNTRY_CURRENCY.get(country.upper(), {"code": "INR", "symbol": "₹"})
            native_sym = cur_info["symbol"]
            
            if e_type == "Dividend":
                # Value representation
                val_str = f"{native_sym} {raw_val:.2f}"
                
                # Estimated payout in native currency
                payout_native = shares * raw_val
                payout_native_str = f"{native_sym} {payout_native:,.2f}"
                
                # Converted payout in display currency
                payout_converted = convert_price(payout_native, country, currency_pairs_map, fx_rates, use_usd)
                payout_converted_str = f"{disp_symbol} {payout_converted:,.2f}"
                
                total_dividends_display += payout_converted
                dividend_count += 1
                
                formatted_records.append({
                    "Date": e_date.strftime("%d-%b-%Y"),
                    "Symbol": sym,
                    "Asset Name": name,
                    "Action Type": "🟢 Dividend",
                    "Value (Per Share)": val_str,
                    "Shares Held": shares,
                    "Estimated Payout (Native)": payout_native_str,
                    "Estimated Payout": payout_converted_str,
                })
                
                chart_dividends.append({
                    "Symbol": sym,
                    "Name": name,
                    "Payout": payout_converted,
                    "Date": e_date,
                    "Month": e_date.strftime("%b %Y"),
                    "Quarter": f"{e_date.year}-Q{(e_date.month-1)//3 + 1}",
                    "MonthSort": e_date.strftime("%Y-%m")
                })
                
            else: # Stock Split / Bonus
                val_str = format_split_ratio(raw_val)
                
                # Impact
                new_shares = shares * raw_val
                diff = new_shares - shares
                sign = "+" if diff >= 0 else ""
                payout_native_str = "N/A"
                payout_converted_str = f"New Qty: {new_shares:,.2f} ({sign}{diff:+,.2f} shares)"
                
                split_count += 1
                
                formatted_records.append({
                    "Date": e_date.strftime("%d-%b-%Y"),
                    "Symbol": sym,
                    "Asset Name": name,
                    "Action Type": "🔄 Stock Split / Bonus",
                    "Value (Per Share)": val_str,
                    "Shares Held": shares,
                    "Estimated Payout (Native)": payout_native_str,
                    "Estimated Payout": payout_converted_str,
                })
                
        # 1. Summary Cards
        m1, m2, m3 = st.columns(3)
        m1.metric("💰 Total Upcoming Dividends", f"{disp_symbol} {total_dividends_display:,.2f}")
        m2.metric("📈 Upcoming Ex-Dividend Events", f"{dividend_count}")
        m3.metric("🔄 Upcoming Splits / Bonuses", f"{split_count}")
        
        st.write("")
        
        # 2. Main Data Grid
        df_display = pd.DataFrame(formatted_records)
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Date": st.column_config.TextColumn("Ex-Date"),
                "Symbol": st.column_config.TextColumn("Symbol"),
                "Asset Name": st.column_config.TextColumn("Asset Name"),
                "Action Type": st.column_config.TextColumn("Action Type"),
                "Value (Per Share)": st.column_config.TextColumn("Value / Ratio"),
                "Shares Held": st.column_config.NumberColumn("Shares Held on Ex-Date", format="%.4f"),
                "Estimated Payout (Native)": st.column_config.TextColumn("Estimated Payout (Native)"),
                "Estimated Payout": st.column_config.TextColumn(f"Estimated Payout / Impact ({disp_symbol})"),
            }
        )

