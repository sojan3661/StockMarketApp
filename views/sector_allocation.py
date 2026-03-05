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
    
with st.spinner("Loading sector allocations..."):
    sectors_data = db.fetch_sectors()
    allocations_data = db.fetch_allocations()

# Create lookup dict for existing allocations { 'Technology': {'Id': 1, 'Allocation': 25.5} }
alloc_dict = {}
for alloc in allocations_data:
    sector_name = alloc.get("Sector")
    if sector_name:
        alloc_dict[sector_name] = {
            "Id": alloc.get("Id"),
            "Allocation": alloc.get("Allocation", 0.0)
        }

# Merge all sectors with their allocations
merged_data = []
for sec in sectors_data:
    sector_name = sec.get("Sector")
    if not sector_name:
        continue
        
    existing = alloc_dict.get(sector_name)
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

if not merged_data:
    st.info("No sectors found! Please add sectors in the Sector Management page first.")
    st.stop()

st.subheader("Edit Allocations")

df = pd.DataFrame(merged_data)

# We don't use a form here because we want the Total Allocation sum to update in real-time
# Streamlit data_editor will refresh the script on edit, but it won't hit the DB until "Save" is clicked.
edited_df = st.data_editor(
    df,
    hide_index=True,
    column_config={
        "Id": None,
        "Sector": st.column_config.TextColumn("Sector / Theme", disabled=True),
        "Allocation": st.column_config.NumberColumn("Allocation %", min_value=0.0, max_value=100.0, step=1.0, format="%.2f%%")
    },
    use_container_width=True
)

st.divider()

# Calculate total allocation dynamically
total_allocation = edited_df["Allocation"].sum()

col1, col2 = st.columns([3, 1])
with col1:
    if total_allocation > 100.0:
        st.error(f"Total Allocation is {total_allocation:.2f}%. It should not exceed 100%.")
    elif total_allocation < 100.0:
        st.warning(f"Total Allocation is {total_allocation:.2f}%. You still have {100.0 - total_allocation:.2f}% to allocate.")
    else:
        st.success(f"Total Allocation is perfectly {total_allocation:.2f}%!")
        
with col2:
    # Save button triggers the DB logic independently of the grid edits
    submitted = st.button("Save Allocations", type="primary", use_container_width=True)

if submitted:
    # Convert dataframe back to a list of dicts for the API
    payload = []
    
    for index, row in edited_df.iterrows():
        record = {
            "Sector": row["Sector"],
            "Allocation": float(row["Allocation"])
        }
        
        # Only include ID in the payload if it actually exists
        if pd.notna(row["Id"]):
            record["Id"] = int(row["Id"]) 
            
        payload.append(record)
        
    with st.spinner("Saving to database..."):
        success = db.upsert_allocations(payload)
        
    if success:
        st.success("Allocations successfully saved!")
        st.rerun()
