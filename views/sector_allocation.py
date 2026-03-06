import streamlit as st
import pandas as pd
import sys
import os
import io
import openpyxl

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Sector Allocation")
st.write("Assign target allocations (percentages) to your market sectors.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
with st.spinner("Loading data..."):
    sectors_data = db.fetch_sectors()
    investment_plans = db.fetch_investment_plan()

if not sectors_data:
    st.info("No sectors found! Please add sectors in the Sector Management page first.")
    st.stop()

if not investment_plans:
    st.info("No investment plans found! Please create a portfolio on the Build Portfolio page first.")
    st.stop()
    
# Convert investment plans into a list of portfolio names
portfolio_names = [plan["Portfolio"] for plan in investment_plans if "Portfolio" in plan]

if not portfolio_names:
    st.info("No valid portfolios found.")
    st.stop()

# Load all allocations for all portfolios upfront to minimize queries
# (Alternative is querying inside each tab, but fetching all at once is faster)
allocations_data = db.fetch_allocations()

# Create lookup dict: { 'Retirement': { 'Technology': {'Id': 1, 'Allocation': 25.5} } }
alloc_dict = {p: {} for p in portfolio_names}
for alloc in allocations_data:
    port_name = alloc.get("Portfolio")
    sector_name = alloc.get("Sector")
    if port_name in alloc_dict and sector_name:
        alloc_dict[port_name][sector_name] = {
            "Id": alloc.get("Id"),
            "Allocation": alloc.get("Allocation", 0.0)
        }

# ==================== Bulk Upload & Template ====================
def generate_allocation_template(portfolios, sectors) -> bytes:
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Allocations"

    # Headers
    ws.append(["Portfolio", "Sector", "Allocation %"])

    # Dropdown validation for Portfolio (A2:A1000)
    if portfolios:
        port_str = ",".join(portfolios)
        dv_port = DataValidation(type="list", formula1=f'"{port_str}"', allow_blank=False, showDropDown=False)
        dv_port.sqref = "A2:A1000"
        ws.add_data_validation(dv_port)

    # Dropdown validation for Sector (B2:B1000)
    if sectors:
        sector_names = [s.get("Sector") for s in sectors if s.get("Sector")]
        if sector_names:
            sector_str = ",".join(sector_names)
            dv_sector = DataValidation(type="list", formula1=f'"{sector_str}"', allow_blank=False, showDropDown=False)
            dv_sector.sqref = "B2:B1000"
            ws.add_data_validation(dv_sector)

    # Number validation for Allocation (C2:C1000)
    dv_alloc = DataValidation(type="decimal", operator="between", formula1="0.0", formula2="100.0", allow_blank=False)
    dv_alloc.error = 'Allocation must be between 0 and 100'
    dv_alloc.errorTitle = 'Invalid Allocation'
    dv_alloc.prompt = 'Enter a percentage from 0 to 100 (e.g., 12.5)'
    dv_alloc.promptTitle = 'Allocation %'
    dv_alloc.sqref = "C2:C1000"
    ws.add_data_validation(dv_alloc)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

st.subheader("Bulk Import Allocations")
col_dl, col_ul = st.columns([1, 1])

with col_dl:
    st.download_button(
        label="📥 Download Allocation Template",
        data=generate_allocation_template(portfolio_names, sectors_data),
        file_name="sector_allocation_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

with col_ul:
    uploaded_alloc_file = st.file_uploader(
        "Upload Allocations",
        type=["xlsx"],
        label_visibility="collapsed"
    )

if uploaded_alloc_file is not None:
    with st.expander("📋 Preview & Import Uploaded Allocations", expanded=True):
        try:
            upload_df = pd.read_excel(uploaded_alloc_file, dtype=str)
            upload_df.columns = [c.strip() for c in upload_df.columns]
            
            required_cols = {"Portfolio", "Sector", "Allocation %"}
            missing = required_cols - set(upload_df.columns)
            
            if missing:
                st.error(f"Missing columns in file: {', '.join(missing)}")
            else:
                st.dataframe(upload_df, use_container_width=True, hide_index=True)
                
                if st.button("🚀 Import All Allocations", type="primary"):
                    # We group modifications by portfolio, as upsert_allocations takes a portfolio arg
                    
                    # 1. Group records by portfolio
                    port_updates = {}
                    fail_count = 0
                    
                    valid_sectors = [s.get("Sector", "").lower() for s in sectors_data]
                    
                    for i, row in upload_df.iterrows():
                        port = str(row.get("Portfolio", "")).strip()
                        sec  = str(row.get("Sector", "")).strip()
                        alloc_val = row.get("Allocation %", "0")
                        
                        try:
                            alloc = float(alloc_val)
                        except ValueError:
                            st.warning(f"Row {i+2}: Invalid Allocation value '{alloc_val}' — skipped.")
                            fail_count += 1
                            continue
                            
                        if not port or not sec:
                            st.warning(f"Row {i+2}: Missing Portfolio or Sector — skipped.")
                            fail_count += 1
                            continue
                            
                        if port not in portfolio_names:
                            st.warning(f"Row {i+2}: Unknown Portfolio '{port}' — skipped.")
                            fail_count += 1
                            continue
                            
                        # Case insensitive sector match to fix common typos
                        matched_sector = next((s.get("Sector") for s in sectors_data if s.get("Sector", "").lower() == sec.lower()), None)
                        
                        if not matched_sector:
                            st.warning(f"Row {i+2}: Unknown Sector '{sec}'. Please add it in Sector Management first — skipped.")
                            fail_count += 1
                            continue
                            
                        if port not in port_updates:
                            port_updates[port] = []
                            
                        port_updates[port].append({
                            "Sector": matched_sector,
                            "Allocation": alloc,
                            "Portfolio": port
                        })
                    
                    # 2. Iterate and Save per Portfolio
                    success_count = 0
                    
                    for port, payload in port_updates.items():
                        # Calculate total allocation for validation
                        total_alloc = sum([r["Allocation"] for r in payload])
                        if total_alloc > 100.0:
                            st.error(f"❌ Failed to import {port}: Total allocation exceeds 100% ({total_alloc}%). Skipping this portfolio.")
                            fail_count += len(payload)
                            continue
                            
                        # Execute upsert block
                        ok = db.upsert_allocations(payload, portfolio=port)
                        if ok:
                            success_count += len(payload)
                        else:
                            st.error(f"❌ Failed to save updates for portfolio: {port}")
                            fail_count += len(payload)
                            
                    if success_count:
                        st.success(f"✅ {success_count} allocation(s) imported successfully.")
                        st.cache_data.clear() # clear caches so UI picks up change immediately
                        st.rerun()
                        
                    if fail_count:
                        st.error(f"⚠️ {fail_count} allocation(s) failed or skipped — see messages above.")
                        
        except Exception as e:
            st.error(f"Error reading file: {e}")

st.divider()
st.subheader("Edit Allocations")

# Create a tab for each portfolio
tabs = st.tabs(portfolio_names)

# Dictionary to store the edited dataframe for each portfolio
edited_dfs = {}

for i, port_name in enumerate(portfolio_names):
    with tabs[i]:
        st.write(f"**Target Allocations for {port_name}**")
        
        # Merge all sectors with their allocations for this specific portfolio
        merged_data = []
        for sec in sectors_data:
            sector_name = sec.get("Sector")
            if not sector_name:
                continue
                
            existing = alloc_dict[port_name].get(sector_name)
            if existing:
                merged_data.append({
                    "Id": existing["Id"],
                    "Sector": sector_name,
                    "Allocation": float(existing["Allocation"])
                })
            else:
                merged_data.append({
                    "Id": None,
                    "Sector": sector_name,
                    "Allocation": 0.0
                })
        
        df = pd.DataFrame(merged_data)
        
        # Unique key for each data editor based on portfolio name
        edited_df = st.data_editor(
            df,
            key=f"editor_{port_name}",
            hide_index=True,
            column_config={
                "Id": None,
                "Sector": st.column_config.TextColumn("Sector / Theme", disabled=True),
                "Allocation": st.column_config.NumberColumn("Allocation %", min_value=0.0, max_value=100.0, step=0.01, format="%.2f%%")
            },
            use_container_width=True
        )
        
        edited_dfs[port_name] = edited_df
        
        # Calculate total allocation dynamically for this tab
        total_allocation = edited_df["Allocation"].sum()
        
        if total_allocation > 100.0:
            st.error(f"Total Allocation is {total_allocation:.2f}%. It should not exceed 100%.")
        elif total_allocation < 100.0:
            st.warning(f"Total Allocation is {total_allocation:.2f}%. You still have {100.0 - total_allocation:.2f}% to allocate.")
        else:
            st.success(f"Total Allocation is perfectly {total_allocation:.2f}%!")
            
        # Display save button for this specific portfolio
        # Using a unique key for the button
        submitted = st.button(f"Save {port_name} Allocations", type="primary", use_container_width=True, key=f"save_{port_name}")
        
        if submitted:
            # Prepare payload for this exact portfolio
            payload = []
            
            for index, row in edited_df.iterrows():
                record = {
                    "Sector": row["Sector"],
                    "Allocation": float(row["Allocation"]),
                    "Portfolio": port_name
                }
                
                # We can't safely reuse Ids when switching arrays like this without careful merging.
                # However, our supabase_client logic drops based on Portfolio + Sector, so omitting Id is fine.
                
                payload.append(record)
                
            with st.spinner(f"Saving {port_name} to database..."):
                success = db.upsert_allocations(payload, portfolio=port_name)
                
            if success:
                st.success(f"Allocations for {port_name} successfully saved!")
                st.rerun()
