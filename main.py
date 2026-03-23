import streamlit as st
import os

st.set_page_config(page_title="Stock Market App", layout="wide", initial_sidebar_state="expanded")

def inject_custom_css():
    css_file = os.path.join(os.path.dirname(__file__), "Assets", "style.css")
    if os.path.exists(css_file):
        with open(css_file, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

inject_custom_css()

dashboard = st.Page("views/dashboard.py", title="Dashboard", icon="🏠")
build_portfolio = st.Page("views/build_portfolio.py", title="Build Portfolio", icon="🏗️")
sector_management = st.Page("views/sector_management.py", title="Sector Management", icon="📋")
sector_allocation = st.Page("views/sector_allocation.py", title="Sector Allocation", icon="📊")
asset_management = st.Page("views/stock_management.py", title="Asset Management", icon="📈")
portfolio_rebalancing = st.Page("views/portfolio_management.py", title="Portfolio Rebalancing", icon="💼")
portfolio = st.Page("views/portfolio.py", title="Portfolio", icon="🌍")
add_transaction = st.Page("views/add_transaction.py", title="Add Transaction", icon="💸")
pnl_report = st.Page("views/pnl_report.py", title="P&L Report", icon="🧾")

pages = [
    dashboard, build_portfolio, sector_management, sector_allocation, 
    asset_management, portfolio_rebalancing, portfolio, add_transaction, pnl_report
]

pg = st.navigation(pages, position="hidden")

with st.sidebar:
    st.markdown("<h2 style='text-align: center; margin-bottom: 20px;'>Stock Market<br>Portfolio Management</h2>", unsafe_allow_html=True)
    for p in pages:
        st.page_link(p)

# Footer at the bottom of the page
st.markdown(
    """
    <div style="position: fixed; bottom: 10px; left: 10px; text-align: left; color: grey; font-size: 12px; z-index: 100;">
        <p>© 2026 <a href="https://www.linkedin.com/in/sojanthomasmattathil/" target="_blank" style="color: grey; text-decoration: none;">Sojan Thomas</a></p>
    </div>
    """,
    unsafe_allow_html=True
)

pg.run()

