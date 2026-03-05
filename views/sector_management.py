import streamlit as st
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Sector Management")
st.write("Manage your market sectors and themes below.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
# Always fetch fresh data from the DB on load instead of session state
# This ensures multiple users/tabs stay in sync with the database
with st.spinner("Loading sectors..."):
    sectors_data = db.fetch_sectors()

# Given there's only one column 'Sector', we extract those directly
existing_names = [s.get('Sector', '').lower() for s in sectors_data]

# ==================== Add a New Sector ====================
st.subheader("Add a New Sector")
with st.form("add_sector_form", clear_on_submit=True):
    new_sector = st.text_input("Sector/Theme Name", placeholder="e.g., Technology, Healthcare, Energy...")
    submitted = st.form_submit_button("Add Sector")
    
    if submitted:
        new_sector = new_sector.strip()
        if not new_sector:
            st.warning("Please enter a valid sector name.")
        elif new_sector.lower() in existing_names:
            st.warning(f"The sector '{new_sector}' already exists.")
        else:
            success = db.add_sector(new_sector)
            if success:
                st.success(f"Successfully added sector: '{new_sector}'")
                st.rerun()

st.divider()

# ==================== Existing Sectors ====================
st.subheader("Current Sectors / Themes")

if not sectors_data:
    st.info("No sectors found in the database. Use the form above to add your first sector!")
else:
    for item in sectors_data:
        # The only column available is 'Sector'
        sector_name = item.get("Sector", "Unknown Sector")
        
        col_name, col_action = st.columns([4, 1])
        with col_name:
            st.write(f"**{sector_name}**")
            
        with col_action:
            if st.button("Delete", key=f"del_{sector_name}", use_container_width=True):
                # Now passing the sector name to delete since there is no ID column
                success = db.delete_sector(sector_name)
                if success:
                    st.success(f"Deleted '{sector_name}'.")
                    st.rerun()
