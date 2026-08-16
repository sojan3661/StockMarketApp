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

def get_nav(nav_df, fund_name):
    if nav_df.empty:
        return None
    result = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav","date"]]
    return result.iloc[0] if not result.empty else None

from concurrent.futures import ThreadPoolExecutor

@st.cache_data(ttl=600)
def get_stock_price(symbol):
    price = None
    if yf and symbol:
        for suffix in [".NS", ".BO", ""]:
            try:
                ticker = yf.Ticker(symbol + suffix)
                p = ticker.fast_info.last_price
                if p and p > 0:
                    price = float(p)
                    return price
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


st.title("Asset Management")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

@st.cache_data(ttl=300)
def load_stock_mgmt_db_data():
    return (
        db.fetch_sectors(),
        db.fetch_stocks(),
        db.fetch_currency_pairs()
    )
    
with st.spinner("Loading Database data..."):
    sectors_data, stocks_data, currency_pairs_data = load_stock_mgmt_db_data()

    # Parallel pre-fetch stock prices for all listed stocks
    all_symbols = [
        s.get("Symbol") for s in stocks_data
        if s.get("Symbol") and s.get("Equity", True) and s.get("Listed", True)
    ]
    batch_fetch_stock_prices(all_symbols)

existing_sectors = [s.get('Sector', '') for s in sectors_data if s.get('Sector')]
existing_symbols = [s.get('Symbol', '').upper() for s in stocks_data if s.get('Symbol')]

available_countries = sorted([cp.get('Country') for cp in currency_pairs_data if cp.get('Country')])
if not available_countries:
    available_countries = ["INDIA"]

# Load MF data silently into cache
nav_df = load_nav_data()

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
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={encoded_query}&quotesCount=10&newsCount=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get("quotes", [])
    except Exception:
        return []

@st.dialog("Search Stock / Mutual Fund Info")
def search_stock_dialog():
    query = st.text_input("Enter Company, Mutual Fund, or Ticker (e.g. Infosys, Axis Bluechip, AAPL):")
    if query and len(query) >= 2:
        # Search 1: Yahoo Finance (Stocks/ETFs/Funds)
        with st.spinner("Searching Yahoo Finance..."):
            yf_quotes = search_yahoo_stocks(query)
            
        # Search 2: AMFI Mutual Funds (Indian MFs)
        amfi_matches = []
        if not nav_df.empty:
            matches = nav_df[nav_df['scheme_name'].str.contains(query, case=False, na=False)]
            if not matches.empty:
                amfi_matches = matches.head(5).to_dict("records")
                
        if not yf_quotes and not amfi_matches:
            st.warning("No results found. Try a different search term.")
        else:
            st.write(f"Search Results for '{query}':")
            
            # Display Yahoo Finance Results
            valid_yf_quotes = [q for q in yf_quotes if q.get("symbol") and q.get("quoteType", "") in ("EQUITY", "ETF", "MUTUALFUND")]
            if valid_yf_quotes:
                st.markdown("##### 📈 Stocks / ETFs (Yahoo Finance)")
                for i, q in enumerate(valid_yf_quotes[:5]):
                    symbol = q.get("symbol", "")
                    name = q.get("longname") or q.get("shortname") or symbol
                    exchange = q.get("exchDisp", "")
                    qtype = q.get("quoteType", "")
                    
                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        st.markdown(f"**{name}** (`{symbol}`) — {exchange} ({qtype})")
                    with col_btn:
                        if st.button("Select", key=f"yf_sel_{i}"):
                            if qtype == "MUTUALFUND":
                                st.session_state.add_asset_type = "Mutual Fund"
                                st.session_state.selected_mf = name
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
                st.markdown("##### 📊 Mutual Funds (AMFI India)")
                for i, row in enumerate(amfi_matches):
                    scheme_name = row.get("scheme_name", "")
                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        st.markdown(f"**{scheme_name}**")
                    with col_btn:
                        if st.button("Select", key=f"amfi_sel_{i}"):
                            st.session_state.add_asset_type = "Mutual Fund"
                            st.session_state.selected_mf = scheme_name
                            st.rerun()

@st.dialog("Clean up Assets")
def cleanup_assets_dialog():
    with st.spinner("Checking asset allocations and transactions..."):
        tx_data = db.fetch_all_transactions()
        alloc_data = db.fetch_stock_allocations()

    symbols_with_tx = {str(t.get("Symbol", "")).strip().upper() for t in tx_data if t.get("Symbol")}
    symbols_with_alloc = {
        str(a.get("Symbol", "")).strip().upper()
        for a in alloc_data
        if a.get("Symbol") and a.get("Allocation") is not None and float(a.get("Allocation")) > 0
    }

    eligible_assets = [
        s for s in stocks_data
        if str(s.get("Symbol", "")).strip().upper() not in symbols_with_tx
        and str(s.get("Symbol", "")).strip().upper() not in symbols_with_alloc
    ]

    if not eligible_assets:
        st.info("ℹ️ No assets eligible for cleanup. All assets either have transaction history or active portfolio allocations (>0%).")
        col_ref, col_close = st.columns(2)
        with col_ref:
            if st.button("🔄 Refresh", use_container_width=True, key="dlg_empty_ref"):
                st.cache_data.clear()
                st.rerun()
        with col_close:
            if st.button("Close", use_container_width=True):
                st.rerun()
        return

    st.write(f"Found **{len(eligible_assets)}** asset(s) with 0% allocation and no transactions:")

    table_data = [
        {
            "Select": False,
            "Symbol": s.get("Symbol", ""),
            "Name": s.get("Name", ""),
            "Asset Type": "Stock" if s.get("Equity", True) else "Mutual Fund",
            "Sector": s.get("Sector", "NA"),
            "Market Cap": s.get("MarketCap", "NA")
        }
        for s in eligible_assets
    ]
    df_eligible = pd.DataFrame(table_data)

    edited_df = st.data_editor(
        df_eligible,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Select": st.column_config.CheckboxColumn(
                "Select",
                help="Check to select specific assets to delete",
                default=False,
            )
        },
        disabled=["Symbol", "Name", "Asset Type", "Sector", "Market Cap"],
        key="cleanup_assets_table"
    )

    selected_rows = edited_df[edited_df["Select"] == True]
    selected_symbols = [str(sym).strip().upper() for sym in selected_rows["Symbol"].tolist()]
    eligible_symbols = [str(s.get("Symbol", "")).strip().upper() for s in eligible_assets if s.get("Symbol")]

    if not selected_symbols:
        st.caption("💡 *No assets selected. Clicking 'Delete Assets' will select and delete **all** eligible assets listed in the table above.*")
    else:
        st.caption(f"💡 *{len(selected_symbols)} of {len(eligible_symbols)} asset(s) selected for deletion.*")

    st.markdown("---")
    col_del, col_ref, col_cancel = st.columns(3)

    with col_del:
        if st.button("🗑️ Delete Assets", type="primary", use_container_width=True):
            targets = selected_symbols if selected_symbols else eligible_symbols
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
        if st.button("🔄 Refresh", use_container_width=True, key="dlg_has_ref"):
            st.cache_data.clear()
            st.rerun()

    with col_cancel:
        if st.button("Cancel", use_container_width=True):
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
    market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "Multi Cap", "ETF", "NA"]

    # Group assets by Sector
    assets_by_sector = {}
    for item in stocks_data:
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
            ltp      = item.get("LTP")
            current_country = item.get("Country", "INDIA")
            
            # Adding a visual tag for Asset type and Listing status
            color_tag = "#3B82F6" if a_type == "Stock" else "#4ADE80"
            
            with st.expander(f"{name} ({sym}) — {mcap}"):
                st.markdown(
                    f"""
                    <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                        <span style="background-color: {color_tag}20; color: {color_tag}; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">{a_type}</span>
                        <span style="background-color: #4B556350; color: #9CA3AF; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">{l_status}</span>
                        <span style="background-color: #6366F120; color: #818CF8; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">{sec}</span>
                        {f'<span style="background-color: #F59E0B20; color: #F59E0B; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">LTP: ₹{ltp}</span>' if ltp is not None else ''}
                    </div>
                    """, unsafe_allow_html=True
                )
                tab_edit, tab_delete = st.tabs(["✏️ Edit", "🗑️ Delete"])

                with tab_edit:
                    # Country selector outside the form
                    edit_country_pre = st.selectbox(
                        "Country",
                        options=available_countries,
                        index=available_countries.index(current_country) if current_country in available_countries else 0,
                        key=f"edit_country_pre_{sym}"
                    )

                    with st.form(f"edit_form_{sym}"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            new_sym_input = st.text_input(
                                "Symbol",
                                value=sym
                            )
                            new_name = st.text_input(
                                "Name",
                                value=name
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
                            if not is_lst or is_eq:
                                 new_ltp = st.number_input("Last Traded Price (LTP)", value=float(ltp or 0.0), min_value=0.0, step=0.1, key=f"ltp_edit_{sym}")
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