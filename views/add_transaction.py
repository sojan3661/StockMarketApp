import streamlit as st
import datetime
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Add Transaction")
st.write("Record your individual Buy and Sell orders to build your historical tracking.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
with st.spinner("Loading available assets..."):
    db_stocks = db.fetch_stocks()

# Create a list of available symbols from StockManagement
if not db_stocks:
    available_symbols = []
    st.info("No assets found in your portfolio yet. Go to 'Stock Management' to start adding assets before recording transactions.")
else:
    available_symbols = sorted([p.get("Symbol", "") for p in db_stocks if p.get("Symbol", "")])

st.subheader("Transaction Details")

# Ensure that user is aware that they need a Transactions table
st.info("💡 Note: Ensure you have a `Transactions` table in Supabase with columns: `Symbol` (text), `Type` (text), `Quantity` (numeric), `Price` (numeric), and `Date` (date).")

with st.form("add_transaction_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        selected_symbol = st.selectbox(
            "Asset Symbol", 
            options=available_symbols,
            help="Select an asset you've already added via Stock Management."
        )
        
        transaction_type = st.radio(
            "Transaction Type",
            options=["Buy", "Sell"],
            horizontal=True
        )
        
        transaction_date = st.date_input(
            "Transaction Date",
            value=datetime.date.today()
        )
        
    with col2:
        quantity = st.number_input(
            "Quantity",
            min_value=0.01, # Allows partial shares/units
            value=1.0,
            step=1.0,
            format="%.4f",
            help="Number of shares or mutual fund units."
        )
        
        price = st.number_input(
            "Price per unit (₹)",
            min_value=0.01,
            value=100.0,
            step=1.0,
            format="%.2f",
            help="The price at which the asset was bought or sold."
        )

    # Submission
    submitted = st.form_submit_button("💾 Save Transaction", type="primary")

if submitted:
    if not available_symbols:
        st.error("Cannot add transaction without an available asset symbol.")
    elif not selected_symbol:
        st.error("Please select an asset symbol.")
    else:
        # Format the date properly for the DB as YYYY-MM-DD
        formatted_date = transaction_date.strftime("%Y-%m-%d")
        
        with st.spinner(f"Processing {transaction_type} transaction..."):
            if transaction_type == "Buy":
                success = db.add_buy_transaction(
                    symbol=selected_symbol,
                    quantity=float(quantity),
                    price=float(price),
                    date=formatted_date
                )
            else:
                success = db.process_sell_transaction(
                    symbol=selected_symbol,
                    sell_qty=float(quantity),
                    sell_avg=float(price),
                    sell_date=formatted_date
                )
            
        if success:
            st.success(f"Successfully recorded {transaction_type} of {quantity} {selected_symbol} @ ₹{price}")
