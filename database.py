import streamlit as st
from supabase import create_client, Client
import pandas as pd
from security import encrypt_data, decrypt_text, decrypt_float
from datetime import datetime
from zoneinfo import ZoneInfo
from utils import calculate_next_version

# 🟢 DYNAMIC ENVIRONMENT ROUTING
env = st.secrets.get("app_config", {}).get("environment", "production")
TASK_TABLE = "household_tasks_dev" if env == "local" else "household_tasks"
PROJECT_BUDGETS_TABLE = "project_budgets_dev" if env == "local" else "project_budgets"
HOUSEHOLD_FINANCE_SETTINGS_TABLE = "household_finance_settings_dev" if env == "local" else "household_finance_settings"
WISH_LIST_TABLE = "wish_list_dev" if env == "local" else "wish_list"

@st.cache_resource
def init_connection() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

supabase = init_connection()


def get_current_household_id():
    house_id = st.session_state.get("household_id")
    if not house_id or house_id == "unassigned":
        raise ValueError("No household is associated with the current session.")
    return house_id


def require_privileged_user():
    if st.session_state.get("user_role") != "developer":
        raise PermissionError("This action requires developer access.")

# ==========================================
# 📋 TO-DO LIST FUNCTIONS
# ==========================================

def get_active_tasks():
    try:
        # Grab the ID from the active session
        house_id = get_current_household_id()
        
        # 🟢 NEW: Added the .eq("household_id", house_id) filter!
        response = supabase.table(TASK_TABLE).select("*").eq("is_completed", False).eq("household_id", house_id).execute()
        data = response.data
        
        # 🟢 DECRYPT DATA BEFORE SENDING TO UI
        if data:
            for row in data:
                row["task_name"] = decrypt_text(row.get("task_name"))
                row["notes"] = decrypt_text(row.get("notes"))
                row["description"] = decrypt_text(row.get("description"))
                
        return data
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        return []

def get_completed_tasks():
    try:
        house_id = get_current_household_id()
        
        # 🟢 NEW: Added the .eq("household_id", house_id) filter!
        response = supabase.table(TASK_TABLE).select("*").eq("is_completed", True).eq("household_id", house_id).order("created_at", desc=True).limit(50).execute()
        data = response.data
        
        # 🟢 DECRYPT DATA BEFORE SENDING TO UI
        if data:
            for row in data:
                row["task_name"] = decrypt_text(row.get("task_name"))
                row["notes"] = decrypt_text(row.get("notes"))
                row["description"] = decrypt_text(row.get("description"))
                
        return data
    except Exception as e:
        print(f"Error fetching completed tasks: {e}")
        return []

def _calculate_next_target_date(target_date_str, recurrence_pattern):
    """Returns the next due date string for recurring tasks."""
    if not target_date_str or not recurrence_pattern:
        return None

    try:
        base_date = pd.to_datetime(target_date_str).date()
    except Exception:
        return None

    pattern = str(recurrence_pattern).strip().lower()
    if pattern == "daily":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(days=1)
    elif pattern == "weekly":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(weeks=1)
    elif pattern == "biweekly":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(weeks=2)
    elif pattern == "monthly":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(months=1)
    elif pattern == "quarterly":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(months=3)
    elif pattern == "every 6 months":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(months=6)
    elif pattern == "yearly":
        next_date = pd.Timestamp(base_date) + pd.DateOffset(years=1)
    else:
        return None

    return next_date.date().isoformat()


def add_new_task(task_name, category, priority, assigned_to, target_date, notes="", is_recurring=False, recurrence_pattern=None):
    try:
        house_id = get_current_household_id()
        
        data = {
            "category": category,
            "priority": priority,
            "assigned_to": assigned_to,
            "target_date": str(target_date) if target_date else None, 
            "is_recurring": bool(is_recurring),
            "recurrence_pattern": recurrence_pattern if is_recurring else None,
            "is_completed": False,
            "household_id": house_id,  # 🟢 NEW: Stamp the task with the family's ID!
            
            # 🟢 ENCRYPT SENSITIVE STRINGS
            "task_name": encrypt_data(task_name),
            "notes": encrypt_data(notes) if notes else None
        }
        supabase.table(TASK_TABLE).insert(data).execute()
        return True
    except Exception as e:
        print(f"Error inserting task: {e}")
        return False

def batch_update_tasks(task_ids, new_status):
    """Updates a list of task IDs to a specific status (True or False)."""
    try:
        house_id = get_current_household_id()
        # If completing tasks, fetch row details first so recurring tasks can roll forward.
        task_rows = []
        if new_status:
            response = (
                supabase
                .table(TASK_TABLE)
                .select("id, task_name, notes, category, priority, assigned_to, target_date, is_recurring, recurrence_pattern")
                .in_("id", task_ids)
                .eq("household_id", house_id)
                .execute()
            )
            task_rows = response.data or []

        # Loop through IDs and update each in the database
        for tid in task_ids:
            supabase.table(TASK_TABLE).update({"is_completed": new_status}).eq("id", tid).eq("household_id", house_id).execute()

        # Spawn next occurrence for recurring tasks that were just completed.
        if new_status and task_rows:
            for task in task_rows:
                if not task.get("is_recurring"):
                    continue

                next_target = _calculate_next_target_date(task.get("target_date"), task.get("recurrence_pattern"))
                if not next_target:
                    continue

                # The fetched task_name and notes are ALREADY encrypted ciphertext here, 
                # so inserting them directly safely preserves the encryption!
                next_task = {
                    "task_name": task.get("task_name"),
                    "notes": task.get("notes"),
                    "category": task.get("category"),
                    "priority": task.get("priority"),
                    "assigned_to": task.get("assigned_to"),
                    "target_date": next_target,
                    "is_recurring": True,
                    "recurrence_pattern": task.get("recurrence_pattern"),
                    "is_completed": False,
                    "household_id": house_id,
                }
                supabase.table(TASK_TABLE).insert(next_task).execute()
        return True
    except Exception as e:
        print(f"Error in batch update: {e}")
        return False

def update_task(task_id, task_name=None, notes=None, category=None, priority=None, assigned_to=None, target_date=None, clear_target_date=False, is_recurring=None, recurrence_pattern=None):
    """Updates specific fields of a task."""
    try:
        house_id = get_current_household_id()
        update_data = {}
        
        # 🟢 ENCRYPT SENSITIVE STRINGS IF PROVIDED
        if task_name is not None:
            update_data["task_name"] = encrypt_data(task_name)
        if notes is not None:
            update_data["notes"] = encrypt_data(notes) if notes else None
            
        if category is not None:
            update_data["category"] = category
        if priority is not None:
            update_data["priority"] = priority
        if assigned_to is not None:
            update_data["assigned_to"] = assigned_to
        if clear_target_date:
            update_data["target_date"] = None
        elif target_date is not None:
            update_data["target_date"] = str(target_date) if target_date else None
        if is_recurring is not None:
            update_data["is_recurring"] = bool(is_recurring)
            update_data["recurrence_pattern"] = recurrence_pattern if is_recurring else None
        elif recurrence_pattern is not None:
            update_data["recurrence_pattern"] = recurrence_pattern
        
        if update_data:
            supabase.table(TASK_TABLE).update(update_data).eq("id", task_id).eq("household_id", house_id).execute()
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
        require_privileged_user()
        # 🟢 Hides any ticket marked "Done"
        response = supabase.table("backlog").select("*").neq("status", "Done").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        print(f"Error fetching backlog: {e}")
        return []

def get_latest_released_version(app_name, fallback_version="0.0.0"):
    """Returns the latest non-empty released version for an app from backlog."""
    try:
        require_privileged_user()
        response = (
            supabase
            .table("backlog")
            .select("version, release_date, created_at")
            .eq("app_name", app_name)
            .eq("status", "Done")
            .order("release_date", desc=True)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        for row in (response.data or []):
            version = str(row.get("version", "")).strip()
            if version:
                return version
        return fallback_version
    except Exception as e:
        print(f"Error fetching latest released version for {app_name}: {e}")
        return fallback_version


def get_current_app_version(app_name, fallback_version="0.0.0"):
    """Returns current app version from cloud release ledger, then backlog fallback."""
    try:
        require_privileged_user()
        response = (
            supabase
            .table("app_release_ledger")
            .select("version, released_at")
            .eq("app_name", app_name)
            .order("released_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            version = str(response.data[0].get("version", "")).strip()
            if version:
                return version
    except Exception as e:
        print(f"Error fetching app version from ledger for {app_name}: {e}")

    return get_latest_released_version(app_name, fallback_version=fallback_version)


def log_release_version(app_name, version, release_target, release_date, backlog_items_released):
    """Writes a release event to the cloud ledger."""
    try:
        require_privileged_user()
        data = {
            "app_name": app_name,
            "version": version,
            "release_target": release_target,
            "release_date": release_date,
            "backlog_items_released": backlog_items_released,
            "created_by": st.session_state.get("username", "unknown")
        }
        supabase.table("app_release_ledger").insert(data).execute()
        return True
    except Exception as e:
        print(f"Error logging release ledger event: {e}")
        return False

def add_backlog_item(feature, notes, status="Backlog", app_name="home_sync", category="Core", priority="Medium", work_notes=""):
    """Adds a new backlog item using the correct database columns."""
    try:
        data = {
            "feature": feature,  
            "notes": notes,
            "work_notes": work_notes,      
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

def update_backlog_item(item_id, feature, notes, status, app_name, category, priority, public_message="", work_notes=""):
    """Updates an existing backlog ticket."""
    try:
        require_privileged_user()
        data = {
            "feature": feature,
            "notes": notes,
            "work_notes": work_notes,
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
        require_privileged_user()
        supabase.table("backlog").delete().eq("id", item_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting backlog item: {e}")
        return False

def cut_release(current_home_sync_version, current_get_fit_version, release_target="all"):
    """
    Cuts a release by:
     1. Find staged backlog items.
     2. Filter by release target: home_sync, get_fit, or all.
     3. Calculate next version per app for the selected target.
     4. Stamp release_date/version and move selected staged tickets to Done.
     5. Return resulting versions for both apps.
    
    Returns: (success: bool, versions: dict, message: str)
    """
    try:
        require_privileged_user()
        
        # Fetch all staged items first.
        staged_response = supabase.table("backlog").select("id, category, app_name").eq("status", "Staged").execute()
        staged_items_all = staged_response.data or []

        if not staged_items_all:
            return False, {
                "home_sync": current_home_sync_version,
                "get_fit": current_get_fit_version
            }, "No staged items found. Add items to 'Staged' status to cut a release."

        if release_target == "home_sync":
            staged_items = [i for i in staged_items_all if i.get("app_name") == "home_sync"]
        elif release_target == "get_fit":
            staged_items = [i for i in staged_items_all if i.get("app_name") == "get_fit"]
        else:
            staged_items = [
                i for i in staged_items_all
                if i.get("app_name") in ["home_sync", "get_fit", "Global"]
            ]

        if not staged_items:
            return False, {
                "home_sync": current_home_sync_version,
                "get_fit": current_get_fit_version
            }, f"No staged items found for target '{release_target}'."
        
        home_sync_categories = [
            item.get("category", "")
            for item in staged_items
            if item.get("app_name") == "home_sync" or (release_target == "all" and item.get("app_name") == "Global")
        ]
        get_fit_categories = [
            item.get("category", "")
            for item in staged_items
            if item.get("app_name") == "get_fit" or (release_target == "all" and item.get("app_name") == "Global")
        ]

        next_home_sync_version = (
            calculate_next_version(current_home_sync_version, home_sync_categories)
            if home_sync_categories else current_home_sync_version
        )
        next_get_fit_version = (
            calculate_next_version(current_get_fit_version, get_fit_categories)
            if get_fit_categories else current_get_fit_version
        )
        
        # Get today's date in Chicago timezone (for consistency with get-fit)
        today_str = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        
        # Update all staged items: set app-aware version, release_date, and move to Done
        for item in staged_items:
            app_name = item.get("app_name")
            if app_name == "home_sync":
                stamped_version = next_home_sync_version
            elif app_name == "get_fit":
                stamped_version = next_get_fit_version
            elif app_name == "Global":
                stamped_version = f"home_sync:{next_home_sync_version} | get_fit:{next_get_fit_version}"
            else:
                stamped_version = ""

            update_data = {
                "version": stamped_version,
                "release_date": today_str,
                "status": "Done"
            }
            supabase.table("backlog").update(update_data).eq("id", item["id"]).execute()

        home_sync_released_count = len([
            i for i in staged_items
            if i.get("app_name") == "home_sync" or (release_target == "all" and i.get("app_name") == "Global")
        ])
        get_fit_released_count = len([
            i for i in staged_items
            if i.get("app_name") == "get_fit" or (release_target == "all" and i.get("app_name") == "Global")
        ])

        if home_sync_categories:
            log_release_version(
                app_name="home_sync",
                version=next_home_sync_version,
                release_target=release_target,
                release_date=today_str,
                backlog_items_released=home_sync_released_count
            )

        if get_fit_categories:
            log_release_version(
                app_name="get_fit",
                version=next_get_fit_version,
                release_target=release_target,
                release_date=today_str,
                backlog_items_released=get_fit_released_count
            )
        
        return True, {
            "home_sync": next_home_sync_version,
            "get_fit": next_get_fit_version
        }, f"✅ Release cut for '{release_target}'! {len(staged_items)} items moved to Done."
        
    except Exception as e:
        print(f"Error cutting release: {e}")
        return False, {
            "home_sync": current_home_sync_version,
            "get_fit": current_get_fit_version
        }, f"Error cutting release: {str(e)}"


def delete_task(task_id):
    """Deletes a to-do list task entirely."""
    try:
        house_id = get_current_household_id()
        supabase.table(TASK_TABLE).delete().eq("id", task_id).eq("household_id", house_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting task: {e}")
        return False

# ==========================================
# 💰 BUDGET FUNCTIONS
# ==========================================

def _can_edit_projects_server_side():
    """Authoritative guard for project write operations."""
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    if st.session_state.get("can_edit_projects") is not None:
        return bool(st.session_state.get("can_edit_projects"))
    return bool(st.session_state.get("can_view_budget", False))

# ==========================================
# 💰 BUDGET FUNCTIONS
# ==========================================

def _can_edit_projects_server_side():
    """Authoritative guard for project write operations."""
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    if st.session_state.get("can_edit_projects") is not None:
        return bool(st.session_state.get("can_edit_projects"))
    return bool(st.session_state.get("can_view_budget", False))

def get_project_budgets():
    """
    Fetches and decrypts all project budget items for the active session's household.
    """
    try:
        house_id = get_current_household_id()
        response = supabase.table(PROJECT_BUDGETS_TABLE) \
            .select("*") \
            .eq("household_id", house_id) \
            .order("priority", desc=False) \
            .execute()
            
        data = response.data
        
        # 🟢 DECRYPT DATA BEFORE SENDING TO STREAMLIT UI
        if data:
            for row in data:
                # Text fields
                row["item"] = decrypt_text(row.get("item"))
                row["description"] = decrypt_text(row.get("description"))
                row["vendors"] = decrypt_text(row.get("vendors"))
                row["notes"] = decrypt_text(row.get("notes"))
                
                # Financial fields (converted back to floats for math)
                row["est_low_cost"] = decrypt_float(row.get("est_low_cost"))
                row["est_high_cost"] = decrypt_float(row.get("est_high_cost"))
                row["actual_cost"] = decrypt_float(row.get("actual_cost"))
                
        return data
    except Exception as e:
        print(f"Error fetching project budgets: {e}")
        return []

def update_project_budget_item(item_id: str, updated_data: dict):
    """
    Encrypts sensitive fields and updates a specific budget item.
    """
    try:
        if not _can_edit_projects_server_side():
            return False
        house_id = get_current_household_id()
        
        payload = dict(updated_data or {})
        
        # 🟢 ENCRYPT SENSITIVE FIELDS BEFORE SAVING
        fields_to_encrypt = ["item", "description", "est_low_cost", "est_high_cost", "actual_cost", "vendors", "notes"]
        
        for field in fields_to_encrypt:
            if field in payload and payload[field] is not None:
                payload[field] = encrypt_data(payload[field])
                
        response = supabase.table(PROJECT_BUDGETS_TABLE) \
            .update(payload) \
            .eq("id", item_id) \
            .eq("household_id", house_id) \
            .execute()
        return True
    except Exception as e:
        print(f"Error updating budget item: {e}")
        return False

def insert_project_budget_item(data: dict):
    """
    Encrypts sensitive fields and inserts a new budget item.
    """
    try:
        if not _can_edit_projects_server_side():
            return False
        house_id = get_current_household_id()
        
        payload = dict(data or {})
        payload["household_id"] = house_id # Ensure the active household ID is stamped
        
        # 🟢 ENCRYPT SENSITIVE FIELDS BEFORE SAVING
        fields_to_encrypt = ["item", "description", "est_low_cost", "est_high_cost", "actual_cost", "vendors", "notes"]
        
        for field in fields_to_encrypt:
            if field in payload and payload[field] is not None:
                payload[field] = encrypt_data(payload[field])
                
        response = supabase.table(PROJECT_BUDGETS_TABLE).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error inserting budget item: {e}")
        return False


def _can_manage_wishlist_row_server_side(row):
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True

    active_auth_user_id = st.session_state.get("auth_user_id")
    row_auth_user_id = row.get("owner_auth_user_id")
    if active_auth_user_id and str(row_auth_user_id or "") == str(active_auth_user_id):
        return True

    active_username = str(st.session_state.get("username", "")).strip()
    row_username = str(row.get("owner_username") or "").strip()
    return bool(active_username and row_username and active_username == row_username)


def get_wish_list_items():
    """Fetches household wish list items with role-aware visibility and decrypts them."""
    try:
        house_id = get_current_household_id()
        query = (
            supabase
            .table(WISH_LIST_TABLE)
            .select("*")
            .eq("household_id", house_id)
            .order("updated_at", desc=True)
        )

        response = query.execute()
        data = response.data or []
        
        # 🟢 DECRYPT DATA BEFORE SENDING IT TO STREAMLIT
        if data:
            for row in data:
                # Text fields
                row["item"] = decrypt_text(row.get("item"))
                row["description"] = decrypt_text(row.get("description"))
                row["vendor"] = decrypt_text(row.get("vendor"))
                row["notes"] = decrypt_text(row.get("notes"))
                
                # Financial fields
                row["estimated_price"] = decrypt_float(row.get("estimated_price"))
                row["actual_cost"] = decrypt_float(row.get("actual_cost"))
                
                # Note: veteran_discount and is_completed are booleans and left alone
                
        return data
    except Exception as e:
        print(f"Error fetching wish list items: {e}")
        return []


def insert_wish_list_item(data: dict):
    """Encrypts and inserts a wish list item for the active household/user."""
    try:
        house_id = get_current_household_id()
        active_auth_user_id = st.session_state.get("auth_user_id")
        active_username = st.session_state.get("username")

        payload = dict(data or {})
        payload["household_id"] = house_id
        payload["is_completed"] = bool(payload.get("is_completed", False))
        payload["owner_auth_user_id"] = active_auth_user_id
        payload["owner_username"] = active_username

        # 🟢 ENCRYPT SENSITIVE FIELDS BEFORE SAVING
        fields_to_encrypt = ["item", "description", "estimated_price", "actual_cost", "vendor", "notes"]
        
        for field in fields_to_encrypt:
            if field in payload and payload[field] is not None:
                payload[field] = encrypt_data(payload[field])

        response = supabase.table(WISH_LIST_TABLE).insert(payload).execute()
        return bool(response.data)
    except Exception as e:
        print(f"Error inserting wish list item: {e}")
        return False


def update_wish_list_item(item_id: str, updated_data: dict):
    """Updates a wish list item with role-aware checks and encrypts the payload."""
    try:
        house_id = get_current_household_id()
        existing = (
            supabase
            .table(WISH_LIST_TABLE)
            .select("id, owner_auth_user_id, owner_username")
            .eq("id", item_id)
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False

        row = existing.data[0]
        if not _can_manage_wishlist_row_server_side(row):
            return False

        payload = dict(updated_data or {})
        payload.pop("owner_auth_user_id", None)
        payload.pop("owner_username", None)
        
        if "is_completed" in payload:
            payload["is_completed"] = bool(payload.get("is_completed", False))

        payload["updated_at"] = datetime.now(ZoneInfo("America/Chicago")).isoformat()

        # 🟢 ENCRYPT SENSITIVE FIELDS BEFORE SAVING
        fields_to_encrypt = ["item", "description", "estimated_price", "actual_cost", "vendor", "notes"]
        
        for field in fields_to_encrypt:
            if field in payload and payload[field] is not None:
                payload[field] = encrypt_data(payload[field])

        (
            supabase
            .table(WISH_LIST_TABLE)
            .update(payload)
            .eq("id", item_id)
            .eq("household_id", house_id)
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error updating wish list item: {e}")
        return False


def delete_wish_list_item(item_id: str):
    """Deletes a wish list item with role-aware row ownership checks."""
    try:
        house_id = get_current_household_id()
        existing = (
            supabase
            .table(WISH_LIST_TABLE)
            .select("id, owner_auth_user_id, owner_username")
            .eq("id", item_id)
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False

        row = existing.data[0]
        if not _can_manage_wishlist_row_server_side(row):
            return False

        (
            supabase
            .table(WISH_LIST_TABLE)
            .delete()
            .eq("id", item_id)
            .eq("household_id", house_id)
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error deleting wish list item: {e}")
        return False


def complete_wish_list_item(item_id: str):
    """Marks a wish list item as completed for the active household."""
    try:
        house_id = get_current_household_id()
        existing = (
            supabase
            .table(WISH_LIST_TABLE)
            .select("id, owner_auth_user_id, owner_username")
            .eq("id", item_id)
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False

        row = existing.data[0]
        if not _can_manage_wishlist_row_server_side(row):
            return False

        (
            supabase
            .table(WISH_LIST_TABLE)
            .update({"is_completed": True, "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat()})
            .eq("id", item_id)
            .eq("household_id", house_id)
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error completing wish list item: {e}")
        return False


def restore_wish_list_item(item_id: str):
    """Restores a completed wish list item back to active status."""
    try:
        house_id = get_current_household_id()
        existing = (
            supabase
            .table(WISH_LIST_TABLE)
            .select("id, owner_auth_user_id, owner_username")
            .eq("id", item_id)
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False

        row = existing.data[0]
        if not _can_manage_wishlist_row_server_side(row):
            return False

        (
            supabase
            .table(WISH_LIST_TABLE)
            .update({"is_completed": False, "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat()})
            .eq("id", item_id)
            .eq("household_id", house_id)
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error restoring wish list item: {e}")
        return False


def get_household_finance_settings():
    """Fetches and decrypts finance settings for the active household."""
    try:
        house_id = get_current_household_id()
        response = (
            supabase
            .table(HOUSEHOLD_FINANCE_SETTINGS_TABLE)
            .select("household_id, projects_funds, projects_funds_year, updated_at")
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if response.data:
            data = response.data[0]
            # 🟢 DECRYPT DOLLAR VALUE
            data["projects_funds"] = decrypt_float(data.get("projects_funds"))
            return data
        return {}
    except Exception as e:
        print(f"Error fetching household finance settings: {e}")
        return {}


def update_household_projects_funds(projects_funds, projects_funds_year=None):
    """Encrypts and upserts projects_funds for the active household."""
    try:
        if not _can_edit_projects_server_side():
            return False
        house_id = get_current_household_id()
        
        payload = {
            "household_id": house_id,
            "projects_funds_year": projects_funds_year,
            "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        }
        
        # 🟢 ENCRYPT DOLLAR VALUE IF IT EXISTS
        if projects_funds is not None:
            payload["projects_funds"] = encrypt_data(projects_funds)
        else:
            payload["projects_funds"] = None
            
        (
            supabase
            .table(HOUSEHOLD_FINANCE_SETTINGS_TABLE)
            .upsert(payload, on_conflict="household_id")
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error updating projects funds: {e}")
        return False


def get_current_user_permissions():
    """Returns role and module-access permissions for the active signed-in user."""
    try:
        house_id = get_current_household_id()
        auth_user_id = st.session_state.get("auth_user_id")
        if not auth_user_id:
            return {}

        for attempt in range(2):
            try:
                try:
                    response = (
                        supabase
                        .table("users")
                        .select(
                            "role, can_view_budget, can_view_projects, can_edit_projects, "
                            "can_view_monthly_budget, can_edit_monthly_budget, "
                            "can_view_wishlist_members, can_view_wishlist_admin"
                        )
                        .eq("auth_user_id", auth_user_id)
                        .eq("household_id", house_id)
                        .limit(1)
                        .execute()
                    )
                except Exception:
                    # Compatibility path before module-level columns are migrated.
                    response = (
                        supabase
                        .table("users")
                        .select("role, can_view_budget")
                        .eq("auth_user_id", auth_user_id)
                        .eq("household_id", house_id)
                        .limit(1)
                        .execute()
                    )

                if response.data:
                    return response.data[0]
                return {}
            except Exception as query_error:
                if attempt == 0:
                    continue
                print(f"Error fetching current user permissions: {query_error}")
        return {}
    except Exception as e:
        print(f"Error fetching current user permissions: {e}")
        return {}

# ==========================================
# 🛡️ ADMIN FUNCTIONS
# ==========================================

def get_household_users_for_admin():
    """Fetches all users in the current household so admins can manage them."""
    try:
        house_id = get_current_household_id()
        # FIXED: Changed 'id' to 'auth_user_id'
        try:
            response = supabase.table("users") \
                .select(
                    "auth_user_id, username, role, can_view_budget, "
                    "can_view_projects, can_edit_projects, "
                    "can_view_monthly_budget, can_edit_monthly_budget, "
                    "can_view_wishlist_members, can_view_wishlist_admin"
                ) \
                .eq("household_id", house_id) \
                .execute()
        except Exception:
            # Compatibility path before module-level columns are migrated.
            response = supabase.table("users") \
                .select("auth_user_id, username, role, can_view_budget") \
                .eq("household_id", house_id) \
                .execute()
        return response.data
    except Exception as e:
        print(f"Error fetching users: {e}")
        return []

def update_user_budget_access(auth_user_id: str, can_view: bool):
    """Allows an Admin/Developer to toggle budget access for a household member."""
    try:
        current_role = st.session_state.get("user_role")
        if current_role not in ["admin", "developer"]:
            return False # Security check!

        house_id = get_current_household_id()
        
        payload = {
            "can_view_budget": bool(can_view),
            "can_view_projects": bool(can_view),
            "can_edit_projects": bool(can_view),
            "can_view_monthly_budget": bool(can_view),
        }

        # FIXED: Changed 'id' to 'auth_user_id'
        supabase.table("users").update(payload) \
            .eq("auth_user_id", auth_user_id).eq("household_id", house_id).execute()
        return True
    except Exception as e:
        print(f"Error updating user access: {e}")
        return False


def update_user_module_permissions(auth_user_id: str, updates: dict):
    """Allows Admin/Developer to update explicit module-level permissions."""
    try:
        current_role = st.session_state.get("user_role")
        if current_role not in ["admin", "developer"]:
            return False

        allowed_keys = {
            "can_view_projects",
            "can_edit_projects",
            "can_view_monthly_budget",
            "can_edit_monthly_budget",
            "can_view_budget",
            "can_view_wishlist_members",
            "can_view_wishlist_admin",
        }
        payload = {k: bool(v) for k, v in (updates or {}).items() if k in allowed_keys}
        if not payload:
            return False

        # Enforce write-implies-read safety invariants.
        if payload.get("can_edit_projects") is True:
            payload["can_view_projects"] = True
        if payload.get("can_view_projects") is False:
            payload["can_edit_projects"] = False

        if payload.get("can_edit_monthly_budget") is True:
            payload["can_view_monthly_budget"] = True
        if payload.get("can_view_monthly_budget") is False:
            payload["can_edit_monthly_budget"] = False

        # Keep legacy rollup for backwards compatibility.
        if "can_view_projects" in payload or "can_view_monthly_budget" in payload:
            projects_view = payload.get("can_view_projects")
            monthly_view = payload.get("can_view_monthly_budget")
            if projects_view is not None and monthly_view is not None:
                payload["can_view_budget"] = bool(projects_view or monthly_view)

        house_id = get_current_household_id()
        (
            supabase
            .table("users")
            .update(payload)
            .eq("auth_user_id", auth_user_id)
            .eq("household_id", house_id)
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error updating module permissions: {e}")
        return False