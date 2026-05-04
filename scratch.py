import sys
import os
import requests
sys.path.append(os.path.abspath('.'))
from Config.supabase_client import db

headers = db._get_headers()
# Try patching with SellAvgToLocal
res = requests.patch(f"{db.url}/rest/v1/Transactions?id=eq.831", headers=headers, json={"SellAvgToLocal": 100})
print("Patch SellAvgToLocal status:", res.status_code, res.text)
# Try patching with SellAvgLocal
res2 = requests.patch(f"{db.url}/rest/v1/Transactions?id=eq.831", headers=headers, json={"SellAvgLocal": 100})
print("Patch SellAvgLocal status:", res2.status_code, res2.text)
