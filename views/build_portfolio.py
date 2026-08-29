import streamlit as st
import sys
import os
import pandas as pd

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Build Portfolio")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()


# Utility to refresh data globally
def refresh_data():
    st.cache_data.clear()
    st.rerun()


# ---------------------------------------------------------
# 1. Fetch Existing Plans
# ---------------------------------------------------------
from Config.data_cache import get_global_app_data, refresh_all_data

app_data = get_global_app_data()
plans = app_data.get("investment_plan", [])
    
# Convert to DataFrame for easier handling
# Convert to DataFrame for easier handling
if plans:
    plans_df = pd.DataFrame(plans)
else:
    plans_df = pd.DataFrame(columns=[
        "Portfolio", "Current Invested Amount", "Monthly SIP", "Number of Months", "Description", "Platform", "Target"
    ])


st.divider()

# ---------------------------------------------------------
# 2. Add New Plan Section
# ---------------------------------------------------------
st.subheader("Add New Investment Plan")

with st.form("add_new_plan_form", clear_on_submit=True):
    new_portfolio = st.text_input(
        "Portfolio Name*", 
        help="Must be a unique name. e.g., 'Retirement', 'Child College Fund'."
    )
    
    col1, col2 = st.columns(2)
    with col1:
        new_invested = st.number_input("Current Invested Amount", min_value=0.0, step=0.01, value=0.0)
        new_sip = st.number_input("Monthly SIP", min_value=0.0, step=0.01, value=0.0, help="Monthly SIP amount for the next n months")
        new_target = st.number_input("Target Amount", min_value=0.0, step=0.01, value=0.0, help="Total amount targeted for this portfolio")
    with col2:
        new_months = st.number_input("Number of Months", min_value=0, step=1, value=0)
        new_desc = st.text_input("Description", placeholder="Optional notes about this portfolio")
        new_platform = st.text_input("Platform", placeholder="Optional platform name")
        
    add_submitted = st.form_submit_button("Create New Plan", type="primary")
    
    if add_submitted:
        if not new_portfolio.strip():
            st.error("Portfolio Name is required.")
        elif not plans_df.empty and (new_portfolio.strip().lower() in plans_df["Portfolio"].str.lower().values):
            st.error(f"A portfolio named '{new_portfolio}' already exists. Please choose a different name.")
        else:
            success = db.upsert_investment_plan(
                portfolio=new_portfolio.strip(),
                current_invested=new_invested,
                monthly_sip=new_sip if new_sip > 0 else None,
                num_months=new_months if new_months > 0 else None,
                description=new_desc.strip() if new_desc else None,
                platform=new_platform.strip() if new_platform else None,
                target=new_target if new_target > 0 else None
            )
            if success:
                st.session_state["show_setup_popup"] = True
                st.session_state["setup_portfolio_name"] = new_portfolio.strip()
                st.session_state["setup_step"] = 1
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Failed to create new investment plan.")

# ---------------------------------------------------------
# Post-Creation Setup Popup Dialog
# ---------------------------------------------------------
@st.dialog("Setup Portfolio Allocations", width="large")
def post_create_setup_dialog(port_name):
    step = st.session_state.get("setup_step", 1)
    
    if step == 1:
        st.markdown(f"### Step 1 of 2: Sector Allocation for **{port_name}**")
        st.caption("Configure target sector allocation percentages for this portfolio.")
        
        sectors_data = db.fetch_sectors()
        allocations_data = db.fetch_allocations(portfolio=port_name)
        
        existing_alloc_map = {}
        if allocations_data:
            for a in allocations_data:
                if a.get("Sector"):
                    existing_alloc_map[a.get("Sector")] = float(a.get("Allocation", 0.0))
                    
        if not sectors_data:
            st.info("No sectors found in Sector Management.")
        else:
            sector_rows = []
            for s in sectors_data:
                sec = s.get("Sector")
                if sec:
                    sector_rows.append({
                        "Sector": sec,
                        "Allocation %": existing_alloc_map.get(sec, 0.0)
                    })
            
            df_sec = pd.DataFrame(sector_rows)
            edited_sec_df = st.data_editor(
                df_sec,
                column_config={
                    "Sector": st.column_config.TextColumn("Sector Name", disabled=True),
                    "Allocation %": st.column_config.NumberColumn("Allocation %", min_value=0.0, max_value=100.0, step=0.5, format="%.2f%%")
                },
                hide_index=True,
                use_container_width=True,
                key=f"sec_editor_{port_name}"
            )
            
            col_b, col_n = st.columns([1, 1])
            with col_b:
                if st.button("Back", key="btn_sec_back", use_container_width=True):
                    st.session_state["show_setup_popup"] = False
                    st.session_state["setup_portfolio_name"] = None
                    st.rerun()
            with col_n:
                if st.button("Next ➡️", type="primary", key="btn_sec_next", use_container_width=True):
                    tot = float(edited_sec_df["Allocation %"].sum())
                    if tot > 100.0:
                        st.error(f"Total sector allocation ({tot:.2f}%) exceeds 100%. Please adjust.")
                    else:
                        payload = []
                        for _, r in edited_sec_df.iterrows():
                            val = float(r["Allocation %"])
                            if val > 0:
                                payload.append({
                                    "Sector": r["Sector"],
                                    "Allocation": val,
                                    "Portfolio": port_name
                                })
                        if payload:
                            db.upsert_allocations(payload, portfolio=port_name)
                        st.session_state["setup_step"] = 2
                        st.session_state["show_setup_popup"] = True
                        st.rerun()

    elif step == 2:
        st.markdown(f"### Step 2 of 2: Portfolio Rebalancing (Stock Allocation) for **{port_name}**")
        st.caption("Configure target asset/stock allocation percentages for this portfolio.")
        
        # Fetch sector allocations for this portfolio to filter assets by allocated sectors
        allocations_data = db.fetch_allocations(portfolio=port_name)
        allocated_sectors = {
            a.get("Sector") for a in allocations_data
            if a.get("Sector") and float(a.get("Allocation", 0.0)) > 0
        }

        stocks_data = db.fetch_stocks()
        if allocated_sectors:
            stocks_data = [s for s in stocks_data if s.get("Sector") in allocated_sectors]
            st.info(f"📋 Showing assets belonging to allocated sectors: **{', '.join(sorted(allocated_sectors))}**")
        else:
            st.info("⚠️ No sectors were allocated in Step 1. Showing all available assets.")

        stock_allocs_data = db.fetch_stock_allocations(portfolio=port_name)
        
        existing_stock_map = {}
        if stock_allocs_data:
            for sa in stock_allocs_data:
                if sa.get("Symbol"):
                    existing_stock_map[sa.get("Symbol")] = float(sa.get("Allocation", 0.0))
                    
        if not stocks_data:
            st.info("No matching stocks/assets found for the allocated sectors.")
        else:
            stock_rows = []
            for stk in stocks_data:
                sym = stk.get("Symbol")
                name = stk.get("Name", "")
                sec = stk.get("Sector", "")
                if sym:
                    stock_rows.append({
                        "Symbol": sym,
                        "Name": name,
                        "Sector": sec,
                        "Allocation %": existing_stock_map.get(sym, 0.0)
                    })
                    
            df_stk = pd.DataFrame(stock_rows)
            edited_stk_df = st.data_editor(
                df_stk,
                column_config={
                    "Symbol": st.column_config.TextColumn("Symbol", disabled=True),
                    "Name": st.column_config.TextColumn("Name", disabled=True),
                    "Sector": st.column_config.TextColumn("Sector", disabled=True),
                    "Allocation %": st.column_config.NumberColumn("Target Allocation %", min_value=0.0, max_value=100.0, step=0.5, format="%.2f%%")
                },
                hide_index=True,
                use_container_width=True,
                key=f"stk_editor_{port_name}"
            )
            
            col_b, col_f = st.columns([1, 1])
            with col_b:
                if st.button("⬅️ Back", key="btn_stk_back", use_container_width=True):
                    st.session_state["setup_step"] = 1
                    st.session_state["show_setup_popup"] = True
                    st.rerun()
            with col_f:
                if st.button("Finish 🎉", type="primary", key="btn_stk_finish", use_container_width=True):
                    stk_payload = []
                    for _, r in edited_stk_df.iterrows():
                        val = float(r["Allocation %"])
                        if val > 0:
                            stk_payload.append({
                                "Symbol": r["Symbol"],
                                "Allocation": val,
                                "Portfolio": port_name
                            })
                    if stk_payload:
                        db.upsert_stock_allocations(stk_payload, portfolio=port_name)
                    st.session_state["show_setup_popup"] = False
                    st.session_state["setup_portfolio_name"] = None
                    st.success(f"Successfully configured allocations for {port_name}!")
                    refresh_data()

# Trigger dialog if setup state is active
if st.session_state.get("show_setup_popup") and st.session_state.get("setup_portfolio_name"):
    p_name = st.session_state.get("setup_portfolio_name")
    st.session_state["show_setup_popup"] = False # Consume trigger so closing window stays closed
    post_create_setup_dialog(p_name)


st.divider()

# ---------------------------------------------------------
# 3. View / Edit / Delete Existing Plans
# ---------------------------------------------------------
st.subheader("Current Investment Plans")

if plans_df.empty:
    st.info("No investment plans found. Create one above to get started.")
else:
    # Iterate over plans
    for index, row in plans_df.iterrows():
        port_id = row.get("Portfolio")
        
        # Calculate expected investment
        inv_amt = float(row.get('Current Invested Amount', 0))
        sip_amt = float(row.get('Monthly SIP') or 0.0)
        months = int(row.get('Number of Months') or 0)
        expected_investment = inv_amt + (sip_amt * months)
        
        target_amt = float(row.get('Target') or 0.0) if pd.notna(row.get('Target')) else 0.0
        target_html = f'<span style="background-color: #8B5CF620; color: #A78BFA; padding: 4px 10px; border-radius: 6px; font-weight: 600;">Target: ₹{target_amt:,.2f}</span>' if target_amt > 0 else ""
        
        platform = row.get("Platform")
        platform_html = f'<span style="background-color: #F59E0B20; color: #FBBF24; padding: 4px 10px; border-radius: 6px; font-weight: 600;">Platform: {platform}</span>' if pd.notna(platform) and platform else ""
        
        # UI Expanders for each plan to keep layout clean
        with st.expander(f"{port_id}", expanded=False):
            badges_html = (
                f'<div style="display: flex; gap: 15px; margin-bottom: 15px; color: #9CA3AF; flex-wrap: wrap;">'
                f'<span style="background-color: #3B82F620; color: #60A5FA; padding: 4px 10px; border-radius: 6px; font-weight: 600;">Invested: ₹{inv_amt:,.2f}</span>'
                f'<span style="background-color: #10B98120; color: #34D399; padding: 4px 10px; border-radius: 6px; font-weight: 600;">Expected: ₹{expected_investment:,.2f}</span>'
                f'{target_html}'
                f'{platform_html}'
                f'</div>'
            )
            st.markdown(badges_html, unsafe_allow_html=True)
            # Using tabs to separate Edit and Delete actions
            tab_edit, tab_delete = st.tabs(["✏️ Edit Plan", "🗑️ Delete Plan"])
            
            # --- EDIT TAB ---
            with tab_edit:
                with st.form(f"edit_plan_form_{port_id}"):
                    edit_portfolio_name = st.text_input(
                        "Portfolio Name*",
                        value=port_id,
                        key=f"edit_name_{port_id}",
                        help="Change the name of this portfolio. This will update it in all transactions and allocations."
                    )
                    
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        edit_invested = st.number_input(
                            "Current Invested Amount",
                            value=float(row.get("Current Invested Amount", 0)),
                            min_value=0.0,
                            step=0.01,
                            key=f"edit_inv_{port_id}"
                        )
                        edit_sip = st.number_input(
                            "Monthly SIP",
                            value=float(row.get("Monthly SIP") or 0.0),
                            min_value=0.0,
                            step=0.01,
                            key=f"edit_sip_{port_id}",
                            help="Monthly SIP amount for the next n months"
                        )
                        edit_target = st.number_input(
                            "Target Amount",
                            value=float(row.get("Target") or 0.0) if pd.notna(row.get("Target")) else 0.0,
                            min_value=0.0,
                            step=0.01,
                            key=f"edit_target_{port_id}",
                            help="Total amount targeted for this portfolio"
                        )
                    with ec2:
                        edit_months = st.number_input(
                            "Number of Months",
                            value=int(row.get("Number of Months") or 0),
                            min_value=0,
                            step=1,
                            key=f"edit_mon_{port_id}"
                        )
                        edit_desc = st.text_input(
                            "Description",
                            value=str(row.get("Description") or "") if pd.notna(row.get("Description")) else "",
                            key=f"edit_desc_{port_id}"
                        )
                        edit_platform = st.text_input(
                            "Platform",
                            value=str(row.get("Platform") or "") if pd.notna(row.get("Platform")) else "",
                            key=f"edit_plat_{port_id}"
                        )
                    
                    edit_submitted = st.form_submit_button("Update Plan")
                    
                    if edit_submitted:
                        new_name_clean = edit_portfolio_name.strip()
                        if not new_name_clean:
                            st.error("Portfolio Name is required.")
                        elif new_name_clean.lower() != port_id.lower() and not plans_df.empty and (new_name_clean.lower() in plans_df["Portfolio"].str.lower().values):
                            st.error(f"A portfolio named '{new_name_clean}' already exists. Please choose a different name.")
                        else:
                            if new_name_clean != port_id:
                                success, msg = db.update_portfolio_name(
                                    old_name=port_id,
                                    new_name=new_name_clean,
                                    current_invested=edit_invested,
                                    monthly_sip=edit_sip if edit_sip > 0 else None,
                                    num_months=edit_months if edit_months > 0 else None,
                                    description=edit_desc if edit_desc else None,
                                    platform=edit_platform if edit_platform else None,
                                    target=edit_target if edit_target > 0 else None
                                )
                                if success:
                                    st.success(msg)
                                    refresh_data()
                                else:
                                    st.error(msg)
                            else:
                                success = db.upsert_investment_plan(
                                    portfolio=port_id,
                                    current_invested=edit_invested,
                                    monthly_sip=edit_sip if edit_sip > 0 else None,
                                    num_months=edit_months if edit_months > 0 else None,
                                    description=edit_desc if edit_desc else None,
                                    platform=edit_platform if edit_platform else None,
                                    target=edit_target if edit_target > 0 else None
                                )
                                if success:
                                    st.success(f"Updated {port_id} successfully!")
                                    refresh_data()
                                else:
                                    st.error(f"Failed to update {port_id}.")
            
            # --- DELETE TAB ---
            with tab_delete:
                inv_amt = float(row.get("Current Invested Amount", 0) or 0.0)
                if inv_amt > 0:
                    st.warning(f"⚠️ Cannot delete plan **{port_id}** because its Current Invested Amount is ₹{inv_amt:,.2f} (> 0). Clear or withdraw investments first.")
                    st.button(f"Delete {port_id}", type="primary", key=f"del_btn_{port_id}", disabled=True, help="Deletion is disabled when Current Invested Amount is greater than 0")
                else:
                    st.warning(f"Are you sure you want to delete the plan **{port_id}**? This will delete all associated transactions, sector allocations, and the dashboard plan. This action cannot be undone.")
                    
                    if st.button(f"Delete {port_id}", type="primary", key=f"del_btn_{port_id}"):
                        if db.delete_investment_plan(portfolio=port_id):
                            st.success(f"Deleted {port_id} successfully!")
                            refresh_data()
                        else:
                            st.error(f"Failed to delete {port_id}.")