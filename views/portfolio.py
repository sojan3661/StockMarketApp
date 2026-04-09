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

@st.cache_data(ttl=18000)
def _fetch_nav_data_cached():
    import urllib.request
    import ssl
    req = urllib.request.Request(
        "https://www.amfiindia.com/spages/NAVAll.txt",
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=context) as response:
        return pd.read_csv(
            response,
            sep=";",
            header=None,
            names=["scheme_code", "isin1", "isin2", "scheme_name", "nav", "date"],
            on_bad_lines="skip"
        )

def load_nav_data():
    try:
        return _fetch_nav_data_cached()
    except Exception:
        return pd.DataFrame()

def get_nav(nav_df, fund_name):
    if nav_df.empty or not fund_name:
        return None
        
    res = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]
        
    res = nav_df.loc[nav_df["scheme_name"].str.lower() == fund_name.lower(), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]
        
    short_name = fund_name[:15].lower()
    res = nav_df.loc[nav_df["scheme_name"].str.lower().str.contains(short_name, na=False, regex=False), ["nav"]]
    if not res.empty:
        return res.iloc[0]["nav"]
        
    return None

def get_stock_info(symbol):
    price = None
    pe = None
    
    # 1. Try NSEPython first
    if nse_eq:
        try:
            quote = nse_eq(symbol)
            if quote and 'priceInfo' in quote and 'lastPrice' in quote['priceInfo']:
                price = float(quote['priceInfo']['lastPrice'])
            
            if quote and 'metadata' in quote and 'pdSymbolPe' in quote['metadata']:
                pe = float(quote['metadata']['pdSymbolPe'])
                
            if price and price > 0:
                return price, pe
        except Exception:
            pass

    # 2. Yahoo Finance Fallback if NSE failed or returned no valid price
    import urllib.request
    import urllib.parse
    import json
    import ssl
    
    for suffix in [".NS", ".BO", ""]:
        try:
            encoded_sym = urllib.parse.quote(symbol)
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded_sym}{suffix}?interval=1d&range=1d"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get("chart", {}).get("result"):
                    meta = data["chart"]["result"][0]["meta"]
                    fallback_price = float(meta.get("regularMarketPrice", 0.0))
                    if fallback_price and fallback_price > 0:
                        return fallback_price, pe
        except Exception:
            continue
            
    return price, pe

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
                fetched_nav = get_nav(nav_df, sym)
                live_price = float(fetched_nav) if fetched_nav is not None else 0.0
                
                # Fallback: If it's a Mutual Fund (e.g., an ETF like GOLDBEES), 
                # but get_nav couldn't find it in AMFI, try fetching live market price.
                if live_price == 0.0:
                    fetched_price, _ = get_stock_info(sym)
                    if fetched_price is not None and fetched_price > 0:
                        live_price = fetched_price
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
    db_sector_allocations = db.fetch_allocations()
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
            
            # Filter sector targets for this specific portfolio
            port_sector_allocations = {
                a["Sector"]: a["Allocation"]
                for a in db_sector_allocations
                if a.get("Portfolio") == port_name and a.get("Sector") and (a.get("Allocation") or 0) > 0
            }
            
            # Get mapping of symbol to sector
            symbol_to_sector = {s.get("Symbol"): s.get("Sector") for s in db_stocks if s.get("Symbol")}
            
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
            if df.empty or (df["Qty"].sum() == 0 and len(port_stock_allocations) == 0):
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
            
            # Pie Chart: Stock vs Allocation (Invested Amount) & Projected Target Allocation
            pie_col1, pie_col2 = st.columns(2)
            
            with pie_col1:
                if not df.empty and df["Invested Amount"].sum() > 0:
                    st.subheader("Current Asset Allocation")
                    
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
                        height=400,
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#E2E8F0")
                    )
                    
                    st.plotly_chart(fig_pie, use_container_width=True)
            
            with pie_col2:
                # Calculate Projected Allocation %
                proj_data = []
                for sym, alloc in port_stock_allocations.items():
                    sector = symbol_to_sector.get(sym)
                    sec_alloc = port_sector_allocations.get(sector, 0)
                    proj_pct = (alloc * sec_alloc) / 100
                    if proj_pct > 0:
                        name = next((s.get("Name") for s in db_stocks if s.get("Symbol") == sym), sym)
                        proj_data.append({"Name": name, "Projected %": proj_pct})
                
                if proj_data:
                    st.subheader("Projected Target Allocation")
                    proj_df = pd.DataFrame(proj_data)
                    fig_proj = go.Figure(go.Pie(
                        labels=proj_df["Name"].tolist(),
                        values=proj_df["Projected %"].tolist(),
                        marker_colors=px.colors.qualitative.Pastel,
                        textinfo="label+percent",
                        textposition="inside",
                    ))
                    fig_proj.update_layout(
                        showlegend=False,
                        margin=dict(t=20, b=20, l=0, r=0),
                        height=400,
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#E2E8F0")
                    )
                    st.plotly_chart(fig_proj, use_container_width=True)
                    
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

                    event_hist = st.dataframe(
                        display_hist,
                        use_container_width=True,
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        key=f"history_grid_{port_name}",
                        column_config={
                            "BuyDate": "Buy Date",
                            "Qty": st.column_config.NumberColumn("Quantity", format="%.4f"),
                            "BuyAvg": st.column_config.NumberColumn("Buy Price", format="₹ %.2f"),
                            "SellDate": "Sell Date",
                            "SellAvg": st.column_config.NumberColumn("Sell Price", format="₹ %.2f"),
                        }
                    )
                    
                    selected_hist_rows = event_hist.selection.rows
                    if selected_hist_rows:
                        selected_hist_index = selected_hist_rows[0]
                        tx_id = hist_df.iloc[selected_hist_index].get("id")
                        sell_date = hist_df.iloc[selected_hist_index].get("SellDate")
                        
                        if tx_id:
                            st.write("") # Spacing
                            action_col1, action_col2 = st.columns([1, 2])
                            
                            with action_col1:
                                if pd.isna(sell_date) or sell_date is None:
                                    action_label = "🗑️ Delete Buy Transaction"
                                    help_text = "This will permanently delete this buy transaction."
                                else:
                                    action_label = "🗑️ Delete Sell Details"
                                    help_text = "This will remove the sell date and sell rate, converting it back to an open buy transaction."
                                    
                                if st.button(action_label, type="primary", help=help_text, key=f"del_tx_{tx_id}_{port_name}"):
                                    with st.spinner("Processing..."):
                                        if pd.isna(sell_date) or sell_date is None:
                                            success = db.delete_transaction(tx_id)
                                        else:
                                            success = db.revert_sell_transaction(tx_id)
                                            
                                        if success:
                                            st.success("Transaction updated successfully!")
                                            get_portfolio_display_data.clear()
                                            st.rerun()

                            with action_col2:
                                with st.expander("✏️ Edit Transaction"):
                                    row_data = hist_df.iloc[selected_hist_index]
                                    with st.form(key=f"edit_form_{tx_id}_{port_name}"):
                                        try:
                                            b_date_val = pd.to_datetime(row_data.get("BuyDate")).date()
                                        except:
                                            b_date_val = None
                                            
                                        s_date_val = None
                                        if not pd.isna(row_data.get("SellDate")) and row_data.get("SellDate") is not None:
                                            try:
                                                s_date_val = pd.to_datetime(row_data.get("SellDate")).date()
                                            except:
                                                s_date_val = None

                                        new_qty = st.number_input("Quantity", value=float(row_data.get("Qty", 0.0)), format="%.4f")
                                        new_buy_avg = st.number_input("Buy Price", value=float(row_data.get("BuyAvg", 0.0)), format="%.2f")
                                        new_buy_date = st.date_input("Buy Date", value=b_date_val)
                                        
                                        has_sell = not pd.isna(row_data.get("SellAvg")) and row_data.get("SellAvg") is not None
                                        
                                        new_sell_avg = None
                                        new_sell_date = None
                                        
                                        if has_sell:
                                            new_sell_avg = st.number_input("Sell Price", value=float(row_data.get("SellAvg", 0.0)), format="%.2f")
                                            new_sell_date = st.date_input("Sell Date", value=s_date_val)
                                            
                                        submit_edit = st.form_submit_button("Update Transaction")
                                        if submit_edit:
                                            sell_d_str = new_sell_date.strftime("%Y-%m-%d") if new_sell_date else None
                                            buy_d_str = new_buy_date.strftime("%Y-%m-%d") if new_buy_date else None
                                            
                                            success = db.update_transaction(
                                                tx_id=tx_id, 
                                                qty=new_qty, 
                                                buy_avg=new_buy_avg, 
                                                buy_date=buy_d_str, 
                                                sell_date=sell_d_str, 
                                                sell_avg=new_sell_avg
                                            )
                                            if success:
                                                st.success("Transaction updated successfully!")
                                                get_portfolio_display_data.clear()
                                                st.rerun()
