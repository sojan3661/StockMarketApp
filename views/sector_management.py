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
    stocks_data = db.fetch_stocks()
    allocations_data = db.fetch_allocations()

# Given there's only one column 'Sector', we extract those directly
existing_names = [s.get('Sector', '').lower() for s in sectors_data]

# ==================== Bulk Import ====================
@st.cache_data
def generate_sector_template() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sectors"
    ws.append(["Sector"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

st.subheader("Bulk Import Sectors")
col_dl, col_ul = st.columns([1, 1])

with col_dl:
    import base64
    b64_data = base64.b64encode(generate_sector_template()).decode()
    href = f'<a href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64_data}" download="sector_template.xlsx" style="display: block; width: 100%; padding: 0.5rem 1rem; background-color: #2D333B; border: 1px solid #4B5563; color: #E2E8F0; text-align: center; text-decoration: none; border-radius: 8px; font-weight: 500; box-sizing: border-box; transition: background-color 0.2s;">📥 Download Sector Template</a>'
    st.markdown(href, unsafe_allow_html=True)

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

@st.dialog("Rename Sector")
def rename_sector_dialog(old_name):
    new_name = st.text_input("New Sector Name", value=old_name)
    if st.button("💾 Save Changes", type="primary"):
        new_name = new_name.strip()
        if not new_name:
            st.error("Name cannot be empty.")
        elif new_name.lower() == old_name.lower():
            st.info("No changes made.")
            st.rerun()
        elif new_name.lower() in existing_names:
            st.error(f"Sector '{new_name}' already exists.")
        else:
            with st.spinner(f"Renaming '{old_name}' to '{new_name}' and migrating data..."):
                success, msg = db.update_sector_name(old_name, new_name)
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)


@st.dialog("Delete Sector")
def delete_sector_dialog(sector_name):
    """Confirmation dialog for deleting a sector.
    - Blocks deletion if stocks are still assigned to it.
    - Warns and asks for confirmation if SectorAllocation entries exist.
    - Deletes allocation entries first, then the sector.
    """
    # 1. Check if any stocks are still mapped to this sector
    linked_stocks = [s for s in stocks_data if s.get("Sector", "") == sector_name]
    if linked_stocks:
        stock_names = ", ".join([s.get("Symbol", "?") for s in linked_stocks[:5]])
        extra = f" and {len(linked_stocks) - 5} more" if len(linked_stocks) > 5 else ""
        st.error(
            f"❌ Cannot delete **{sector_name}** — {len(linked_stocks)} stock(s) are still assigned to it: "
            f"**{stock_names}{extra}**.\n\nPlease reassign or remove those stocks first."
        )
        if st.button("Close"):
            st.rerun()
        return

    # 2. Check for SectorAllocation entries
    linked_allocs = [a for a in allocations_data if a.get("Sector", "") == sector_name]

    if linked_allocs:
        portfolios = sorted(set(a.get("Portfolio", "Unknown") for a in linked_allocs))
        portfolio_list = ", ".join([f"**{p}**" for p in portfolios])
        st.warning(
            f"⚠️ **{sector_name}** has allocation entries in {len(linked_allocs)} portfolio record(s): "
            f"{portfolio_list}.\n\nDeleting this sector will also remove those allocation entries."
        )
        st.markdown("---")
        st.markdown("Are you sure you want to proceed?")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("✅ Yes, Delete Everything", type="primary", use_container_width=True):
                with st.spinner("Removing sector allocations..."):
                    alloc_ok = db.delete_allocations_by_sector(sector_name)
                if not alloc_ok:
                    st.error("Failed to delete sector allocation entries. Sector not deleted.")
                    return
                with st.spinner(f"Deleting sector '{sector_name}'..."):
                    sec_ok = db.delete_sector(sector_name)
                if sec_ok:
                    st.success(f"✅ Deleted '{sector_name}' and its allocation entries.")
                    st.rerun()
                else:
                    st.error("Allocation entries deleted, but failed to delete the sector record.")
        with col_no:
            if st.button("Cancel", use_container_width=True):
                st.rerun()
    else:
        # No allocations — simple confirmation
        st.warning(f"Are you sure you want to delete **{sector_name}**? This cannot be undone.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("🗑️ Yes, Delete", type="primary", use_container_width=True):
                with st.spinner(f"Deleting '{sector_name}'..."):
                    ok = db.delete_sector(sector_name)
                if ok:
                    st.success(f"Deleted '{sector_name}'.")
                    st.rerun()
        with col_no:
            if st.button("Cancel", use_container_width=True):
                st.rerun()


if not sectors_data:
    st.info("No sectors found in the database. Use the form above to add your first sector!")
else:
    for item in sectors_data:
        sector_name = item.get("Sector", "Unknown Sector")

        # Count stocks and allocations for this sector (for display badge)
        stock_count = sum(1 for s in stocks_data if s.get("Sector", "") == sector_name)
        alloc_count = sum(1 for a in allocations_data if a.get("Sector", "") == sector_name)

        col_name, col_edit, col_del = st.columns([3, 1, 1])
        with col_name:
            badges = ""
            if stock_count:
                badges += f'<span style="background-color: #3B82F620; color: #60A5FA; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; margin-left: 8px;">{stock_count} stock(s)</span>'
            if alloc_count:
                badges += f'<span style="background-color: #F59E0B20; color: #F59E0B; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; margin-left: 6px;">{alloc_count} allocation(s)</span>'
            st.markdown(
                f"""
                <div style="background-color: #1A1D24; padding: 15px; border-radius: 8px; border: 1px solid #2D333B; margin-bottom: 10px;">
                    <span style="font-weight: 600; font-size: 1.1rem; color: #F8FAFC;">{sector_name}</span>{badges}
                </div>
                """,
                unsafe_allow_html=True
            )

        with col_edit:
            st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
            if st.button("✏️ Edit", key=f"edit_{sector_name}", use_container_width=True):
                rename_sector_dialog(sector_name)

        with col_del:
            st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Delete", key=f"del_{sector_name}", type="primary", use_container_width=True):
                delete_sector_dialog(sector_name)
