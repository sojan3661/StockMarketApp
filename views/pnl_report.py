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

col1, col2 = st.columns(2)

with col1:
    selected_portfolio = st.selectbox("Select Portfolio", ["All Portfolios"] + portfolio_names)

# Filter transactions strictly to CLOSED ones (where SellAvg is not null)
if selected_portfolio != "All Portfolios":
    tx_to_process_pre = [tx for tx in all_transactions if tx.get("Portfolio") == selected_portfolio and tx.get("SellAvg") is not None]
else:
    tx_to_process_pre = [tx for tx in all_transactions if tx.get("SellAvg") is not None]

# Extract financial years from closed transactions
available_fys = set()
for tx in tx_to_process_pre:
    sell_date_str = tx.get("SellDate")
    fy = "Unknown"
    if sell_date_str:
        try:
            date_obj = pd.to_datetime(sell_date_str)
            if pd.notna(date_obj):
                year = date_obj.year
                month = date_obj.month
                if month >= 4:
                    fy = f"FY {year}-{str(year+1)[-2:]}"
                else:
                    fy = f"FY {year-1}-{str(year)[-2:]}"
        except Exception:
            pass
    tx["_FY"] = fy
    if fy != "Unknown":
        available_fys.add(fy)

fys = sorted(list(available_fys), reverse=True)

with col2:
    selected_fy = st.selectbox("Select Financial Year", ["All"] + fys)

# Apply FY filter
if selected_fy != "All":
    tx_to_process = [tx for tx in tx_to_process_pre if tx.get("_FY") == selected_fy]
else:
    tx_to_process = tx_to_process_pre

if not tx_to_process:
    st.info("No sold (closed) transactions found for the selected criteria.")
    st.stop()

# Calculate P&L
realized_pnl_total = 0.0
short_term_pnl_total = 0.0
long_term_pnl_total = 0.0
asset_pnl = {}

for tx in tx_to_process:
    sym = tx.get("Symbol")
    qty = float(tx.get("Qty", 0))
    buy_avg = float(tx.get("BuyAvg", 0))
    sell_avg = float(tx.get("SellAvg"))
    buy_date_str = tx.get("BuyDate")
    sell_date_str = tx.get("SellDate")
    realized = (sell_avg - buy_avg) * qty
    realized_pnl_total += realized
    
    is_long_term = False
    if buy_date_str and sell_date_str:
        try:
            buy_dt = pd.to_datetime(buy_date_str)
            sell_dt = pd.to_datetime(sell_date_str)
            if pd.notna(buy_dt) and pd.notna(sell_dt):
                if (sell_dt - buy_dt).days > 365:
                    is_long_term = True
        except Exception:
            pass
            
    if is_long_term:
        long_term_pnl_total += realized
    else:
        short_term_pnl_total += realized
    
    if sym not in asset_pnl:
        asset_pnl[sym] = {
            "Symbol": sym,
            "Qty Sold": 0.0,
            "Realized P&L": 0.0,
        }
        
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

m1, m2, m3 = st.columns(3)
m1.metric("Total Realized P&L", f"₹{realized_pnl_total:,.2f}")
m2.metric("Short Term P&L", f"₹{short_term_pnl_total:,.2f}")
m3.metric("Long Term P&L", f"₹{long_term_pnl_total:,.2f}")

st.divider()

@st.dialog("Transaction Details")
def show_transactions_dialog(symbol, sym_tx):
    st.markdown(f"**Asset:** {symbol}")
    df = pd.DataFrame(sym_tx)
    if not df.empty:
        for date_col in ["Buy Date", "Sell Date"]:
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%d-%b-%Y")
                
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Qty": st.column_config.NumberColumn(format="%.4f"),
                "Buy Avg": st.column_config.NumberColumn(format="₹%.2f"),
                "Sell Avg": st.column_config.NumberColumn(format="₹%.2f"),
                "Realized P&L": st.column_config.NumberColumn(format="₹%.2f"),
            }
        )
    else:
        st.info("No transactions found.")

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
    event = st.dataframe(
        df_pnl,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Realized P&L": st.column_config.NumberColumn(format="₹%.2f"),
            "Qty Sold": st.column_config.NumberColumn(format="%.4f")
        }
    )
    
    if event.selection.rows:
        selected_idx = event.selection.rows[0]
        selected_sym = df_pnl.iloc[selected_idx]["Symbol"]
        
        sym_tx = []
        for tx in tx_to_process:
            if tx.get("Symbol") == selected_sym:
                qty = float(tx.get("Qty", 0))
                buy_avg = float(tx.get("BuyAvg", 0))
                sell_avg = float(tx.get("SellAvg", 0))
                realized = (sell_avg - buy_avg) * qty
                sym_tx.append({
                    "Buy Date": tx.get("BuyDate"),
                    "Sell Date": tx.get("SellDate"),
                    "Symbol": selected_sym,
                    "Qty": qty,
                    "Buy Avg": buy_avg,
                    "Sell Avg": sell_avg,
                    "Realized P&L": realized
                })
        
        show_transactions_dialog(selected_sym, sym_tx)
