import streamlit as st
from supabase import create_client
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from security import encrypt_data

supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])

def migrate_finance(table_name):
    print(f"\n🚀 Starting encryption migration for: {table_name}")
    response = supabase.table(table_name).select("*").execute()
    rows = response.data
    
    if not rows:
        print("No data found to migrate.")
        return

    success_count = 0
    for row in rows:
        update_payload = {}
        val = row.get("projects_funds")
        
        # Check if it has a value and isn't already encrypted
        if val is not None and str(val).strip() != "" and not str(val).startswith("gAAAA"):
            update_payload["projects_funds"] = encrypt_data(val)
                
        if update_payload:
            # We use household_id as the match here since there is no standard 'id' column
            supabase.table(table_name).update(update_payload).eq("household_id", row["household_id"]).execute()
            success_count += 1
            print(f"🔒 Encrypted settings for Household: {row['household_id']}")
            
    print(f"✅ Migration complete for {table_name}! {success_count} rows encrypted.")

if __name__ == "__main__":
    migrate_finance("household_finance_settings")