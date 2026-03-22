import sys
import os
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from Config.supabase_client import db
try:
    from nsepython import nse_eq, index_pe_pb_div
except ImportError:
    pass

def get_stock_info(symbol):
    try:
        quote = nse_eq(symbol)
        price = float(quote["priceInfo"]["lastPrice"])
        pe = None
        if 'metadata' in quote:
            md = quote['metadata']
            pe = md.get('pdSymbolPe') or md.get('pdSectorPe') or md.get('pe')
            if pe is not None:
                pe = float(pe)
        return price, pe
    except Exception:
        pass
    return 0.0, None

db_stocks = db.fetch_stocks()
db_stock_allocs = db.fetch_stock_allocations()

for alloc in db_stock_allocs:
    sym = alloc.get("Symbol")
    if sym in ['RELIANCE', 'TCS', 'HDFCBANK']:
        print(f"Testing {sym}...")
        p, pe = get_stock_info(sym)
        print(f"  PE: {pe}")

