import streamlit as st
import pandas as pd
import sys
import os
import math
from nsepython import nse_eq

# Add root path for Config import
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from Config.supabase_client import db


st.title("Portfolio Rebalancing")

# -----------------------------
# Supabase Check
# -----------------------------
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.stop()


# -----------------------------
# Load Mutual Fund NAV (cached)
# -----------------------------
@st.cache_data(ttl=3600)
def load_nav_data():
    nav_df = pd.read_csv(
        "https://www.amfiindia.com/spages/NAVAll.txt",
        sep=";",
        header=None,
        names=[
            "scheme_code",
            "isin1",
            "isin2",
            "scheme_name",
            "nav",
            "date"
        ],
        on_bad_lines="skip"
    )
    return nav_df


# -----------------------------
# Cache NSE Price
# -----------------------------
@st.cache_data(ttl=600)
def get_stock_price(symbol):
    try:
        data = nse_eq(symbol)
        return data["priceInfo"]["lastPrice"]
    except:
        return 0


# -----------------------------
# Get MF NAV
# -----------------------------
def get_nav(nav_df, fund_name):
    res = nav_df.loc[nav_df["scheme_name"].eq(fund_name), ["nav"]]
    if not res.empty:
        return float(res.iloc[0]["nav"])
    return 0


# -----------------------------
# Load Database Data
# -----------------------------
with st.spinner("Loading data..."):
    db_sectors = db.fetch_sectors()
    db_allocations = db.fetch_allocations()
    db_stocks = db.fetch_stocks()
    db_stock_allocations = db.fetch_stock_allocations()
    open_transactions = db.fetch_open_transactions()
    db_investment_plan = db.fetch_investment_plan()

nav_df = load_nav_data()

# -----------------------------
# Aggregate Transactions
# -----------------------------
tx_df = pd.DataFrame(open_transactions)

if not tx_df.empty:
    tx_df["InvestedTotal"] = tx_df["Qty"] * tx_df["BuyAvg"]

    tx_agg = (
        tx_df.groupby("Symbol")
        .agg({"Qty": "sum", "InvestedTotal": "sum"})
        .to_dict("index")
    )
else:
    tx_agg = {}


# -----------------------------
# Sector Allocation Dict
# -----------------------------
sector_alloc_dict = {
    alloc["Sector"]: alloc["Allocation"]
    for alloc in db_allocations
    if alloc.get("Sector")
}


# -----------------------------
# Expected Investment
# -----------------------------
total_expected = 0

if not db_investment_plan:
    st.info("No investment plans found!")
    st.stop()

# Support list of plans vs single plan
plans_list = db_investment_plan if isinstance(db_investment_plan, list) else [db_investment_plan]
portfolio_names = [p["Portfolio"] for p in plans_list if "Portfolio" in p]

if not portfolio_names:
    st.info("No valid portfolios found.")
    st.stop()

# -----------------------------
# Tabs Generation 
# -----------------------------
tabs = st.tabs(portfolio_names)

for i, port_name in enumerate(portfolio_names):
    
    with tabs[i]:
        
        # 1. Filter allocations for this specific portfolio
        port_allocations = [a for a in db_allocations if a.get("Portfolio") == port_name]
        sector_alloc_dict = {
            alloc["Sector"]: alloc["Allocation"]
            for alloc in port_allocations
            if alloc.get("Sector")
        }
        
        # 1b. Filter stock targets for this specific portfolio
        port_stock_allocations = {
            a["Symbol"]: a["Allocation"]
            for a in db_stock_allocations
            if a.get("Portfolio") == port_name and a.get("Symbol")
        }
        
        # 2. Calculate Expected Investment for this specific portfolio
        plan_details = next((p for p in plans_list if p.get("Portfolio") == port_name), {})
        monthly_sip = plan_details.get("Monthly SIP") or 0
        months = plan_details.get("Number of Months") or 0
        
        # Current invested = sum of Qty * BuyAvg for open transactions in this portfolio
        port_open_tx = [tx for tx in open_transactions if tx.get("Portfolio") == port_name]
        current_invested = sum(
            float(tx.get("Qty", 0)) * float(tx.get("BuyAvg", 0))
            for tx in port_open_tx
        )
        
        total_expected = plan_details.get("Current Invested Amount", 0) + (monthly_sip * months)
        
        # 3. Header
        st.subheader(f"Asset Allocation for {port_name}")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("💰 Current Invested", f"₹{current_invested:,.2f}")
            
        expected_metric_placeholder = col2.empty()
        inflow_metric_placeholder = col3.empty()
            
        show_only_inflow = st.checkbox("Show only Inflow Instruments", key=f"show_inflow_{port_name}")
        
        total_inflow = 0
            
        if not db_sectors:
            st.info("No sectors found!")
            continue

        with st.form(f"alloc_form_{port_name}"):
            
            master_updates = []
            
            for sector_row in db_sectors:
                
                sector_name = sector_row.get("Sector")
                target_alloc = sector_alloc_dict.get(sector_name, 0)
                
                # Skip sectors with no allocation assigned for this portfolio
                if not target_alloc or target_alloc <= 0:
                    continue
                sector_expected = total_expected * (target_alloc / 100)
                
                with st.expander(
                    f"📁 {sector_name} (Target Sector Allocation: {target_alloc}%) - Expected ₹{sector_expected:,.2f}",
                    expanded=True
                ):
                    
                    sector_stocks = [s for s in db_stocks if s.get("Sector") == sector_name]
                    
                    if not sector_stocks:
                        st.info("No assets in this sector")
                        continue
                        
                    rows = []
                    
                    for p in sector_stocks:
                        sym = p.get("Symbol")
                        name = p.get("Name")
                        
                        alloc = float(port_stock_allocations.get(sym, 0.0))
                        
                        agg = tx_agg.get(sym, {"Qty":0,"InvestedTotal":0})
                        qty = agg["Qty"]
                        invested = agg["InvestedTotal"]
                        
                        # Price
                        if p.get("Equity", True):
                            if p.get("Listed", True):
                                price = get_stock_price(sym)
                            else:
                                price = float(p.get("LTP") or 0.0)
                        else:
                            price = get_nav(nav_df, name)
                            
                        # Asset target expected = Total * Sector % * Asset %
                        expected = total_expected * (target_alloc/100) * (alloc/100)
                        inflow = max(0, expected - invested)
                        
                        total_inflow += inflow
                        
                        buy = math.ceil(inflow/price) if price > 0 else 0
                        
                        rows.append({
                            "Symbol": sym,
                            "Name": name,
                            "LTP": price,
                            "Qty": qty,
                            "Invested": invested,
                            "Allocation %": alloc,
                            "Expected": expected,
                            "Inflow": inflow,
                            "Buy": buy
                        })
                        
                    df = pd.DataFrame(rows)
                    
                    display_df = df[df["Inflow"] > 0] if show_only_inflow else df
                    
                    if display_df.empty:
                        # Validate the full sector and retain current allocations even if not displayed
                        sector_sum = df["Allocation %"].sum()
                        if sector_sum > 100:
                            st.warning(f"⚠ Allocation exceeds 100% ({sector_sum:.2f}%)")
                        else:
                            st.caption(f"Sector total: {sector_sum:.2f}% / 100%")
                            
                        updates = df[["Symbol", "Name", "Allocation %"]].rename(
                            columns={"Allocation %": "Allocation"}
                        )
                        master_updates.extend(updates.to_dict("records"))
                        continue
                    
                    edited_df = st.data_editor(
                        display_df,
                        key=f"editor_{port_name}_{sector_name}", # Need composite key to avoid conflicts across tabs
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Name": None,   # Hidden — used internally for MF save fallback
                            "Allocation %": st.column_config.NumberColumn(
                                "Allocation %",
                                min_value=0.0,
                                max_value=100.0,
                                step=0.5
                            ),
                            "LTP": st.column_config.NumberColumn(format="₹%.2f"),
                            "Invested": st.column_config.NumberColumn(format="₹%.2f"),
                            "Expected": st.column_config.NumberColumn(format="₹%.2f"),
                            "Inflow": st.column_config.NumberColumn(format="₹%.2f"),
                        },
                        disabled=[
                            "Symbol", "Name", "LTP", "Qty", "Invested", "Expected", "Inflow", "Buy"
                        ]
                    )
                    
                    df.update(edited_df)
                    
                    # Allocation validation
                    sector_sum = df["Allocation %"].sum()
                    if sector_sum > 100:
                        st.warning(f"⚠ Allocation exceeds 100% ({sector_sum:.2f}%)")
                    else:
                        st.caption(f"Sector total: {sector_sum:.2f}% / 100%")
                        
                    updates = df[["Symbol", "Name", "Allocation %"]].rename(
                        columns={"Allocation %": "Allocation"}
                    )
                    master_updates.extend(updates.to_dict("records"))
            
            displayed_expected_investment = current_invested + total_inflow
            expected_metric_placeholder.metric("🎯 Expected Investment", f"₹{displayed_expected_investment:,.2f}")
            inflow_metric_placeholder.metric("💵 Total Inflow", f"₹{total_inflow:,.2f}")
            
            st.divider()
            submitted = st.form_submit_button(
                f"💾 Save {port_name} Asset Allocations",
                type="primary",
                use_container_width=True
            )
            
            if submitted:
                with st.spinner("Saving allocations..."):
                    success = db.upsert_stock_allocations(master_updates, port_name)
                    
                if success:
                    st.success("🎉 Allocations saved successfully!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Error saving allocations")