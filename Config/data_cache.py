import streamlit as st
import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

from Config.supabase_client import SupabaseClient

db = SupabaseClient()

@st.cache_data(ttl=3600)
def load_amfi_nav_data():
    """Fetch and cache AMFI NAV dataset for Indian mutual funds."""
    url = "https://www.amfiindia.com/spages/NAVAll.txt"
    try:
        df = pd.read_csv(
            url,
            sep=";",
            on_bad_lines="skip",
            dtype=str,
            header=None,
            engine="python"
        )
        if df.shape[1] >= 6:
            df = df.iloc[:, [0, 1, 2, 3, 4, 5]]
            df.columns = ["scheme_code", "isin1", "isin2", "scheme_name", "nav", "date"]
            df["scheme_code"] = df["scheme_code"].astype(str).str.strip()
            df["scheme_name"] = df["scheme_name"].astype(str).str.strip()
            df["isin1"] = df["isin1"].astype(str).str.strip()
            df["isin2"] = df["isin2"].astype(str).str.strip()
            df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
            df = df.dropna(subset=["nav"])
            return df
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=1800)
def get_stock_price(symbol, country="INDIA"):
    """Fetch live stock price using smart ticker targeting and yfinance."""
    if not yf or not symbol:
        return None
    s = str(symbol).strip()
    if not s or s.isdigit() or s.upper() in ("NA", "NONE", ""):
        return None

    country_upper = str(country).upper() if country else "INDIA"
    if s.endswith((".NS", ".BO")):
        targets = [s]
    elif s.startswith("0P"):
        targets = [s + ".BO", s]
    elif country_upper == "USA":
        targets = [s]
    else:
        targets = [s + ".NS", s + ".BO", s]

    for t_str in targets:
        try:
            ticker = yf.Ticker(t_str)
            p = getattr(ticker.fast_info, "last_price", None)
            if p and p > 0:
                return float(p)
            if t_str.startswith("0P"):
                hist = ticker.history(period="5d")
                if not hist.empty and "Close" in hist.columns:
                    series = hist["Close"].dropna()
                    if not series.empty:
                        val = float(series.iloc[-1])
                        if val > 0:
                            return val
        except Exception:
            continue
        if s.endswith((".NS", ".BO")):
            break
            
    return None


def get_stock_info(symbol, country="INDIA"):
    """Returns (price, pe_ratio) for a stock."""
    price = get_stock_price(symbol, country)
    return price or 0.0, None


@st.cache_data(ttl=1800)
def fetch_yf_mf_nav(symbol, country="INDIA"):
    return get_stock_price(symbol, country)


def batch_fetch_stock_prices(stocks_list):
    """Pre-fetch stock prices for multiple assets in parallel using ThreadPoolExecutor."""
    if not stocks_list or not yf:
        return
    items_to_fetch = [
        s for s in stocks_list
        if (isinstance(s, dict) and s.get("Symbol") and s.get("Listed", True) and not str(s.get("Symbol", "")).isdigit())
        or (isinstance(s, str) and s and not s.isdigit())
    ]
    if not items_to_fetch:
        return

    def _worker(item):
        if isinstance(item, dict):
            sym = item.get("Symbol")
            country = item.get("Country", "INDIA")
        else:
            sym = item
            country = "INDIA"
        get_stock_price(sym, country)

    with ThreadPoolExecutor(max_workers=min(25, len(items_to_fetch))) as executor:
        list(executor.map(_worker, items_to_fetch))


@st.cache_data(ttl=3600)
def fetch_all_app_data():
    """
    Central function to load and cache ALL application data.
    Cached for 1 hour or until st.cache_data.clear() is called.
    """
    if not db.is_configured():
        return {}

    db_sectors           = db.fetch_sectors()
    db_stocks            = db.fetch_stocks()
    db_allocations       = db.fetch_allocations()
    db_stock_allocations = db.fetch_stock_allocations()
    open_transactions    = db.fetch_open_transactions()
    all_transactions     = db.fetch_all_transactions()
    db_investment_plan   = db.fetch_investment_plan()
    db_currency_pairs    = db.fetch_currency_pairs()
    nav_df               = load_amfi_nav_data()

    # Pre-fetch live prices in parallel
    batch_fetch_stock_prices(db_stocks)

    return {
        "sectors": db_sectors,
        "stocks": db_stocks,
        "allocations": db_allocations,
        "stock_allocations": db_stock_allocations,
        "open_transactions": open_transactions,
        "all_transactions": all_transactions,
        "investment_plan": db_investment_plan,
        "currency_pairs": db_currency_pairs,
        "nav_df": nav_df
    }


def get_global_app_data():
    """Retrieve global cached app data dictionary."""
    return fetch_all_app_data()


def refresh_all_data():
    """Flushes cache and reloads all application data globally."""
    st.cache_data.clear()
    for k in list(st.session_state.keys()):
        if k.startswith("port_stock_allocations_") or k.startswith("editor_") or k.startswith("symbols_"):
            del st.session_state[k]
    st.rerun()


def _find_nav_in_df(nav_df, fund_name, fallback_name=None):
    if nav_df is None or nav_df.empty:
        return None
    search_terms = []
    if fund_name:
        search_terms.append(str(fund_name).strip())
    if fallback_name and fallback_name != fund_name:
        search_terms.append(str(fallback_name).strip())
    for term_str in search_terms:
        if not term_str or term_str.upper() in ("NA", "NONE", ""):
            continue
        term_lower = term_str.lower()
        term_upper = term_str.upper()
        if "scheme_code" in nav_df.columns:
            res = nav_df.loc[nav_df["scheme_code"].astype(str).str.strip() == term_str, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        isin_cols = [c for c in ["isin1", "isin2"] if c in nav_df.columns]
        if isin_cols:
            mask = pd.Series(False, index=nav_df.index)
            for col in isin_cols:
                mask = mask | (nav_df[col].astype(str).str.strip().str.upper() == term_upper)
            res = nav_df.loc[mask, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        if "scheme_name" in nav_df.columns:
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.strip().str.lower() == term_lower, ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
        if "scheme_name" in nav_df.columns and len(term_lower) >= 3:
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.lower().str.contains(term_lower, na=False, regex=False), ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
            short_term = term_lower[:15]
            res = nav_df.loc[nav_df["scheme_name"].astype(str).str.lower().str.contains(short_term, na=False, regex=False), ["nav", "date"]]
            if not res.empty:
                return res.iloc[0]
    return None


def get_nav(nav_df, fund_name, fallback_name=None, country="INDIA"):
    res = _find_nav_in_df(nav_df, fund_name, fallback_name)
    if res is not None:
        try:
            return float(res["nav"] if isinstance(res, (dict, pd.Series)) else res)
        except (ValueError, TypeError, KeyError):
            pass
    yf_nav = fetch_yf_mf_nav(fund_name, country) or (fetch_yf_mf_nav(fallback_name, country) if fallback_name else None)
    if yf_nav is not None:
        return float(yf_nav)
    return None


def resolve_asset_ltp(item, nav_df):
    """
    Returns the Last Traded Price (LTP) / NAV for an asset item.
    - Listed Equity: Live stock price via yfinance, fallback to DB LTP.
    - Mutual Fund: Live NAV via AMFI data, fallback to DB LTP.
    - Unlisted Stock / Other: DB LTP.
    """
    sym = item.get("Symbol", "")
    name = item.get("Name", "")
    is_eq = item.get("Equity", True)
    is_lst = item.get("Listed", True)
    country = item.get("Country", "INDIA")
    db_ltp = item.get("LTP")

    if is_lst:
        if is_eq:
            price = get_stock_price(sym, country)
            if price is not None:
                try:
                    return float(price)
                except (ValueError, TypeError):
                    pass
            if db_ltp is not None:
                try:
                    return float(db_ltp)
                except (ValueError, TypeError):
                    pass
        else:
            # Mutual Fund: Check AMFI in-memory dataset FIRST (< 1ms)
            nav_res = _find_nav_in_df(nav_df, sym, name)
            if nav_res is not None:
                try:
                    return float(nav_res["nav"])
                except (ValueError, TypeError, KeyError):
                    pass
            # Fallback to yfinance if non-numeric symbol
            if sym and not str(sym).isdigit():
                price = get_stock_price(sym, country)
                if price is not None:
                    return float(price)
            if db_ltp is not None:
                try:
                    return float(db_ltp)
                except (ValueError, TypeError):
                    pass
    else:
        # Unlisted stock
        if db_ltp is not None:
            try:
                return float(db_ltp)
            except (ValueError, TypeError):
                pass

    return None


def calculate_open_capital_gains(open_txs, live_price_map=None, currency_pairs_map=None, fx_rates=None, use_usd=False, symbol_filter=None):
    """
    Calculate Short-Term (<=365 days) and Long-Term (>365 days) unrealized capital gains for open positions.
    """
    stcg_invested, stcg_current = 0.0, 0.0
    ltcg_invested, ltcg_current = 0.0, 0.0
    now = pd.Timestamp.now()

    usd_inr = 84.0
    if use_usd and currency_pairs_map and fx_rates:
        cp = currency_pairs_map.get("USA", {})
        sym = cp.get("Symbol", "") if isinstance(cp, dict) else ""
        if sym and fx_rates.get(sym):
            usd_inr = float(fx_rates.get(sym) or 84.0)

    live_map = live_price_map or {}

    for tx in (open_txs or []):
        sym = tx.get("Symbol", "")
        if not sym:
            continue
        if symbol_filter and sym not in symbol_filter:
            continue

        sell_avg  = tx.get("SellAvg")
        sell_date = tx.get("SellDate")
        is_open   = (
            (sell_avg  is None or sell_avg  == "" or (isinstance(sell_avg,  float) and pd.isna(sell_avg))) and
            (sell_date is None or sell_date == "" or (isinstance(sell_date, float) and pd.isna(sell_date)))
        )
        if not is_open:
            continue

        qty = float(tx.get("Qty", 0.0) or 0.0)
        if qty <= 0:
            continue

        buy_avg     = float(tx.get("BuyAvg", 0.0) or 0.0)
        buy_val_inr = float(tx.get("BuyValue", 0) or (buy_avg * qty))
        buy_val_usd = float(tx.get("BuyValueUSD", 0) or 0)

        if use_usd:
            b_val = buy_val_usd if buy_val_usd > 0 else (buy_val_inr / usd_inr if usd_inr > 0 else buy_val_inr)
        else:
            b_val = buy_val_inr

        lp = float(live_map.get(sym, 0.0) or 0.0)
        if lp <= 0:
            lp = buy_avg if not use_usd else (buy_avg / usd_inr if usd_inr > 0 else buy_avg)
        c_val = qty * lp

        buy_date_str = tx.get("BuyDate")
        is_long = False
        if buy_date_str:
            try:
                buy_dt = pd.to_datetime(buy_date_str)
                if pd.notna(buy_dt) and (now - buy_dt).days > 365:
                    is_long = True
            except Exception:
                pass

        if is_long:
            ltcg_invested += b_val
            ltcg_current  += c_val
        else:
            stcg_invested += b_val
            stcg_current  += c_val

    stcg_gain = stcg_current - stcg_invested
    stcg_pct  = (stcg_gain / stcg_invested * 100) if stcg_invested > 0 else 0.0
    ltcg_gain = ltcg_current - ltcg_invested
    ltcg_pct  = (ltcg_gain / ltcg_invested * 100) if ltcg_invested > 0 else 0.0

    return {
        "stcg_invested": stcg_invested,
        "stcg_current": stcg_current,
        "stcg_gain": stcg_gain,
        "stcg_pct": stcg_pct,
        "ltcg_invested": ltcg_invested,
        "ltcg_current": ltcg_current,
        "ltcg_gain": ltcg_gain,
        "ltcg_pct": ltcg_pct,
    }

