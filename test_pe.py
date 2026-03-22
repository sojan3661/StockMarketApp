import sys
import os
import pandas as pd
from datetime import datetime, timedelta
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from Config.supabase_client import db

open_tx = db.fetch_open_transactions()
db_stocks = db.fetch_stocks()
stocks_map = {s["Symbol"]: s for s in db_stocks} 

# check fetching PE
try:
    from nsepython import nse_eq, index_pe_pb_div
    q = nse_eq("RELIANCE")
    md = q.get("metadata", {})
    pe = md.get('pdSymbolPe') or md.get('pdSectorPe') or md.get('pe')
    print("Reliance PE:", pe)
except Exception as e:
    print("Error:", e)

