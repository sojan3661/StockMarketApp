import streamlit as st
import requests
import os

class SupabaseClient:
    """A minimal REST client for interacting with Supabase."""
    
    def __init__(self, url=None, key=None):
        # 1. Try provided arguments first
        # 2. Try Streamlit secrets second
        # 3. Try Environment variables third
        # 4. Fallback to hardcoded defaults
        
        self.url = url or self._get_secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL") or ""
        self.key = key or self._get_secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY") or ""
        
        # Hardcode your credentials here if not using secrets/env vars
        if not self.url:
            self.url = "https://huduumqtkewniumvspwd.supabase.co" # <-- Paste your Betarise Supabase URL here
        if not self.key:
            self.key = "sb_publishable_RB8_B_OfCsS5xRCuqybtqw_SsPo-aX4" # <-- Paste your Betarise Anon Key here

    def _get_secret(self, key_name):
        try:
            return st.secrets[key_name]
        except (KeyError, FileNotFoundError):
            return None

    def _get_headers(self):
        """Builds the headers required for Supabase REST API."""
        if not self.url or not self.key:
            return None
            
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    def is_configured(self):
        """Checks if the client has the basic credentials needed to run."""
        return bool(self.url and self.key)

    # ==========================================
    # SECTORS API METHODS
    # ==========================================

    def fetch_sectors(self):
        """Fetches all sectors from the Sectors table."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/Sectors?select=*"
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching sectors: {e}")
            return []

    def add_sector(self, sector_name):
        """Adds a new sector to the Sectors table using the 'Sector' column."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/Sectors"
        data = {"Sector": sector_name}
        
        try:
            response = requests.post(endpoint, headers=headers, json=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: Row-Level Security (RLS) policy in Supabase blocked this insert. Please enable insert access for 'anon' users in your Supabase 'Sectors' table settings.")
            else:
                st.error(f"Error adding sector: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error adding sector: {e}")
            return False

    def delete_sector(self, sector_name):
        """Deletes a sector from the Sectors table by matching the 'Sector' column."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/Sectors?Sector=eq.{sector_name}"
        
        try:
            response = requests.delete(endpoint, headers=headers)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: Row-Level Security (RLS) policy in Supabase blocked this deletion. Please enable delete access for 'anon' users in your Supabase 'Sectors' table settings.")
            else:
                st.error(f"Error deleting sector: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error deleting sector: {e}")
            return False

    # ==========================================
    # SECTOR ALLOCATION API METHODS
    # ==========================================

    def fetch_allocations(self, portfolio=None):
        """Fetches all sector allocations from the SectorAllocation table."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/SectorAllocation?select=*"
        if portfolio:
            endpoint += f"&Portfolio=eq.{portfolio}"
            
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching allocations: {e}")
            return []

    def upsert_allocations(self, allocations_list, portfolio):
        """
        Since the SectorAllocation table doesn't have a unique constraint on Sector,
        a standard Upsert/Merge will fail or duplicate. 
        We resolve this by first deleting existing allocations for the passed sectors and portfolio, 
        then inserting the new ones.
        """
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/SectorAllocation"
        
        try:
            # 1. Delete existing records for these sectors inside this portfolio
            for alloc in allocations_list:
                sector_name = alloc.get("Sector", "")
                if sector_name:
                    # properly URL encode sector name for safely building the delete URL
                    from urllib.parse import quote
                    safe_sector = quote(str(sector_name).strip(), safe="")
                    safe_port = quote(str(portfolio).strip(), safe="")
                    
                    delete_url = f"{endpoint}?Sector=eq.{safe_sector}&Portfolio=eq.{safe_port}"
                    requests.delete(delete_url, headers=headers)
                    
            # 2. Insert the fresh records (excluding any Id column since those will be newly generated)
            clean_payload = []
            for alloc in allocations_list:
                clean_payload.append({
                    "Sector": alloc["Sector"],
                    "Allocation": alloc["Allocation"],
                    "Portfolio": portfolio
                })
                
            response = requests.post(endpoint, headers=headers, json=clean_payload)
            response.raise_for_status()
            
            return True
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: Row-Level Security (RLS) policy in Supabase blocked this action.")
            else:
                st.error(f"Error saving allocations: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error saving allocations: {e}")
            return False


    # ==========================================
    # STOCK MANAGEMENT API METHODS
    # ==========================================

    def fetch_stocks(self):
        """Fetches all stocks from the StockManagement table."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/StockManagement?select=*"
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching stocks: {e}")
            return []

    def add_stock(self, symbol, name, is_equity, sector, is_listed=True, market_cap="NA"):
        """Adds a new stock to the StockManagement table."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/StockManagement"
        data = {
            "Symbol": symbol,
            "Name": name,
            "Equity": is_equity,
            "Sector": sector,
            "Listed": is_listed,
            "MarketCap": market_cap
        }
        
        try:
            response = requests.post(endpoint, headers=headers, json=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: Row-Level Security (RLS) policy in Supabase blocked this insert. Please enable insert access for 'anon' users in your Supabase 'StockManagement' table settings.")
            else:
                st.error(f"Error adding stock: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error adding stock: {e}")
            return False

    def delete_stock(self, symbol):
        """Deletes a stock from the StockManagement table."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/StockManagement?Symbol=eq.{symbol}"
        
        try:
            response = requests.delete(endpoint, headers=headers)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: Row-Level Security (RLS) policy in Supabase blocked this deletion.")
            else:
                st.error(f"Error deleting stock: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error deleting stock: {e}")
            return False

    def update_stock(self, symbol, name, is_equity, sector, is_listed, market_cap):
        """Updates an existing stock record in StockManagement by Symbol (primary key)."""
        headers = self._get_headers()
        if not headers:
            return False

        from urllib.parse import quote
        safe_sym = quote(str(symbol).strip(), safe="")
        endpoint = f"{self.url}/rest/v1/StockManagement?Symbol=eq.{safe_sym}"

        data = {
            "Name": name,
            "Equity": is_equity,
            "Sector": sector,
            "Listed": is_listed,
            "MarketCap": market_cap
        }

        try:
            response = requests.patch(endpoint, headers=headers, json=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: RLS policy blocked this update on StockManagement.")
            else:
                st.error(f"Error updating stock: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error updating stock: {e}")
            return False


    # ==========================================
    # ASSET ALLOCATION API METHODS
    # ==========================================

    def fetch_stock_allocations(self, portfolio=None):
        """Fetches all stock allocations from the StockAllocation table."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/StockAllocation?select=*"
        if portfolio:
            endpoint += f"&Portfolio=eq.{portfolio}"
            
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching stock allocations: {e}")
            return []

    def upsert_stock_allocations(self, updates_list, portfolio):
        """
        Updates the 'Allocation' column for multiple rows in the StockAllocation table.
        Replaces the old row-by-row patch logic for StockManagement.
        """
        from urllib.parse import quote

        headers = self._get_headers()

        if not headers:
            return False

        endpoint = f"{self.url}/rest/v1/StockAllocation"

        try:
            # 1. Delete existing records for these symbols inside this portfolio
            for item in updates_list:
                sym = item.get("Symbol")
                if sym:
                    safe_sym = quote(str(sym).strip(), safe="")
                    safe_port = quote(str(portfolio).strip(), safe="")
                    delete_url = f"{endpoint}?Symbol=eq.{safe_sym}&Portfolio=eq.{safe_port}"
                    requests.delete(delete_url, headers=headers)

            # 2. Insert new records
            clean_payload = []
            for item in updates_list:
                sym = item.get("Symbol")
                alloc = item.get("Allocation")
                if sym:
                    clean_payload.append({
                        "Symbol": sym,
                        "Allocation": float(alloc),
                        "Portfolio": portfolio
                    })
                    
            if clean_payload:
                response = requests.post(endpoint, headers=headers, json=clean_payload)
                response.raise_for_status()

            return True

        except requests.exceptions.HTTPError as e:
            # Need to carefully handle the response referencing due to multiple requests above
            st.error("Error: Action was blocked by RLS or encountered an HTTP issue saving to StockAllocation.")
            return False
        except Exception as e:
            st.error(f"Error saving stock allocations: {e}")
            return False


    # ==========================================
    # TRANSACTIONS API METHODS
    # ==========================================

    def fetch_open_transactions(self, portfolio=None):
        """Fetches all open transactions where SellAvg is null."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/Transactions?SellAvg=is.null&select=*"
        if portfolio:
            from urllib.parse import quote
            safe_port = quote(str(portfolio).strip(), safe="")
            endpoint += f"&Portfolio=eq.{safe_port}"
            
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching open transactions: {e}")
            return []

    def fetch_transactions_by_symbol(self, symbol, portfolio=None):
        """Fetches all transactions for a specific symbol."""
        headers = self._get_headers()
        if not headers:
            return []
            
        from urllib.parse import quote
        safe_sym = quote(str(symbol).strip(), safe="")
        endpoint = f"{self.url}/rest/v1/Transactions?Symbol=eq.{safe_sym}"
        
        if portfolio:
            safe_port = quote(str(portfolio).strip(), safe="")
            endpoint += f"&Portfolio=eq.{safe_port}"
            
        endpoint += "&order=BuyDate.asc"
        
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching transactions for {symbol}: {e}")
            return []

    def add_buy_transaction(self, symbol, quantity, price, date, portfolio=None):
        """Adds a new recorded BUY transaction to the Transactions table."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/Transactions"
        data = {
            "Symbol": symbol,
            "Qty": quantity,
            "BuyAvg": price,
            "BuyDate": date
            # SellDate and SellAvg remain null automatically
        }
        
        if portfolio:
            data["Portfolio"] = portfolio
        
        try:
            response = requests.post(endpoint, headers=headers, json=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: RLS policy blocked this insert in Transactions.")
            else:
                st.error(f"Error adding buy transaction: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error adding buy transaction: {e}")
            return False

    def process_sell_transaction(self, symbol, sell_qty, sell_avg, sell_date, portfolio=None):
        """Processes a SELL by matching it against open BUYS (FIFO)."""
        headers = self._get_headers()
        if not headers:
            return False
            
        from urllib.parse import quote
        safe_sym = quote(str(symbol).strip(), safe="")
        
        # 1. Fetch all OPEN transactions for this symbol (SellDate is null) ordered by BuyDate (FIFO)
        endpoint = f"{self.url}/rest/v1/Transactions?Symbol=eq.{safe_sym}&SellDate=is.null"
        
        if portfolio:
            safe_port = quote(str(portfolio).strip(), safe="")
            endpoint += f"&Portfolio=eq.{safe_port}"
            
        endpoint += "&order=BuyDate.asc"
        
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            open_rows = response.json()
            
            if not open_rows:
                st.error(f"No open Buy transactions found for {symbol} to sell against!")
                return False
                
            remaining_to_sell = float(sell_qty)
            
            # Count total available to prevent partial failure midway if insufficient qty
            total_available = sum([float(r.get("Qty", 0)) for r in open_rows])
            if remaining_to_sell > total_available:
                st.error(f"Insufficient open quantity to sell. You are trying to sell {remaining_to_sell}, but only have {total_available} open.")
                return False
                
            base_endpoint = f"{self.url}/rest/v1/Transactions"
            
            # 2. Iterate and apply FIFO
            for row in open_rows:
                if remaining_to_sell <= 0:
                    break
                    
                row_id = row.get("id")
                row_qty = float(row.get("Qty", 0))
                
                if remaining_to_sell >= row_qty:
                    # Fully consume this row
                    patch_url = f"{base_endpoint}?id=eq.{row_id}"
                    patch_data = {
                        "SellDate": sell_date,
                        "SellAvg": sell_avg
                    }
                    p_res = requests.patch(patch_url, headers=headers, json=patch_data)
                    p_res.raise_for_status()
                    
                    remaining_to_sell -= row_qty
                else:
                    # Partial consume (Split the row)
                    
                    # A. Patch the original row to reduce its Qty (keeping it OPEN)
                    new_open_qty = row_qty - remaining_to_sell
                    patch_url = f"{base_endpoint}?id=eq.{row_id}"
                    p_res = requests.patch(patch_url, headers=headers, json={"Qty": new_open_qty})
                    p_res.raise_for_status()
                    
                    # B. Post a new row for the SOLD portion
                    post_data = {
                        "Symbol": symbol,
                        "BuyDate": row.get("BuyDate"),
                        "BuyAvg": row.get("BuyAvg"),
                        "SellDate": sell_date,
                        "SellAvg": sell_avg,
                        "Qty": remaining_to_sell
                    }
                    if portfolio:
                        post_data["Portfolio"] = portfolio
                        
                    n_res = requests.post(base_endpoint, headers=headers, json=post_data)
                    n_res.raise_for_status()
                    
                    remaining_to_sell = 0
                    
            return True
            
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None:
                st.error(f"Error processing sell transaction: HTTP {e.response.status_code} - {e.response.text}")
            else:
                 st.error(f"Error processing sell transaction: {e}")
            return False
        except Exception as e:
            st.error(f"Error processing sell transaction: {e}")
            return False


    # ==========================================
    # INVESTMENT PLAN API METHODS
    # ==========================================

    def fetch_investment_plan(self):
        """Fetches all investment plans. Returns a list of records."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/Investment%20Plan?select=*"
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching investment plans: {e}")
            return []

    def upsert_investment_plan(self, portfolio, current_invested, monthly_sip, num_months, description):
        """Upserts an investment plan. Primary key is Portfolio."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/Investment%20Plan"
        headers["Prefer"] = "return=minimal, resolution=merge-duplicates"
        
        data = {
            "Portfolio": portfolio,
            "Current Invested Amount": current_invested,
            "Monthly SIP": monthly_sip,
            "Number of Months": num_months,
            "Description": description
        }
        
        try:
            response = requests.post(endpoint, headers=headers, json=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: RLS policy blocked this action on Investment Plan.")
            else:
                st.error(f"Error saving investment plan: HTTP {response.status_code} - {response.text}")
            return False
        except Exception as e:
            st.error(f"Error saving investment plan: {e}")
            return False

    def delete_investment_plan(self, portfolio):
        """Deletes an investment plan."""
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/Investment%20Plan?Portfolio=eq.{portfolio}"
        
        try:
            response = requests.delete(endpoint, headers=headers)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401 or response.status_code == 403:
                st.error("Error: RLS policy blocked this deletion.")
            else:
                st.error(f"Error deleting investment plan: HTTP {response.status_code} - {e.response.text}")
            return False
        except Exception as e:
            st.error(f"Error deleting investment plan: {e}")
            return False


# Create a singleton instance that can be imported across the app
db = SupabaseClient()
