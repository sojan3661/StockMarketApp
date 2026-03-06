import streamlit as st
import pandas as pd
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Portfolio Management")
st.write("Assign target allocations (percentages) to the specific stocks within each sector.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
with st.spinner("Loading stock data..."):
    db_sectors = db.fetch_sectors()
    db_allocations = db.fetch_allocations()
    db_stocks = db.fetch_stocks()
    open_transactions = db.fetch_open_transactions()

# Calculate Qty and Invested Amount for each stock from open transactions
tx_agg = {}
for tx in open_transactions:
    sym = tx.get("Symbol", "")
    if not sym:
        continue
    
    qty = float(tx.get("Qty", 0.0))
    buy_avg = float(tx.get("BuyAvg", 0.0))
    
    if sym not in tx_agg:
        tx_agg[sym] = {"Qty": 0.0, "InvestedTotal": 0.0}
        
    tx_agg[sym]["Qty"] += qty
    tx_agg[sym]["InvestedTotal"] += (buy_avg * qty)

# Quick dictionary for sector target allocations { 'Technology': 25.0 }
sector_alloc_dict = {alloc["Sector"]: alloc["Allocation"] for alloc in db_allocations if alloc.get("Sector")}

st.subheader("Asset Allocation by Sector")

if not db_sectors:
    st.info("No sectors found! Please add sectors in the Sector Management page first.")
    st.stop()

# Track all edited dataframes in session state or simply via a dictionary
edited_dfs = {}

with st.form("portfolio_allocations_form"):
    # Process each sector and create an expander/container
    for sector_row in db_sectors:
        sector_name = sector_row.get("Sector", "")
        if not sector_name:
            continue
            
        target_alloc = sector_alloc_dict.get(sector_name, 0.0)
        
        with st.expander(f"📁 {sector_name} (Target Sector Allocation: {target_alloc}%)", expanded=True):
            
            # Filter existing stocks for THIS sector
            sector_stocks = [item for item in db_stocks if item.get("Sector") == sector_name]
            
            if not sector_stocks:
                st.info(f"No assets assigned to {sector_name} yet. Go to 'Stock Management' to add some.")
                continue
            
            # Prepare the dataframe for the data editor
            display_data = []
            for p in sector_stocks:
                alloc_val = p.get("Allocation")
                display_data.append({
                    "Symbol": p.get("Symbol", "Unknown"),
                    "Name": p.get("Name", "Unknown"),
                    "Allocation": float(alloc_val) if alloc_val is not None else 0.0
                })
                
            df = pd.DataFrame(display_data)

            st.caption(f"Set the allocation % for the assets in {sector_name}:")
            
            # Keep track of the inputs for this sector
            if sector_name not in edited_dfs:
                edited_dfs[sector_name] = {}
                
            current_sector_sum = 0.0
            
            for p in sector_stocks:
                sym = p.get("Symbol", "Unknown")
                alloc_val = p.get("Allocation")
                current_alloc = float(alloc_val) if alloc_val is not None and not pd.isna(alloc_val) else 0.0
                
                # Fetch aggregated data
                agg = tx_agg.get(sym, {"Qty": 0.0, "InvestedTotal": 0.0})
                current_qty = agg["Qty"]
                invested_amt = agg["InvestedTotal"]

                # Create a layout: Symbol (2), Qty (3), Invested (3), Input (4)
                col1, col2, col3, col4 = st.columns([2, 3, 3, 4])
                with col1:
                    st.write(f"**{sym}**")
                with col2:
                    st.write(f"Qty: {current_qty:.4f}")
                with col3:
                    st.write(f"Invested: ₹{invested_amt:,.2f}")
                with col4:
                    # Use number input, step=0.1 allows floats easily
                    new_alloc = st.number_input(
                        f"Allocation % for {sym}",
                        value=current_alloc,
                        min_value=0.0,
                        max_value=100.0,
                        step=0.1,
                        format="%.2f",
                        key=f"alloc_{sector_name}_{sym}",
                        label_visibility="collapsed"
                    )
                    edited_dfs[sector_name][sym] = new_alloc
                    current_sector_sum += new_alloc
                    
            # Show warning if sum formatting exceeds 100%
            st.write("") # spacer
            if current_sector_sum > 100.0:
                st.warning(f"⚠️ Total asset allocations within this sector ({current_sector_sum:.2f}%) exceed 100%.")
            else:
                st.caption(f"Current Sum: {current_sector_sum:.2f}% / 100.00% max")


    st.divider()

    # Save Button at the Bottom
    submitted = st.form_submit_button("💾 Save All Asset Allocations", type="primary", use_container_width=True)

if submitted:
    master_updates = []
    
    for sector_name, symbols_dict in edited_dfs.items():
        for sym, alloc in symbols_dict.items():
            master_updates.append({
                "Symbol": sym,
                "Allocation": alloc
            })
            
    with st.spinner("Saving asset allocations to Database..."):
        success = db.upsert_stock_allocations(master_updates)
        
    if success:
        st.success("🎉 Asset allocations successfully saved!")
