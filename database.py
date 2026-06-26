import streamlit as st
from supabase import create_client, Client
import pandas as pd
from security import encrypt_data, decrypt_text, decrypt_float
import calendar
from datetime import datetime, date
from zoneinfo import ZoneInfo
from utils import calculate_next_version
from constants import (
    ALLOWANCE_CATEGORY_NAME,
    DEFAULT_BUDGET_CATEGORIES,
    PROJECT_EXPENSE_CATEGORY,
    is_allowance_category,
    is_allowance_subcategory,
    is_system_managed_allowance_category,
    is_system_project_expense_category,
)

# 🟢 DYNAMIC ENVIRONMENT ROUTING
env = st.secrets.get("app_config", {}).get("environment", "production")
TASK_TABLE = "household_tasks_dev" if env == "local" else "household_tasks"
PROJECT_BUDGETS_TABLE = "project_budgets_dev" if env == "local" else "project_budgets"
HOUSEHOLD_FINANCE_SETTINGS_TABLE = "household_finance_settings_dev" if env == "local" else "household_finance_settings"
WISH_LIST_TABLE = "wish_list_dev" if env == "local" else "wish_list"

INCOME_PAY_FREQUENCIES = (
    "weekly",
    "bi_weekly",
    "semi_monthly",
    "monthly",
    "school_year_monthly",
    "quarterly",
    "annually",
    "one_time",
)

INCOME_PAY_FREQUENCY_LABELS = {
    "weekly": "Weekly",
    "bi_weekly": "Bi-weekly",
    "semi_monthly": "Semi-monthly",
    "monthly": "Monthly",
    "school_year_monthly": "School year (monthly)",
    "quarterly": "Quarterly",
    "annually": "Annually",
    "one_time": "One-time",
}

SCHOOL_YEAR_ACTIVE_MONTHS = frozenset({9, 10, 11, 12, 1, 2, 3, 4, 5, 6})


def school_year_active_month(month: int) -> bool:
    """Sep–Jun are active pay months for school-year income."""
    return month in SCHOOL_YEAR_ACTIVE_MONTHS


def school_year_rollover_source_month(year: int, month: int) -> str:
    """Month to copy school-year income from when rolling into an active month."""
    if month == 9:
        return f"{year}-06"
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def normalize_income_pay_frequency(value) -> str:
    freq = str(value or "monthly").strip().lower()
    if freq in INCOME_PAY_FREQUENCY_LABELS:
        return freq
    return "monthly"


def income_pay_frequency_label(value) -> str:
    return INCOME_PAY_FREQUENCY_LABELS.get(normalize_income_pay_frequency(value), "Monthly")


def income_is_recurring_frequency(pay_frequency) -> bool:
    return normalize_income_pay_frequency(pay_frequency) != "one_time"


def normalize_income_amount_for_month(amount, pay_frequency, month_year=None) -> float:
    """Convert a per-payment income amount into an estimated monthly total."""
    freq = normalize_income_pay_frequency(pay_frequency)
    safe_amount = float(amount or 0)
    if freq == "school_year_monthly":
        if month_year:
            _, month = map(int, str(month_year).split("-"))
            if not school_year_active_month(month):
                return 0.0
        return safe_amount
    if freq == "weekly":
        return safe_amount * 52 / 12
    if freq == "bi_weekly":
        return safe_amount * 26 / 12
    if freq == "semi_monthly":
        return safe_amount * 2
    if freq == "monthly":
        return safe_amount
    if freq == "quarterly":
        return safe_amount / 3
    if freq == "annually":
        return safe_amount / 12
    return safe_amount


def _row_month_year(row, default=None):
    return row.get("month_year") or default


def annualize_income_amount(amount, pay_frequency) -> float:
    """Project a per-payment amount to an estimated annual total."""
    freq = normalize_income_pay_frequency(pay_frequency)
    safe_amount = float(amount or 0)
    if freq == "one_time":
        return safe_amount
    if freq == "school_year_monthly":
        return safe_amount * 10
    if freq == "weekly":
        return safe_amount * 52
    if freq == "bi_weekly":
        return safe_amount * 26
    if freq == "semi_monthly":
        return safe_amount * 24
    if freq == "monthly":
        return safe_amount * 12
    if freq == "quarterly":
        return safe_amount * 4
    if freq == "annually":
        return safe_amount
    return safe_amount


def sum_income_for_month(incomes_df, selected_month=None) -> float:
    if incomes_df is None or incomes_df.empty:
        return 0.0
    total = 0.0
    for _, row in incomes_df.iterrows():
        freq = row.get("pay_frequency")
        if not freq:
            freq = "monthly" if row.get("is_recurring") else "one_time"
        month_year = _row_month_year(row, selected_month)
        total += normalize_income_amount_for_month(
            row.get("take_home_amount"), freq, month_year=month_year
        )
    return total


def _income_row_frequency(row) -> str:
    freq = row.get("pay_frequency")
    if not freq:
        return "monthly" if row.get("is_recurring") else "one_time"
    return normalize_income_pay_frequency(freq)


def sum_gross_for_month(incomes_df, selected_month=None) -> float:
    if incomes_df is None or incomes_df.empty:
        return 0.0
    total = 0.0
    for _, row in incomes_df.iterrows():
        month_year = _row_month_year(row, selected_month)
        total += normalize_income_amount_for_month(
            row.get("gross_amount"), _income_row_frequency(row), month_year=month_year
        )
    return total


def sum_taxable_gross_for_month(incomes_df, selected_month=None) -> float:
    if incomes_df is None or incomes_df.empty:
        return 0.0
    total = 0.0
    for _, row in incomes_df.iterrows():
        if not bool(row.get("is_taxable", False)):
            continue
        month_year = _row_month_year(row, selected_month)
        total += normalize_income_amount_for_month(
            row.get("gross_amount"), _income_row_frequency(row), month_year=month_year
        )
    return total


def sum_nontaxable_gross_for_month(incomes_df, selected_month=None) -> float:
    if incomes_df is None or incomes_df.empty:
        return 0.0
    total = 0.0
    for _, row in incomes_df.iterrows():
        if bool(row.get("is_taxable", False)):
            continue
        month_year = _row_month_year(row, selected_month)
        total += normalize_income_amount_for_month(
            row.get("gross_amount"), _income_row_frequency(row), month_year=month_year
        )
    return total


def compute_annual_income_totals(incomes_df) -> dict:
    """Estimated annual income figures from frequency-aware streams."""
    if incomes_df is None or incomes_df.empty:
        return {
            "annual_takehome": 0.0,
            "annual_gross": 0.0,
            "annual_taxable": 0.0,
            "annual_non_taxable": 0.0,
        }
    annual_takehome = 0.0
    annual_gross = 0.0
    annual_taxable = 0.0
    annual_nontaxable = 0.0
    for _, row in incomes_df.iterrows():
        freq = _income_row_frequency(row)
        annual_takehome += annualize_income_amount(row.get("take_home_amount"), freq)
        gross_annual = annualize_income_amount(row.get("gross_amount"), freq)
        annual_gross += gross_annual
        if bool(row.get("is_taxable", False)):
            annual_taxable += gross_annual
        else:
            annual_nontaxable += gross_annual
    return {
        "annual_takehome": annual_takehome,
        "annual_gross": annual_gross,
        "annual_taxable": annual_taxable,
        "annual_non_taxable": annual_nontaxable,
    }

@st.cache_resource
def init_connection() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

supabase = init_connection()

HOME_MGMT_PERMISSION_KEYS = (
    "can_view_home_solar",
    "can_edit_home_solar",
    "can_view_home_security",
    "can_edit_home_security",
    "can_view_home_garage",
    "can_edit_home_garage",
    "can_view_home_logs",
    "can_edit_home_logs",
)
HOME_MGMT_PERMISSION_KEY_SET = frozenset(HOME_MGMT_PERMISSION_KEYS)


@st.cache_data(ttl=60, show_spinner=False)
def _home_mgmt_permissions_available() -> bool:
    try:
        (
            supabase.table("users")
            .select("can_view_home_solar")
            .limit(1)
            .execute()
        )
        return True
    except Exception:
        return False


def home_mgmt_permissions_available() -> bool:
    return _home_mgmt_permissions_available()


def clear_home_mgmt_permissions_cache():
    clear_fn = getattr(_home_mgmt_permissions_available, "clear", None)
    if callable(clear_fn):
        clear_fn()


def get_current_household_id():
    house_id = st.session_state.get("household_id")
    if not house_id or house_id == "unassigned":
        raise ValueError("No household is associated with the current session.")
    return house_id


def require_privileged_user():
    if st.session_state.get("user_role") != "developer":
        raise PermissionError("This action requires developer access.")


@st.cache_data(ttl=3600, show_spinner=False)
def get_available_users(household_id):
    """Fetch usernames for a household (used by to-do assignee pickers)."""
    try:
        response = supabase.table("users").select("username").eq("household_id", household_id).execute()
        return [user["username"] for user in response.data] if response.data else []
    except Exception as e:
        print(f"Could not fetch users: {e}")
        return []

# ==========================================
# 📋 TO-DO LIST FUNCTIONS
# ==========================================

def _decrypt_task_rows(data):
    if not data:
        return []
    for row in data:
        row["task_name"] = decrypt_text(row.get("task_name"))
        row["notes"] = decrypt_text(row.get("notes"))
        row["description"] = decrypt_text(row.get("description"))
    return data


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_active_tasks_cached(household_id):
    response = (
        supabase.table(TASK_TABLE)
        .select("*")
        .eq("is_completed", False)
        .eq("household_id", household_id)
        .execute()
    )
    return _decrypt_task_rows(response.data or [])


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_completed_tasks_cached(household_id):
    response = (
        supabase.table(TASK_TABLE)
        .select("*")
        .eq("is_completed", True)
        .eq("household_id", household_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return _decrypt_task_rows(response.data or [])


def clear_task_list_cache():
    for cached_fn in (_fetch_active_tasks_cached, _fetch_completed_tasks_cached):
        clear_fn = getattr(cached_fn, "clear", None)
        if callable(clear_fn):
            clear_fn()


def get_active_tasks():
    try:
        house_id = get_current_household_id()
        return list(_fetch_active_tasks_cached(house_id))
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        return []


def get_completed_tasks():
    try:
        house_id = get_current_household_id()
        return [dict(row) for row in _fetch_completed_tasks_cached(house_id)]
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
        clear_task_list_cache()
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
        clear_task_list_cache()
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
            clear_task_list_cache()
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
        clear_task_list_cache()
        return True
    except Exception as e:
        print(f"Error deleting task: {e}")
        return False

# ==========================================
# 🏦 MONTHLY BUDGET MODULE FUNCTIONS
# ==========================================

def get_budget_table(base_name):
    """Helper to route to the correct dev or prod budget table."""
    try:
        env = st.secrets["app_config"].get("environment", "production")
        return f"{base_name}_dev" if env == "local" else base_name
    except Exception:
        return base_name

def get_user_finance_settings(household_id, username):
    """Fetches the user's specific finance UI settings and privacy toggles."""
    target_table = get_budget_table("user_finance_settings")
    try:
        response = supabase.table(target_table).select("*").eq("household_id", household_id).eq("username", username).execute()
        if response.data:
            data = response.data[0]
            # Decrypt optional future fields if you add them (like personal_savings_goal)
            return data
        return {"share_budget_with_admin": False, "default_view": "Household"}
    except Exception as e:
        print(f"Error fetching finance settings: {e}")
        return {"share_budget_with_admin": False, "default_view": "Household"}

def ensure_household_initialized(household_id):
    """Silent check to inject defaults if the household has no categories."""
    target_table = get_budget_table("budget_categories")
    try:
        # Check if they have any data
        existing = supabase.table(target_table).select("id").eq("household_id", household_id).limit(1).execute()
        if existing.data:
            return 
        
        # If no data, run the injection
        initialize_default_categories(household_id)
    except Exception as e:
        print(f"Initialization silent check failed: {e}")

def get_budget_categories(household_id, is_personal=False, username=None):
    """Fetches categories and decrypts their target budgets."""
    target_table = get_budget_table("budget_categories")
    try:
        query = supabase.table(target_table).select("*").eq("household_id", household_id).eq("is_active", True).eq("is_personal", is_personal)
        
        if is_personal and username:
            query = query.eq("username", username)
            
        response = query.execute()
        
        if response.data:
            for row in response.data:
                if row.get("category_name"):
                    row["category_name"] = decrypt_text(row.get("category_name"))
                if row.get("sub_category_name"):
                    row["sub_category_name"] = decrypt_text(row.get("sub_category_name"))
                # 🟢 NEW: Decrypt the target budget
                if row.get("target_budget") is not None:
                    row["target_budget"] = decrypt_float(row.get("target_budget"))
                else:
                    row["target_budget"] = 0.0
                    
            return pd.DataFrame(response.data)
            
        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching categories: {e}")
        return pd.DataFrame()

def get_household_incomes(household_id, month_year, is_personal_income=False, username=None): # 🟢 ADD ARGS HERE
    target_table = get_budget_table("household_incomes")
    try:
        # 🟢 ADD THE .eq() FILTERS TO THE QUERY
        query = supabase.table(target_table).select("*").eq("household_id", household_id).eq("month_year", month_year).eq("is_personal_income", is_personal_income)
        
        if is_personal_income and username:
            query = query.eq("owner_username", username)
            
        response = query.execute()
        if response.data:
            for row in response.data:
                if row.get("source_name"):
                    row["source_name"] = decrypt_text(row.get("source_name"))
                if row.get("take_home_amount") is not None:
                    row["take_home_amount"] = decrypt_float(row.get("take_home_amount"))
                if row.get("gross_amount") is not None:
                    row["gross_amount"] = decrypt_float(row.get("gross_amount"))
                row["pay_frequency"] = normalize_income_pay_frequency(
                    row.get("pay_frequency")
                    or ("monthly" if row.get("is_recurring") else "one_time")
                )
                if row.get("payment_date") and not isinstance(row.get("payment_date"), str):
                    row["payment_date"] = row["payment_date"].isoformat()
                    
            return pd.DataFrame(response.data)
        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching incomes: {e}")
        return pd.DataFrame()

def get_monthly_expenses(household_id, month_year, include_private_members=True):
    """
    Fetches and decrypts the event stream of expenses.
    If include_private_members is False, it filters out users who toggled their privacy on.
    """
    target_table = get_budget_table("expenses")
    try:
        response = supabase.table(target_table).select("*").eq("household_id", household_id).eq("month_year", month_year).execute()
        
        if not response.data:
            return pd.DataFrame()
            
        # 🟢 THE ROLLUP PRIVACY CHECK
        if not include_private_members:
            # In a real scenario, you'd fetch the user_finance_settings here and filter out 
            # auth_user_ids where share_budget_with_admin == False
            pass 

        for row in response.data:
            row["amount"] = decrypt_float(row.get("amount"))
            row["details"] = decrypt_text(row.get("details"))
            
        return pd.DataFrame(response.data)
    except Exception as e:
        print(f"Error fetching expenses: {e}")
        return pd.DataFrame()


def get_expenses_for_period(household_id, start_month, end_month, include_private_members=True):
    """Fetch and decrypt expenses whose month_year falls within [start_month, end_month]."""
    target_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(target_table)
            .select("*")
            .eq("household_id", household_id)
            .gte("month_year", start_month)
            .lte("month_year", end_month)
            .execute()
        )
        if not response.data:
            return pd.DataFrame()

        if not include_private_members:
            pass

        for row in response.data:
            row["amount"] = decrypt_float(row.get("amount"))
            row["details"] = decrypt_text(row.get("details"))

        return pd.DataFrame(response.data)
    except Exception as e:
        print(f"Error fetching expenses for period: {e}")
        return pd.DataFrame()


def get_household_incomes_for_period(
    household_id,
    start_month,
    end_month,
    is_personal_income=False,
    username=None,
):
    """Fetch and decrypt incomes whose month_year falls within [start_month, end_month]."""
    target_table = get_budget_table("household_incomes")
    try:
        query = (
            supabase.table(target_table)
            .select("*")
            .eq("household_id", household_id)
            .gte("month_year", start_month)
            .lte("month_year", end_month)
            .eq("is_personal_income", is_personal_income)
        )
        if is_personal_income and username:
            query = query.eq("owner_username", username)

        response = query.execute()
        if not response.data:
            return pd.DataFrame()

        for row in response.data:
            if row.get("source_name"):
                row["source_name"] = decrypt_text(row.get("source_name"))
            if row.get("take_home_amount") is not None:
                row["take_home_amount"] = decrypt_float(row.get("take_home_amount"))
            if row.get("gross_amount") is not None:
                row["gross_amount"] = decrypt_float(row.get("gross_amount"))
            row["pay_frequency"] = normalize_income_pay_frequency(
                row.get("pay_frequency")
                or ("monthly" if row.get("is_recurring") else "one_time")
            )
            if row.get("payment_date") and not isinstance(row.get("payment_date"), str):
                row["payment_date"] = row["payment_date"].isoformat()

        return pd.DataFrame(response.data)
    except Exception as e:
        print(f"Error fetching incomes for period: {e}")
        return pd.DataFrame()


def get_distinct_budget_years(household_id) -> list:
    """Return calendar years with logged budget data, newest first; always includes current year."""
    years = set()
    current_year = date.today().year
    years.add(current_year)

    for table_key in ("expenses", "household_incomes"):
        target_table = get_budget_table(table_key)
        try:
            response = (
                supabase.table(target_table)
                .select("month_year")
                .eq("household_id", household_id)
                .execute()
            )
            for row in response.data or []:
                month_year = row.get("month_year")
                if month_year and len(str(month_year)) >= 4:
                    try:
                        years.add(int(str(month_year)[:4]))
                    except ValueError:
                        pass
        except Exception as e:
            print(f"Error fetching budget years from {table_key}: {e}")

    return sorted(years, reverse=True)


def log_expense_and_check_project(auth_user_id, username, household_id, month_year, date_logged, category_id, amount, details, is_personal_spend=False, is_recurring=False):
    """Logs an expense, now tracking if it is a recurring monthly bill."""
    if not is_personal_spend:
        if not _can_edit_monthly_budget_server_side():
            return False
    elif (
        str(auth_user_id) != str(st.session_state.get("auth_user_id"))
        and not _is_budget_privileged()
    ):
        return False

    target_table = get_budget_table("expenses")
    try:
        payload = {
            "household_id": household_id,
            "auth_user_id": auth_user_id,
            "username": username,
            "month_year": month_year,
            "date_logged": date_logged.isoformat(),
            "category_id": category_id,
            "amount": encrypt_data(float(amount)),
            "details": encrypt_data(details),
            "is_personal_spend": is_personal_spend,
            "is_recurring": is_recurring  # 🟢 NEW: Recurring flag added to payload
        }
        response = supabase.table(target_table).insert(payload).execute()
        if not response.data:
            return False

        expense_id = response.data[0].get("id")
        if (
            expense_id
            and not is_personal_spend
            and is_allowance_subcategory_id(category_id)
        ):
            recipient = get_allowance_recipient_username(category_id)
            if recipient:
                _insert_allowance_personal_income(
                    household_id=household_id,
                    expense_id=expense_id,
                    recipient_username=recipient,
                    amount=float(amount),
                    payment_date=date_logged,
                    month_year=month_year,
                )
        return True
    except Exception as e:
        print(f"Error logging expense: {e}")
        return False

def get_members_sharing_personal_budget(household_id, exclude_username=None):
    """Usernames in this household who allow family admins to view their personal budget."""
    settings_table = get_budget_table("user_finance_settings")
    try:
        response = (
            supabase.table(settings_table)
            .select("username")
            .eq("household_id", household_id)
            .eq("share_budget_with_admin", True)
            .execute()
        )
        usernames = [row["username"] for row in (response.data or []) if row.get("username")]
        if exclude_username:
            usernames = [name for name in usernames if name != exclude_username]
        return usernames
    except Exception as e:
        print(f"Error fetching sharing members: {e}")
        return []


def member_allows_personal_budget_sharing(household_id, username):
    settings = get_user_finance_settings(household_id, username)
    return bool(settings.get("share_budget_with_admin", False))


def get_shared_member_expenses(household_id, month_year, username=None):
    """Fetches personal expenses for members who opted to share with family admins."""
    expenses_table = get_budget_table("expenses")
    settings_table = get_budget_table("user_finance_settings")

    try:
        settings_res = (
            supabase.table(settings_table)
            .select("username")
            .eq("household_id", household_id)
            .eq("share_budget_with_admin", True)
            .execute()
        )
        sharing_users = [row["username"] for row in (settings_res.data or []) if row.get("username")]
        if username:
            if username not in sharing_users:
                return pd.DataFrame()
            sharing_users = [username]

        if not sharing_users:
            return pd.DataFrame()

        exp_res = (
            supabase.table(expenses_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_spend", True)
            .in_("username", sharing_users)
            .execute()
        )

        if exp_res.data:
            for row in exp_res.data:
                row["amount"] = decrypt_float(row.get("amount"))
                row["details"] = decrypt_text(row.get("details"))
            return pd.DataFrame(exp_res.data)

        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching shared expenses: {e}")
        return pd.DataFrame()

def get_cash_flow_routing(household_id):
    """Fetches and decrypts the Treasury routing targets."""
    target_table = get_budget_table("cash_flow_routing")
    try:
        response = supabase.table(target_table).select("*").eq("household_id", household_id).eq("is_active", True).execute()
        if response.data:
            for row in response.data:
                row["destination_account"] = decrypt_text(row.get("destination_account"))
                row["line_item"] = decrypt_text(row.get("line_item"))
                row["annual_goal"] = decrypt_float(row.get("annual_goal"))
                row["monthly_target"] = decrypt_float(row.get("monthly_target"))
            return pd.DataFrame(response.data)
        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching routing: {e}")
        return pd.DataFrame()

def calculate_spend_money(paycheck_amount, routing_df):
    """
    Executes the Y39 Excel Formula: =(Paycheck/2 - FractionalRoutingTotals) / 2
    """
    try:
        if routing_df.empty:
            return 0.0
            
        # Excel "Two" column logic = Monthly Target divided by 2
        routing_df["fractional_target"] = routing_df["monthly_target"] / 2
        
        # Sum up all the fractional targets for Bills and Income (Table378 & Table3786)
        fractional_routing_total = routing_df["fractional_target"].sum()
        
        # The exact Y39 Waterfall Math
        spend_money = ((float(paycheck_amount) / 2) - fractional_routing_total) / 2
        
        return round(spend_money, 2)
    except Exception as e:
        print(f"Error calculating spend money: {e}")
        return 0.0

def insert_budget_category(household_id, category_name, sub_category_name=None, is_personal=False, username=None, target_budget=0.0):
    """Inserts a category, now streamlined without 'Type'."""
    if is_personal:
        if not _is_budget_privileged() and username != st.session_state.get("username"):
            return False
    elif not _can_edit_monthly_budget_server_side():
        return False

    if is_system_project_expense_category(category_name, sub_category_name):
        return False
    if is_system_managed_allowance_category(category_name, sub_category_name):
        return False

    target_table = get_budget_table("budget_categories")
    try:
        safe_target = float(target_budget) if target_budget not in [None, ""] else 0.0
        payload = {
            "household_id": household_id,
            "category_name": encrypt_data(category_name),
            "sub_category_name": encrypt_data(sub_category_name) if sub_category_name else None,
            "is_active": True,
            "is_personal": is_personal,
            "username": username,
            "target_budget": encrypt_data(safe_target)
        }
        supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error inserting category: {e}")
        return False

def insert_household_income(
    household_id,
    month_year,
    source_name,
    take_home,
    gross,
    is_taxable,
    owner_username,
    is_windfall,
    pay_frequency=None,
    is_personal_income=False,
    payment_date=None,
    *,
    is_recurring=None,
):
    """Inserts a new income stream, safely handling empty (None) values."""
    if is_personal_income:
        if not _is_budget_privileged() and owner_username != st.session_state.get("username"):
            return False
    elif not _is_budget_privileged():
        return False

    if pay_frequency:
        freq = normalize_income_pay_frequency(pay_frequency)
    elif is_recurring is not None:
        freq = normalize_income_pay_frequency("monthly" if is_recurring else "one_time")
    else:
        freq = "monthly"

    target_table = get_budget_table("household_incomes")
    try:
        # 🟢 SAFETY NET: Convert None to 0.0 before trying to make it a float
        safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
        safe_gross = float(gross) if gross not in [None, ""] else 0.0
        if payment_date is None:
            payment_date = date.today()
        elif isinstance(payment_date, str):
            payment_date = datetime.strptime(payment_date, "%Y-%m-%d").date()
        
        payload = {
            "household_id": household_id,
            "month_year": month_year,
            "source_name": encrypt_data(source_name),
            "take_home_amount": encrypt_data(safe_take_home),
            "gross_amount": encrypt_data(safe_gross),
            "is_taxable": is_taxable,
            "owner_username": owner_username,
            "is_windfall": is_windfall,
            "is_recurring": income_is_recurring_frequency(freq),
            "pay_frequency": freq,
            "is_personal_income": is_personal_income,
            "payment_date": payment_date.isoformat(),
        }
        supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error inserting income: {e}")
        return False

def delete_budget_category(category_id):
    """Soft-deletes a category by setting is_active to False."""
    if is_system_project_expense_category_id(category_id):
        return False
    if is_allowance_subcategory_id(category_id):
        return False
    if not _can_edit_category_server_side(category_id):
        return False
    target_table = get_budget_table("budget_categories")
    try:
        # Instead of .delete(), we update the status to keep the record for history
        supabase.table(target_table).update({"is_active": False}).eq("id", category_id).execute()
        return True
    except Exception as e:
        print(f"Error deactivating category: {e}")
        return False

def delete_household_income(income_id):
    """Deletes an income stream from the database."""
    if _household_income_is_allowance_linked(income_id):
        return False
    if not _can_edit_household_income_server_side(income_id):
        return False
    target_table = get_budget_table("household_incomes")
    try:
        supabase.table(target_table).delete().eq("id", income_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting income: {e}")
        return False

def update_household_income(
    income_id,
    source_name,
    take_home,
    gross,
    is_taxable,
    owner_username,
    is_windfall,
    pay_frequency=None,
    payment_date=None,
    *,
    is_recurring=None,
):
    """Updates an existing income stream."""
    if _household_income_is_allowance_linked(income_id):
        return False
    if not _can_edit_household_income_server_side(income_id):
        return False
    if pay_frequency:
        freq = normalize_income_pay_frequency(pay_frequency)
    elif is_recurring is not None:
        freq = normalize_income_pay_frequency("monthly" if is_recurring else "one_time")
    else:
        freq = "monthly"

    target_table = get_budget_table("household_incomes")
    try:
        safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
        safe_gross = float(gross) if gross not in [None, ""] else 0.0
        payload = {
            "source_name": encrypt_data(source_name),
            "take_home_amount": encrypt_data(safe_take_home),
            "gross_amount": encrypt_data(safe_gross),
            "is_taxable": is_taxable,
            "owner_username": owner_username,
            "is_windfall": is_windfall,
            "is_recurring": income_is_recurring_frequency(freq),
            "pay_frequency": freq,
        }
        if payment_date is not None:
            if isinstance(payment_date, str):
                payment_date = datetime.strptime(payment_date, "%Y-%m-%d").date()
            payload["payment_date"] = payment_date.isoformat()
        supabase.table(target_table).update(payload).eq("id", income_id).execute()
        return True
    except Exception as e:
        print(f"Error updating income: {e}")
        return False

def get_individual_expenses(household_id, auth_user_id, month_year):
    """Fetches and decrypts expenses specifically for the logged-in individual."""
    target_table = get_budget_table("expenses")
    try:
        response = supabase.table(target_table).select("*").eq("household_id", household_id).eq("auth_user_id", auth_user_id).eq("month_year", month_year).execute()
        if response.data:
            for row in response.data:
                row["amount"] = decrypt_float(row.get("amount"))
                row["details"] = decrypt_text(row.get("details"))
            return pd.DataFrame(response.data)
        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching individual expenses: {e}")
        return pd.DataFrame()

def update_user_privacy_toggle(household_id, username, share_with_admin):
    """Updates the user's finance settings to hide/show their data in the Master Rollup."""
    target_table = get_budget_table("user_finance_settings")
    try:
        # First, check if a settings row already exists for this user
        response = supabase.table(target_table).select("id").eq("household_id", household_id).eq("username", username).execute()
        
        if response.data:
            # Update existing row
            supabase.table(target_table).update({"share_budget_with_admin": share_with_admin}).eq("id", response.data[0]["id"]).execute()
        else:
            # Create new row
            payload = {
                "household_id": household_id,
                "username": username,
                "share_budget_with_admin": share_with_admin
            }
            supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error updating privacy toggle: {e}")
        return False

def initialize_default_categories(household_id):
    """
    Checks if a household has categories. If not, it iterates through the 
    DEFAULT_BUDGET_CATEGORIES, encrypts them, and injects them into the database.
    """
    target_table = get_budget_table("budget_categories")
    try:
        # 1. Check if they already have categories to prevent accidental duplication
        existing = supabase.table(target_table).select("id").eq("household_id", household_id).limit(1).execute()
        if existing.data:
            return True # Household is already initialized
            
        # 2. Build the encrypted payload batch
        payloads = []
        for cat in DEFAULT_BUDGET_CATEGORIES:
            payloads.append({
                "household_id": household_id,
                "category_name": encrypt_data(cat["name"]),
                "sub_category_name": encrypt_data(cat["sub"]) if cat["sub"] else None,
                "category_type": cat["type"],
                "is_active": True
            })
            
        # 3. Batch insert into Supabase
        if payloads:
            supabase.table(target_table).insert(payloads).execute()
            
        return True
    except Exception as e:
        print(f"Error initializing default categories: {e}")
        return False

# ==========================================
# 💰 BUDGET FUNCTIONS (Projects / Guardrails / Wish List)
# ==========================================

def _is_budget_privileged():
    role = st.session_state.get("user_role", "member")
    return role in ["admin", "developer"]


def _can_edit_monthly_budget_server_side():
    return _is_budget_privileged()


def _can_edit_projects_server_side():
    """Authoritative guard for project write operations."""
    if _is_budget_privileged():
        return True
    return bool(st.session_state.get("can_edit_projects", False))


def _fetch_expense_flags(expense_id):
    target_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(target_table)
            .select("is_personal_spend, auth_user_id, household_id, category_id, date_logged, month_year")
            .eq("id", expense_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception:
        return None


def _can_edit_expense_server_side(expense_id) -> bool:
    record = _fetch_expense_flags(expense_id)
    if not record:
        return False
    if record.get("household_id") != get_current_household_id():
        return False
    if record.get("is_personal_spend"):
        if _is_budget_privileged():
            return True
        return str(record.get("auth_user_id")) == str(st.session_state.get("auth_user_id"))
    return _can_edit_monthly_budget_server_side()


def _fetch_category_flags(category_id):
    target_table = get_budget_table("budget_categories")
    try:
        response = (
            supabase.table(target_table)
            .select("is_personal, username, household_id, category_name, sub_category_name")
            .eq("id", category_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception:
        return None


def is_system_project_expense_category_id(category_id) -> bool:
    record = _fetch_category_flags(category_id)
    if not record:
        return False
    if record.get("household_id") != get_current_household_id():
        return False
    category_name = decrypt_text(record.get("category_name"))
    sub_category_name = decrypt_text(record.get("sub_category_name"))
    return is_system_project_expense_category(category_name, sub_category_name)


def is_allowance_subcategory_id(category_id) -> bool:
    record = _fetch_category_flags(category_id)
    if not record:
        return False
    if record.get("household_id") != get_current_household_id():
        return False
    category_name = decrypt_text(record.get("category_name"))
    sub_category_name = decrypt_text(record.get("sub_category_name"))
    return is_allowance_subcategory(category_name, sub_category_name) and bool(record.get("username"))


def get_allowance_recipient_username(category_id):
    record = _fetch_category_flags(category_id)
    if not record:
        return None
    category_name = decrypt_text(record.get("category_name"))
    sub_category_name = decrypt_text(record.get("sub_category_name"))
    if not is_allowance_subcategory(category_name, sub_category_name):
        return None
    return record.get("username") or None


def _can_edit_category_server_side(category_id, *, is_personal=None, username=None) -> bool:
    record = _fetch_category_flags(category_id)
    if not record:
        return False
    if record.get("household_id") != get_current_household_id():
        return False
    is_personal_cat = bool(record.get("is_personal"))
    if is_personal_cat:
        if _is_budget_privileged():
            return True
        owner = record.get("username")
        return bool(owner) and owner == st.session_state.get("username")
    return _can_edit_monthly_budget_server_side()


def _can_edit_household_income_server_side(income_id) -> bool:
    target_table = get_budget_table("household_incomes")
    try:
        response = (
            supabase.table(target_table)
            .select("is_personal_income, owner_username, household_id")
            .eq("id", income_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return False
        record = response.data[0]
        if record.get("household_id") != get_current_household_id():
            return False
        if record.get("is_personal_income"):
            if _is_budget_privileged():
                return True
            return record.get("owner_username") == st.session_state.get("username")
        return _is_budget_privileged()
    except Exception:
        return False


def _household_income_is_allowance_linked(income_id) -> bool:
    target_table = get_budget_table("household_incomes")
    try:
        response = (
            supabase.table(target_table)
            .select("source_expense_id")
            .eq("id", income_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return False
        return response.data[0].get("source_expense_id") is not None
    except Exception:
        return False


def _insert_allowance_personal_income(
    *,
    household_id,
    expense_id,
    recipient_username,
    amount,
    payment_date,
    month_year,
):
    """Create personal income for an allowance household expense (internal sync)."""
    target_table = get_budget_table("household_incomes")
    if isinstance(payment_date, str):
        payment_date = datetime.strptime(payment_date[:10], "%Y-%m-%d").date()
    safe_amount = float(amount)
    payload = {
        "household_id": household_id,
        "month_year": month_year,
        "source_name": encrypt_data("Allowance"),
        "take_home_amount": encrypt_data(safe_amount),
        "gross_amount": encrypt_data(safe_amount),
        "is_taxable": False,
        "owner_username": recipient_username,
        "is_windfall": False,
        "is_recurring": False,
        "pay_frequency": "one_time",
        "is_personal_income": True,
        "payment_date": payment_date.isoformat(),
        "source_expense_id": str(expense_id) if expense_id is not None else None,
    }
    supabase.table(target_table).insert(payload).execute()


def _delete_allowance_income_for_expense(expense_id):
    target_table = get_budget_table("household_incomes")
    try:
        supabase.table(target_table).delete().eq("source_expense_id", str(expense_id)).execute()
    except Exception as e:
        print(f"Error deleting allowance income for expense {expense_id}: {e}")


def _update_allowance_income_for_expense(expense_id, amount, payment_date, month_year):
    target_table = get_budget_table("household_incomes")
    if isinstance(payment_date, str):
        payment_date = datetime.strptime(payment_date[:10], "%Y-%m-%d").date()
    safe_amount = float(amount)
    payload = {
        "take_home_amount": encrypt_data(safe_amount),
        "gross_amount": encrypt_data(safe_amount),
        "payment_date": payment_date.isoformat(),
        "month_year": month_year,
    }
    try:
        supabase.table(target_table).update(payload).eq("source_expense_id", str(expense_id)).execute()
    except Exception as e:
        print(f"Error updating allowance income for expense {expense_id}: {e}")

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

        if data:
            for row in data:
                row["item"] = decrypt_text(row.get("item"))
                row["description"] = decrypt_text(row.get("description"))
                row["vendors"] = decrypt_text(row.get("vendors"))
                row["notes"] = decrypt_text(row.get("notes"))
                row["est_low_cost"] = decrypt_float(row.get("est_low_cost"))
                row["est_high_cost"] = decrypt_float(row.get("est_high_cost"))
                row["actual_cost"] = decrypt_float(row.get("actual_cost"))

        return data
    except Exception as e:
        print(f"Error fetching project budgets: {e}")
        return []


def _resolve_project_expense_category_id(household_id):
    """Find the household budget category used for project-linked expenses."""
    categories_df = get_budget_categories(household_id, is_personal=False)
    if categories_df is None or categories_df.empty:
        return None
    for _, row in categories_df.iterrows():
        if is_system_project_expense_category(row.get("category_name"), row.get("sub_category_name")):
            return row.get("id")
    return None


def ensure_project_expense_category(household_id):
    """
    Ensure the household has a Projects budget category for project purchase logging.
    Creates one automatically if missing (including for households with other categories).
    Returns the category id, or None on failure.
    """
    existing_id = _resolve_project_expense_category_id(household_id)
    if existing_id:
        return existing_id

    target_table = get_budget_table("budget_categories")
    try:
        payload = {
            "household_id": household_id,
            "category_name": encrypt_data(PROJECT_EXPENSE_CATEGORY["name"]),
            "sub_category_name": encrypt_data(PROJECT_EXPENSE_CATEGORY["sub"]),
            "is_active": True,
            "is_personal": False,
            "target_budget": encrypt_data(0.0),
        }
        response = supabase.table(target_table).insert(payload).execute()
        if response.data:
            return response.data[0].get("id")
        return None
    except Exception as e:
        print(f"Error ensuring project expense category: {e}")
        return None


def ensure_allowance_categories(household_id):
    """
    Ensure each household member has an Allowance sub-category for payout logging.
    Idempotent — safe to call on every Financial Hub load.
    """
    if not household_id:
        return False

    target_table = get_budget_table("budget_categories")
    try:
        users = _fetch_household_users_cached(household_id) or []
        usernames = [u.get("username") for u in users if u.get("username")]
        if not usernames:
            return True

        categories_df = get_budget_categories(household_id, is_personal=False)
        existing_by_username = {}
        if categories_df is not None and not categories_df.empty:
            for _, row in categories_df.iterrows():
                if not is_allowance_subcategory(
                    row.get("category_name"), row.get("sub_category_name")
                ):
                    continue
                linked = row.get("username")
                if linked:
                    existing_by_username[linked] = row.get("id")

        for member_username in usernames:
            if member_username in existing_by_username:
                continue
            payload = {
                "household_id": household_id,
                "category_name": encrypt_data(ALLOWANCE_CATEGORY_NAME),
                "sub_category_name": encrypt_data(member_username),
                "is_active": True,
                "is_personal": False,
                "username": member_username,
                "target_budget": encrypt_data(0.0),
            }
            supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error ensuring allowance categories: {e}")
        return False


def add_project_purchase_expense(project_id, purchase_date, amount, product_or_service=None):
    """
    Adds a dated purchase to a project (actual_cost + audit note) and logs a matching
    household budget expense under the Projects category.
    """
    if not _can_edit_projects_server_side():
        return False

    try:
        house_id = get_current_household_id()
        auth_user_id = st.session_state.get("auth_user_id")
        username = st.session_state.get("username")
        safe_amount = float(amount)

        project_res = (
            supabase.table(PROJECT_BUDGETS_TABLE)
            .select("*")
            .eq("id", project_id)
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if not project_res.data:
            return False

        row = project_res.data[0]
        project_name = decrypt_text(row.get("item")) or "Project"
        current_actual = decrypt_float(row.get("actual_cost")) or 0.0
        new_actual = current_actual + safe_amount

        existing_notes = decrypt_text(row.get("notes")) or ""
        cleaned_notes = existing_notes.replace("[COMPLETED]", "").strip() if existing_notes else ""
        if isinstance(purchase_date, str):
            purchase_date = datetime.strptime(purchase_date[:10], "%Y-%m-%d").date()
        product_label = str(product_or_service or "").strip()
        audit_line = f"[{purchase_date.isoformat()}] Expense logged: ${safe_amount:,.2f}"
        if product_label:
            audit_line = f"{audit_line} — {product_label}"
        new_notes = f"{cleaned_notes}\n{audit_line}".strip() if cleaned_notes else audit_line

        update_payload = {
            "actual_cost": new_actual,
            "notes": new_notes,
        }
        if not update_project_budget_item(project_id, update_payload):
            return False

        category_id = ensure_project_expense_category(house_id)
        if not category_id:
            print("Could not resolve Projects budget category; project actual updated but expense not logged.")
            return True

        month_year = purchase_date.strftime("%Y-%m")
        if product_label:
            details = f"{project_name} — {product_label}"
        else:
            details = f"{project_name} — project purchase"
        expenses_table = get_budget_table("expenses")
        expense_payload = {
            "household_id": house_id,
            "auth_user_id": auth_user_id,
            "username": username,
            "month_year": month_year,
            "date_logged": purchase_date.isoformat(),
            "category_id": category_id,
            "amount": encrypt_data(safe_amount),
            "details": encrypt_data(details),
            "is_personal_spend": False,
            "is_recurring": False,
        }
        supabase.table(expenses_table).insert(expense_payload).execute()
        return True
    except Exception as e:
        print(f"Error adding project purchase expense: {e}")
        return False


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


def delete_project_budget_item(item_id: str) -> bool:
    """Permanently deletes a project budget record for the active household."""
    try:
        if not _can_edit_projects_server_side():
            return False
        house_id = get_current_household_id()
        supabase.table(PROJECT_BUDGETS_TABLE).delete().eq("id", item_id).eq("household_id", house_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting project budget item: {e}")
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
                            "can_view_wishlist_members, can_view_wishlist_admin, "
                            "can_view_home_solar, can_edit_home_solar, "
                            "can_view_home_security, can_edit_home_security, "
                            "can_view_home_garage, can_edit_home_garage, "
                            "can_view_home_logs, can_edit_home_logs"
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

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_household_users_cached(household_id):
    try:
        response = (
            supabase.table("users")
            .select(
                "auth_user_id, username, role, can_view_budget, "
                "can_view_projects, can_edit_projects, "
                "can_view_monthly_budget, can_edit_monthly_budget, "
                "can_view_wishlist_members, can_view_wishlist_admin, "
                "can_view_home_solar, can_edit_home_solar, "
                "can_view_home_security, can_edit_home_security, "
                "can_view_home_garage, can_edit_home_garage, "
                "can_view_home_logs, can_edit_home_logs"
            )
            .eq("household_id", household_id)
            .execute()
        )
        return response.data or []
    except Exception:
        response = (
            supabase.table("users")
            .select("auth_user_id, username, role, can_view_budget")
            .eq("household_id", household_id)
            .execute()
        )
        return response.data or []


def clear_household_users_cache():
    clear_fn = getattr(_fetch_household_users_cached, "clear", None)
    if callable(clear_fn):
        clear_fn()


def get_household_users_for_admin():
    """Fetches all users in the current household so admins can manage them."""
    try:
        house_id = get_current_household_id()
        return list(_fetch_household_users_cached(house_id))
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
            "can_view_home_solar",
            "can_edit_home_solar",
            "can_view_home_security",
            "can_edit_home_security",
            "can_view_home_garage",
            "can_edit_home_garage",
            "can_view_home_logs",
            "can_edit_home_logs",
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

        home_mgmt_pairs = [
            ("can_view_home_solar", "can_edit_home_solar"),
            ("can_view_home_security", "can_edit_home_security"),
            ("can_view_home_garage", "can_edit_home_garage"),
            ("can_view_home_logs", "can_edit_home_logs"),
        ]
        for view_key, edit_key in home_mgmt_pairs:
            if payload.get(edit_key) is True:
                payload[view_key] = True
            if payload.get(view_key) is False:
                payload[edit_key] = False

        # Keep legacy rollup for backwards compatibility.
        if "can_view_projects" in payload or "can_view_monthly_budget" in payload:
            projects_view = payload.get("can_view_projects")
            monthly_view = payload.get("can_view_monthly_budget")
            if projects_view is not None and monthly_view is not None:
                payload["can_view_budget"] = bool(projects_view or monthly_view)

        house_id = get_current_household_id()
        home_payload = {k: v for k, v in payload.items() if k in HOME_MGMT_PERMISSION_KEY_SET}
        base_payload = {k: v for k, v in payload.items() if k not in HOME_MGMT_PERMISSION_KEY_SET}

        if home_payload and not home_mgmt_permissions_available():
            if not base_payload:
                print(
                    "Home Management permissions require migration 015. "
                    "Apply migrations/015_add_home_management_permissions.sql in Supabase."
                )
                return False
            print(
                "Skipping Home Management permission updates until migration 015 is applied."
            )
            home_payload = {}

        for update_payload in (base_payload, home_payload):
            if not update_payload:
                continue
            (
                supabase
                .table("users")
                .update(update_payload)
                .eq("auth_user_id", auth_user_id)
                .eq("household_id", house_id)
                .execute()
            )
        clear_household_users_cache()
        clear_home_mgmt_permissions_cache()
        ensure_allowance_categories(house_id)
        return True
    except Exception as e:
        print(f"Error updating module permissions: {e}")
        return False
    
def delete_expense(expense_id):
    """Safely removes an expense from the ledger."""
    if not _can_edit_expense_server_side(expense_id):
        return False
    record = _fetch_expense_flags(expense_id)
    if record and record.get("category_id") and is_allowance_subcategory_id(record["category_id"]):
        _delete_allowance_income_for_expense(expense_id)
    target_table = get_budget_table("expenses")
    try:
        supabase.table(target_table).delete().eq("id", expense_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting expense: {e}")
        return False

def update_expense(expense_id, amount, details, is_recurring, date_logged=None):
    """Updates an existing expense amount, details, recurring status, and optionally the date logged."""
    if not _can_edit_expense_server_side(expense_id):
        return False
    record = _fetch_expense_flags(expense_id)
    target_table = get_budget_table("expenses")
    try:
        payload = {
            "amount": encrypt_data(float(amount)),
            "details": encrypt_data(details),
            "is_recurring": is_recurring,
        }
        if date_logged:
            payload["date_logged"] = date_logged.strftime("%Y-%m-%d")
            payload["month_year"] = date_logged.strftime("%Y-%m")
        supabase.table(target_table).update(payload).eq("id", expense_id).execute()
        if record and record.get("category_id") and is_allowance_subcategory_id(record["category_id"]):
            effective_date = date_logged
            if effective_date is None and record.get("date_logged"):
                effective_date = datetime.strptime(str(record["date_logged"])[:10], "%Y-%m-%d").date()
            month_year = (
                date_logged.strftime("%Y-%m")
                if date_logged
                else record.get("month_year")
            )
            if effective_date and month_year:
                _update_allowance_income_for_expense(
                    expense_id, amount, effective_date, month_year
                )
        return True
    except Exception as e:
        print(f"Error updating expense: {e}")
        return False
    
def update_budget_category(category_id, category_name, sub_category_name, target_budget):
    """Updates an existing category's names and target budget."""
    if is_system_project_expense_category_id(category_id):
        return False
    if is_allowance_subcategory_id(category_id):
        return False
    if not _can_edit_category_server_side(category_id):
        return False
    target_table = get_budget_table("budget_categories")
    try:
        safe_target = float(target_budget) if target_budget not in [None, ""] else 0.0
        payload = {
            "category_name": encrypt_data(category_name),
            "sub_category_name": encrypt_data(sub_category_name) if sub_category_name else None,
            "target_budget": encrypt_data(safe_target)
        }
        supabase.table(target_table).update(payload).eq("id", category_id).execute()
        return True
    except Exception as e:
        print(f"Error updating category: {e}")
        return False
    
def auto_rollover_recurring_expenses(household_id, selected_month):
    """Silently rolls over recurring expenses, keeping their exact day of the month, ONLY if the day has arrived."""
    target_table = get_budget_table("expenses")
    
    # 1. Calculate the exact previous month
    year, month = map(int, selected_month.split('-'))
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
    prev_month_str = f"{prev_year}-{prev_month:02d}"
    
    try:
        # 2. Fetch last month's recurring expenses
        prev_res = supabase.table(target_table).select("*").eq("household_id", household_id).eq("month_year", prev_month_str).eq("is_recurring", True).execute()
        
        if not prev_res.data:
            return False 
            
        # 3. Fetch this month's to check for duplicates
        curr_res = supabase.table(target_table).select("id, details, username, category_id").eq("household_id", household_id).eq("month_year", selected_month).eq("is_recurring", True).execute()
        
        existing_signatures = []
        if curr_res.data:
            for row in curr_res.data:
                decrypted_details = decrypt_text(row.get("details")) if row.get("details") else ""
                existing_signatures.append(f"{decrypted_details}_{row.get('username')}_{row.get('category_id')}")
                
        injected_any = False
        
        # 🟢 TIME GATE 1: Grab today's exact date
        today = datetime.now().date()
        
        for row in prev_res.data:
            details = decrypt_text(row.get("details")) if row.get("details") else ""
            username = row.get("username")
            category_id = row.get("category_id")
            
            signature = f"{details}_{username}_{category_id}"
            
            # If it hasn't been rolled over yet, evaluate it
            if signature not in existing_signatures:
                amount = decrypt_float(row.get("amount")) if row.get("amount") is not None else 0.0
                
                prev_date_str = row.get("date_logged")
                if prev_date_str:
                    prev_date = datetime.strptime(prev_date_str, "%Y-%m-%d").date()
                    target_day = prev_date.day
                else:
                    target_day = 1 
                    
                _, last_day_of_new_month = calendar.monthrange(year, month)
                new_day = min(target_day, last_day_of_new_month)
                
                new_date_logged = date(year, month, new_day)
                
                # 🟢 TIME GATE 2: Only inject if the target date is TODAY or in the PAST
                if new_date_logged <= today:
                    log_expense_and_check_project(
                        auth_user_id=row.get("auth_user_id"),
                        username=username,
                        household_id=household_id,
                        month_year=selected_month,
                        date_logged=new_date_logged,
                        category_id=category_id,
                        amount=amount,
                        details=details,
                        is_personal_spend=row.get("is_personal_spend", False),
                        is_recurring=True
                    )
                    injected_any = True
                
        return injected_any
    except Exception as e:
        print(f"Error rolling over expenses: {e}")
        return False


def auto_rollover_recurring_incomes(household_id, selected_month):
    """Rolls recurring income streams into a new month once their payment day arrives."""
    target_table = get_budget_table("household_incomes")

    year, month = map(int, selected_month.split("-"))
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
    prev_month_str = f"{prev_year}-{prev_month:02d}"

    try:
        rows_to_roll = []

        prev_res = (
            supabase.table(target_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("month_year", prev_month_str)
            .eq("is_recurring", True)
            .execute()
        )
        if prev_res.data:
            rows_to_roll.extend(prev_res.data)

        if school_year_active_month(month) and prev_month in (7, 8):
            source_month_str = school_year_rollover_source_month(year, month)
            if source_month_str != prev_month_str:
                school_res = (
                    supabase.table(target_table)
                    .select("*")
                    .eq("household_id", household_id)
                    .eq("month_year", source_month_str)
                    .eq("is_recurring", True)
                    .execute()
                )
                if school_res.data:
                    for row in school_res.data:
                        freq = normalize_income_pay_frequency(row.get("pay_frequency") or "monthly")
                        if freq == "school_year_monthly":
                            rows_to_roll.append(row)

        if not rows_to_roll:
            return False

        curr_res = (
            supabase.table(target_table)
            .select("id, source_name, owner_username, is_personal_income")
            .eq("household_id", household_id)
            .eq("month_year", selected_month)
            .eq("is_recurring", True)
            .execute()
        )

        existing_signatures = []
        if curr_res.data:
            for row in curr_res.data:
                source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
                existing_signatures.append(
                    f"{source}_{row.get('owner_username')}_{row.get('is_personal_income')}"
                )

        injected_any = False
        today = datetime.now().date()
        processed_signatures = set()

        for row in rows_to_roll:
            freq = normalize_income_pay_frequency(row.get("pay_frequency") or "monthly")
            if freq == "school_year_monthly" and not school_year_active_month(month):
                continue

            source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
            owner = row.get("owner_username")
            is_personal = row.get("is_personal_income", False)
            signature = f"{source}_{owner}_{is_personal}"
            if signature in existing_signatures or signature in processed_signatures:
                continue
            processed_signatures.add(signature)

            prev_date_str = row.get("payment_date")
            if prev_date_str:
                prev_date = datetime.strptime(str(prev_date_str)[:10], "%Y-%m-%d").date()
                target_day = prev_date.day
            else:
                target_day = 1

            _, last_day_of_new_month = calendar.monthrange(year, month)
            new_day = min(target_day, last_day_of_new_month)
            new_payment_date = date(year, month, new_day)

            if new_payment_date > today:
                continue

            payload = {
                "household_id": household_id,
                "month_year": selected_month,
                "source_name": row.get("source_name"),
                "take_home_amount": row.get("take_home_amount"),
                "gross_amount": row.get("gross_amount"),
                "is_taxable": row.get("is_taxable", True),
                "owner_username": owner,
                "is_windfall": row.get("is_windfall", False),
                "is_recurring": True,
                "pay_frequency": row.get("pay_frequency") or "monthly",
                "is_personal_income": is_personal,
                "payment_date": new_payment_date.isoformat(),
            }
            supabase.table(target_table).insert(payload).execute()
            injected_any = True

        return injected_any
    except Exception as e:
        print(f"Error rolling over incomes: {e}")
        return False

def get_recurring_schedule(household_id, month_year, is_personal=False):
    """Fetches recurring expenses to determine upcoming dates, filtered by scope."""
    target_table = get_budget_table("expenses")
    try:
        res = supabase.table(target_table).select("category_id, date_logged") \
            .eq("household_id", household_id) \
            .eq("month_year", month_year) \
            .eq("is_recurring", True) \
            .eq("is_personal_spend", is_personal).execute()
        
        schedule = {}
        for row in res.data:
            cat_id = row.get("category_id")
            date_str = row.get("date_logged")
            # Only add to schedule if it's a valid date string
            if date_str:
                day = datetime.strptime(date_str, "%Y-%m-%d").day
                schedule[cat_id] = day
        return schedule
    except Exception as e:
        print(f"Error fetching recurring schedule: {e}")
        return {}