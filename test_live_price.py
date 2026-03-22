import sys
import os
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from Config.supabase_client import db

open_tx = db.fetch_open_transactions()
db_stocks = db.fetch_stocks()
stocks_map = {s["Symbol"]: s for s in db_stocks}

def live_price(stock_info):
    sym = stock_info.get("Symbol", "")
    is_eq = stock_info.get("Equity", True)
    is_lst = stock_info.get("Listed", True)
    
    # Check if bug in live_price causes single float
    if not is_lst:
        return float(stock_info.get("LTP") or 0.0), None
    if is_eq:
        try:
            from nsepython import nse_eq
            quote = nse_eq(sym)
            price = float(quote.get("priceInfo", {}).get("lastPrice", 0))
            pe = None
            if 'metadata' in quote:
                md = quote['metadata']
                pe = md.get('pdSymbolPe') or md.get('pdSectorPe') or md.get('pe')
                if pe is not None:
                    pe = float(pe)
            return price, pe
        except Exception as e:
            return 0.0, None
    return 0.0, None

# Test building a sample row
for tx in open_tx[:5]:
    sym = tx.get("Symbol")
    if sym in stocks_map:
        price, pe = live_price(stocks_map[sym])
        print(f"{sym}: Price={price}, PE={pe}")
