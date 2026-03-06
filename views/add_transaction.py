import streamlit as st
import datetime
import io
import sys
import os
import openpyxl

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Add Transaction")


# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
with st.spinner("Loading available assets & portfolios..."):
    db_stocks = db.fetch_stocks()
    db_investment_plan = db.fetch_investment_plan()
    
# Create a list of available portfolios
if not db_investment_plan:
    available_portfolios = []
else:
    plans_list = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
    available_portfolios = sorted([p.get("Portfolio", "") for p in plans_list if p.get("Portfolio", "")])

# Create a list of available symbols from StockManagement
if not db_stocks:
    available_symbols = []
    st.info("No assets found in your portfolio yet. Go to 'Stock Management' to start adding assets before recording transactions.")
else:
    available_symbols = sorted([p.get("Symbol", "") for p in db_stocks if p.get("Symbol", "")])

# -----------------------------
# Download Template Button
# -----------------------------
def generate_transaction_template(portfolios) -> bytes:
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"

    # Headers
    ws.append(["Portfolio", "Symbol", "Type", "Qty", "Avg", "Date"])

    # Dropdown validation for the Type column (C2:C1000)
    dv_type = DataValidation(
        type="list",
        formula1='"Buy,Sell"',
        allow_blank=True,
        showDropDown=False      # False = show the dropdown arrow in Excel
    )
    dv_type.sqref = "C2:C1000"
    ws.add_data_validation(dv_type)
    
    # Dropdown validation for the Portfolio column (A2:A1000)
    if portfolios:
        # Excel data validation lists need to be comma separated strings, and under 255 chars usually. 
        # Joining the list of portfolios.
        port_string = ",".join(portfolios)
        # Wrap in quotes for Excel formula
        dv_port = DataValidation(
            type="list",
            formula1=f'"{port_string}"',
            allow_blank=True,
            showDropDown=False
        )
        dv_port.sqref = "A2:A1000"
        ws.add_data_validation(dv_port)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

col_dl, col_ul, _ = st.columns([1, 1, 2])
with col_dl:
    st.download_button(
        label="📥 Download Template",
        data=generate_transaction_template(available_portfolios),
        file_name="transaction_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

with col_ul:
    uploaded_file = st.file_uploader(
        "Upload Transactions",
        type=["xlsx"],
        label_visibility="collapsed"
    )

# -----------------------------
# Bulk Upload Processing
# -----------------------------
if uploaded_file is not None:
    with st.expander("📋 Preview & Import Uploaded Transactions", expanded=True):
        try:
            import pandas as pd
            upload_df = pd.read_excel(uploaded_file, dtype=str)

            # Normalise column names (strip spaces, title-case)
            upload_df.columns = [c.strip() for c in upload_df.columns]

            required_cols = {"Portfolio", "Symbol", "Type", "Qty", "Avg", "Date"}
            missing = required_cols - set(upload_df.columns)
            if missing:
                st.error(f"Missing columns in file: {', '.join(missing)}")
            else:
                # Build a display copy with Date formatted as dd-mmm-yyyy
                display_df = upload_df.copy()
                try:
                    display_df["Date"] = pd.to_datetime(
                        upload_df["Date"], dayfirst=True
                    ).dt.strftime("%d-%b-%Y")
                except Exception:
                    pass  # Leave as-is if parsing fails

                st.dataframe(display_df, use_container_width=True, hide_index=True)

                if st.button("🚀 Import All Transactions", type="primary"):
                    success_count = 0
                    fail_count = 0

                    for i, row in upload_df.iterrows():
                        port  = str(row["Portfolio"]).strip()
                        sym   = str(row["Symbol"]).strip()
                        ttype = str(row["Type"]).strip().capitalize()
                        qty   = float(row["Qty"])
                        avg   = float(row["Avg"])
                        date  = str(row["Date"]).strip()[:10]  # Ensure YYYY-MM-DD

                        if ttype == "Buy":
                            ok = db.add_buy_transaction(
                                symbol=sym,
                                quantity=qty,
                                price=avg,
                                date=date,
                                portfolio=port
                            )
                        elif ttype == "Sell":
                            ok = db.process_sell_transaction(
                                symbol=sym,
                                sell_qty=qty,
                                sell_avg=avg,
                                sell_date=date,
                                portfolio=port
                            )
                        else:
                            st.warning(f"Row {i+2}: Unknown Type '{ttype}' — skipped.")
                            fail_count += 1
                            continue

                        if ok:
                            success_count += 1
                        else:
                            fail_count += 1

                    if success_count:
                        st.success(f"✅ {success_count} transaction(s) imported successfully.")
                    if fail_count:
                        st.error(f"❌ {fail_count} transaction(s) failed — see errors above.")

        except Exception as e:
            st.error(f"Error reading file: {e}")

st.subheader("Transaction Details")

# Ensure that user is aware that they need a Transactions table
st.info("💡 Note: Ensure you have a `Transactions` table in Supabase with columns: `Symbol` (text), `Type` (text), `Quantity` (numeric), `Price` (numeric), and `Date` (date).")

with st.form("add_transaction_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        selected_portfolio = st.selectbox(
            "Portfolio", 
            options=available_portfolios,
            help="Select the portfolio this transaction belongs to."
        )
        
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
                    date=formatted_date,
                    portfolio=selected_portfolio
                )
            else:
                success = db.process_sell_transaction(
                    symbol=selected_symbol,
                    sell_qty=float(quantity),
                    sell_avg=float(price),
                    sell_date=formatted_date,
                    portfolio=selected_portfolio
                )
            
        if success:
            st.success(f"Successfully recorded {transaction_type} of {quantity} {selected_symbol} @ ₹{price}")
