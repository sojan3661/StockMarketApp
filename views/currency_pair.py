import streamlit as st
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Currency Pairs Management")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

# -----------------------------------------------
# Load Data from Central Cache
# -----------------------------------------------
from Config.data_cache import get_global_app_data, refresh_all_data

app_data = get_global_app_data()
currency_pairs_data = app_data.get("currency_pairs", [])

# Extract existing countries to prevent duplicates
existing_countries = [cp.get('Country', '').upper() for cp in currency_pairs_data if cp.get('Country')]

st.divider()

# ==================== Add a New Currency Pair ====================
st.subheader("Add a New Currency Pair")
with st.form("add_currency_pair_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        new_country = st.text_input("Country (Primary Key)", placeholder="e.g., INDIA, US, UK...")
        new_base = st.text_input("Base Currency", placeholder="e.g., INR, USD, GBP...")
    with col2:
        new_pair = st.text_input("Pair Currency (Optional)", placeholder="e.g., USDINR (Leave blank for default)")
        new_symbol = st.text_input("Symbol (Optional)", placeholder="e.g., ₹, $, £")
        
    submitted = st.form_submit_button("Add Currency Pair")
    
    if submitted:
        new_country = new_country.strip()
        new_base = new_base.strip()
        new_pair = new_pair.strip() if new_pair.strip() else None
        new_symbol = new_symbol.strip() if new_symbol.strip() else None
        
        if not new_country or not new_base:
            st.warning("Please provide at least a Country and Base Currency.")
        elif new_country.upper() in existing_countries:
            st.warning(f"A currency pair for '{new_country}' already exists.")
        else:
            success = db.add_currency_pair(new_country, new_base, new_pair, new_symbol)
            if success:
                st.success(f"Successfully added currency pair for '{new_country}'")
                st.rerun()

st.divider()

# ==================== Existing Currency Pairs ====================
st.subheader("Current Currency Pairs")

@st.dialog("Edit Currency Pair")
def edit_currency_pair_dialog(country, base_currency, pair_currency, symbol):
    st.info(f"Editing settings for Country: **{country}**")
    
    new_base = st.text_input("Base Currency", value=base_currency)
    new_pair = st.text_input("Pair Currency (Optional)", value=pair_currency if pair_currency else "")
    new_symbol = st.text_input("Symbol (Optional)", value=symbol if symbol else "")
    
    if st.button("💾 Save Changes", type="primary"):
        new_base = new_base.strip()
        new_pair = new_pair.strip() if new_pair.strip() else None
        new_symbol = new_symbol.strip() if new_symbol.strip() else None
        
        if not new_base:
            st.error("Base Currency cannot be empty.")
        else:
            with st.spinner(f"Updating currency pair for '{country}'..."):
                success = db.update_currency_pair(country, new_base, new_pair, new_symbol)
            if success:
                st.success(f"Updated currency pair for '{country}' successfully!")
                st.rerun()

@st.dialog("Delete Currency Pair")
def delete_currency_pair_dialog(country):
    st.warning(f"Are you sure you want to delete the currency pair for **{country}**? This cannot be undone.")
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("🗑️ Yes, Delete", type="primary", use_container_width=True):
            with st.spinner(f"Deleting '{country}'..."):
                ok = db.delete_currency_pair(country)
            if ok:
                st.success(f"Deleted '{country}'.")
                st.rerun()
    with col_no:
        if st.button("Cancel", use_container_width=True):
            st.rerun()

if not currency_pairs_data:
    st.info("No currency pairs found in the database. Use the form above to add your first currency pair!")
else:
    # Render table header using st.columns
    st.markdown(
        """
        <div style="background-color: #1E293B; padding: 12px 16px; border-radius: 8px; border: 1px solid #334155; margin-bottom: 16px;">
        """, unsafe_allow_html=True
    )
    
    header_cols = st.columns([2, 2, 2, 1, 1, 1])
    header_cols[0].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Country</span>", unsafe_allow_html=True)
    header_cols[1].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Base Currency</span>", unsafe_allow_html=True)
    header_cols[2].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Pair Currency</span>", unsafe_allow_html=True)
    header_cols[3].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Symbol</span>", unsafe_allow_html=True)
    header_cols[4].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Edit</span>", unsafe_allow_html=True)
    header_cols[5].markdown("<span style='color: #F8FAFC; font-weight: bold;'>Delete</span>", unsafe_allow_html=True)
    
    st.markdown("<hr style='margin: 0.5rem 0; border-color: #334155;'>", unsafe_allow_html=True)
    
    for item in currency_pairs_data:
        country = item.get("Country", "Unknown")
        base = item.get("BaseCurrency", "Unknown")
        pair = item.get("PairCurrency") or "—"
        symbol = item.get("Symbol") or "—"
        
        row_cols = st.columns([2, 2, 2, 1, 1, 1])
        row_cols[0].write(f"**{country}**")
        row_cols[1].write(base)
        row_cols[2].markdown(f"<span style='background: #3B82F620; color: #60A5FA; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;'>{pair}</span>", unsafe_allow_html=True)
        row_cols[3].markdown(f"<span style='font-size: 1.1rem; color: #FCD34D;'>{symbol}</span>", unsafe_allow_html=True)
        
        if row_cols[4].button("✏️", key=f"edit_{country}", help="Edit"):
            edit_currency_pair_dialog(country, item.get("BaseCurrency", ""), item.get("PairCurrency", ""), item.get("Symbol", ""))
        
        if row_cols[5].button("🗑️", key=f"del_{country}", help="Delete"):
            delete_currency_pair_dialog(country)
            
        st.markdown("<hr style='margin: 0.5rem 0; border-color: #334155; opacity: 0.3;'>", unsafe_allow_html=True)
        
    st.markdown("</div>", unsafe_allow_html=True)
