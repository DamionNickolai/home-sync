import streamlit as st
from supabase import create_client
import sys
import os

# Ensure the script can find your security.py file in the root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from security import encrypt_data

# Initialize Supabase
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])

def migrate_project_budgets(table_name):
    print(f"\n🚀 Starting encryption migration for: {table_name}")
    
    # Fetch all current rows
    response = supabase.table(table_name).select("*").execute()
    rows = response.data
    
    if not rows:
        print("No data found to migrate.")
        return

    success_count = 0
    fields_to_encrypt = ["item", "description", "est_low_cost", "est_high_cost", "actual_cost", "vendors", "notes"]

    for row in rows:
        update_payload = {}
        
        for field in fields_to_encrypt:
            val = row.get(field)
            # If there is a value, and it isn't already encrypted (Fernet tokens start with 'gAAAA')
            if val is not None and str(val).strip() != "" and not str(val).startswith("gAAAA"):
                update_payload[field] = encrypt_data(val)
                
        if update_payload:
            supabase.table(table_name).update(update_payload).eq("id", row["id"]).execute()
            success_count += 1
            print(f"🔒 Encrypted row ID: {row['id']}")
            
    print(f"✅ Migration complete for {table_name}! {success_count} rows encrypted.")

if __name__ == "__main__":
    # We are ONLY targeting the dev table right now!
    migrate_project_budgets("project_budgets_dev")