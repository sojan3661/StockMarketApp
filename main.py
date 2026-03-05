import streamlit as st

st.set_page_config(page_title="Stock Market App", layout="wide")

# Define the pages for the multi-page app
pages = {
    "Menu": [
        st.Page("views/sector_management.py", title="Sector Management", icon="📋"),
        st.Page("views/sector_allocation.py", title="Sector Allocation", icon="📊"),
    ]
}

pg = st.navigation(pages)
pg.run()

