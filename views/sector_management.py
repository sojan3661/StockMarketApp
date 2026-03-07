import streamlit as st
import sys
import os
import io
import openpyxl

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Sector Management")

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

# ==================== Bulk Upload & Template ====================
def generate_sector_template() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sectors"

    # Headers
    ws.append(["Sector"])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

st.subheader("Bulk Import Sectors")
col_dl, col_ul = st.columns([1, 1])

with col_dl:
    st.download_button(
        label="📥 Download Sector Template",
        data=generate_sector_template(),
        file_name="sector_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

with col_ul:
    uploaded_sector_file = st.file_uploader(
        "Upload Sectors",
        type=["xlsx"],
        label_visibility="collapsed"
    )

if uploaded_sector_file is not None:
    with st.expander("📋 Preview & Import Uploaded Sectors", expanded=True):
        try:
            import pandas as pd
            upload_df = pd.read_excel(uploaded_sector_file, dtype=str)
            upload_df.columns = [c.strip() for c in upload_df.columns]
            
            if "Sector" not in upload_df.columns:
                st.error("Missing column in file: Sector")
            else:
                st.dataframe(upload_df, use_container_width=True, hide_index=True)
                
                if st.button("🚀 Import All Sectors", type="primary"):
                    success_count = 0
                    fail_count = 0
                    
                    for i, row in upload_df.iterrows():
                        sec_name = str(row.get("Sector", "")).strip()
                        
                        if not sec_name or pd.isna(sec_name) or sec_name.lower() == "nan":
                            st.warning(f"Row {i+2}: Missing Sector name — skipped.")
                            fail_count += 1
                            continue
                            
                        # Prevent duplicates
                        if sec_name.lower() in existing_names:
                            st.warning(f"Row {i+2}: Sector '{sec_name}' already exists — skipped.")
                            fail_count += 1
                            continue
                        
                        ok = db.add_sector(sec_name)
                        if ok:
                            success_count += 1
                            existing_names.append(sec_name.lower()) # local cache update
                        else:
                            fail_count += 1
                            
                    if success_count:
                        st.success(f"✅ {success_count} sector(s) imported successfully.")
                    if fail_count:
                        st.error(f"❌ {fail_count} sector(s) failed — see errors/warnings above.")
                        
        except Exception as e:
            st.error(f"Error reading file: {e}")

st.divider()

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
