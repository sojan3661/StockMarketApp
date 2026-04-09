import requests
import json

def get_yf_price(symbol):
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}.NS?interval=1d&range=1d"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data.get("chart", {}).get("result"):
            meta = data["chart"]["result"][0]["meta"]
            return meta.get("regularMarketPrice")
    except Exception as e:
        print(e)
    return None

print(f"Price for TCS: {get_yf_price('TCS')}")
print(f"Price for RELIANCE: {get_yf_price('RELIANCE')}")
