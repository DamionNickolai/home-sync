import streamlit as st
from supabase import create_client, Client
import pandas as pd

# 🟢 DYNAMIC ENVIRONMENT ROUTING
env = st.secrets.get("app_config", {}).get("environment", "production")
TASK_TABLE = "household_tasks_dev" if env == "local" else "household_tasks"

@st.cache_resource
def init_connection() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# ==========================================
# 📋 TO-DO LIST FUNCTIONS
# ==========================================

def get_active_tasks():
    try:
        # Grab the ID from the active session
        house_id = st.session_state.get("household_id", "unassigned")
        
        # 🟢 NEW: Added the .eq("household_id", house_id) filter!
        response = supabase.table(TASK_TABLE).select("*").eq("is_completed", False).eq("household_id", house_id).execute()
        return response.data
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        return []

def get_completed_tasks():
    try:
        house_id = st.session_state.get("household_id", "unassigned")
        
        # 🟢 NEW: Added the .eq("household_id", house_id) filter!
        response = supabase.table(TASK_TABLE).select("*").eq("is_completed", True).eq("household_id", house_id).order("created_at", desc=True).limit(50).execute()
        return response.data
    except Exception as e:
        print(f"Error fetching completed tasks: {e}")
        return []

def add_new_task(task_name, category, priority, assigned_to, target_date):
    try:
        house_id = st.session_state.get("household_id", "unassigned")
        
        data = {
            "task_name": task_name,
            "category": category,
            "priority": priority,
            "assigned_to": assigned_to,
            "target_date": str(target_date) if target_date else None, 
            "is_completed": False,
            "household_id": house_id  # 🟢 NEW: Stamp the task with the family's ID!
        }
        supabase.table(TASK_TABLE).insert(data).execute()
        return True
    except Exception as e:
        print(f"Error inserting task: {e}")
        return False

def batch_update_tasks(task_ids, new_status):
    """Updates a list of task IDs to a specific status (True or False)."""
    try:
        # Loop through IDs and update each in the database
        for tid in task_ids:
            supabase.table(TASK_TABLE).update({"is_completed": new_status}).eq("id", tid).execute()
        return True
    except Exception as e:
        print(f"Error in batch update: {e}")
        return False

def update_task(task_id, task_name=None, category=None, priority=None, assigned_to=None, target_date=None):
    """Updates specific fields of a task."""
    try:
        update_data = {}
        if task_name is not None:
            update_data["task_name"] = task_name
        if category is not None:
            update_data["category"] = category
        if priority is not None:
            update_data["priority"] = priority
        if assigned_to is not None:
            update_data["assigned_to"] = assigned_to
        if target_date is not None:
            update_data["target_date"] = str(target_date) if target_date else None
        
        if update_data:
            supabase.table(TASK_TABLE).update(update_data).eq("id", task_id).execute()
            return True
        return False
    except Exception as e:
        print(f"Error updating task: {e}")
        return False
    
# ==========================================
# 📝 BACKLOG FUNCTIONS (Shared Table)
# ==========================================

def get_all_backlog_items():
    """Fetches active backlog items (Hides 'Done')."""
    try:
        # 🟢 Hides any ticket marked "Done"
        response = supabase.table("backlog").select("*").neq("status", "Done").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        print(f"Error fetching backlog: {e}")
        return []

def add_backlog_item(feature, notes, status="Backlog", app_name="home_sync", category="Core", priority="Medium"):
    """Adds a new backlog item using the correct database columns."""
    try:
        data = {
            "feature": feature,  
            "notes": notes,      
            "status": status,
            "app_name": app_name,
            "category": category,
            "priority": priority
        }
        supabase.table("backlog").insert(data).execute()
        return True
    except Exception as e:
        print(f"Error inserting backlog item: {e}")
        return False

def update_backlog_item(item_id, feature, notes, status, app_name, category, priority, public_message=""):
    """Updates an existing backlog ticket."""
    try:
        data = {
            "feature": feature,
            "notes": notes,
            "status": status,
            "app_name": app_name,
            "category": category,
            "priority": priority,
            "public_message": public_message  # 🟢 NEW: Added to the payload
        }
        supabase.table("backlog").update(data).eq("id", item_id).execute()
        return True
    except Exception as e:
        print(f"Error updating backlog item: {e}")
        return False
    
def delete_backlog_item(item_id):
    """Deletes a backlog ticket entirely."""
    try:
        supabase.table("backlog").delete().eq("id", item_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting backlog item: {e}")
        return False

def delete_task(task_id):
    """Deletes a to-do list task entirely."""
    try:
        supabase.table("tasks").delete().eq("id", task_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting task: {e}")
        return False