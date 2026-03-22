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

def get_stock_info(symbol):
    if nse_eq:
        try:
            quote = nse_eq(symbol)
            price = None
            pe = None
            
            if 'priceInfo' in quote and 'lastPrice' in quote['priceInfo']:
                price = float(quote['priceInfo']['lastPrice'])
            
            if 'metadata' in quote and 'pdSymbolPe' in quote['metadata']:
                pe = float(quote['metadata']['pdSymbolPe'])
                
            return price, pe
        except Exception:
            pass
    return None, None

st.title("Portfolio Overview")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()
    
@st.cache_data(ttl=300)
def get_portfolio_display_data(db_stocks, open_transactions, nav_df, port_stock_allocations_tuple):
    port_stock_allocations = dict(port_stock_allocations_tuple)
    
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
        
        # Get target allocation for this specific portfolio
        alloc = float(port_stock_allocations.get(sym, 0.0))
        
        agg = tx_agg.get(sym, {"Qty": 0.0, "InvestedTotal": 0.0})
        qty = agg["Qty"]
        invested_amt = agg["InvestedTotal"]
        
        avg_buy = (invested_amt / qty) if qty > 0 else 0.0
        pct_allocation = (invested_amt / total_invested_portfolio) if total_invested_portfolio > 0 else 0.0
        
        live_price = 0.0
        pe_ratio = None
        is_listed = p.get("Listed", True)
        
        if is_listed:
            if is_equity:
                fetched_price, fetched_pe = get_stock_info(sym)
                live_price = fetched_price if fetched_price is not None else 0.0
                pe_ratio = fetched_pe
            else:
                fetched_nav = get_nav(nav_df, name)
                live_price = float(fetched_nav) if fetched_nav is not None else 0.0
        else:
            live_price = float(p.get("LTP") or 0.0)
        
        current_value = qty * live_price
        
        display_data.append({
            "Sector": p.get("Sector", "Unknown"),
            "Symbol": sym,
            "Name": name,
            "Asset Type": "Stock" if is_equity else "Mutual Fund",
            "Listing": "Listed" if p.get("Listed", True) else "Unlisted",
            "PE Ratio": pe_ratio,
            "Qty": qty,
            "Invested Amount": invested_amt,
            "% of Allocation": pct_allocation * 100,
            "Avg Buy": avg_buy,
            "Live Price": live_price,
            "Current Value": current_value
        })
        
    if not display_data:
        return pd.DataFrame(columns=["Sector", "Symbol", "Name", "Asset Type", "Listing", "PE Ratio",
                                     "Qty", "Invested Amount",
                                     "% of Allocation", "Avg Buy", "Live Price", "Current Value"])
    df = pd.DataFrame(display_data)
    df = df.sort_values(by=["Sector", "Symbol"], ascending=[True, True])
    df = df.reset_index(drop=True)
    return df

with st.spinner("Loading portfolio data and live market prices..."):
    db_stocks = db.fetch_stocks()
    open_transactions = db.fetch_open_transactions()
    nav_df = load_nav_data()
    db_stock_allocations = db.fetch_stock_allocations()
    db_investment_plan = db.fetch_investment_plan()
    
# Support list of plans vs single plan
plans_list = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

if not db_stocks:
    st.info("No assets found in your portfolio yet. Go to 'Stock Management' to start adding them.")
elif not portfolio_names:
    st.info("No investment plans found. Create a portfolio in the Build Portfolio page first.")
else:
    tabs = st.tabs(portfolio_names)
    
    for i, port_name in enumerate(portfolio_names):
        with tabs[i]:
            st.divider()
            st.subheader(f"Assets for {port_name}")
            
            # Filter stock targets for this specific portfolio (only allocation > 0)
            port_stock_allocations = {
                a["Symbol"]: a["Allocation"]
                for a in db_stock_allocations
                if a.get("Portfolio") == port_name and a.get("Symbol") and (a.get("Allocation") or 0) > 0
            }
            
            # Only show assets mapped to this portfolio in StockAllocation
            mapped_symbols = set(port_stock_allocations.keys())
            port_stocks = [s for s in db_stocks if s.get("Symbol") in mapped_symbols]
            
            # Filter open transactions for this specific portfolio
            port_open_transactions = [
                tx for tx in open_transactions 
                if tx.get("Portfolio") == port_name
            ]
            
            # Tuple conversion so it is hashable for st.cache_data
            port_alloc_tuple = tuple(port_stock_allocations.items())
            
            with st.spinner(f"Calculating live valuations for {port_name}..."):
                df = get_portfolio_display_data(port_stocks, port_open_transactions, nav_df, port_alloc_tuple)
            
            # If a portfolio has no open transactions and no asset allocations, it might be empty
            if df.empty or (df["Qty"].sum() == 0 and df["Target Allocation %"].sum() == 0):
                st.info(f"No assets or allocations found for {port_name}.")
                continue
            
            # Summary metrics
            total_invested = df["Invested Amount"].sum()
            total_current_value = df["Current Value"].sum()
            gain_loss = total_current_value - total_invested
            gain_loss_pct = (gain_loss / total_invested * 100) if total_invested > 0 else 0
            
            # Calculate Average PE Ratio
            # Exclude DEBT and ETF/INDEX FUND sectors
            pe_df = df[
                (df["PE Ratio"].notnull()) & 
                (~df["Sector"].str.upper().isin(["DEBT", "ETF/INDEX FUND"]))
            ]
            avg_pe = pe_df["PE Ratio"].mean() if not pe_df.empty else 0.0
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("💰 Total Invested", f"₹{total_invested:,.2f}")
            m2.metric("📈 Current Value", f"₹{total_current_value:,.2f}")
            m3.metric("📊 Gain / Loss", f"₹{gain_loss:,.2f}", delta=f"{gain_loss_pct:.2f}%")
            m4.metric("🎯 Average PE", f"{avg_pe:.2f}")
            
            import plotly.graph_objects as go
            import plotly.express as px
            
            st.divider()
            
            # Pie Chart: Stock vs Allocation (Invested Amount)
            if not df.empty and df["Invested Amount"].sum() > 0:
                st.subheader("Asset Allocation")
                
                # Filter out zero-investment rows for a cleaner pie chart
                pie_df = df[df["Invested Amount"] > 0]
                
                fig_pie = go.Figure(go.Pie(
                    labels=pie_df["Name"].tolist(),
                    values=pie_df["Invested Amount"].tolist(),
                    hole=0.4,
                    marker_colors=px.colors.qualitative.Pastel,
                    textinfo="label+percent",
                    textposition="inside",
                ))
                
                fig_pie.update_layout(
                    showlegend=False,
                    margin=dict(t=20, b=20, l=0, r=0),
                    height=600,
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color="#E2E8F0")
                )
                
                st.plotly_chart(fig_pie, use_container_width=True)
                st.divider()
            
            # Download button for the current portfolio table
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label=f"📥 Download {port_name} Portfolio as CSV",
                data=csv,
                file_name=f"{port_name.replace(' ', '_')}_portfolio.csv",
                mime="text/csv",
            )

            event = st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key=f"data_grid_{port_name}",
                column_config={
                    "Symbol": None,
                    "Asset Type": None,
                    "PE Ratio": st.column_config.NumberColumn("PE \nRatio", format="%.2f"),
                    "Qty": st.column_config.NumberColumn("Current \nQty", format="%.4f"),
                    "Invested Amount": st.column_config.NumberColumn("Current \nInvested Amount", format="₹ %.2f"),
                    "% of Allocation": st.column_config.NumberColumn("% of \nAllocation", format="%.2f%%"),
                    "Avg Buy": st.column_config.NumberColumn("Avg \nBuy Price", format="₹ %.2f"),
                    "Live Price": st.column_config.NumberColumn("Live \nPrice", format="₹ %.2f"),
                    "Current Value": st.column_config.NumberColumn("Current \nValue", format="₹ %.2f"),
                }
            )

            # Order History View specific to this tab
            selected_rows = event.selection.rows
            if selected_rows:
                selected_index = selected_rows[0]
                selected_symbol = df.iloc[selected_index]["Symbol"]
                selected_name = df.iloc[selected_index]["Name"]
                
                st.divider()
                st.subheader(f"Order History: {selected_name} ({selected_symbol})")
                
                with st.spinner(f"Loading transaction history for {selected_symbol}..."):
                    history = db.fetch_transactions_by_symbol(selected_symbol, portfolio=port_name)
                    
                if not history:
                    st.info("No transaction history found for this asset.")
                else:
                    hist_df = pd.DataFrame(history)
                    
                    if "SellDate" not in hist_df.columns:
                        hist_df["SellDate"] = None
                    if "SellAvg" not in hist_df.columns:
                        hist_df["SellAvg"] = None
                        
                    display_hist = hist_df[["BuyDate", "Qty", "BuyAvg", "SellDate", "SellAvg"]].copy()

                    for date_col in ["BuyDate", "SellDate"]:
                        display_hist[date_col] = pd.to_datetime(
                            display_hist[date_col], errors="coerce"
                        ).dt.strftime("%d-%b-%Y")

                    st.dataframe(
                        display_hist,
                        use_container_width=True,
                        hide_index=True,
                        key=f"history_grid_{port_name}",
                        column_config={
                            "BuyDate": "Buy Date",
                            "Qty": st.column_config.NumberColumn("Quantity", format="%.4f"),
                            "BuyAvg": st.column_config.NumberColumn("Buy Price", format="₹ %.2f"),
                            "SellDate": "Sell Date",
                            "SellAvg": st.column_config.NumberColumn("Sell Price", format="₹ %.2f"),
                        }
                    )
