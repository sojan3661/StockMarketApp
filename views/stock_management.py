import streamlit as st
import pandas as pd
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

# Optionally import nsepython
try:
    from nsepython import nse_eq
except ImportError:
    nse_eq = None

# Cache the mutual fund data from AMFI
@st.cache_data(ttl=3600)  # cache the dataframe for 1 hour
def load_nav_data():
    try:
        nav_df = pd.read_csv(
            "https://www.amfiindia.com/spages/NAVAll.txt",
            sep=";",
            header=None,
            names=[
                "scheme_code",
                "isin1",
                "isin2",
                "scheme_name",
                "nav",
                "date"
            ],
            on_bad_lines="skip"
        )
        return nav_df
    except Exception as e:
        st.error(f"Error fetching Mutual Fund Data: {e}")
        return pd.DataFrame()

def get_nav(nav_df, fund_name):
    if nav_df.empty:
        return None
    result = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav","date"]]
    return result.iloc[0] if not result.empty else None

def get_stock_price(symbol):
    if nse_eq:
        try:
            quote = nse_eq(symbol)
            if 'priceInfo' in quote and 'lastPrice' in quote['priceInfo']:
                return quote['priceInfo']['lastPrice']
        except Exception as e:
            # Revert to gentle handling in case symbol doesn't exist
            pass
    return None

st.title("Stock Management")
st.write("Manage your stocks and mutual funds portfolio.")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()
    
with st.spinner("Loading Database data..."):
    sectors_data = db.fetch_sectors()
    stocks_data = db.fetch_stocks()

existing_sectors = [s.get('Sector', '') for s in sectors_data if s.get('Sector')]
existing_symbols = [s.get('Symbol', '').upper() for s in stocks_data if s.get('Symbol')]

# Load MF data silently into cache
nav_df = load_nav_data()

# Pre-fill from session state if available
if 'selected_mf' not in st.session_state:
    st.session_state.selected_mf = ""

@st.dialog("Search Mutual Fund")
def search_mf_dialog():
    search_term = st.text_input("Enter Mutual Fund Name to Search:")
    if search_term and len(search_term) >= 3:
        if not nav_df.empty:
            matches = nav_df[nav_df['scheme_name'].str.contains(search_term, case=False, na=False)]
            if matches.empty:
                st.write("No matches found.")
            else:
                st.write(f"Found {len(matches)} matches. Showing top 20:")
                for idx, row in matches.head(20).iterrows():
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

# Using st.form prevents the page from reloading when typing or selecting dropdowns inside it
with st.form("add_asset_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        if asset_type == "Stock":
            stock_name = st.text_input("Name", placeholder="e.g., Reliance Industries")
            stock_symbol = st.text_input("Symbol", placeholder="e.g., RELIANCE")
            market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "ETF", "NA"]
            market_cap = st.selectbox("Market Cap", options=market_cap_options)
        else:
            stock_name = st.text_input("Name (from search)", value=st.session_state.selected_mf)
            default_sym = st.session_state.selected_mf if st.session_state.selected_mf else "NA"
            stock_symbol = st.text_input("Symbol", value=default_sym, help="Not required for Mutual Funds")
            market_cap_options = ["Large Cap", "Mid Cap", "Small Cap", "ETF", "Multi Cap", "NA"]
            market_cap = st.selectbox("Market Cap / Category", options=market_cap_options, index=5)
        
    with col2:
        if existing_sectors:
            sector_choice = st.selectbox("Sector", options=existing_sectors)
        else:
            sector_choice = st.text_input("Sector (No DB sectors found)")

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
            success = db.add_stock(stock_symbol, stock_name, is_equity, sector_choice, is_listed, market_cap)
            if success:
                st.success(f"Successfully added: {stock_name} ({stock_symbol})")
                if asset_type == "Mutual Fund":
                    st.session_state.selected_mf = "" 
                st.session_state.asset_added = True # trigger to clear or show state
                st.rerun()

st.divider()

# ==================== Existing Stocks/MFs ====================
st.subheader("Current Portfolio Assets")

if not stocks_data:
    st.info("No stocks or mutual funds found in the database. Use the form above to add your first asset!")
else:
    for item in stocks_data:
        sym = item.get("Symbol", "Unknown")
        name = item.get("Name", "Unknown")
        is_eq = item.get("Equity", False)
        sec = item.get("Sector", "Unknown")
        is_lst = item.get("Listed", True)
        mcap = item.get("MarketCap", "NA")
        
        col_info, col_action = st.columns([5, 1])
        with col_info:
            a_type = "Stock" if is_eq else "Mutual Fund"
            l_status = "Listed" if is_lst else "Unlisted"
            st.write(f"**{name}** ({sym}) - *{a_type} ({l_status})* | Cap: {mcap} | Sector: {sec}")
            
        with col_action:
            if st.button("Delete", key=f"del_{sym}", use_container_width=True):
                success = db.delete_stock(sym)
                if success:
                    st.success(f"Deleted '{sym}'.")
                    st.rerun()
