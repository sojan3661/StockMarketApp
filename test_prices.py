import sys
import os
sys.path.append(os.path.abspath('.'))
from Config.supabase_client import db
from views.portfolio import get_stock_info

db_stocks = db.fetch_stocks()
failed_stocks = []
for p in db_stocks:
    sym = p.get("Symbol")
    is_equity = p.get("Equity", False)
    is_listed = p.get("Listed", True)
    if is_equity and is_listed and sym:
        price, pe = get_stock_info(sym)
        if not price or price == 0:
            failed_stocks.append(sym)
            print(f"FAILED: {sym}")
        else:
            print(f"SUCCESS: {sym} -> {price}")
