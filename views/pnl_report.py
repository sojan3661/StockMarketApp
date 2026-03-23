import streamlit as st
import pandas as pd
import sys
import os

# Add root path for Config import
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("🧾 Realized P&L Report")
st.markdown("View profit and loss exclusively for your closed (sold) transactions.")

if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()

with st.spinner("Crunching transaction history..."):
    all_transactions = db.fetch_all_transactions()
    db_plans = db.fetch_investment_plan()

# Choose portfolio
if not db_plans:
    st.info("No investment plans/portfolios found.")
    st.stop()
    
plans_list = db_plans if isinstance(db_plans, list) else [db_plans]
portfolio_names = sorted([p.get("Portfolio") for p in plans_list if p.get("Portfolio")])

if not portfolio_names:
    st.info("No valid portfolios found.")
    st.stop()

selected_portfolio = st.selectbox("Select Portfolio", ["All Portfolios"] + portfolio_names)

# Filter transactions strictly to CLOSED ones (where SellAvg is not null)
if selected_portfolio != "All Portfolios":
    tx_to_process = [tx for tx in all_transactions if tx.get("Portfolio") == selected_portfolio and tx.get("SellAvg") is not None]
else:
    tx_to_process = [tx for tx in all_transactions if tx.get("SellAvg") is not None]

if not tx_to_process:
    st.info("No sold (closed) transactions found for the selected portfolio.")
    st.stop()

# Calculate P&L
realized_pnl_total = 0.0
asset_pnl = {}

for tx in tx_to_process:
    sym = tx.get("Symbol")
    qty = float(tx.get("Qty", 0))
    buy_avg = float(tx.get("BuyAvg", 0))
    sell_avg = float(tx.get("SellAvg"))
    
    if sym not in asset_pnl:
        asset_pnl[sym] = {
            "Symbol": sym,
            "Qty Sold": 0.0,
            "Realized P&L": 0.0,
        }
        
    realized = (sell_avg - buy_avg) * qty
    realized_pnl_total += realized
    
    asset_pnl[sym]["Realized P&L"] += realized
    asset_pnl[sym]["Qty Sold"] += qty

st.markdown(
    """
    <div style="background-color: #1E222A; padding: 8px 15px; border-radius: 8px 8px 0 0; border: 1px solid #2D333B; border-bottom: none; margin-top: 20px; margin-bottom: 15px;">
        <h3 style="margin: 0; color: #F8FAFC; font-size: 1.05rem;">📊 Overview</h3>
    </div>
    """,
    unsafe_allow_html=True
)

st.metric("Total Realized P&L (Booked)", f"₹{realized_pnl_total:,.2f}")

st.markdown(
    """
    <div style="background-color: #1E222A; padding: 8px 15px; border-radius: 8px 8px 0 0; border: 1px solid #2D333B; border-bottom: none; margin-top: 20px; margin-bottom: 10px;">
        <h3 style="margin: 0; color: #F8FAFC; font-size: 1.05rem;">📝 Asset-wise Breakdown</h3>
    </div>
    """,
    unsafe_allow_html=True
)

df_pnl = pd.DataFrame(list(asset_pnl.values()))
if not df_pnl.empty:
    st.dataframe(
        df_pnl,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Realized P&L": st.column_config.NumberColumn(format="₹%.2f"),
            "Qty Sold": st.column_config.NumberColumn(format="%.4f")
        }
    )
