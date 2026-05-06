def fetch_exchange_rate(pair_currency, date_str):
    # Fallback 1: Yahoo Finance query2 API
    try:
        import urllib.request
        import json
        import ssl
        import datetime
        
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        period1 = int(dt.timestamp())
        period2 = int((dt + datetime.timedelta(days=5)).timestamp())
        
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{pair_currency}=X?period1={period1}&period2={period2}&interval=1d"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            res = data.get("chart", {}).get("result")
            if res and res[0].get("indicators", {}).get("quote"):
                closes = res[0]["indicators"]["quote"][0].get("close")
                for c in closes:
                    if c is not None:
                        return float(c)
    except Exception as e:
        print(f"Error fetching {pair_currency} via Yahoo API fallback: {e}")

    # Fallback 2: Frankfurter API
    try:
        import urllib.request
        import json
        import ssl
        
        if len(pair_currency) == 6:
            base = pair_currency[:3]
            target = pair_currency[3:]
            url = f"https://api.frankfurter.app/{date_str}?from={base}&to={target}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                rates = data.get("rates", {})
                if target in rates:
                    return float(rates[target])
    except Exception as e:
        print(f"Error fetching {pair_currency} via Frankfurter API fallback: {e}")

    return None

print(fetch_exchange_rate("USDINR", "2023-01-05"))
