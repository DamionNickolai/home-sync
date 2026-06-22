import streamlit as st
from supabase import create_client
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from security import encrypt_data

supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])

def migrate_tasks(table_name):
    print(f"\n🚀 Starting encryption migration for: {table_name}")
    response = supabase.table(table_name).select("*").execute()
    rows = response.data
    
    if not rows:
        print("No data found to migrate.")
        return

    success_count = 0
    fields_to_encrypt = ["task_name", "description", "notes"]

    for row in rows:
        update_payload = {}
        for field in fields_to_encrypt:
            val = row.get(field)
            if val is not None and str(val).strip() != "" and not str(val).startswith("gAAAA"):
                update_payload[field] = encrypt_data(val)
                
        if update_payload:
            supabase.table(table_name).update(update_payload).eq("id", row["id"]).execute()
            success_count += 1
            print(f"🔒 Encrypted task ID: {row['id']}")
            
    print(f"✅ Migration complete for {table_name}! {success_count} rows encrypted.")

if __name__ == "__main__":
    migrate_tasks("household_tasks")