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

def get_stock_price(symbol):
    price = None
    if yf:
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


def search_international_stock(query):
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
            quotes = data.get("quotes", [])
            results = []
            for q in quotes:
                symbol = q.get("symbol", "")
                name = q.get("longname") or q.get("shortname") or symbol
                exchange = q.get("exchDisp", "")
                qtype = q.get("quoteType", "")
                if symbol and qtype in ("EQUITY", "ETF", "MUTUALFUND"):
                    results.append({"symbol": symbol, "name": name, "exchange": exchange, "type": qtype})
            return results
    except Exception:
        return []


@st.dialog("Search International Stock")
def search_international_stock_dialog(form_key_prefix="add"):
    search_q = st.text_input("Search by company name or ticker:", placeholder="e.g. Apple, AAPL, Samsung")
    if search_q and len(search_q) >= 2:
        with st.spinner("Searching..."):
            results = search_international_stock(search_q)
        if not results:
            st.warning("No results found. Try a different search term.")
        else:
            st.write(f"Found {len(results)} result(s):")
            for i, r in enumerate(results):
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    st.markdown(f"**{r['name']}** `{r['symbol']}` — {r['exchange']} ({r['type']})")
                with col_btn:
                    if st.button("Select", key=f"intl_sel_{form_key_prefix}_{i}"):
                        st.session_state[f"intl_selected_symbol_{form_key_prefix}"] = r["symbol"]
                        st.session_state[f"intl_selected_name_{form_key_prefix}"] = r["name"]
                        st.rerun()


st.title("Asset Management")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()
    
with st.spinner("Loading Database data..."):
    sectors_data = db.fetch_sectors()
    stocks_data = db.fetch_stocks()
    currency_pairs_data = db.fetch_currency_pairs()

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

# Session state for international stock symbol selection (Add form)
if 'intl_selected_symbol_add' not in st.session_state:
    st.session_state.intl_selected_symbol_add = ""

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

@st.dialog("Search Stock Info")
def search_stock_dialog():
    search_sym = st.text_input("Enter Stock Symbol (e.g. RELIANCE, TCS):")
    if search_sym:
        search_sym = search_sym.upper()
        with st.spinner("Fetching data..."):
            price, pe, company = None, "N/A", search_sym
            
            if yf:
                for suffix in [".NS", ".BO", ""]:
                    try:
                        ticker = yf.Ticker(search_sym + suffix)
                        p = ticker.fast_info.last_price
                        if p:
                            price = float(p)
                            info = ticker.info
                            company = info.get("longName") or info.get("shortName") or search_sym
                            pe = info.get("trailingPE", "N/A")
                            break
                    except Exception:
                        continue
                        
            if price:
                st.success(f"**{company}** ({search_sym})")
                m1, m2 = st.columns(2)
                m1.metric("Live Price", f"₹ {price}")
                m2.metric("PE Ratio", f"{pe}")
            else:
                st.error(f"Could not fetch details for {search_sym}. Invalid symbol or server blocked.")

st.subheader("Bulk Import Assets")
col_dl, col_ul, col_search = st.columns([1, 1, 1])

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
    if st.button("🔍 Search Stock", use_container_width=True):
        search_stock_dialog()

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

@st.dialog("Search Mutual Fund")
def search_mf_dialog():
    search_term = st.text_input("Enter Mutual Fund Name to Search:")
    if search_term and len(search_term) >= 3:
        if not nav_df.empty:
            matches = nav_df[nav_df['scheme_name'].str.contains(search_term, case=False, na=False)]
            if matches.empty:
                st.write("No matches found.")
            else:
                st.write(f"Found {len(matches)} matches:")
                for idx, row in matches.iterrows():
                    col_name, col_btn = st.columns([4, 1])
                    with col_name:
                        st.write(row['scheme_name'])
                    with col_btn:
                        if st.button("Select", key=f"sel_{idx}"):
                            st.session_state.selected_mf = row['scheme_name']
                            st.rerun()

# ==================== Add a New Stock/MF ====================
st.subheader("Add a New Stock / Mutual Fund")

# Radio buttons outside form so they can conditionally render the form fields
asset_type = st.radio("Asset Type", options=["Stock", "Mutual Fund"], horizontal=True)

if asset_type == "Mutual Fund":
    if st.button("🔍 Search Mutual Fund"):
        search_mf_dialog()
else:
    listing_status = st.radio("Listing Status", options=["Listed", "Unlisted"], horizontal=True)

# Country selector outside form to reactively show/hide the international search button
country_choice_pre = st.selectbox("Country", options=available_countries, key="add_country_pre")
is_non_india = country_choice_pre.upper() != "INDIA"

# Show international stock search button outside form when non-India country is selected
if is_non_india and asset_type == "Stock":
    col_mcap_label, col_intl_btn = st.columns([3, 1])
    with col_mcap_label:
        st.caption("💡 Use the button to search and auto-fill the stock symbol for this country.")
    with col_intl_btn:
        if st.button("🔍 Search Stock", key="intl_search_add_btn", use_container_width=True):
            search_international_stock_dialog(form_key_prefix="add")

# Using st.form prevents the page from reloading when typing or selecting dropdowns inside it
with st.form("add_asset_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        if asset_type == "Stock":
            prefill_name = st.session_state.get("intl_selected_name_add", "")
            prefill_symbol = st.session_state.get("intl_selected_symbol_add", "")
            stock_name = st.text_input("Name", value=prefill_name, placeholder="e.g., Reliance Industries")
            # Pre-fill symbol from international search if available
            stock_symbol = st.text_input("Symbol", value=prefill_symbol, placeholder="e.g., RELIANCE")
            market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "ETF", "NA"]
            market_cap = st.selectbox("Market Cap", options=market_cap_options)
        else:
            stock_name = st.text_input("Name (from search)", value=st.session_state.selected_mf)
            default_sym = st.session_state.selected_mf if st.session_state.selected_mf else "NA"
            stock_symbol = st.text_input("Symbol", value=default_sym, help="Not required for Mutual Funds")
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
                # Clear international symbol selection after successful add
                st.session_state.intl_selected_symbol_add = ""
                st.session_state.intl_selected_name_add = ""
                st.session_state.asset_added = True
                st.rerun()

st.divider()

# ==================== Existing Stocks/MFs ====================
st.subheader("Current Portfolio Assets")

if not stocks_data:
    st.info("No stocks or mutual funds found in the database. Use the form above to add your first asset!")
else:
    market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "Multi Cap", "ETF", "NA"]

    for item in stocks_data:
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
                # Session state key for this stock's international symbol selection
                intl_edit_key = f"intl_selected_symbol_edit_{sym}"
                intl_edit_name_key = f"intl_selected_name_edit_{sym}"
                if intl_edit_key not in st.session_state:
                    st.session_state[intl_edit_key] = ""
                if intl_edit_name_key not in st.session_state:
                    st.session_state[intl_edit_name_key] = ""

                # Country selector outside the form to reactively show international search button
                edit_country_pre = st.selectbox(
                    "Country",
                    options=available_countries,
                    index=available_countries.index(current_country) if current_country in available_countries else 0,
                    key=f"edit_country_pre_{sym}"
                )
                edit_is_non_india = edit_country_pre.upper() != "INDIA"

                # Show international stock search button when non-India country selected
                if edit_is_non_india and is_eq:
                    col_hint, col_intl_edit_btn = st.columns([3, 1])
                    with col_hint:
                        st.caption("💡 Search to auto-fill symbol for this country.")
                    with col_intl_edit_btn:
                        if st.button("🔍 Search Stock", key=f"intl_search_edit_btn_{sym}", use_container_width=True):
                            search_international_stock_dialog(form_key_prefix=f"edit_{sym}")

                with st.form(f"edit_form_{sym}"):
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        new_sym_input = st.text_input(
                            "Symbol",
                            value=st.session_state.get(intl_edit_key) or sym
                        )
                        new_name = st.text_input(
                            "Name",
                            value=st.session_state.get(intl_edit_name_key) or name
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
                        new_is_lst = new_listing == "Listed"
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
                                st.session_state[intl_edit_key] = ""
                                st.session_state[intl_edit_name_key] = ""
                                st.rerun()
                            else:
                                st.error(msg)
                        else:
                            # Standard Update
                            ok = db.update_stock(sym, new_name.strip(), new_is_eq, new_sector, new_is_lst, new_mcap, new_ltp, edit_country_pre)
                            if ok:
                                st.success(f"Updated '{sym}' successfully!")
                                st.session_state[intl_edit_key] = ""
                                st.session_state[intl_edit_name_key] = ""
                                st.rerun()

            with tab_delete:
                st.warning(f"Are you sure you want to delete **{name}** ({sym})? This cannot be undone.")
                if st.button("🗑️ Confirm Delete", key=f"del_{sym}", type="primary"):
                    ok = db.delete_stock(sym)
                    if ok:
                        st.success(f"Deleted '{sym}'.")
                        st.rerun()