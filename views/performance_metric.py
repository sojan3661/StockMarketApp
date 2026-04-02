import streamlit as st
import sys
import os
import pandas as pd

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

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

with st.spinner("Loading FY Details..."):
    fy_data = db.fetch_fy_details()

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
