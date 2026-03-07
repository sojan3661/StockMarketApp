import streamlit as st

st.set_page_config(page_title="Stock Market App", layout="wide")

# Define the pages for the multi-page app
pages = {
    "Menu": [
        st.Page("views/dashboard.py", title="Dashboard", icon="🏠"),
        st.Page("views/build_portfolio.py", title="Build Portfolio", icon="🏗️"),
        st.Page("views/sector_management.py", title="Sector Management", icon="📋"),
        st.Page("views/sector_allocation.py", title="Sector Allocation", icon="📊"),
        st.Page("views/stock_management.py", title="Asset Management", icon="📈"),
        st.Page("views/portfolio_management.py", title="Portfolio Rebalancing", icon="💼"),
        st.Page("views/portfolio.py", title="Portfolio", icon="🌍"),
        st.Page("views/add_transaction.py", title="Add Transaction", icon="💸"),
    ]
}

pg = st.navigation(pages)
pg.run()

