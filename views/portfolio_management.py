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
st.write("Assign target allocations (percentages) to the specific stocks within each sector.")

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

if db_investment_plan:

    current_invested = db_investment_plan.get("Current Invested Amount", 0)

    monthly_sip = db_investment_plan.get("Monthly SIP") or 0

    months = db_investment_plan.get("Number of Months") or 0

    total_expected = current_invested + (monthly_sip * months)


# -----------------------------
# Header
# -----------------------------
col1, col2 = st.columns([3,1])

with col1:
    st.subheader("Asset Allocation by Sector")

with col2:
    st.metric("Total Expected Investment", f"₹{total_expected:,.2f}")


if not db_sectors:
    st.info("No sectors found!")
    st.stop()


# -----------------------------
# Main Form
# -----------------------------
with st.form("portfolio_allocations_form"):

    master_updates = []

    for sector_row in db_sectors:

        sector_name = sector_row.get("Sector")

        target_alloc = sector_alloc_dict.get(sector_name, 0)

        sector_expected = total_expected * (target_alloc / 100)

        with st.expander(
            f"📁 {sector_name} "
            f"(Target Sector Allocation: {target_alloc}%) "
            f"- Expected ₹{sector_expected:,.2f}",
            expanded=True
        ):

            sector_stocks = [
                s for s in db_stocks
                if s.get("Sector") == sector_name
            ]

            if not sector_stocks:
                st.info("No assets in this sector")
                continue


            rows = []

            for p in sector_stocks:

                sym = p.get("Symbol")
                name = p.get("Name")

                alloc = float(p.get("Allocation") or 0)

                agg = tx_agg.get(sym, {"Qty":0,"InvestedTotal":0})

                qty = agg["Qty"]

                invested = agg["InvestedTotal"]


                # Price
                if p.get("Equity", True):

                    price = get_stock_price(sym)

                else:

                    price = get_nav(nav_df, name)


                expected = total_expected * (target_alloc/100) * (alloc/100)

                inflow = max(0, expected - invested)

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


            edited_df = st.data_editor(

                df,

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
                    "Symbol",
                    "Name",
                    "LTP",
                    "Qty",
                    "Invested",
                    "Expected",
                    "Inflow",
                    "Buy"
                    # "Allocation %" is editable — intentionally excluded from disabled list
                ]

            )


            # Allocation validation
            sector_sum = edited_df["Allocation %"].sum()

            if sector_sum > 100:
                st.warning(f"⚠ Allocation exceeds 100% ({sector_sum:.2f}%)")
            else:
                st.caption(f"Sector total: {sector_sum:.2f}% / 100%")


            updates = edited_df[["Symbol", "Name", "Allocation %"]].rename(

                columns={"Allocation %": "Allocation"}

            )

            master_updates.extend(updates.to_dict("records"))


    st.divider()

    submitted = st.form_submit_button(
        "💾 Save All Asset Allocations",
        type="primary",
        use_container_width=True
    )


# -----------------------------
# Save Updates
# -----------------------------
if submitted:

    with st.spinner("Saving allocations..."):

        success = db.upsert_stock_allocations(master_updates)

    if success:

        st.success("🎉 Allocations saved successfully!")
        st.cache_data.clear()   # Bust cached DB + NAV data
        st.rerun()              # Reload page with fresh data from DB

    else:

        st.error("Error saving allocations")