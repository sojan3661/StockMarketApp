import streamlit as st
import pandas as pd
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

# Optionally import nsepython for live pricing
try:
    from nsepython import nse_eq
except ImportError:
    nse_eq = None

@st.cache_data(ttl=3600)
def load_nav_data():
    try:
        nav_df = pd.read_csv(
            "https://www.amfiindia.com/spages/NAVAll.txt",
            sep=";",
            header=None,
            names=["scheme_code", "isin1", "isin2", "scheme_name", "nav", "date"],
            on_bad_lines="skip"
        )
        return nav_df
    except Exception as e:
        return pd.DataFrame()

def get_nav(nav_df, fund_name):
    if nav_df.empty:
        return None
    result = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav","date"]]
    return result.iloc[0]["nav"] if not result.empty else None

def get_stock_price(symbol):
    if nse_eq:
        try:
            quote = nse_eq(symbol)
            if 'priceInfo' in quote and 'lastPrice' in quote['priceInfo']:
                return float(quote['priceInfo']['lastPrice'])
        except Exception:
            pass
    return None

st.title("Portfolio Overview")
st.write("A summary view of all your assets sorted by Sector, including Live Valuations.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
@st.cache_data(ttl=300)
def get_portfolio_display_data(db_stocks, open_transactions, nav_df):
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
        
    total_invested_portfolio = sum([v["InvestedTotal"] for v in tx_agg.values()])

    display_data = []
    
    for p in db_stocks:
        sym = p.get("Symbol", "Unknown")
        name = p.get("Name", "Unknown")
        is_equity = p.get("Equity", False)
        
        alloc_val = p.get("Allocation")
        alloc = float(alloc_val) if alloc_val is not None and not pd.isna(alloc_val) else 0.0
        
        agg = tx_agg.get(sym, {"Qty": 0.0, "InvestedTotal": 0.0})
        qty = agg["Qty"]
        invested_amt = agg["InvestedTotal"]
        
        avg_buy = (invested_amt / qty) if qty > 0 else 0.0
        pct_allocation = (invested_amt / total_invested_portfolio) if total_invested_portfolio > 0 else 0.0
        
        live_price = 0.0
        is_listed = p.get("Listed", True)
        
        if is_listed:
            if is_equity:
                fetched_price = get_stock_price(sym)
                live_price = fetched_price if fetched_price is not None else 0.0
            else:
                fetched_nav = get_nav(nav_df, name)
                live_price = float(fetched_nav) if fetched_nav is not None else 0.0
        
        current_value = qty * live_price
        
        display_data.append({
            "Sector": p.get("Sector", "Unknown"),
            "Symbol": sym,
            "Name": name,
            "Asset Type": "Stock" if is_equity else "Mutual Fund",
            "Listing": "Listed" if p.get("Listed", True) else "Unlisted",
            "Target Allocation %": alloc,
            "Qty": qty,
            "Invested Amount": invested_amt,
            "% of Allocation": pct_allocation * 100,
            "Avg Buy": avg_buy,
            "Live Price": live_price,
            "Current Value": current_value
        })
        
    df = pd.DataFrame(display_data)
    df = df.sort_values(by=["Sector", "Symbol"], ascending=[True, True])
    df = df.reset_index(drop=True)
    return df

with st.spinner("Loading portfolio data and live market prices..."):
    db_stocks = db.fetch_stocks()
    open_transactions = db.fetch_open_transactions()
    nav_df = load_nav_data()

if not db_stocks:
    st.info("No assets found in your portfolio yet. Go to 'Stock Management' to start adding them.")
else:
    with st.spinner("Calculating live valuations..."):
        df = get_portfolio_display_data(db_stocks, open_transactions, nav_df)
    
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Symbol": None,
            "Asset Type": None,
            "Target Allocation %": st.column_config.NumberColumn("Target \nAllocation %", format="%.2f%%"),
            "Qty": st.column_config.NumberColumn("Current \nQty", format="%.4f"),
            "Invested Amount": st.column_config.NumberColumn("Current \nInvested Amount", format="₹ %.2f"),
            "% of Allocation": st.column_config.NumberColumn("% of \nAllocation", format="%.2f%%"),
            "Avg Buy": st.column_config.NumberColumn("Avg \nBuy Price", format="₹ %.2f"),
            "Live Price": st.column_config.NumberColumn("Live \nPrice", format="₹ %.2f"),
            "Current Value": st.column_config.NumberColumn("Current \nValue", format="₹ %.2f"),
        }
    )

    # 3. Order History View
    selected_rows = event.selection.rows
    if selected_rows:
        selected_index = selected_rows[0]
        selected_symbol = df.iloc[selected_index]["Symbol"]
        selected_name = df.iloc[selected_index]["Name"]
        
        st.divider()
        st.subheader(f"Order History: {selected_name} ({selected_symbol})")
        
        with st.spinner(f"Loading transaction history for {selected_symbol}..."):
            history = db.fetch_transactions_by_symbol(selected_symbol)
            
        if not history:
            st.info("No transaction history found for this asset.")
        else:
            hist_df = pd.DataFrame(history)
            
            # Format display dataframe for history
            if "SellDate" not in hist_df.columns:
                hist_df["SellDate"] = None
            if "SellAvg" not in hist_df.columns:
                hist_df["SellAvg"] = None
                
            display_hist = hist_df[["BuyDate", "Qty", "BuyAvg", "SellDate", "SellAvg"]].copy()
            
            st.dataframe(
                display_hist,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "BuyDate": "Buy Date",
                    "Qty": st.column_config.NumberColumn("Quantity", format="%.4f"),
                    "BuyAvg": st.column_config.NumberColumn("Buy Price", format="₹ %.2f"),
                    "SellDate": "Sell Date",
                    "SellAvg": st.column_config.NumberColumn("Sell Price", format="₹ %.2f"),
                }
            )
