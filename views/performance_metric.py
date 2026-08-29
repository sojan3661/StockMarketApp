import streamlit as st
import sys
import os
import pandas as pd

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

@st.dialog("Transaction Contribution Details", width="large")
def show_transaction_contributions(year, month, filtered_txs):
    st.write(f"### Open Transactions contributing to **{month} {year}**")
    if filtered_txs.empty:
        st.info("No contributing transactions found.")
    else:
        # Display the contribution summary by portfolio
        portfolio_summary = filtered_txs.groupby("Portfolio")["BuyValue"].sum().reset_index()
        portfolio_summary = portfolio_summary.rename(columns={"BuyValue": "Total Buy Value (INR)"})
        
        st.write("#### Contribution by Portfolio")
        st.dataframe(
            portfolio_summary,
            column_config={
                "Total Buy Value (INR)": st.column_config.NumberColumn(format="₹%.2f")
            },
            use_container_width=True
        )
        
        st.write("#### Detailed Transactions")
        # Format columns for display
        display_cols = ["Symbol", "BuyDate", "Qty", "BuyAvg", "BuyValue", "Portfolio"]
        available_cols = [c for c in display_cols if c in filtered_txs.columns]
        
        tx_display = filtered_txs[available_cols].copy()
        if "BuyValue" in tx_display.columns:
            tx_display["BuyValue"] = pd.to_numeric(tx_display["BuyValue"], errors="coerce").fillna(0.0)
        if "BuyAvg" in tx_display.columns:
            tx_display["BuyAvg"] = pd.to_numeric(tx_display["BuyAvg"], errors="coerce").fillna(0.0)
            
        st.dataframe(
            tx_display,
            column_config={
                "BuyValue": st.column_config.NumberColumn(format="₹%.2f"),
                "BuyAvg": st.column_config.NumberColumn(format="₹%.2f"),
                "Qty": st.column_config.NumberColumn(format="%.4f")
            },
            use_container_width=True
        )
    
    if st.button("Close"):
        st.rerun()


st.title("Performance Metric")

# Custom styling for standard text/display alignment
st.markdown("""
<style>
div[data-testid="column"]:first-child [data-testid="stBaseButton-secondary"] {
    background-color: #1A1D24 !important;
    border: 1px solid #2D333B !important;
    border-radius: 8px !important;
    padding: 14px 16px !important;
    text-align: left !important;
    color: #F8FAFC !important;
    font-weight: 600 !important;
    font-size: 1rem !important;
    cursor: pointer !important;
    transition: border-color 0.18s, background-color 0.18s !important;
    margin-bottom: 6px !important;
}
div[data-testid="column"]:first-child [data-testid="stBaseButton-secondary"]:hover {
    background-color: #22262F !important;
    border-color: #4B5563 !important;
}
</style>
""", unsafe_allow_html=True)

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()

@st.cache_data(ttl=300)
def load_perf_db_data():
    return db.fetch_fy_details()

with st.spinner("Loading FY Details..."):
    fy_data = load_perf_db_data()

existing_fys = [str(item.get("FY", "")).strip() for item in fy_data]

# ==================== Add New FY Details ====================
st.subheader("Add Financial Year Details")
with st.form("add_fy_form", clear_on_submit=True):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        fy_input = st.text_input("FY (e.g. 2024-25)", placeholder="YYYY-YY")
        target_input = st.number_input("Target (%)", min_value=0.0, step=0.1, format="%.2f")
    with col2:
        cash_start = st.number_input("Cash Start", min_value=0.0, step=1000.0)
        cash_end = st.number_input("Cash End", min_value=0.0, step=1000.0)
    with col3:
        stock_start = st.number_input("Stock Start", min_value=0.0, step=1000.0)
        stock_end = st.number_input("Stock End", min_value=0.0, step=1000.0)
    with col4:
        mf_start = st.number_input("MF Start", min_value=0.0, step=1000.0)
        mf_end = st.number_input("MF End", min_value=0.0, step=1000.0)

    submitted = st.form_submit_button("Add Record")
    if submitted:
        fy_clean = fy_input.strip()
        if not fy_clean:
            st.warning("Please enter a valid Financial Year (FY).")
        elif fy_clean in existing_fys:
            st.warning(f"FY '{fy_clean}' already exists. Please use 'Edit' to modify it.")
        else:
            success = db.add_fy_detail(
                fy=fy_clean,
                cash_start=cash_start,
                stock_start=stock_start,
                mf_start=mf_start,
                target=target_input,
                cash_end=cash_end,
                stock_end=stock_end,
                mf_end=mf_end
            )
            if success:
                st.success(f"Successfully added FY details for: '{fy_clean}'")
                st.rerun()

st.divider()

# ==================== Monthly Buy Value of Open Transactions ====================
st.subheader("Monthly Buy Value of Open Transactions (INR)")

from Config.data_cache import get_global_app_data, refresh_all_data

app_data = get_global_app_data()
all_transactions = app_data.get("all_transactions", [])

if all_transactions:
    df_tx = pd.DataFrame(all_transactions)
    # Check if necessary columns exist
    if not df_tx.empty and "BuyDate" in df_tx.columns and "BuyValue" in df_tx.columns:
        # Filter for open transactions (where SellDate is null/None/empty)
        if "SellDate" in df_tx.columns:
            open_tx = df_tx[
                df_tx["SellDate"].isna() | 
                (df_tx["SellDate"].astype(str).str.strip() == "") | 
                (df_tx["SellDate"].astype(str).str.strip().str.lower() == "none") |
                (df_tx["SellDate"].astype(str).str.strip().str.lower() == "nan")
            ].copy()
        else:
            open_tx = df_tx.copy()
        
        # Filter out rows without a valid BuyDate
        open_tx = open_tx[open_tx["BuyDate"].notna() & (open_tx["BuyDate"] != "")].copy()
        
        if not open_tx.empty:
            open_tx["BuyDate_dt"] = pd.to_datetime(open_tx["BuyDate"], errors="coerce")
            open_tx = open_tx[open_tx["BuyDate_dt"].notna()].copy()
            
            if not open_tx.empty:
                open_tx["Year"] = open_tx["BuyDate_dt"].dt.year
                open_tx["Month"] = open_tx["BuyDate_dt"].dt.strftime("%b")
                open_tx["BuyValue"] = pd.to_numeric(open_tx["BuyValue"], errors="coerce").fillna(0.0)
                
                # Pivot table
                pivot_df = open_tx.pivot_table(
                    index="Year",
                    columns="Month",
                    values="BuyValue",
                    aggfunc="sum",
                    fill_value=0.0
                )
                
                # Sort month columns in calendar order
                months_ordered = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                pivot_df = pivot_df.reindex(columns=months_ordered, fill_value=0.0)
                pivot_df = pivot_df.sort_index()
                
                # Add Total column
                pivot_df["Total"] = pivot_df.sum(axis=1)
                
                # Add Total row
                total_row = pivot_df.sum(axis=0)
                pivot_df.loc["Total"] = total_row
                
                # Format Year index to string so Streamlit doesn't format it with commas
                pivot_df.index = pivot_df.index.map(str)
                
                # Formatting for the number columns (e.g. ₹100,000.00)
                col_config = {
                    col: st.column_config.NumberColumn(
                        label=col,
                        format="₹%.2f"
                    ) for col in pivot_df.columns
                }
                
                # Session state tracking for active selection to prevent loop on dialog closing
                if "active_selection" not in st.session_state:
                    st.session_state["active_selection"] = None
                
                event = st.dataframe(
                    pivot_df,
                    column_config=col_config,
                    use_container_width=True,
                    on_select="rerun",
                    selection_mode="single-cell",
                    key="monthly_buy_dataframe"
                )
                
                # Check selection state
                selected_cells = event.selection.cells
                
                if selected_cells:
                    if st.session_state["active_selection"] != selected_cells:
                        st.session_state["active_selection"] = selected_cells
                        
                        row_idx, col_name = selected_cells[0]
                        selected_year = pivot_df.index[row_idx]
                        selected_month = col_name
                        
                        # Filter transactions based on selection
                        filtered_tx = open_tx.copy()
                        
                        if selected_year != "Total":
                            filtered_tx = filtered_tx[filtered_tx["Year"] == int(selected_year)]
                            
                        if selected_month != "Total":
                            filtered_tx = filtered_tx[filtered_tx["Month"] == selected_month]
                            
                        # Trigger dialog popup
                        show_transaction_contributions(selected_year, selected_month, filtered_tx)
                else:
                    st.session_state["active_selection"] = None
            else:
                st.info("No open transactions with valid Buy Dates found.")
        else:
            st.info("No open transactions found to display.")
    else:
        st.info("No transaction data available or missing required fields ('BuyDate', 'BuyValue').")
else:
    st.info("No transaction records found.")

st.divider()

# ==================== Existing Records ====================
st.subheader("Existing Performance Metrics")

if not fy_data:
    st.info("No Performance Metrics found in the database. Use the form above to add your first record!")
else:
    for item in fy_data:
        fy_val = item.get("FY", "Unknown FY")
        c_s = float(item.get("CashStart") or 0.0)
        s_s = float(item.get("StockStart") or 0.0)
        m_s = float(item.get("MFStart") or 0.0)
        c_e = float(item.get("CashEnd") or 0.0)
        s_e = float(item.get("StockEnd") or 0.0)
        m_e = float(item.get("MFEnd") or 0.0)
        start_total = c_s + s_s + m_s
        end_total = c_e + s_e + m_e
        tgt = float(item.get("Target") or 0.0)
        target_value = start_total * (tgt / 100.0)
        cu_target_value = start_total + target_value
        
        with st.expander(f"{fy_val} — Target: {tgt:,.2f}% | Target Value: ₹{target_value:,.2f} | Cumulative Target: ₹{cu_target_value:,.2f}"):
            st.markdown(
                f"""
                <div style="display: flex; gap: 15px; margin-bottom: 15px; flex-wrap: wrap;">
                    <span style="background-color: #6366F120; color: #818CF8; padding: 4px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 600;">Target: {tgt:,.2f}%</span>
                    <span style="background-color: #1A1D24; color: #94A3B8; padding: 4px 8px; border-radius: 4px; border: 1px solid #2D333B; font-size: 0.85rem;">Investment Value at beginning: ₹{start_total:,.2f}</span>
                    <span style="background-color: #1A1D24; color: #94A3B8; padding: 4px 8px; border-radius: 4px; border: 1px solid #2D333B; font-size: 0.85rem;">Target Value: ₹{target_value:,.2f}</span>
                    <span style="background-color: #1A1D24; color: #94A3B8; padding: 4px 8px; border-radius: 4px; border: 1px solid #2D333B; font-size: 0.85rem;">Cumulative Target Value: ₹{cu_target_value:,.2f}</span>
                    <span style="background-color: #1A1D24; color: #94A3B8; padding: 4px 8px; border-radius: 4px; border: 1px solid #2D333B; font-size: 0.85rem;">Investment Value at End: ₹{end_total:,.2f}</span>
                </div>
                """, unsafe_allow_html=True
            )
            
            tab_edit, tab_delete = st.tabs(["✏️ Edit", "🗑️ Delete"])
            
            with tab_edit:
                with st.form(f"edit_form_{fy_val}"):
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        new_cash_s = st.number_input("Cash Start", value=c_s, step=1000.0, key=f"e_cs_{fy_val}")
                        new_stock_s = st.number_input("Stock Start", value=s_s, step=1000.0, key=f"e_ss_{fy_val}")
                        new_mf_s = st.number_input("MF Start", value=m_s, step=1000.0, key=f"e_mfs_{fy_val}")
                        new_tgt = st.number_input("Target (%)", value=tgt, step=0.1, format="%.2f", key=f"e_tgt_{fy_val}")
                    with ec2:
                        new_cash_e = st.number_input("Cash End", value=c_e, step=1000.0, key=f"e_ce_{fy_val}")
                        new_stock_e = st.number_input("Stock End", value=s_e, step=1000.0, key=f"e_se_{fy_val}")
                        new_mf_e = st.number_input("MF End", value=m_e, step=1000.0, key=f"e_mfe_{fy_val}")

                    if st.form_submit_button("💾 Save Changes", type="primary"):
                        success = db.update_fy_detail(
                            fy=fy_val,
                            cash_start=new_cash_s,
                            stock_start=new_stock_s,
                            mf_start=new_mf_s,
                            target=new_tgt,
                            cash_end=new_cash_e,
                            stock_end=new_stock_e,
                            mf_end=new_mf_e
                        )
                        if success:
                            st.success(f"Updated '{fy_val}' successfully!")
                            st.rerun()
                        else:
                            st.error("Failed to update.")

            with tab_delete:
                st.warning(f"Are you sure you want to delete **{fy_val}**? This cannot be undone.")
                if st.button("🗑️ Confirm Delete", key=f"del_{fy_val}", type="primary"):
                    ok = db.delete_fy_detail(fy_val)
                    if ok:
                        st.success(f"Deleted '{fy_val}'.")
                        st.rerun()
