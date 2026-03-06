import streamlit as st
import sys
import os

# Add the app root directory to Python path to allow imports from Config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Config.supabase_client import db

st.title("Build Portfolio")
st.write("Set your investment plan details.")

# Verification check for credentials
if not db.is_configured():
    st.warning("⚠️ Supabase credentials not found!")
    st.info("Please set your credentials directly inside the init method of `Config/supabase_client.py`.")
    st.stop()

# Fetch existing investment plan
with st.spinner("Loading investment plan..."):
    plan = db.fetch_investment_plan()

# Form
with st.form("investment_plan_form"):
    current_invested = st.number_input(
        "Current Invested Amount",
        value=float(plan.get("Current Invested Amount", 0.0) if plan else 0.0),
        min_value=0.0,
        step=0.01
    )
    monthly_sip = st.number_input(
        "Monthly SIP",
        value=float(plan.get("Monthly SIP", 0.0) if plan and plan.get("Monthly SIP") else 0.0),
        min_value=0.0,
        step=0.01
    )
    num_months = st.number_input(
        "Number of Months",
        value=int(plan.get("Number of Months", 0) if plan and plan.get("Number of Months") else 0),
        min_value=0,
        step=1
    )
    
    submitted = st.form_submit_button("Save Investment Plan")
    
    if submitted:
        if db.upsert_investment_plan(current_invested, monthly_sip if monthly_sip > 0 else None, num_months if num_months > 0 else None):
            st.success("Investment plan saved successfully!")
            st.rerun()
        else:
            st.error("Failed to save investment plan.")