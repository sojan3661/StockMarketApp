import streamlit as st
import pandas as pd
import sys
import os

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
                "Allocation": st.column_config.NumberColumn("Allocation %", min_value=0.0, max_value=100.0, step=1.0, format="%.2f%%")
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
