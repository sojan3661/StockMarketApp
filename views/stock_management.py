import streamlit as st
import pandas as pd
import sys
import os
import io
import openpyxl

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

try:
    import yfinance as yf
except ImportError:
    yf = None

# Cache the mutual fund data from AMFI
@st.cache_data(ttl=18000)  # cache the dataframe for 5 hours
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
    except Exception as e:
        st.error(f"Error fetching Mutual Fund Data: {e}")
        return pd.DataFrame()

def _find_nav_in_df(nav_df, *identifiers):
    if nav_df.empty:
        return None
    for item in identifiers:
        if not item:
            continue
        term_str = str(item).strip()
        if not term_str or term_str.upper() in ("NA", "NONE", ""):
            continue
        term_lower = term_str.lower()
        term_upper = term_str.upper()
        if "scheme_code" in nav_df.columns:
            res = nav_df.loc[nav_df["scheme_code"].astype(str).str.strip() == term_str, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        isin_cols = [c for c in ["isin1", "isin2"] if c in nav_df.columns]
        if isin_cols:
            mask = pd.Series(False, index=nav_df.index)
            for col in isin_cols:
                mask = mask | (nav_df[col].astype(str).str.strip().str.upper() == term_upper)
            res = nav_df.loc[mask, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        if "scheme_name" in nav_df.columns:
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.strip().str.lower() == term_lower, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        if "scheme_name" in nav_df.columns and len(term_lower) >= 3:
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.lower().str.contains(term_lower, na=False, regex=False), ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
            short_term = term_lower[:15]
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.lower().str.contains(short_term, na=False, regex=False), ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
    return None

@st.cache_data(ttl=1800)
def fetch_yf_mf_nav(symbol, country="INDIA"):
    return get_stock_price(symbol, country)

def get_nav(nav_df, fund_name, fallback_name=None, country="INDIA"):
    res = _find_nav_in_df(nav_df, fund_name, fallback_name)
    if res is not None:
        return res
    yf_nav = fetch_yf_mf_nav(fund_name, country) or (fetch_yf_mf_nav(fallback_name, country) if fallback_name else None)
    if yf_nav is not None:
        return {"nav": yf_nav, "date": "Live (Yahoo)"}
    return None

def resolve_asset_ltp(item, nav_df):
    """
    Returns the Last Traded Price (LTP) / NAV for an asset item.
    - Listed Equity: Live stock price via yfinance, fallback to DB LTP.
    - Mutual Fund: Live NAV via AMFI data, fallback to DB LTP.
    - Unlisted Stock / Other: DB LTP.
    """
    sym = item.get("Symbol", "")
    name = item.get("Name", "")
    is_eq = item.get("Equity", True)
    is_lst = item.get("Listed", True)
    country = item.get("Country", "INDIA")
    db_ltp = item.get("LTP")

    if is_lst:
        if is_eq:
            price = get_stock_price(sym, country)
            if price is not None:
                try:
                    return float(price)
                except (ValueError, TypeError):
                    pass
            if db_ltp is not None:
                try:
                    return float(db_ltp)
                except (ValueError, TypeError):
                    pass
        else:
            # Mutual Fund: Check AMFI in-memory dataset FIRST (< 1ms)
            nav_res = _find_nav_in_df(nav_df, sym, name)
            if nav_res is not None:
                try:
                    return float(nav_res["nav"])
                except (ValueError, TypeError, KeyError):
                    pass
            # Fallback to yfinance if non-numeric symbol
            if sym and not str(sym).isdigit():
                price = get_stock_price(sym, country)
                if price is not None:
                    return float(price)
            if db_ltp is not None:
                try:
                    return float(db_ltp)
                except (ValueError, TypeError):
                    pass
    else:
        # Unlisted stock
        if db_ltp is not None:
            try:
                return float(db_ltp)
            except (ValueError, TypeError):
                pass

    return None


from concurrent.futures import ThreadPoolExecutor

@st.cache_data(ttl=1800)
def get_stock_price(symbol, country="INDIA"):
    if not yf or not symbol:
        return None
    s = str(symbol).strip()
    if not s or s.isdigit() or s.upper() in ("NA", "NONE", ""):
        return None

    country_upper = str(country).upper() if country else "INDIA"
    if s.endswith((".NS", ".BO")):
        targets = [s]
    elif s.startswith("0P"):
        targets = [s + ".BO", s]
    elif country_upper == "USA":
        targets = [s]
    else:
        targets = [s + ".NS", s + ".BO", s]

    for t_str in targets:
        try:
            ticker = yf.Ticker(t_str)
            p = getattr(ticker.fast_info, "last_price", None)
            if p and p > 0:
                return float(p)
            if t_str.startswith("0P"):
                hist = ticker.history(period="5d")
                if not hist.empty and "Close" in hist.columns:
                    series = hist["Close"].dropna()
                    if not series.empty:
                        val = float(series.iloc[-1])
                        if val > 0:
                            return val
        except Exception:
            continue
        if s.endswith((".NS", ".BO")):
            break
            
    return None


def batch_fetch_stock_prices(stocks_list):
    """Pre-fetch stock prices for multiple assets in parallel using ThreadPoolExecutor."""
    if not stocks_list or not yf:
        return
    items_to_fetch = [
        s for s in stocks_list
        if isinstance(s, dict) and s.get("Symbol") and s.get("Listed", True) and not str(s.get("Symbol", "")).isdigit()
    ]
    if not items_to_fetch:
        return

    def _worker(item):
        sym = item.get("Symbol")
        country = item.get("Country", "INDIA")
        get_stock_price(sym, country)

    with ThreadPoolExecutor(max_workers=min(25, len(items_to_fetch))) as executor:
        list(executor.map(_worker, items_to_fetch))


st.title("Asset Management")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

# -----------------------------------------------
# Load Data from Central Cache
# -----------------------------------------------
from Config.data_cache import get_global_app_data, refresh_all_data, get_stock_price, get_nav, resolve_asset_ltp

app_data = get_global_app_data()
sectors_data        = app_data.get("sectors", [])
stocks_data         = app_data.get("stocks", [])
currency_pairs_data = app_data.get("currency_pairs", [])
nav_df              = app_data.get("nav_df", pd.DataFrame())

existing_sectors = [s.get('Sector', '') for s in sectors_data if s.get('Sector')]
existing_symbols = [s.get('Symbol', '').upper() for s in stocks_data if s.get('Symbol')]

available_countries = sorted([cp.get('Country') for cp in currency_pairs_data if cp.get('Country')])
if not available_countries:
    available_countries = ["INDIA"]

# Pre-fill from session state if available
if 'selected_mf' not in st.session_state:
    st.session_state.selected_mf = ""

if 'selected_stock_symbol' not in st.session_state:
    st.session_state.selected_stock_symbol = ""
if 'selected_stock_name' not in st.session_state:
    st.session_state.selected_stock_name = ""
if 'selected_stock_country' not in st.session_state:
    st.session_state.selected_stock_country = ""

if 'add_asset_type' not in st.session_state:
    st.session_state.add_asset_type = "Stock"

if 'search_existing_query' not in st.session_state:
    st.session_state.search_existing_query = ""
if 'edit_target_symbol' not in st.session_state:
    st.session_state.edit_target_symbol = ""



# ==================== Bulk Import ====================
@st.cache_data
def generate_asset_template(sectors) -> bytes:
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Assets"

    # Headers
    ws.append(["Name", "Symbol", "Asset Type", "Market Cap", "Sector", "Listing Status", "LTP"])

    # Dropdown validation for Asset Type (C2:C1000)
    dv_type = DataValidation(type="list", formula1='"Stock,Mutual Fund"', allow_blank=False, showDropDown=False)
    dv_type.sqref = "C2:C1000"
    ws.add_data_validation(dv_type)

    # Dropdown validation for Market Cap (D2:D1000)
    dv_cap = DataValidation(type="list", formula1='"Large Cap,Mid Cap,Small Cap,Multi Cap,ETF,NA"', allow_blank=True, showDropDown=False)
    dv_cap.sqref = "D2:D1000"
    ws.add_data_validation(dv_cap)
    
    # Dropdown validation for Sector (E2:E1000)
    if sectors:
        sector_str = ",".join(sectors)
        dv_sector = DataValidation(type="list", formula1=f'"{sector_str}"', allow_blank=True, showDropDown=False)
        dv_sector.sqref = "E2:E1000"
        ws.add_data_validation(dv_sector)

    # Dropdown validation for Listing Status (F2:F1000)
    dv_list = DataValidation(type="list", formula1='"Listed,Unlisted"', allow_blank=True, showDropDown=False)
    dv_list.sqref = "F2:F1000"
    ws.add_data_validation(dv_list)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

def search_yahoo_stocks(query):
    """Search Yahoo Finance for stocks matching a query string."""
    import urllib.request
    import urllib.parse
    import json
    import ssl
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={encoded_query}&quotesCount=50&newsCount=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get("quotes", [])
    except Exception:
        return []

@st.dialog("Search Stock / Mutual Fund Info", width="large")
def search_stock_dialog():
    query = st.text_input("Enter Company, Mutual Fund, or Ticker (e.g. Infosys, Axis Bluechip, AAPL):")
    if query and len(query) >= 2:
        q_clean = query.strip().lower()

        # Search 0: Existing Portfolio Assets in DB
        existing_matches = [
            s for s in stocks_data
            if q_clean in str(s.get("Name", "")).lower()
            or q_clean in str(s.get("Symbol", "")).lower()
            or q_clean in str(s.get("Sector", "")).lower()
        ]

        # Search 1: Yahoo Finance (Stocks/ETFs/Funds)
        with st.spinner("Searching Yahoo Finance..."):
            yf_quotes = search_yahoo_stocks(query)
            
        # Search 2: AMFI Mutual Funds (Indian MFs)
        amfi_matches = []
        if not nav_df.empty:
            q_str = query.strip()
            mask = nav_df['scheme_name'].astype(str).str.contains(q_str, case=False, na=False)
            if 'scheme_code' in nav_df.columns:
                mask = mask | nav_df['scheme_code'].astype(str).str.contains(q_str, case=False, na=False)
            if 'isin1' in nav_df.columns:
                mask = mask | nav_df['isin1'].astype(str).str.contains(q_str, case=False, na=False)
            if 'isin2' in nav_df.columns:
                mask = mask | nav_df['isin2'].astype(str).str.contains(q_str, case=False, na=False)
            matches = nav_df[mask]
            if not matches.empty:
                amfi_matches = matches.to_dict("records")
                
        if not existing_matches and not yf_quotes and not amfi_matches:
            st.warning("No results found. Try a different search term.")
        else:
            st.write(f"Search Results for '{query}':")

            # Display Existing Database Assets
            if existing_matches:
                st.markdown(f"##### 📁 Existing Portfolio Assets (in Database) ({len(existing_matches)})")
                for i, item in enumerate(existing_matches):
                    sym   = item.get("Symbol", "")
                    name  = item.get("Name", "")
                    sec   = item.get("Sector", "Uncategorized")
                    mcap  = item.get("MarketCap", "NA")
                    is_eq = item.get("Equity", True)
                    a_type = "Stock" if is_eq else "Mutual Fund"
                    ltp_val = resolve_asset_ltp(item, nav_df)
                    ltp_str = f"₹{ltp_val:,.2f}" if ltp_val is not None else "LTP: N/A"

                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        st.markdown(f"**{name}** (`{sym}`) — **{ltp_str}** | {sec} | {mcap} ({a_type}) *(Existing)*")
                    with col_btn:
                        if st.button("✏️ Edit", key=f"db_edit_sel_{i}_{sym}"):
                            st.session_state.search_existing_query = sym
                            st.session_state.edit_target_symbol = sym
                            st.rerun()
            
            # Display Yahoo Finance Results
            valid_yf_quotes = [q for q in yf_quotes if q.get("symbol") and q.get("quoteType", "") in ("EQUITY", "ETF", "MUTUALFUND")]
            if valid_yf_quotes:
                st.markdown(f"##### 📈 Stocks / ETFs (Yahoo Finance) ({len(valid_yf_quotes)})")
                for i, q in enumerate(valid_yf_quotes):
                    symbol = q.get("symbol", "")
                    name = q.get("longname") or q.get("shortname") or symbol
                    exchange = q.get("exchDisp", "")
                    qtype = q.get("quoteType", "")
                    
                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        st.markdown(f"**{name}** (`{symbol}`) — {exchange} ({qtype})")
                    with col_btn:
                        if st.button("Select", key=f"yf_sel_{i}_{symbol}"):
                            if qtype == "MUTUALFUND":
                                st.session_state.add_asset_type = "Mutual Fund"
                                st.session_state.selected_mf = name
                                clean_s = symbol
                                if clean_s.endswith((".NS", ".BO")):
                                    clean_s = clean_s[:-3]
                                st.session_state.selected_stock_symbol = clean_s
                            else:
                                st.session_state.add_asset_type = "Stock"
                                st.session_state.selected_stock_symbol = symbol
                                st.session_state.selected_stock_name = name
                                if symbol.endswith(".NS") or symbol.endswith(".BO") or "NSE" in exchange.upper() or "BSE" in exchange.upper():
                                    st.session_state.selected_stock_country = "INDIA"
                                else:
                                    st.session_state.selected_stock_country = "USA"
                            st.rerun()
            
            # Display AMFI Results
            if amfi_matches:
                st.markdown(f"##### 📊 Mutual Funds (AMFI India) ({len(amfi_matches)})")
                for i, row in enumerate(amfi_matches):
                    scheme_name = row.get("scheme_name", "")
                    scheme_code = row.get("scheme_code", "")
                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        code_str = f" (`{scheme_code}`)" if pd.notna(scheme_code) and scheme_code else ""
                        st.markdown(f"**{scheme_name}**{code_str}")
                    with col_btn:
                        if st.button("Select", key=f"amfi_sel_{i}_{scheme_code}"):
                            st.session_state.add_asset_type = "Mutual Fund"
                            st.session_state.selected_mf = scheme_name
                            st.session_state.selected_stock_symbol = str(scheme_code) if scheme_code else ""
                            st.rerun()

@st.dialog("Search Yahoo Finance", width="large")
def search_yf_for_asset_dialog(target_sym, target_name):
    st.write(f"Search Yahoo Finance to look up ticker / price info for **{target_name or target_sym}**.")
    search_q = st.text_input("Yahoo Finance Search Query:", value=target_name or target_sym, key=f"yf_q_in_{target_sym}")
    if search_q and len(search_q.strip()) >= 2:
        with st.spinner("Searching Yahoo Finance..."):
            quotes = search_yahoo_stocks(search_q.strip())
        
        valid_quotes = [q for q in quotes if q.get("symbol") and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")]
        if not valid_quotes:
            st.warning(f"No Yahoo Finance results found for '{search_q}'. Try another symbol or company name.")
        else:
            st.markdown(f"##### 📈 Search Results (Yahoo Finance) ({len(valid_quotes)}):")
            for i, q in enumerate(valid_quotes):
                symbol   = q.get("symbol", "")
                q_name   = q.get("longname") or q.get("shortname") or symbol
                exchange = q.get("exchDisp", "")
                qtype    = q.get("quoteType", "")
                
                clean_sym = symbol
                if clean_sym.endswith(".NS"):
                    clean_sym = clean_sym[:-3]
                elif clean_sym.endswith(".BO"):
                    clean_sym = clean_sym[:-3]
                
                col_info, col_btn = st.columns([3, 1])
                with col_info:
                    st.markdown(f"**{q_name}** (`{symbol}`) — {exchange} ({qtype})")
                with col_btn:
                    if st.button("Apply Ticker", key=f"apply_yf_{i}_{target_sym}_{symbol}"):
                        is_india = ("NSE" in exchange.upper() or "BSE" in exchange.upper() or symbol.endswith(".NS") or symbol.endswith(".BO"))
                        c_country = "INDIA" if is_india else "USA"
                        
                        if target_sym == "add_new":
                            st.session_state.add_asset_type = "Mutual Fund" if qtype == "MUTUALFUND" else "Stock"
                            st.session_state.selected_stock_symbol = clean_sym
                            st.session_state.selected_stock_name = q_name
                            st.session_state.selected_stock_country = c_country
                        else:
                            st.session_state[f"applied_yf_sym_{target_sym}"] = clean_sym
                            st.session_state[f"applied_yf_name_{target_sym}"] = q_name
                            st.session_state[f"applied_yf_country_{target_sym}"] = c_country
                            st.session_state.edit_target_symbol = target_sym
                        st.rerun()

@st.dialog("Clean up Assets", width="large")
def cleanup_assets_dialog():
    with st.spinner("Checking asset allocations and transactions..."):
        tx_data = db.fetch_all_transactions()
        alloc_data = db.fetch_stock_allocations()

    symbols_with_tx = {str(t.get("Symbol", "")).strip().upper() for t in tx_data if t.get("Symbol")}
    symbols_with_open_tx = {
        str(t.get("Symbol", "")).strip().upper()
        for t in tx_data
        if t.get("Symbol") and (t.get("SellDate") is None and t.get("SellAvg") is None)
    }
    symbols_with_alloc = {
        str(a.get("Symbol", "")).strip().upper()
        for a in alloc_data
        if a.get("Symbol") and a.get("Allocation") is not None and float(a.get("Allocation")) > 0
    }

    # 1. Eligible for Delete: 0% allocation & NO transactions
    delete_eligible_assets = [
        s for s in stocks_data
        if str(s.get("Symbol", "")).strip().upper() not in symbols_with_tx
        and str(s.get("Symbol", "")).strip().upper() not in symbols_with_alloc
    ]

    # 2. Active Assets (0 open transactions, currently ACTIVE != False)
    active_to_inactive_assets = [
        s for s in stocks_data
        if str(s.get("Symbol", "")).strip().upper() in symbols_with_tx
        and str(s.get("Symbol", "")).strip().upper() not in symbols_with_open_tx
        and s.get("ACTIVE", True) is not False
    ]

    # 3. Currently Inactive Assets (ACTIVE == False)
    currently_inactive_assets = [
        s for s in stocks_data
        if s.get("ACTIVE") is False
    ]

    tab_delete, tab_status = st.tabs(["🧹 Delete Assets (No TX)", "🔄 Asset Status Management"])

    with tab_delete:
        if not delete_eligible_assets:
            st.info("ℹ️ No assets eligible for deletion. (All assets either have active portfolio allocations or transaction history).")
            col_ref, col_close = st.columns(2)
            with col_ref:
                if st.button("🔄 Refresh", use_container_width=True, key="del_empty_ref"):
                    st.cache_data.clear()
                    st.rerun()
            with col_close:
                if st.button("Close", use_container_width=True, key="del_empty_close"):
                    st.rerun()
        else:
            st.write(f"Found **{len(delete_eligible_assets)}** asset(s) with 0% allocation and no transactions:")

            table_data_del = [
                {
                    "Select": False,
                    "Symbol": s.get("Symbol", ""),
                    "Name": s.get("Name", ""),
                    "LTP": f"₹{resolve_asset_ltp(s, nav_df):,.2f}" if resolve_asset_ltp(s, nav_df) is not None else "N/A",
                    "Asset Type": "Stock" if s.get("Equity", True) else "Mutual Fund",
                    "Sector": s.get("Sector", "NA"),
                    "Market Cap": s.get("MarketCap", "NA")
                }
                for s in delete_eligible_assets
            ]
            df_del = pd.DataFrame(table_data_del)

            edited_df_del = st.data_editor(
                df_del,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Select",
                        help="Check to select specific assets to delete",
                        default=False,
                    )
                },
                disabled=["Symbol", "Name", "LTP", "Asset Type", "Sector", "Market Cap"],
                key="cleanup_delete_table"
            )

            selected_rows_del = edited_df_del[edited_df_del["Select"] == True]
            selected_syms_del = [str(sym).strip().upper() for sym in selected_rows_del["Symbol"].tolist()]
            eligible_syms_del = [str(s.get("Symbol", "")).strip().upper() for s in delete_eligible_assets if s.get("Symbol")]

            if not selected_syms_del:
                st.caption("💡 *No assets selected. Clicking 'Delete Assets' will select and delete **all** eligible assets listed in the table above.*")
            else:
                st.caption(f"💡 *{len(selected_syms_del)} of {len(eligible_syms_del)} asset(s) selected for deletion.*")

            st.markdown("---")
            col_del, col_ref, col_cancel = st.columns(3)

            with col_del:
                if st.button("🗑️ Delete Assets", type="primary", use_container_width=True, key="del_btn"):
                    targets = selected_syms_del if selected_syms_del else eligible_syms_del
                    success_count = 0
                    fail_count = 0

                    with st.spinner(f"Deleting {len(targets)} asset(s)..."):
                        for sym in targets:
                            if db.delete_stock(sym):
                                success_count += 1
                            else:
                                fail_count += 1

                    if success_count:
                        st.success(f"✅ Successfully deleted {success_count} asset(s).")
                    if fail_count:
                        st.error(f"❌ Failed to delete {fail_count} asset(s).")

                    st.cache_data.clear()
                    st.rerun()

            with col_ref:
                if st.button("🔄 Refresh", use_container_width=True, key="del_has_ref"):
                    st.cache_data.clear()
                    st.rerun()

            with col_cancel:
                if st.button("Cancel", use_container_width=True, key="del_cancel_btn"):
                    st.rerun()

    with tab_status:
        sub_active, sub_inactive = st.tabs(["🟢 Active Assets (Mark Inactive)", "🔴 Inactive Assets (Mark Active)"])

        with sub_active:
            if not active_to_inactive_assets:
                st.info("ℹ️ No active assets found with 0 open positions (all sold).")
                col_ref, col_close = st.columns(2)
                with col_ref:
                    if st.button("🔄 Refresh", use_container_width=True, key="act_empty_ref"):
                        st.cache_data.clear()
                        st.rerun()
                with col_close:
                    if st.button("Close", use_container_width=True, key="act_empty_close"):
                        st.rerun()
            else:
                st.write(f"Found **{len(active_to_inactive_assets)}** active asset(s) with 0 open positions (all sold):")

                table_data_act = [
                    {
                        "Select": False,
                        "Symbol": s.get("Symbol", ""),
                        "Name": s.get("Name", ""),
                        "LTP": f"₹{resolve_asset_ltp(s, nav_df):,.2f}" if resolve_asset_ltp(s, nav_df) is not None else "N/A",
                        "Asset Type": "Stock" if s.get("Equity", True) else "Mutual Fund",
                        "Sector": s.get("Sector", "NA"),
                        "Market Cap": s.get("MarketCap", "NA")
                    }
                    for s in active_to_inactive_assets
                ]
                df_act = pd.DataFrame(table_data_act)

                edited_df_act = st.data_editor(
                    df_act,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(
                            "Select",
                            help="Check to select specific assets to mark inactive",
                            default=False,
                        )
                    },
                    disabled=["Symbol", "Name", "LTP", "Asset Type", "Sector", "Market Cap"],
                    key="cleanup_active_table"
                )

                selected_rows_act = edited_df_act[edited_df_act["Select"] == True]
                selected_syms_act = [str(sym).strip().upper() for sym in selected_rows_act["Symbol"].tolist()]
                eligible_syms_act = [str(s.get("Symbol", "")).strip().upper() for s in active_to_inactive_assets if s.get("Symbol")]

                if not selected_syms_act:
                    st.caption("💡 *No assets selected. Clicking 'Mark as Inactive' will select and mark **all** listed assets as inactive.*")
                else:
                    st.caption(f"💡 *{len(selected_syms_act)} of {len(eligible_syms_act)} asset(s) selected.*")

                st.markdown("---")
                col_inact, col_ref, col_cancel = st.columns(3)

                with col_inact:
                    if st.button("💤 Mark as Inactive", type="primary", use_container_width=True, key="act_to_inact_btn"):
                        targets = selected_syms_act if selected_syms_act else eligible_syms_act
                        success_count = 0
                        fail_count = 0

                        with st.spinner(f"Setting {len(targets)} asset(s) to Inactive..."):
                            for sym in targets:
                                if db.set_stock_active_status(sym, is_active=False):
                                    success_count += 1
                                else:
                                    fail_count += 1

                        if success_count:
                            st.success(f"✅ Successfully marked {success_count} asset(s) as Inactive.")
                        if fail_count:
                            st.error(f"❌ Failed to update {fail_count} asset(s).")

                        st.cache_data.clear()
                        st.rerun()

                with col_ref:
                    if st.button("🔄 Refresh", use_container_width=True, key="act_has_ref"):
                        st.cache_data.clear()
                        st.rerun()

                with col_cancel:
                    if st.button("Cancel", use_container_width=True, key="act_cancel_btn"):
                        st.rerun()

        with sub_inactive:
            if not currently_inactive_assets:
                st.info("ℹ️ No inactive assets currently found in the database.")
                col_ref, col_close = st.columns(2)
                with col_ref:
                    if st.button("🔄 Refresh", use_container_width=True, key="inact_empty_ref"):
                        st.cache_data.clear()
                        st.rerun()
                with col_close:
                    if st.button("Close", use_container_width=True, key="inact_empty_close"):
                        st.rerun()
            else:
                st.write(f"Found **{len(currently_inactive_assets)}** currently inactive asset(s):")

                table_data_inact = [
                    {
                        "Select": False,
                        "Symbol": s.get("Symbol", ""),
                        "Name": s.get("Name", ""),
                        "LTP": f"₹{resolve_asset_ltp(s, nav_df):,.2f}" if resolve_asset_ltp(s, nav_df) is not None else "N/A",
                        "Asset Type": "Stock" if s.get("Equity", True) else "Mutual Fund",
                        "Sector": s.get("Sector", "NA"),
                        "Market Cap": s.get("MarketCap", "NA")
                    }
                    for s in currently_inactive_assets
                ]
                df_inact = pd.DataFrame(table_data_inact)

                edited_df_inact = st.data_editor(
                    df_inact,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(
                            "Select",
                            help="Check to select specific assets to mark active",
                            default=False,
                        )
                    },
                    disabled=["Symbol", "Name", "LTP", "Asset Type", "Sector", "Market Cap"],
                    key="cleanup_inactive_table"
                )

                selected_rows_inact = edited_df_inact[edited_df_inact["Select"] == True]
                selected_syms_inact = [str(sym).strip().upper() for sym in selected_rows_inact["Symbol"].tolist()]
                eligible_syms_inact = [str(s.get("Symbol", "")).strip().upper() for s in currently_inactive_assets if s.get("Symbol")]

                if not selected_syms_inact:
                    st.caption("💡 *No assets selected. Clicking 'Mark as Active' will select and mark **all** listed assets as active.*")
                else:
                    st.caption(f"💡 *{len(selected_syms_inact)} of {len(eligible_syms_inact)} asset(s) selected.*")

                st.markdown("---")
                col_act, col_ref, col_cancel = st.columns(3)

                with col_act:
                    if st.button("⚡ Mark as Active", type="primary", use_container_width=True, key="inact_to_act_btn"):
                        targets = selected_syms_inact if selected_syms_inact else eligible_syms_inact
                        success_count = 0
                        fail_count = 0

                        with st.spinner(f"Setting {len(targets)} asset(s) to Active..."):
                            for sym in targets:
                                if db.set_stock_active_status(sym, is_active=True):
                                    success_count += 1
                                else:
                                    fail_count += 1

                        if success_count:
                            st.success(f"✅ Successfully marked {success_count} asset(s) as Active.")
                        if fail_count:
                            st.error(f"❌ Failed to update {fail_count} asset(s).")

                        st.cache_data.clear()
                        st.rerun()

                with col_ref:
                    if st.button("🔄 Refresh", use_container_width=True, key="inact_has_ref"):
                        st.cache_data.clear()
                        st.rerun()

                with col_cancel:
                    if st.button("Cancel", use_container_width=True, key="inact_cancel_btn"):
                        st.rerun()

st.subheader("Bulk Import Assets")
col_dl, col_ul, col_search, col_cleanup, col_refresh = st.columns([1, 1, 1, 1, 1])

with col_dl:
    import base64
    b64_data = base64.b64encode(generate_asset_template(existing_sectors)).decode()
    href = f'<a href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64_data}" download="asset_template.xlsx" style="display: block; width: 100%; padding: 0.5rem 1rem; background-color: #2D333B; border: 1px solid #4B5563; color: #E2E8F0; text-align: center; text-decoration: none; border-radius: 8px; font-weight: 500; box-sizing: border-box; transition: background-color 0.2s;">📥 Download Asset Template</a>'
    st.markdown(href, unsafe_allow_html=True)

with col_ul:
    uploaded_asset_file = st.file_uploader(
        "Upload Assets",
        type=["xlsx"],
        label_visibility="collapsed"
    )

with col_search:
    if st.button("🔍 Search Assets", use_container_width=True):
        search_stock_dialog()

with col_cleanup:
    if st.button("🧹 Clean up Assets", use_container_width=True):
        cleanup_assets_dialog()

with col_refresh:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if uploaded_asset_file is not None:
    with st.expander("📋 Preview & Import Uploaded Assets", expanded=True):
        try:
            upload_df = pd.read_excel(uploaded_asset_file, dtype=str)
            upload_df.columns = [c.strip() for c in upload_df.columns]
            
            required_cols = {"Name", "Symbol", "Asset Type", "Sector"}
            missing = required_cols - set(upload_df.columns)
            
            if missing:
                st.error(f"Missing columns in file: {', '.join(missing)}")
            else:
                st.dataframe(upload_df, use_container_width=True, hide_index=True)
                
                if st.button("🚀 Import All Assets", type="primary"):
                    success_count = 0
                    fail_count = 0
                    
                    for i, row in upload_df.iterrows():
                        name   = str(row.get("Name", "")).strip()
                        sym    = str(row.get("Symbol", "")).strip().upper()
                        a_type = str(row.get("Asset Type", "Stock")).strip()
                        cap    = str(row.get("Market Cap", "NA")).strip()
                        sec    = str(row.get("Sector", "NA")).strip()
                        l_stat = str(row.get("Listing Status", "Listed")).strip()
                        ltp_val = row.get("LTP")
                        try:
                            ltp = float(ltp_val) if pd.notna(ltp_val) else None
                        except:
                            ltp = None
                        
                        if not name or not sym or pd.isna(name) or pd.isna(sym):
                            st.warning(f"Row {i+2}: Missing Name or Symbol — skipped.")
                            fail_count += 1
                            continue
                            
                        # Prevent duplicates
                        if sym in existing_symbols and a_type == "Stock":
                            st.warning(f"Row {i+2}: Symbol '{sym}' already exists — skipped.")
                            fail_count += 1
                            continue
                            
                        is_eq = True if a_type == "Stock" else False
                        is_lst = True if l_stat == "Listed" or not is_eq else False
                        
                        ok = db.add_stock(sym, name, is_eq, sec, is_lst, cap, ltp)
                        if ok:
                            success_count += 1
                            existing_symbols.append(sym) # add to local cache to prevent duplicates within same sheet
                        else:
                            fail_count += 1
                            
                    if success_count:
                        st.success(f"✅ {success_count} asset(s) imported successfully.")
                    if fail_count:
                        st.error(f"❌ {fail_count} asset(s) failed — see errors/warnings above.")
                        
        except Exception as e:
            st.error(f"Error reading file: {e}")

st.divider()



# ==================== Add a New Stock/MF ====================
st.subheader("Add a New Stock / Mutual Fund")

col_add_hdr1, col_add_hdr2 = st.columns([3, 1])
with col_add_hdr1:
    st.caption("Fill in asset details below or search Yahoo Finance to pre-fill info.")
with col_add_hdr2:
    if st.button("🔍 Search Yahoo Finance for Ticker", key="btn_search_yf_add", use_container_width=True):
        search_yf_for_asset_dialog("add_new", st.session_state.get("selected_stock_name") or st.session_state.get("selected_stock_symbol") or "")

# Radio buttons outside form so they can conditionally render the form fields
asset_type = st.radio("Asset Type", options=["Stock", "Mutual Fund"], horizontal=True, key="add_asset_type")

if asset_type == "Stock":
    listing_status = st.radio("Listing Status", options=["Listed", "Unlisted"], horizontal=True)

# Country selector outside form
default_country = st.session_state.get("selected_stock_country") or "INDIA"
if default_country in available_countries:
    country_index = available_countries.index(default_country)
else:
    country_index = 0

country_choice_pre = st.selectbox("Country", options=available_countries, index=country_index, key="add_country_pre")

# Using st.form prevents the page from reloading when typing or selecting dropdowns inside it
with st.form("add_asset_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        if asset_type == "Stock":
            prefill_name = st.session_state.get("selected_stock_name", "")
            prefill_symbol = st.session_state.get("selected_stock_symbol", "")
            
            clean_symbol = prefill_symbol
            if clean_symbol.endswith(".NS"):
                clean_symbol = clean_symbol[:-3]
            elif clean_symbol.endswith(".BO"):
                clean_symbol = clean_symbol[:-3]

            stock_name = st.text_input("Name", value=prefill_name, placeholder="e.g., Reliance Industries")
            col_sym, col_price = st.columns([2, 1])
            with col_sym:
                stock_symbol = st.text_input("Symbol", value=clean_symbol, placeholder="e.g., RELIANCE")
            with col_price:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                if stock_symbol and asset_type == "Stock" and listing_status == "Listed":
                    price = get_stock_price(stock_symbol)
                    if price:
                        st.markdown(f"<p style='margin: 0; padding-top: 10px; font-size: 14px;'><b>LTP:</b> {price:,.2f}</p>", unsafe_allow_html=True)
                    else:
                        st.markdown("<p style='margin: 0; padding-top: 10px; font-size: 14px; color: #ff4b4b;'>⚠️ LTP not found</p>", unsafe_allow_html=True)
            market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "ETF", "NA"]
            market_cap = st.selectbox("Market Cap", options=market_cap_options)
        else:
            stock_name = st.text_input("Name (from search)", value=st.session_state.selected_mf)
            default_sym = st.session_state.selected_mf if st.session_state.selected_mf else "NA"
            col_sym, col_price = st.columns([2, 1])
            with col_sym:
                stock_symbol = st.text_input("Symbol", value=default_sym, help="Not required for Mutual Funds")
            with col_price:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                if stock_name:
                    res = get_nav(nav_df, stock_name)
                    if res is not None:
                        try:
                            nav_val = float(res['nav'])
                            st.markdown(f"<p style='margin: 0; padding-top: 10px; font-size: 14px;'><b>NAV:</b> {nav_val:,.2f}</p>", unsafe_allow_html=True)
                        except Exception:
                            st.markdown(f"<p style='margin: 0; padding-top: 10px; font-size: 14px;'><b>NAV:</b> {res['nav']}</p>", unsafe_allow_html=True)
                    else:
                        st.markdown("<p style='margin: 0; padding-top: 10px; font-size: 14px; color: #ff4b4b;'>⚠️ NAV not found</p>", unsafe_allow_html=True)
            market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "ETF", "Multi Cap", "NA"]
            market_cap = st.selectbox("Market Cap / Category", options=market_cap_options, index=5)
        
        if asset_type == "Stock":
            if listing_status == "Unlisted":
                stock_ltp = st.number_input("Last Traded Price (LTP)", min_value=0.0, step=0.1, help="Manual LTP for unlisted stocks")
            else:
                stock_ltp = None
        else:
            stock_ltp = None

    with col2:
        sector_choice = st.selectbox("Sector", options=existing_sectors if existing_sectors else ["NA"])

    # Submission
    submitted = st.form_submit_button("Preview & Add Asset", type="primary")

if submitted:
    stock_name = stock_name.strip()
    stock_symbol = stock_symbol.strip().upper()
    
    if not stock_name or not stock_symbol:
        st.warning("Please provide both Name and Symbol.")
    elif stock_symbol in existing_symbols and asset_type == "Stock":
        st.warning(f"The symbol '{stock_symbol}' already exists in your table.")
    else:
        is_listed = True if (asset_type == "Stock" and listing_status == "Listed") or (asset_type == "Mutual Fund") else False
        is_equity = True if asset_type == "Stock" else False
        
        # 1. Preview Prices
        price_preview_success = False
        if asset_type == "Stock" and is_listed:
            price = get_stock_price(stock_symbol)
            if price is not None:
                st.info(f"📈 Live Price for {stock_symbol}: ₹{price}")
                price_preview_success = True
            else:
                st.warning(f"⚠️ Could not fetch live price for {stock_symbol}. Adding anyway...")
                price_preview_success = True # Still allow add
        elif asset_type == "Mutual Fund":
            res = get_nav(nav_df, stock_name)
            if res is not None:
                st.info(f"📊 Live NAV for {stock_name}: ₹{res['nav']} (As of {res['date']})")
                price_preview_success = True
                if stock_symbol == "NA":
                    stock_symbol = str(res.name) if hasattr(res, 'name') else stock_name[:10]
            else:
                st.warning(f"⚠️ Could not find exact Mutual Fund name '{stock_name}' in AMFI data. Adding anyway...")
                price_preview_success = True
        else:
            # Unlisted stock
            price_preview_success = True

        # 2. Insert to DB
        if price_preview_success:
            success = db.add_stock(stock_symbol, stock_name, is_equity, sector_choice, is_listed, market_cap, stock_ltp, country_choice_pre)
            if success:
                st.success(f"Successfully added: {stock_name} ({stock_symbol})")
                if asset_type == "Mutual Fund":
                    st.session_state.selected_mf = ""
                else:
                    st.session_state.selected_stock_symbol = ""
                    st.session_state.selected_stock_name = ""
                    st.session_state.selected_stock_country = ""
                st.session_state.asset_added = True
                st.rerun()

st.divider()

# ==================== Existing Stocks/MFs ====================
st.subheader("Current Portfolio Assets")

if not stocks_data:
    st.info("No stocks or mutual funds found in the database. Use the form above to add your first asset!")
else:
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        search_asset_query = st.text_input(
            "🔍 Search Existing Assets for Updating / Viewing",
            value=st.session_state.get("search_existing_query", ""),
            placeholder="Type Name, Symbol, Sector, or Market Cap...",
            key="existing_asset_search_input"
        )
        st.session_state.search_existing_query = search_asset_query
    with col_s2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("Clear Filter", use_container_width=True, key="clear_asset_search"):
            st.session_state.search_existing_query = ""
            st.session_state.edit_target_symbol = ""
            st.rerun()

    display_stocks_data = stocks_data
    if search_asset_query.strip():
        sq = search_asset_query.strip().lower()
        display_stocks_data = [
            item for item in stocks_data
            if sq in str(item.get("Name", "")).lower()
            or sq in str(item.get("Symbol", "")).lower()
            or sq in str(item.get("Sector", "")).lower()
            or sq in str(item.get("MarketCap", "")).lower()
        ]
        if not display_stocks_data:
            st.info(f"No existing assets matched '{search_asset_query}'.")

    target_edit_sym = st.session_state.get("edit_target_symbol", "").upper()
    if target_edit_sym:
        target_item = next((item for item in stocks_data if item.get("Symbol", "").upper() == target_edit_sym), None)
        if target_item:
            st.success(f"✏️ Selected **{target_item.get('Name')}** (`{target_edit_sym}`) for updating.")

    market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "Multi Cap", "ETF", "NA"]

    # Group assets by Sector
    assets_by_sector = {}
    for item in display_stocks_data:
        sec = item.get("Sector") or "Uncategorized"
        if sec not in assets_by_sector:
            assets_by_sector[sec] = []
        assets_by_sector[sec].append(item)
    
    # Sort sector names (Uncategorized at the end)
    sorted_sectors = sorted([s for s in assets_by_sector.keys() if s != "Uncategorized"])
    if "Uncategorized" in assets_by_sector:
        sorted_sectors.append("Uncategorized")
        
    for sector_name in sorted_sectors:
        sector_assets = assets_by_sector[sector_name]
        
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 10px; margin-top: 25px; margin-bottom: 15px; border-bottom: 2px solid #3B82F620; padding-bottom: 8px;">
                <h3 style="margin: 0; font-size: 1.4rem;">📁 {sector_name}</h3>
                <span style="background-color: #3B82F620; color: #3B82F6; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: bold; margin-top: 4px;">{len(sector_assets)}</span>
            </div>
            """, 
            unsafe_allow_html=True
        )
        
        for item in sector_assets:
            sym   = item.get("Symbol", "Unknown")
            name  = item.get("Name", "Unknown")
            is_eq = item.get("Equity", False)
            sec   = item.get("Sector", "Unknown")
            is_lst = item.get("Listed", True)
            mcap  = item.get("MarketCap", "NA")

            a_type   = "Stock" if is_eq else "Mutual Fund"
            l_status = "Listed" if is_lst else "Unlisted"
            ltp_val  = resolve_asset_ltp(item, nav_df)
            ltp_str  = f"₹{ltp_val:,.2f}" if ltp_val is not None else "LTP: N/A"
            current_country = item.get("Country", "INDIA")
            
            # Adding a visual tag for Asset type and Listing status
            color_tag = "#3B82F6" if a_type == "Stock" else "#4ADE80"
            is_auto_expanded = (target_edit_sym and sym.upper() == target_edit_sym) or (len(display_stocks_data) == 1)

            with st.expander(f"{name} ({sym}) — {ltp_str} | {mcap}", expanded=is_auto_expanded):
                ltp_badge = f'<span style="background-color: #F59E0B20; color: #F59E0B; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">LTP: ₹{ltp_val:,.2f}</span>' if ltp_val is not None else '<span style="background-color: #EF444420; color: #EF4444; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">LTP: N/A</span>'
                st.markdown(
                    f"""
                    <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                        <span style="background-color: {color_tag}20; color: {color_tag}; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">{a_type}</span>
                        <span style="background-color: #4B556350; color: #9CA3AF; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">{l_status}</span>
                        <span style="background-color: #6366F120; color: #818CF8; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">{sec}</span>
                        {ltp_badge}
                    </div>
                    """, unsafe_allow_html=True
                )
                tab_edit, tab_delete = st.tabs(["✏️ Edit", "🗑️ Delete"])

                with tab_edit:
                    applied_sym = st.session_state.get(f"applied_yf_sym_{sym}", sym)
                    applied_name = st.session_state.get(f"applied_yf_name_{sym}", name)
                    applied_country = st.session_state.get(f"applied_yf_country_{sym}", current_country)

                    col_yf1, col_yf2 = st.columns([3, 1])
                    with col_yf1:
                        if st.session_state.get(f"applied_yf_sym_{sym}"):
                            st.success(f"Applied Yahoo Finance ticker: `{applied_sym}` ({applied_name})")
                        else:
                            st.caption(f"Search Yahoo Finance specifically for `{sym}` to update ticker/name.")
                    with col_yf2:
                        if st.button("🔍 Search Yahoo Finance", key=f"btn_search_yf_{sym}", use_container_width=True):
                            search_yf_for_asset_dialog(sym, name)

                    # Country selector outside the form
                    edit_country_pre = st.selectbox(
                        "Country",
                        options=available_countries,
                        index=available_countries.index(applied_country) if applied_country in available_countries else 0,
                        key=f"edit_country_pre_{sym}"
                    )

                    with st.form(f"edit_form_{sym}"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            new_sym_input = st.text_input(
                                "Symbol",
                                value=applied_sym
                            )
                            new_name = st.text_input(
                                "Name",
                                value=applied_name
                            )
                            new_mcap = st.selectbox(
                                "Market Cap",
                                options=market_cap_options,
                                index=market_cap_options.index(mcap) if mcap in market_cap_options else len(market_cap_options) - 1
                            )
                        with ec2:
                            new_sector = st.selectbox(
                                "Sector",
                                options=existing_sectors,
                                index=existing_sectors.index(sec) if sec in existing_sectors else 0
                            )
                            db_ltp = item.get("LTP")
                            if not is_lst or is_eq:
                                 new_ltp = st.number_input("Last Traded Price (LTP)", value=float(db_ltp or ltp_val or 0.0), min_value=0.0, step=0.1, key=f"ltp_edit_{sym}")
                            else:
                                 new_ltp = None
                                 
                            new_asset_type = st.selectbox(
                                "Asset Type",
                                options=["Stock", "Mutual Fund"],
                                index=0 if is_eq else 1
                            )
                            new_listing = st.selectbox(
                                "Listing Status",
                                options=["Listed", "Unlisted"],
                                index=0 if is_lst else 1
                            )

                        save_btn = st.form_submit_button("💾 Save Changes", type="primary")
                        if save_btn:
                            st.session_state.pop(f"applied_yf_sym_{sym}", None)
                            st.session_state.pop(f"applied_yf_name_{sym}", None)
                            st.session_state.pop(f"applied_yf_country_{sym}", None)

                            new_is_eq  = new_asset_type == "Stock"
                            new_is_lst = True if new_asset_type == "Mutual Fund" else (new_listing == "Listed")
                            new_s = new_sym_input.strip()
                            
                            if not new_s or not new_name.strip():
                                st.error("Symbol and Name cannot be empty.")
                            elif new_s != sym:
                                # Symbol has changed - Trigger migration
                                with st.spinner(f"Migrating symbol from '{sym}' to '{new_s}'..."):
                                    success, msg = db.update_stock_symbol(
                                        old_symbol=sym,
                                        new_symbol=new_s,
                                        name=new_name.strip(),
                                        is_equity=new_is_eq,
                                        sector=new_sector,
                                        is_listed=new_is_lst,
                                        market_cap=new_mcap,
                                        ltp=new_ltp,
                                        country=edit_country_pre
                                    )
                                if success:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                            else:
                                # Standard Update
                                ok = db.update_stock(sym, new_name.strip(), new_is_eq, new_sector, new_is_lst, new_mcap, new_ltp, edit_country_pre)
                                if ok:
                                    st.success(f"Updated '{sym}' successfully!")
                                    st.rerun()

                with tab_delete:
                    st.warning(f"Are you sure you want to delete **{name}** ({sym})? This cannot be undone.")
                    if st.button("🗑️ Confirm Delete", key=f"del_{sym}", type="primary"):
                        ok = db.delete_stock(sym)
                        if ok:
                            st.success(f"Deleted '{sym}'.")
                            st.rerun()