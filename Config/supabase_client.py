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

    def fetch_allocations(self):
        """Fetches all sector allocations from the SectorAllocation table."""
        headers = self._get_headers()
        if not headers:
            return []
            
        endpoint = f"{self.url}/rest/v1/SectorAllocation?select=*"
        try:
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching allocations: {e}")
            return []

    def upsert_allocations(self, allocations_list):
        """
        Since the SectorAllocation table doesn't have a unique constraint on Sector,
        a standard Upsert/Merge will fail or duplicate. 
        We resolve this by first deleting existing allocations for the passed sectors, 
        then inserting the new ones.
        """
        headers = self._get_headers()
        if not headers:
            return False
            
        endpoint = f"{self.url}/rest/v1/SectorAllocation"
        
        try:
            # 1. Delete existing records for these sectors
            for alloc in allocations_list:
                sector_name = alloc.get("Sector", "")
                if sector_name:
                    delete_url = f"{endpoint}?Sector=eq.{sector_name}"
                    requests.delete(delete_url, headers=headers)
                    
            # 2. Insert the fresh records (excluding any Id column since those will be newly generated)
            clean_payload = []
            for alloc in allocations_list:
                clean_payload.append({
                    "Sector": alloc["Sector"],
                    "Allocation": alloc["Allocation"]
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


# Create a singleton instance that can be imported across the app
db = SupabaseClient()
