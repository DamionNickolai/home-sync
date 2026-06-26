import streamlit as st
from supabase import create_client, Client
import pandas as pd
from security import encrypt_data, decrypt_text, decrypt_float
import calendar
import uuid
from datetime import datetime, date
from zoneinfo import ZoneInfo
from utils import calculate_next_version
from income_schedule import (
    income_is_sub_monthly_frequency,
    paycheck_occurrences_in_month,
    resolve_version_at_date,
)
from expense_schedule import (
    expense_is_sub_monthly_frequency,
    bill_occurrences_in_month,
)
from constants import (
    ALLOWANCE_CATEGORY_NAME,
    ALLOWANCE_INCOME_SOURCE_NAME,
    DEFAULT_BUDGET_CATEGORIES,
    PROJECT_EXPENSE_CATEGORY,
    allowance_recipient_username,
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


def _income_row_is_materialized_occurrence(row) -> bool:
    """Stream-linked sub-monthly rows are one ledger row per paycheck."""
    if not row.get("stream_id"):
        return False
    freq = _income_row_frequency(row)
    return income_is_sub_monthly_frequency(freq)


def income_amount_for_month_total(amount, pay_frequency, month_year=None, *, row=None) -> float:
    """Convert a ledger row into its contribution to monthly actual totals."""
    if row is not None and _income_row_is_materialized_occurrence(row):
        return float(amount or 0)
    return normalize_income_amount_for_month(amount, pay_frequency, month_year=month_year)


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


def monthly_amount_to_per_payment(
    monthly_amount,
    pay_frequency,
    *,
    month_year: str | None = None,
    versions: list[dict] | None = None,
) -> float:
    """Convert a monthly projected total into per-payment stream amount."""
    freq = normalize_expense_pay_frequency(pay_frequency)
    safe_monthly = float(monthly_amount or 0)
    if (
        month_year
        and versions
        and expense_is_sub_monthly_frequency(freq)
    ):
        occurrences = bill_occurrences_in_month(versions, month_year)
        if occurrences:
            return safe_monthly / len(occurrences)
    if freq == "weekly":
        return safe_monthly * 12 / 52
    if freq == "bi_weekly":
        return safe_monthly * 12 / 26
    if freq == "semi_monthly":
        return safe_monthly / 2
    if freq == "monthly":
        return safe_monthly
    if freq == "quarterly":
        return safe_monthly * 3
    if freq == "annually":
        return safe_monthly * 12
    return safe_monthly


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
        total += income_amount_for_month_total(
            row.get("take_home_amount"),
            freq,
            month_year=month_year,
            row=row,
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
        total += income_amount_for_month_total(
            row.get("gross_amount"),
            _income_row_frequency(row),
            month_year=month_year,
            row=row,
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
        total += income_amount_for_month_total(
            row.get("gross_amount"),
            _income_row_frequency(row),
            month_year=month_year,
            row=row,
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
        total += income_amount_for_month_total(
            row.get("gross_amount"),
            _income_row_frequency(row),
            month_year=month_year,
            row=row,
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
    counted_stream_annual: set[str] = set()
    for _, row in incomes_df.iterrows():
        freq = _income_row_frequency(row)
        stream_id = row.get("stream_id")
        if stream_id and (
            income_is_sub_monthly_frequency(freq) or freq == "monthly"
        ):
            stream_key = str(stream_id)
            if stream_key in counted_stream_annual:
                continue
            counted_stream_annual.add(stream_key)
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


def get_income_streams_table():
    return get_budget_table("household_income_streams")


def get_income_stream_versions_table():
    return get_budget_table("household_income_stream_versions")


def _parse_iso_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _month_year_from_date(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def _payment_date_in_month(payment_anchor_day: int, effective_from: date, month_year: str) -> date:
    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    day = min(max(int(payment_anchor_day or 1), 1), last_day)
    if effective_from.year == year and effective_from.month == month:
        day = min(max(effective_from.day, day), last_day)
    return date(year, month, day)


def _fetch_household_income_row(income_id):
    target_table = get_budget_table("household_incomes")
    response = (
        supabase.table(target_table)
        .select("*")
        .eq("id", income_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def _income_stream_signature(source_name, owner_username, is_personal_income) -> str:
    return f"{source_name}_{owner_username}_{is_personal_income}"


def ensure_income_stream_for_row(income_id) -> str | None:
    """Link a legacy monthly income row to a stream + version (idempotent)."""
    row = _fetch_household_income_row(income_id)
    if not row:
        return None
    if row.get("stream_id"):
        return str(row["stream_id"])

    if row.get("source_expense_id"):
        return None

    freq = normalize_income_pay_frequency(
        row.get("pay_frequency") or ("monthly" if row.get("is_recurring") else "one_time")
    )
    if freq == "one_time":
        return None

    payment_date = _parse_iso_date(row.get("payment_date")) or date.today()
    stream_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    streams_table = get_income_streams_table()
    versions_table = get_income_stream_versions_table()
    incomes_table = get_budget_table("household_incomes")

    supabase.table(streams_table).insert(
        {
            "id": stream_id,
            "household_id": row["household_id"],
            "owner_username": row.get("owner_username"),
            "is_personal_income": bool(row.get("is_personal_income", False)),
            "display_name": row.get("source_name"),
            "is_active": True,
        }
    ).execute()

    supabase.table(versions_table).insert(
        {
            "id": version_id,
            "stream_id": stream_id,
            "effective_from": payment_date.isoformat(),
            "take_home_amount": row.get("take_home_amount"),
            "gross_amount": row.get("gross_amount"),
            "is_taxable": bool(row.get("is_taxable", True)),
            "is_windfall": bool(row.get("is_windfall", False)),
            "pay_frequency": freq,
            "payment_anchor_day": payment_date.day,
        }
    ).execute()

    supabase.table(incomes_table).update(
        {"stream_id": stream_id, "version_id": version_id}
    ).eq("id", income_id).execute()
    return stream_id


def resolve_income_version(stream_id, as_of_date: date) -> dict | None:
    streams_table = get_income_streams_table()
    versions_table = get_income_stream_versions_table()

    stream_res = (
        supabase.table(streams_table)
        .select("id, is_active, ended_on")
        .eq("id", stream_id)
        .limit(1)
        .execute()
    )
    if not stream_res.data:
        return None
    stream = stream_res.data[0]
    if not stream.get("is_active", True):
        return None
    ended_on = _parse_iso_date(stream.get("ended_on"))
    if ended_on and as_of_date > ended_on:
        return None

    version_res = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .lte("effective_from", as_of_date.isoformat())
        .order("effective_from", desc=True)
        .limit(1)
        .execute()
    )
    return version_res.data[0] if version_res.data else None


def get_income_stream_versions(stream_id) -> list[dict]:
    versions_table = get_income_stream_versions_table()
    response = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .order("effective_from", desc=False)
        .execute()
    )
    rows = response.data or []
    for row in rows:
        row["take_home_amount"] = decrypt_float(row.get("take_home_amount"))
        row["gross_amount"] = decrypt_float(row.get("gross_amount"))
        if row.get("effective_from") and not isinstance(row.get("effective_from"), str):
            row["effective_from"] = row["effective_from"].isoformat()
    return rows


def _create_income_stream_and_version(
    *,
    household_id,
    owner_username,
    is_personal_income,
    display_name,
    take_home,
    gross,
    is_taxable,
    is_windfall,
    pay_frequency,
    effective_from: date,
):
    freq = normalize_income_pay_frequency(pay_frequency)
    stream_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
    safe_gross = float(gross) if gross not in [None, ""] else 0.0

    supabase.table(get_income_streams_table()).insert(
        {
            "id": stream_id,
            "household_id": household_id,
            "owner_username": owner_username,
            "is_personal_income": is_personal_income,
            "display_name": encrypt_data(display_name),
            "is_active": True,
        }
    ).execute()

    supabase.table(get_income_stream_versions_table()).insert(
        {
            "id": version_id,
            "stream_id": stream_id,
            "effective_from": effective_from.isoformat(),
            "take_home_amount": encrypt_data(safe_take_home),
            "gross_amount": encrypt_data(safe_gross),
            "is_taxable": is_taxable,
            "is_windfall": is_windfall,
            "pay_frequency": freq,
            "payment_anchor_day": effective_from.day,
        }
    ).execute()
    return stream_id, version_id


def _fetch_stream_versions_raw(stream_id) -> list[dict]:
    versions_table = get_income_stream_versions_table()
    response = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .order("effective_from", desc=False)
        .execute()
    )
    return response.data or []


def _uses_paycheck_occurrences(versions: list[dict]) -> bool:
    return any(
        income_is_sub_monthly_frequency(
            normalize_income_pay_frequency(version.get("pay_frequency") or "monthly")
        )
        for version in versions
    )


def _materialize_income_occurrence(
    *,
    stream: dict,
    version: dict,
    month_year: str,
    payment_date: date,
    household_id: str,
    existing: dict | None,
) -> bool:
    """Insert/update one paycheck ledger row using a pre-fetched existing row.

    Skips the write entirely when the row is locked or already reflects the
    current version (idempotent re-materialization).
    """
    today = datetime.now().date()
    if payment_date > today:
        return False
    if existing and existing.get("is_locked"):
        return False
    if existing and str(existing.get("version_id") or "") == str(version.get("id") or ""):
        return False

    incomes_table = get_budget_table("household_incomes")
    payment_date_str = payment_date.isoformat()
    freq = normalize_income_pay_frequency(version.get("pay_frequency") or "monthly")
    payload = {
        "household_id": household_id,
        "month_year": month_year,
        "source_name": stream.get("display_name"),
        "take_home_amount": version.get("take_home_amount"),
        "gross_amount": version.get("gross_amount"),
        "is_taxable": version.get("is_taxable", True),
        "owner_username": stream.get("owner_username"),
        "is_windfall": version.get("is_windfall", False),
        "is_recurring": income_is_recurring_frequency(freq),
        "pay_frequency": freq,
        "is_personal_income": stream.get("is_personal_income", False),
        "payment_date": payment_date_str,
        "stream_id": stream["id"],
        "version_id": version.get("id"),
    }

    if existing:
        supabase.table(incomes_table).update(payload).eq("id", existing["id"]).execute()
    else:
        supabase.table(incomes_table).insert(payload).execute()
    return True


def _cleanup_stale_income_occurrences(
    stream_id,
    month_year: str,
    household_id: str,
    expected_payment_dates: set[str],
) -> None:
    incomes_table = get_budget_table("household_incomes")
    response = (
        supabase.table(incomes_table)
        .select("id, payment_date, is_locked")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .execute()
    )
    for row in response.data or []:
        if row.get("is_locked"):
            continue
        payment_date = str(row.get("payment_date") or "")[:10]
        if payment_date not in expected_payment_dates:
            supabase.table(incomes_table).delete().eq("id", row["id"]).execute()


def _expected_income_occurrences(stream_id, versions, month_year):
    """Return list of (payment_date, version) expected in month_year for a stream.

    Pure/in-memory: resolves the governing version from the provided ``versions``
    list without additional database round-trips.
    """
    if _uses_paycheck_occurrences(versions):
        return [
            (occ["payment_date"], occ["version"])
            for occ in paycheck_occurrences_in_month(versions, month_year)
        ]

    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    version = resolve_version_at_date(versions, date(year, month, last_day))
    if not version:
        version = resolve_version_at_date(versions, date(year, month, 1))
    if not version:
        return []
    effective_from = _parse_iso_date(version.get("effective_from")) or date(year, month, 1)
    if _month_year_from_date(effective_from) > month_year:
        return []
    payment_date = _payment_date_in_month(
        version.get("payment_anchor_day") or 1,
        effective_from,
        month_year,
    )
    return [(payment_date, version)]


def materialize_income_month(stream_id, month_year, household_id=None) -> bool:
    """Create or update ledger rows for each paycheck in month_year.

    Idempotent and batched: fetches existing ledger rows for the month in a
    single query and only writes rows that are missing or out of date.
    """
    streams_table = get_income_streams_table()
    incomes_table = get_budget_table("household_incomes")

    stream_res = (
        supabase.table(streams_table)
        .select("*")
        .eq("id", stream_id)
        .limit(1)
        .execute()
    )
    if not stream_res.data:
        return False
    stream = stream_res.data[0]
    if not stream.get("is_active", True):
        return False
    house_id = household_id or stream.get("household_id")

    versions = _fetch_stream_versions_raw(stream_id)
    if not versions:
        return False

    expected = _expected_income_occurrences(stream_id, versions, month_year)

    # Single query: all existing ledger rows for this stream + month.
    existing_res = (
        supabase.table(incomes_table)
        .select("id, payment_date, version_id, is_locked")
        .eq("household_id", house_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .execute()
    )
    existing_by_date = {
        str(row.get("payment_date") or "")[:10]: row
        for row in (existing_res.data or [])
    }

    injected_any = False
    expected_dates: set[str] = set()
    for payment_date, version in expected:
        date_str = payment_date.isoformat()
        expected_dates.add(date_str)
        if _materialize_income_occurrence(
            stream=stream,
            version=version,
            month_year=month_year,
            payment_date=payment_date,
            household_id=house_id,
            existing=existing_by_date.get(date_str),
        ):
            injected_any = True

    # Cleanup stale rows using the already-fetched data (no extra query).
    for date_str, row in existing_by_date.items():
        if row.get("is_locked"):
            continue
        if date_str not in expected_dates:
            supabase.table(incomes_table).delete().eq("id", row["id"]).execute()

    return injected_any


def _upsert_income_stream_version(
    stream_id,
    effective_from: date,
    *,
    take_home,
    gross,
    is_taxable,
    is_windfall,
    pay_frequency,
) -> str:
    freq = normalize_income_pay_frequency(pay_frequency)
    safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
    safe_gross = float(gross) if gross not in [None, ""] else 0.0
    versions_table = get_income_stream_versions_table()
    effective_from_str = effective_from.isoformat()
    version_payload = {
        "take_home_amount": encrypt_data(safe_take_home),
        "gross_amount": encrypt_data(safe_gross),
        "is_taxable": is_taxable,
        "is_windfall": is_windfall,
        "pay_frequency": freq,
        "payment_anchor_day": effective_from.day,
    }
    existing_version = (
        supabase.table(versions_table)
        .select("id")
        .eq("stream_id", stream_id)
        .eq("effective_from", effective_from_str)
        .limit(1)
        .execute()
    )
    if existing_version.data:
        version_id = existing_version.data[0]["id"]
        supabase.table(versions_table).update(version_payload).eq("id", version_id).execute()
    else:
        version_id = str(uuid.uuid4())
        supabase.table(versions_table).insert(
            {
                "id": version_id,
                "stream_id": stream_id,
                "effective_from": effective_from_str,
                **version_payload,
            }
        ).execute()
    return version_id


def _rematerialize_stream_from_month(stream_id, from_month_year: str, household_id: str) -> None:
    """Re-apply versions to unlocked monthly rows at or after from_month_year."""
    incomes_table = get_budget_table("household_incomes")
    materialize_income_month(stream_id, from_month_year, household_id)
    response = (
        supabase.table(incomes_table)
        .select("month_year")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .gt("month_year", from_month_year)
        .execute()
    )
    months = sorted({row["month_year"] for row in (response.data or [])})
    for month_year in months:
        materialize_income_month(stream_id, month_year, household_id)


def schedule_income_change(
    income_id,
    effective_from,
    source_name,
    take_home,
    gross,
    is_taxable,
    owner_username,
    is_windfall,
    pay_frequency,
):
    """New version from effective_from; past monthly rows unchanged."""
    if not _can_edit_household_income_server_side(income_id):
        return False
    stream_id = ensure_income_stream_for_row(income_id)
    if not stream_id:
        return False

    if isinstance(effective_from, str):
        effective_from = datetime.strptime(effective_from[:10], "%Y-%m-%d").date()

    row = _fetch_household_income_row(income_id)
    if not row:
        return False
    household_id = row["household_id"]
    freq = normalize_income_pay_frequency(pay_frequency)
    safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
    safe_gross = float(gross) if gross not in [None, ""] else 0.0
    _upsert_income_stream_version(
        stream_id,
        effective_from,
        take_home=safe_take_home,
        gross=safe_gross,
        is_taxable=is_taxable,
        is_windfall=is_windfall,
        pay_frequency=freq,
    )

    supabase.table(get_income_streams_table()).update(
        {"display_name": encrypt_data(source_name), "owner_username": owner_username}
    ).eq("id", stream_id).execute()

    from_month = _month_year_from_date(effective_from)
    _rematerialize_stream_from_month(stream_id, from_month, household_id)
    return True


def end_income_stream(income_id, end_date=None) -> bool:
    """Stop future rollover; preserve all monthly ledger rows."""
    if not _can_edit_household_income_server_side(income_id):
        return False
    stream_id = ensure_income_stream_for_row(income_id)
    if not stream_id:
        return False
    if end_date is None:
        end_date = date.today()
    elif isinstance(end_date, str):
        end_date = datetime.strptime(end_date[:10], "%Y-%m-%d").date()

    supabase.table(get_income_streams_table()).update(
        {"is_active": False, "ended_on": end_date.isoformat()}
    ).eq("id", stream_id).execute()
    return True


def delete_household_income_month_only(income_id) -> bool:
    """Remove one monthly ledger row only (does not end the stream)."""
    return delete_household_income(income_id)


# ==========================================
# 💳 EXPENSE STREAMS (effective-from + bi-weekly)
# ==========================================

normalize_expense_pay_frequency = normalize_income_pay_frequency
expense_pay_frequency_label = income_pay_frequency_label
EXPENSE_PAY_FREQUENCY_LABELS = INCOME_PAY_FREQUENCY_LABELS
normalize_expense_amount_for_month = normalize_income_amount_for_month


def expense_is_recurring_frequency(pay_frequency) -> bool:
    return normalize_expense_pay_frequency(pay_frequency) != "one_time"


def get_expense_streams_table():
    return get_budget_table("household_expense_streams")


def get_expense_stream_versions_table():
    return get_budget_table("household_expense_stream_versions")


def _fetch_expense_row(expense_id):
    target_table = get_budget_table("expenses")
    response = (
        supabase.table(target_table)
        .select("*")
        .eq("id", expense_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def _expense_stream_signature(details, category_id, username, is_personal_spend) -> str:
    return f"{details}_{category_id}_{username}_{is_personal_spend}"


def ensure_expense_stream_for_row(expense_id) -> str | None:
    """Link a legacy expense row to a stream + version (idempotent)."""
    row = _fetch_expense_row(expense_id)
    if not row:
        return None
    if row.get("stream_id"):
        return str(row["stream_id"])

    freq = normalize_expense_pay_frequency(
        row.get("pay_frequency") or ("monthly" if row.get("is_recurring") else "one_time")
    )
    if freq == "one_time":
        return None

    date_logged = _parse_iso_date(row.get("date_logged")) or date.today()
    amount = decrypt_float(row.get("amount")) if row.get("amount") is not None else 0.0
    details = decrypt_text(row.get("details")) if row.get("details") else ""
    stream_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())

    supabase.table(get_expense_streams_table()).insert(
        {
            "id": stream_id,
            "household_id": row["household_id"],
            "category_id": row.get("category_id"),
            "auth_user_id": row.get("auth_user_id"),
            "username": row.get("username"),
            "is_personal_spend": bool(row.get("is_personal_spend", False)),
            "display_name": encrypt_data(details),
            "is_active": True,
        }
    ).execute()

    supabase.table(get_expense_stream_versions_table()).insert(
        {
            "id": version_id,
            "stream_id": stream_id,
            "effective_from": date_logged.isoformat(),
            "amount": encrypt_data(amount),
            "pay_frequency": freq,
            "payment_anchor_day": date_logged.day,
        }
    ).execute()

    expenses_table = get_budget_table("expenses")
    supabase.table(expenses_table).update(
        {"stream_id": stream_id, "version_id": version_id, "pay_frequency": freq}
    ).eq("id", expense_id).execute()
    return stream_id


def resolve_expense_version(stream_id, as_of_date: date) -> dict | None:
    streams_table = get_expense_streams_table()
    versions_table = get_expense_stream_versions_table()

    stream_res = (
        supabase.table(streams_table)
        .select("id, is_active, ended_on")
        .eq("id", stream_id)
        .limit(1)
        .execute()
    )
    if not stream_res.data:
        return None
    stream = stream_res.data[0]
    if not stream.get("is_active", True):
        return None
    ended_on = _parse_iso_date(stream.get("ended_on"))
    if ended_on and as_of_date > ended_on:
        return None

    version_res = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .lte("effective_from", as_of_date.isoformat())
        .order("effective_from", desc=True)
        .limit(1)
        .execute()
    )
    return version_res.data[0] if version_res.data else None


def get_expense_stream_versions(stream_id) -> list[dict]:
    versions_table = get_expense_stream_versions_table()
    response = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .order("effective_from", desc=False)
        .execute()
    )
    rows = response.data or []
    for row in rows:
        row["amount"] = decrypt_float(row.get("amount"))
        if row.get("effective_from") and not isinstance(row.get("effective_from"), str):
            row["effective_from"] = row["effective_from"].isoformat()
    return rows


def _expense_stream_active_in_month(stream: dict, month_year: str) -> bool:
    year, month = map(int, month_year.split("-"))
    month_start = date(year, month, 1)
    if not stream.get("is_active", True):
        return False
    ended_on = _parse_iso_date(stream.get("ended_on"))
    return not (ended_on and ended_on < month_start)


def _expense_stream_schedule_day(
    stream_id,
    versions: list[dict],
    month_year: str,
) -> int | None:
    if _uses_expense_bill_occurrences(versions):
        occurrences = bill_occurrences_in_month(versions, month_year)
        if occurrences:
            return occurrences[0]["date_logged"].day

    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    version = resolve_expense_version(stream_id, date(year, month, last_day))
    if version:
        return int(version.get("payment_anchor_day") or 1)
    return None


def project_expense_stream_for_month(
    stream_id,
    month_year: str,
    versions: list[dict] | None = None,
) -> float:
    """Estimated monthly projected cost for one expense stream."""
    streams_table = get_expense_streams_table()
    stream_res = (
        supabase.table(streams_table)
        .select("id, is_active, ended_on")
        .eq("id", stream_id)
        .limit(1)
        .execute()
    )
    if not stream_res.data:
        return 0.0
    stream = stream_res.data[0]
    if not _expense_stream_active_in_month(stream, month_year):
        return 0.0

    if versions is None:
        versions = get_expense_stream_versions(stream_id)
    if not versions:
        return 0.0

    first_effective = _parse_iso_date(versions[0].get("effective_from")) or date.today()
    if _month_year_from_date(first_effective) > month_year:
        return 0.0

    if _uses_expense_bill_occurrences(versions):
        total = 0.0
        for occurrence in bill_occurrences_in_month(versions, month_year):
            total += float(occurrence.get("version", {}).get("amount") or 0)
        return total

    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    version = resolve_expense_version(stream_id, date(year, month, last_day))
    if not version:
        return 0.0
    amount = decrypt_float(version.get("amount")) if version.get("amount") is not None else 0.0
    freq = normalize_expense_pay_frequency(version.get("pay_frequency") or "monthly")
    return normalize_expense_amount_for_month(amount, freq, month_year=month_year)


def get_expense_stream_projections(
    household_id,
    month_year: str,
    *,
    is_personal_spend: bool = False,
    username: str | None = None,
) -> tuple[dict, dict]:
    """Projected monthly cost and bill-day schedule per category from expense streams.

    Categories present in the returned projections dict should use stream totals
    instead of static category target_budget values.
    """
    streams_table = get_expense_streams_table()
    query = (
        supabase.table(streams_table)
        .select("id, category_id, is_active, ended_on")
        .eq("household_id", household_id)
        .eq("is_personal_spend", is_personal_spend)
        .eq("is_active", True)
    )
    if is_personal_spend and username:
        query = query.eq("username", username)

    response = query.execute()

    projections: dict = {}
    schedule: dict = {}

    for stream in response.data or []:
        if not _expense_stream_active_in_month(stream, month_year):
            continue
        cat_id = stream.get("category_id")
        if cat_id is None:
            continue

        stream_id = stream["id"]
        versions = get_expense_stream_versions(stream_id)
        if not versions:
            continue
        first_effective = _parse_iso_date(versions[0].get("effective_from")) or date.today()
        if _month_year_from_date(first_effective) > month_year:
            continue

        projected = project_expense_stream_for_month(stream_id, month_year, versions=versions)
        projections[cat_id] = projections.get(cat_id, 0.0) + projected

        bill_day = _expense_stream_schedule_day(stream_id, versions, month_year)
        if bill_day is not None:
            if cat_id not in schedule or bill_day < schedule[cat_id]:
                schedule[cat_id] = bill_day

    normalized_projections = {str(k): float(v) for k, v in projections.items()}
    normalized_schedule = {str(k): int(v) for k, v in schedule.items()}
    return normalized_projections, normalized_schedule


def sum_expense_stream_projections_for_months(
    household_id,
    month_years: list[str],
    *,
    is_personal_spend: bool = False,
    username: str | None = None,
) -> dict:
    """Sum stream-based projections across multiple months (annual / YTD reports)."""
    totals: dict = {}
    for month_year in month_years:
        projections, _ = get_expense_stream_projections(
            household_id,
            month_year,
            is_personal_spend=is_personal_spend,
            username=username,
        )
        for cat_id, amount in projections.items():
            totals[cat_id] = totals.get(cat_id, 0.0) + float(amount)
    return totals


def _fetch_category_scope(category_id) -> dict | None:
    target_table = get_budget_table("budget_categories")
    response = (
        supabase.table(target_table)
        .select("household_id, is_personal, username")
        .eq("id", category_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def _update_category_target_budget_only(category_id, monthly_target: float) -> bool:
    if is_system_project_expense_category_id(category_id):
        return False
    if is_allowance_subcategory_id(category_id):
        return False
    if not _can_edit_category_server_side(category_id):
        return False
    target_table = get_budget_table("budget_categories")
    try:
        safe_target = float(monthly_target) if monthly_target not in [None, ""] else 0.0
        supabase.table(target_table).update(
            {"target_budget": encrypt_data(safe_target)}
        ).eq("id", category_id).execute()
        return True
    except Exception as e:
        print(f"Error updating category target budget: {e}")
        return False


def _billing_anchor_from_stream_history(
    stream_id,
    household_id: str,
) -> tuple[int, date] | None:
    """Infer billing anchor from expense rows already logged for this stream."""
    from collections import Counter

    expenses_table = get_budget_table("expenses")
    response = (
        supabase.table(expenses_table)
        .select("date_logged")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .order("date_logged", desc=False)
        .limit(24)
        .execute()
    )
    dates = [
        _parse_iso_date(row.get("date_logged"))
        for row in (response.data or [])
        if row.get("date_logged")
    ]
    dates = [d for d in dates if d]
    if not dates:
        return None

    day_counts = Counter(d.day for d in dates)
    non_one = {day: count for day, count in day_counts.items() if day != 1}
    anchor_day = max(non_one or day_counts, key=(non_one or day_counts).get)

    anchor_date = next((d for d in dates if d.day == anchor_day), dates[0])
    return anchor_day, anchor_date


def sync_category_target_to_expense_streams(
    category_id,
    household_id: str,
    monthly_target: float,
) -> int:
    """Push a category monthly target into linked active expense streams."""
    streams_table = get_expense_streams_table()
    response = (
        supabase.table(streams_table)
        .select("id")
        .eq("household_id", household_id)
        .eq("category_id", category_id)
        .eq("is_active", True)
        .execute()
    )
    updated = 0
    from_month = _month_year_from_date(date.today())
    versions_table = get_expense_stream_versions_table()
    for row in response.data or []:
        stream_id = str(row["id"])
        versions = get_expense_stream_versions(stream_id)
        if not versions:
            continue

        expenses_table = get_budget_table("expenses")
        ledger_res = (
            supabase.table(expenses_table)
            .select("date_logged")
            .eq("household_id", household_id)
            .eq("stream_id", stream_id)
            .eq("month_year", from_month)
            .order("date_logged", desc=False)
            .execute()
        )
        ledger_dates = [
            _parse_iso_date(row.get("date_logged"))
            for row in (ledger_res.data or [])
            if row.get("date_logged")
        ]

        history_anchor = _billing_anchor_from_stream_history(stream_id, household_id)
        governing = None
        if history_anchor:
            anchor_day, _ = history_anchor
            for ver in versions:
                if int(ver.get("payment_anchor_day") or 0) == anchor_day:
                    governing = ver
                    break
        if governing is None and ledger_dates:
            anchor_day = ledger_dates[0].day
            for ver in versions:
                if int(ver.get("payment_anchor_day") or 0) == anchor_day:
                    governing = ver
                    break
            if governing is None:
                for ver in versions:
                    eff = _parse_iso_date(ver.get("effective_from"))
                    if eff and eff.day == anchor_day:
                        governing = ver
                        break
        if governing is None:
            non_month_start = [
                ver
                for ver in versions
                if (_parse_iso_date(ver.get("effective_from")) or date.today()).day != 1
            ]
            governing = non_month_start[0] if non_month_start else versions[0]

        freq = normalize_expense_pay_frequency(governing.get("pay_frequency") or "monthly")
        governing_id = governing["id"]

        if history_anchor:
            anchor_day, anchor_date = history_anchor
            current_anchor = int(governing.get("payment_anchor_day") or 0)
            current_eff = _parse_iso_date(governing.get("effective_from"))
            if current_anchor != anchor_day or (
                current_eff and current_eff.day == 1 and anchor_day != 1
            ):
                supabase.table(versions_table).update(
                    {
                        "payment_anchor_day": anchor_day,
                        "effective_from": anchor_date.isoformat(),
                    }
                ).eq("id", governing_id).execute()
                governing["payment_anchor_day"] = anchor_day
                governing["effective_from"] = anchor_date.isoformat()

        schedule_versions = [governing]
        occurrences = bill_occurrences_in_month(schedule_versions, from_month)
        if not occurrences:
            occurrences = bill_occurrences_in_month(versions, from_month)
        if not occurrences:
            continue

        per_payment = float(monthly_target) / len(occurrences)

        supabase.table(versions_table).update(
            {
                "amount": encrypt_data(per_payment),
                "pay_frequency": freq,
            }
        ).eq("id", governing_id).execute()

        for ver in versions:
            if ver["id"] == governing_id:
                continue
            eff = _parse_iso_date(ver.get("effective_from"))
            if eff and _month_year_from_date(eff) == from_month:
                supabase.table(versions_table).delete().eq("id", ver["id"]).execute()

        _rematerialize_expense_stream_from_month(stream_id, from_month, household_id)
        updated += 1
    if updated > 0:
        target_table = get_budget_table("budget_categories")
        supabase.table(target_table).update(
            {"target_budget": encrypt_data(float(monthly_target))}
        ).eq("id", category_id).execute()
    return updated


def sync_category_target_from_stream_monthly(
    category_id,
    household_id: str,
    month_year: str,
) -> bool:
    """Pull stream monthly projection back into category target_budget."""
    scope = _fetch_category_scope(category_id)
    if not scope:
        return False
    projections, _ = get_expense_stream_projections(
        household_id,
        month_year,
        is_personal_spend=bool(scope.get("is_personal", False)),
        username=scope.get("username"),
    )
    monthly = projections.get(str(category_id))
    if monthly is None:
        return False
    ok = _update_category_target_budget_only(category_id, monthly)
    return ok


def _upsert_expense_stream_version(
    stream_id,
    effective_from: date,
    *,
    amount,
    pay_frequency,
) -> str:
    freq = normalize_expense_pay_frequency(pay_frequency)
    safe_amount = float(amount) if amount not in [None, ""] else 0.0
    versions_table = get_expense_stream_versions_table()
    effective_from_str = effective_from.isoformat()
    version_payload = {
        "amount": encrypt_data(safe_amount),
        "pay_frequency": freq,
        "payment_anchor_day": effective_from.day,
    }
    existing_version = (
        supabase.table(versions_table)
        .select("id")
        .eq("stream_id", stream_id)
        .eq("effective_from", effective_from_str)
        .limit(1)
        .execute()
    )
    if existing_version.data:
        version_id = existing_version.data[0]["id"]
        supabase.table(versions_table).update(version_payload).eq("id", version_id).execute()
    else:
        version_id = str(uuid.uuid4())
        supabase.table(versions_table).insert(
            {
                "id": version_id,
                "stream_id": stream_id,
                "effective_from": effective_from_str,
                **version_payload,
            }
        ).execute()
    return version_id


def _create_expense_stream_and_version(
    *,
    household_id,
    category_id,
    auth_user_id,
    username,
    is_personal_spend,
    display_name,
    amount,
    pay_frequency,
    effective_from: date,
):
    freq = normalize_expense_pay_frequency(pay_frequency)
    stream_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    safe_amount = float(amount) if amount not in [None, ""] else 0.0

    supabase.table(get_expense_streams_table()).insert(
        {
            "id": stream_id,
            "household_id": household_id,
            "category_id": category_id,
            "auth_user_id": auth_user_id,
            "username": username,
            "is_personal_spend": is_personal_spend,
            "display_name": encrypt_data(display_name),
            "is_active": True,
        }
    ).execute()

    supabase.table(get_expense_stream_versions_table()).insert(
        {
            "id": version_id,
            "stream_id": stream_id,
            "effective_from": effective_from.isoformat(),
            "amount": encrypt_data(safe_amount),
            "pay_frequency": freq,
            "payment_anchor_day": effective_from.day,
        }
    ).execute()
    return stream_id, version_id


def _fetch_expense_stream_versions_raw(stream_id) -> list[dict]:
    versions_table = get_expense_stream_versions_table()
    response = (
        supabase.table(versions_table)
        .select("*")
        .eq("stream_id", stream_id)
        .order("effective_from", desc=False)
        .execute()
    )
    return response.data or []


def _uses_expense_bill_occurrences(versions: list[dict]) -> bool:
    return any(
        expense_is_sub_monthly_frequency(
            normalize_expense_pay_frequency(version.get("pay_frequency") or "monthly")
        )
        for version in versions
    )


def _materialize_expense_occurrence(
    *,
    stream: dict,
    version: dict,
    month_year: str,
    date_logged: date,
    household_id: str,
    existing: dict | None,
) -> str | None:
    """Insert/update one expense ledger row using a pre-fetched existing row.

    Skips the write when the row is locked or already reflects the current
    version (idempotent re-materialization).
    """
    today = datetime.now().date()
    if date_logged > today:
        return None
    if existing and existing.get("is_locked"):
        return None
    if existing and str(existing.get("version_id") or "") == str(version.get("id") or ""):
        return None

    expenses_table = get_budget_table("expenses")
    date_str = date_logged.isoformat()
    freq = normalize_expense_pay_frequency(version.get("pay_frequency") or "monthly")
    payload = {
        "household_id": household_id,
        "auth_user_id": stream.get("auth_user_id"),
        "username": stream.get("username"),
        "month_year": month_year,
        "date_logged": date_str,
        "category_id": stream.get("category_id"),
        "amount": version.get("amount"),
        "details": stream.get("display_name"),
        "is_personal_spend": stream.get("is_personal_spend", False),
        "is_recurring": expense_is_recurring_frequency(freq),
        "pay_frequency": freq,
        "stream_id": stream["id"],
        "version_id": version.get("id"),
    }

    if existing:
        expense_id = existing["id"]
        supabase.table(expenses_table).update(payload).eq("id", expense_id).execute()
        return str(expense_id)

    response = supabase.table(expenses_table).insert(payload).execute()
    if response.data:
        return str(response.data[0].get("id"))
    return None


def _cleanup_stale_expense_occurrences(
    stream_id,
    month_year: str,
    household_id: str,
    expected_dates: set[str],
) -> None:
    expenses_table = get_budget_table("expenses")
    response = (
        supabase.table(expenses_table)
        .select("id, date_logged, is_locked")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .execute()
    )
    for row in response.data or []:
        if row.get("is_locked"):
            continue
        logged = str(row.get("date_logged") or "")[:10]
        if logged not in expected_dates:
            supabase.table(expenses_table).delete().eq("id", row["id"]).execute()


def materialize_expense_month(stream_id, month_year, household_id=None) -> bool:
    """Create or update expense ledger rows for each bill date in month_year."""
    streams_table = get_expense_streams_table()

    stream_res = (
        supabase.table(streams_table)
        .select("*")
        .eq("id", stream_id)
        .limit(1)
        .execute()
    )
    if not stream_res.data:
        return False
    stream = stream_res.data[0]
    if not stream.get("is_active", True):
        return False
    house_id = household_id or stream.get("household_id")

    versions = _fetch_expense_stream_versions_raw(stream_id)
    if not versions:
        return False

    expected = _expected_expense_occurrences(stream_id, versions, month_year)

    # Single query: all existing ledger rows for this stream + month.
    expenses_table = get_budget_table("expenses")
    existing_res = (
        supabase.table(expenses_table)
        .select("id, date_logged, version_id, is_locked")
        .eq("household_id", house_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .execute()
    )
    existing_by_date = {
        str(row.get("date_logged") or "")[:10]: row
        for row in (existing_res.data or [])
    }

    injected_any = False
    expected_dates: set[str] = set()
    for bill_date, version in expected:
        date_str = bill_date.isoformat()
        expected_dates.add(date_str)
        if _materialize_expense_occurrence(
            stream=stream,
            version=version,
            month_year=month_year,
            date_logged=bill_date,
            household_id=house_id,
            existing=existing_by_date.get(date_str),
        ):
            injected_any = True

    # Cleanup stale rows using the already-fetched data (no extra query).
    for date_str, row in existing_by_date.items():
        if row.get("is_locked"):
            continue
        if date_str not in expected_dates:
            supabase.table(expenses_table).delete().eq("id", row["id"]).execute()

    return injected_any


def _expected_expense_occurrences(stream_id, versions, month_year):
    """Return list of (bill_date, version) expected in month_year for a stream.

    Pure/in-memory: resolves the governing version from the provided ``versions``
    list without additional database round-trips.
    """
    if _uses_expense_bill_occurrences(versions):
        return [
            (occ["date_logged"], occ["version"])
            for occ in bill_occurrences_in_month(versions, month_year)
        ]

    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    version = resolve_version_at_date(versions, date(year, month, last_day))
    if not version:
        version = resolve_version_at_date(versions, date(year, month, 1))
    if not version:
        return []
    effective_from = _parse_iso_date(version.get("effective_from")) or date(year, month, 1)
    if _month_year_from_date(effective_from) > month_year:
        return []
    bill_date = _payment_date_in_month(
        version.get("payment_anchor_day") or 1,
        effective_from,
        month_year,
    )
    return [(bill_date, version)]


def _rematerialize_expense_stream_from_month(stream_id, from_month_year: str, household_id: str) -> None:
    expenses_table = get_budget_table("expenses")
    materialize_expense_month(stream_id, from_month_year, household_id)
    response = (
        supabase.table(expenses_table)
        .select("month_year")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .gt("month_year", from_month_year)
        .execute()
    )
    months = sorted({row["month_year"] for row in (response.data or [])})
    for month_year in months:
        materialize_expense_month(stream_id, month_year, household_id)


def schedule_expense_change(
    expense_id,
    effective_from,
    amount,
    details,
    pay_frequency,
    category_id=None,
):
    """New version from effective_from; past expense rows unchanged."""
    if not _can_edit_expense_server_side(expense_id):
        return False
    stream_id = ensure_expense_stream_for_row(expense_id)
    if not stream_id:
        return False

    if isinstance(effective_from, str):
        effective_from = datetime.strptime(effective_from[:10], "%Y-%m-%d").date()

    row = _fetch_expense_row(expense_id)
    if not row:
        return False
    household_id = row["household_id"]
    safe_amount = float(amount) if amount not in [None, ""] else 0.0

    _upsert_expense_stream_version(
        stream_id,
        effective_from,
        amount=safe_amount,
        pay_frequency=pay_frequency,
    )

    stream_update = {"display_name": encrypt_data(details)}
    if category_id is not None:
        stream_update["category_id"] = category_id
    supabase.table(get_expense_streams_table()).update(stream_update).eq("id", stream_id).execute()

    from_month = _month_year_from_date(effective_from)
    _rematerialize_expense_stream_from_month(stream_id, from_month, household_id)
    _sync_allowance_for_stream_month(stream_id, from_month, household_id)
    if row.get("category_id"):
        sync_category_target_from_stream_monthly(row["category_id"], household_id, from_month)
    return True


def end_expense_stream(expense_id, end_date=None) -> bool:
    """Stop future rollover; preserve ledger history."""
    if not _can_edit_expense_server_side(expense_id):
        return False
    stream_id = ensure_expense_stream_for_row(expense_id)
    if not stream_id:
        return False
    if end_date is None:
        end_date = date.today()
    elif isinstance(end_date, str):
        end_date = datetime.strptime(end_date[:10], "%Y-%m-%d").date()

    supabase.table(get_expense_streams_table()).update(
        {"is_active": False, "ended_on": end_date.isoformat()}
    ).eq("id", stream_id).execute()
    return True


def delete_expense_month_only(expense_id) -> bool:
    """Remove one expense ledger row only (does not end the stream)."""
    return delete_expense(expense_id)


def _sync_allowance_for_stream_month(stream_id, month_year, household_id) -> None:
    """Re-sync allowance personal income when a household allowance stream materializes."""
    expenses_table = get_budget_table("expenses")
    response = (
        supabase.table(expenses_table)
        .select(
            "id, category_id, amount, date_logged, month_year, is_personal_spend, "
            "is_recurring, pay_frequency"
        )
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .eq("is_personal_spend", False)
        .execute()
    )
    for row in response.data or []:
        category_id = row.get("category_id")
        if not category_id or not is_allowance_subcategory_id(
            category_id, household_id=household_id
        ):
            continue
        recipient = get_allowance_recipient_username(category_id)
        if not recipient:
            continue
        amount = decrypt_float(row.get("amount")) if row.get("amount") is not None else 0.0
        freq = _resolve_allowance_pay_frequency(
            expense_flags=row,
            is_recurring=bool(row.get("is_recurring")),
        )
        _sync_allowance_personal_income(
            household_id=household_id,
            expense_id=row.get("id"),
            recipient_username=recipient,
            amount=amount,
            payment_date=row.get("date_logged"),
            month_year=month_year,
            is_recurring=freq != "one_time",
            pay_frequency=freq,
        )


def _log_expense_via_stream(
    *,
    auth_user_id,
    username,
    household_id,
    month_year,
    date_logged,
    category_id,
    amount,
    details,
    is_personal_spend,
    pay_frequency,
) -> str | None:
    """Create stream + materialize; returns first expense id for the month."""
    freq = normalize_expense_pay_frequency(pay_frequency)
    if isinstance(date_logged, str):
        date_logged = datetime.strptime(date_logged[:10], "%Y-%m-%d").date()

    stream_id, _ = _create_expense_stream_and_version(
        household_id=household_id,
        category_id=category_id,
        auth_user_id=auth_user_id,
        username=username,
        is_personal_spend=is_personal_spend,
        display_name=details,
        amount=amount,
        pay_frequency=freq,
        effective_from=date_logged,
    )
    materialize_expense_month(stream_id, month_year, household_id)
    expenses_table = get_budget_table("expenses")
    response = (
        supabase.table(expenses_table)
        .select("id")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .order("date_logged", desc=False)
        .limit(1)
        .execute()
    )
    if response.data:
        expense_id = str(response.data[0]["id"])
    else:
        expense_id = None
    sync_category_target_from_stream_monthly(category_id, household_id, month_year)
    return expense_id


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
            row["pay_frequency"] = normalize_expense_pay_frequency(
                row.get("pay_frequency")
                or ("monthly" if row.get("is_recurring") else "one_time")
            )
            
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
            row["pay_frequency"] = normalize_expense_pay_frequency(
                row.get("pay_frequency")
                or ("monthly" if row.get("is_recurring") else "one_time")
            )

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


def log_expense_and_check_project(
    auth_user_id,
    username,
    household_id,
    month_year,
    date_logged,
    category_id,
    amount,
    details,
    is_personal_spend=False,
    is_recurring=False,
    pay_frequency=None,
):
    """Logs an expense; recurring bills use expense streams + materialized ledger rows."""
    if not is_personal_spend:
        if not _can_edit_monthly_budget_server_side():
            return False
    elif (
        str(auth_user_id) != str(st.session_state.get("auth_user_id"))
        and not _is_budget_privileged()
    ):
        return False

    if pay_frequency:
        freq = normalize_expense_pay_frequency(pay_frequency)
    elif is_recurring:
        freq = "monthly"
    else:
        freq = "one_time"

    try:
        expense_id = None
        if freq == "one_time":
            target_table = get_budget_table("expenses")
            payload = {
                "household_id": household_id,
                "auth_user_id": auth_user_id,
                "username": username,
                "month_year": month_year,
                "date_logged": date_logged.isoformat()
                if hasattr(date_logged, "isoformat")
                else str(date_logged)[:10],
                "category_id": category_id,
                "amount": encrypt_data(float(amount)),
                "details": encrypt_data(details),
                "is_personal_spend": is_personal_spend,
                "is_recurring": False,
                "pay_frequency": freq,
            }
            response = supabase.table(target_table).insert(payload).execute()
            if not response.data:
                return False
            expense_id = response.data[0].get("id")
        else:
            if isinstance(date_logged, str):
                date_logged = datetime.strptime(date_logged[:10], "%Y-%m-%d").date()
            expense_id = _log_expense_via_stream(
                auth_user_id=auth_user_id,
                username=username,
                household_id=household_id,
                month_year=month_year,
                date_logged=date_logged,
                category_id=category_id,
                amount=float(amount),
                details=details,
                is_personal_spend=is_personal_spend,
                pay_frequency=freq,
            )
            if not expense_id:
                return False

        if (
            expense_id
            and not is_personal_spend
            and is_allowance_subcategory_id(category_id, household_id=household_id)
        ):
            recipient = get_allowance_recipient_username(category_id)
            if recipient:
                expense_flags = _fetch_expense_flags(expense_id)
                stream_id = expense_flags.get("stream_id") if expense_flags else None
                if stream_id and freq != "one_time":
                    _sync_allowance_for_stream_month(stream_id, month_year, household_id)
                else:
                    _sync_allowance_personal_income(
                        household_id=household_id,
                        expense_id=expense_id,
                        recipient_username=recipient,
                        amount=float(amount),
                        payment_date=date_logged,
                        month_year=month_year,
                        is_recurring=freq != "one_time",
                        pay_frequency=freq,
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
        safe_take_home = float(take_home) if take_home not in [None, ""] else 0.0
        safe_gross = float(gross) if gross not in [None, ""] else 0.0
        if payment_date is None:
            payment_date = date.today()
        elif isinstance(payment_date, str):
            payment_date = datetime.strptime(payment_date, "%Y-%m-%d").date()

        if freq == "one_time":
            payload = {
                "household_id": household_id,
                "month_year": month_year,
                "source_name": encrypt_data(source_name),
                "take_home_amount": encrypt_data(safe_take_home),
                "gross_amount": encrypt_data(safe_gross),
                "is_taxable": is_taxable,
                "owner_username": owner_username,
                "is_windfall": is_windfall,
                "is_recurring": False,
                "pay_frequency": freq,
                "is_personal_income": is_personal_income,
                "payment_date": payment_date.isoformat(),
            }
            supabase.table(target_table).insert(payload).execute()
            return True

        stream_id, version_id = _create_income_stream_and_version(
            household_id=household_id,
            owner_username=owner_username,
            is_personal_income=is_personal_income,
            display_name=source_name,
            take_home=safe_take_home,
            gross=safe_gross,
            is_taxable=is_taxable,
            is_windfall=is_windfall,
            pay_frequency=freq,
            effective_from=payment_date,
        )
        return materialize_income_month(stream_id, month_year, household_id)
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
            .select(
                "is_personal_spend, auth_user_id, household_id, category_id, "
                "date_logged, month_year, is_recurring, pay_frequency, stream_id"
            )
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


def _try_session_household_id():
    try:
        return get_current_household_id()
    except ValueError:
        return None


def _allowance_recipient_from_record(record) -> str | None:
    if not record:
        return None
    return allowance_recipient_username(
        decrypt_text(record.get("category_name")),
        decrypt_text(record.get("sub_category_name")),
        username_field=record.get("username"),
    )


def is_allowance_subcategory_id(category_id, *, household_id=None) -> bool:
    record = _fetch_category_flags(category_id)
    if not record:
        return False
    expected_household = household_id or _try_session_household_id()
    if expected_household and record.get("household_id") != expected_household:
        return False
    return _allowance_recipient_from_record(record) is not None


def get_allowance_recipient_username(category_id):
    record = _fetch_category_flags(category_id)
    return _allowance_recipient_from_record(record)


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
    return _sync_allowance_personal_income(
        household_id=household_id,
        expense_id=expense_id,
        recipient_username=recipient_username,
        amount=amount,
        payment_date=payment_date,
        month_year=month_year,
    )


def _find_allowance_income_stream_id(household_id, owner_username) -> str | None:
    streams_table = get_income_streams_table()
    response = (
        supabase.table(streams_table)
        .select("id, display_name")
        .eq("household_id", household_id)
        .eq("owner_username", owner_username)
        .eq("is_personal_income", True)
        .eq("is_active", True)
        .execute()
    )
    for row in response.data or []:
        label = decrypt_text(row.get("display_name")) if row.get("display_name") else ""
        if label == ALLOWANCE_INCOME_SOURCE_NAME:
            return str(row["id"])
    return None


def _resolve_allowance_pay_frequency(*, expense_flags=None, pay_frequency=None, is_recurring=None) -> str:
    """Map a household allowance expense to the personal income pay frequency."""
    if pay_frequency:
        return normalize_income_pay_frequency(pay_frequency)
    if expense_flags:
        freq = expense_flags.get("pay_frequency")
        if freq:
            return normalize_income_pay_frequency(freq)
        if expense_flags.get("is_recurring"):
            return "monthly"
    if is_recurring:
        return "monthly"
    return "one_time"


def _ensure_allowance_income_stream(
    household_id,
    owner_username,
    amount,
    payment_date: date,
    pay_frequency="monthly",
) -> str:
    """Personal Allowance income stream mirroring the household expense schedule."""
    safe_amount = float(amount)
    freq = normalize_income_pay_frequency(pay_frequency)
    stream_id = _find_allowance_income_stream_id(household_id, owner_username)
    if stream_id:
        current_version = resolve_income_version(stream_id, payment_date)
        current_amount = (
            decrypt_float(current_version.get("take_home_amount"))
            if current_version
            else None
        )
        current_freq = (
            normalize_income_pay_frequency(current_version.get("pay_frequency") or "monthly")
            if current_version
            else None
        )
        amount_changed = (
            current_version is None
            or current_amount is None
            or abs(float(current_amount) - safe_amount) > 0.009
        )
        freq_changed = current_version is None or current_freq != freq
        if amount_changed or freq_changed:
            _upsert_income_stream_version(
                stream_id,
                payment_date,
                take_home=safe_amount,
                gross=safe_amount,
                is_taxable=False,
                is_windfall=False,
                pay_frequency=freq,
            )
            from_month = _month_year_from_date(payment_date)
            _rematerialize_stream_from_month(stream_id, from_month, household_id)
        return stream_id

    stream_id, _ = _create_income_stream_and_version(
        household_id=household_id,
        owner_username=owner_username,
        is_personal_income=True,
        display_name=ALLOWANCE_INCOME_SOURCE_NAME,
        take_home=safe_amount,
        gross=safe_amount,
        is_taxable=False,
        is_windfall=False,
        pay_frequency=freq,
        effective_from=payment_date,
    )
    return stream_id


def _link_allowance_expense_to_income_row(
    expense_id,
    household_id,
    stream_id,
    month_year,
    payment_date=None,
) -> None:
    """Attach household expense id to the matching materialized allowance income row."""
    incomes_table = get_budget_table("household_incomes")
    expense_key = str(expense_id) if expense_id is not None else None
    if not expense_key:
        return
    query = (
        supabase.table(incomes_table)
        .select("id")
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
    )
    if payment_date is not None:
        if isinstance(payment_date, str):
            payment_date_str = payment_date[:10]
        else:
            payment_date_str = payment_date.isoformat()
        query = query.eq("payment_date", payment_date_str)
    response = query.limit(1).execute()
    if response.data:
        supabase.table(incomes_table).update({"source_expense_id": expense_key}).eq(
            "id", response.data[0]["id"]
        ).execute()


def _sync_allowance_personal_income(
    *,
    household_id,
    expense_id,
    recipient_username,
    amount,
    payment_date,
    month_year,
    is_recurring=None,
    pay_frequency=None,
) -> bool:
    """Upsert personal income for a household allowance expense.

    One-time allowance → direct ledger row (one_time).
    Recurring allowance → income stream + materialized paycheck rows using the
    household expense pay frequency (monthly, bi-weekly, etc.).
    """
    expense_flags = _fetch_expense_flags(expense_id) if expense_id is not None else None
    if is_recurring is None:
        is_recurring = bool(expense_flags and expense_flags.get("is_recurring"))
    freq = _resolve_allowance_pay_frequency(
        expense_flags=expense_flags,
        pay_frequency=pay_frequency,
        is_recurring=is_recurring,
    )

    if isinstance(payment_date, str):
        payment_date = datetime.strptime(payment_date[:10], "%Y-%m-%d").date()

    if freq != "one_time":
        try:
            stream_id = _ensure_allowance_income_stream(
                household_id,
                recipient_username,
                amount,
                payment_date,
                pay_frequency=freq,
            )
            materialize_income_month(stream_id, month_year, household_id)
            _link_allowance_expense_to_income_row(
                expense_id,
                household_id,
                stream_id,
                month_year,
                payment_date=payment_date,
            )
            return True
        except Exception as e:
            print(f"Error syncing recurring allowance income for expense {expense_id}: {e}")
            return False

    target_table = get_budget_table("household_incomes")
    safe_amount = float(amount)
    expense_key = str(expense_id) if expense_id is not None else None
    payload = {
        "household_id": household_id,
        "month_year": month_year,
        "source_name": encrypt_data(ALLOWANCE_INCOME_SOURCE_NAME),
        "take_home_amount": encrypt_data(safe_amount),
        "gross_amount": encrypt_data(safe_amount),
        "is_taxable": False,
        "owner_username": recipient_username,
        "is_windfall": False,
        "is_recurring": False,
        "pay_frequency": "one_time",
        "is_personal_income": True,
        "payment_date": payment_date.isoformat(),
        "source_expense_id": expense_key,
        "stream_id": None,
        "version_id": None,
    }
    try:
        if expense_key:
            existing = (
                supabase.table(target_table)
                .select("id")
                .eq("source_expense_id", expense_key)
                .limit(1)
                .execute()
            )
            if existing.data:
                supabase.table(target_table).update(payload).eq("id", existing.data[0]["id"]).execute()
                return True
        supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error syncing allowance personal income for expense {expense_id}: {e}")
        return False


def _delete_allowance_income_for_expense(expense_id):
    target_table = get_budget_table("household_incomes")
    try:
        supabase.table(target_table).delete().eq("source_expense_id", str(expense_id)).execute()
    except Exception as e:
        print(f"Error deleting allowance income for expense {expense_id}: {e}")


def _update_allowance_income_for_expense(expense_id, amount, payment_date, month_year):
    record = _fetch_expense_flags(expense_id)
    if not record:
        return
    recipient = get_allowance_recipient_username(record.get("category_id"))
    if not recipient:
        return
    if isinstance(payment_date, str):
        payment_date = datetime.strptime(payment_date[:10], "%Y-%m-%d").date()
    freq = _resolve_allowance_pay_frequency(
        expense_flags=record,
        is_recurring=bool(record.get("is_recurring")),
    )
    stream_id = record.get("stream_id")
    household_id = record.get("household_id")
    if stream_id and freq != "one_time" and household_id:
        _sync_allowance_for_stream_month(stream_id, month_year, household_id)
        return
    _sync_allowance_personal_income(
        household_id=household_id,
        expense_id=expense_id,
        recipient_username=recipient,
        amount=float(amount),
        payment_date=payment_date,
        month_year=month_year,
        is_recurring=freq != "one_time",
        pay_frequency=freq,
    )

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


def allowance_categories_in_sync(household_id) -> bool:
    """True when every household member has an Allowance sub-category."""
    if not household_id:
        return True
    users = _fetch_household_users_cached(household_id) or []
    usernames = {u.get("username") for u in users if u.get("username")}
    if not usernames:
        return True

    categories_df = get_budget_categories(household_id, is_personal=False)
    if categories_df is None or categories_df.empty:
        return False

    existing: set[str] = set()
    for _, row in categories_df.iterrows():
        if not is_allowance_subcategory(
            row.get("category_name"), row.get("sub_category_name")
        ):
            continue
        linked = allowance_recipient_username(
            row.get("category_name"),
            row.get("sub_category_name"),
            username_field=row.get("username"),
        )
        if linked:
            existing.add(linked)
    return usernames <= existing


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
                linked = allowance_recipient_username(
                    row.get("category_name"),
                    row.get("sub_category_name"),
                    username_field=row.get("username"),
                )
                if linked and not row.get("username"):
                    supabase.table(target_table).update({"username": linked}).eq(
                        "id", row.get("id")
                    ).execute()
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

        repair_allowance_income_links(household_id)
        return True
    except Exception as e:
        print(f"Error ensuring allowance categories: {e}")
        return False


def repair_allowance_income_links(household_id) -> int:
    """Create or refresh personal income rows for allowance household expenses."""
    target_expenses = get_budget_table("expenses")
    try:
        response = (
            supabase.table(target_expenses)
            .select(
                "id, category_id, amount, date_logged, month_year, is_personal_spend, "
                "is_recurring, pay_frequency"
            )
            .eq("household_id", household_id)
            .eq("is_personal_spend", False)
            .execute()
        )
        expense_rows = response.data or []

        # Build an in-memory allowance category -> recipient map with a single
        # batched query, avoiding a per-expense-row category lookup (N+1).
        allowance_recipient_by_cat = {}
        cat_table = get_budget_table("budget_categories")
        cat_resp = (
            supabase.table(cat_table)
            .select("id, is_personal, username, household_id, category_name, sub_category_name")
            .eq("household_id", household_id)
            .execute()
        )
        for crow in (cat_resp.data or []):
            recipient = _allowance_recipient_from_record(crow)
            if recipient:
                allowance_recipient_by_cat[crow.get("id")] = recipient

        # Batch-fetch already-linked allowance income rows so we can skip the
        # expensive per-row sync (stream lookup + materialize) when an expense is
        # already correctly linked with a matching amount/month.
        incomes_table = get_budget_table("household_incomes")
        existing_resp = (
            supabase.table(incomes_table)
            .select("id, source_expense_id, take_home_amount, month_year, pay_frequency")
            .eq("household_id", household_id)
            .not_.is_("source_expense_id", "null")
            .execute()
        )
        existing_by_expense = {}
        for er in (existing_resp.data or []):
            sek = er.get("source_expense_id")
            if sek is not None:
                existing_by_expense[str(sek)] = er

        repaired = 0
        for row in expense_rows:
            category_id = row.get("category_id")
            recipient = allowance_recipient_by_cat.get(category_id) if category_id else None
            if not recipient:
                continue
            payment_date = row.get("date_logged")
            month_year = row.get("month_year")
            if not payment_date or not month_year:
                continue
            amount = decrypt_float(row.get("amount")) if row.get("amount") is not None else 0.0

            existing_income = existing_by_expense.get(str(row.get("id")))
            if existing_income is not None and existing_income.get("month_year") == month_year:
                existing_amount = (
                    decrypt_float(existing_income.get("take_home_amount"))
                    if existing_income.get("take_home_amount") is not None
                    else None
                )
                expected_freq = _resolve_allowance_pay_frequency(
                    expense_flags=row,
                    is_recurring=bool(row.get("is_recurring")),
                )
                existing_freq = _resolve_allowance_pay_frequency(
                    pay_frequency=existing_income.get("pay_frequency"),
                    is_recurring=bool(row.get("is_recurring")),
                )
                if (
                    existing_amount is not None
                    and abs(existing_amount - amount) <= 0.009
                    and existing_freq == expected_freq
                ):
                    # Already linked and matching — nothing to repair.
                    continue

            if _sync_allowance_personal_income(
                household_id=household_id,
                expense_id=row.get("id"),
                recipient_username=recipient,
                amount=amount,
                payment_date=payment_date,
                month_year=month_year,
                is_recurring=bool(row.get("is_recurring")),
                pay_frequency=row.get("pay_frequency"),
            ):
                repaired += 1
        return repaired
    except Exception as e:
        print(f"Error repairing allowance income links: {e}")
        return 0


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
    try:
        import streamlit as st

        household_id = st.session_state.get("household_id")
        if household_id:
            st.session_state.pop(f"allowance_categories_ready_{household_id}", None)
    except Exception:
        pass


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
    if (
        record
        and record.get("category_id")
        and is_allowance_subcategory_id(
            record["category_id"], household_id=record.get("household_id")
        )
    ):
        _delete_allowance_income_for_expense(expense_id)
    target_table = get_budget_table("expenses")
    try:
        supabase.table(target_table).delete().eq("id", expense_id).execute()
        return True
    except Exception as e:
        print(f"Error deleting expense: {e}")
        return False

def update_expense(
    expense_id,
    amount,
    details,
    is_recurring,
    date_logged=None,
    pay_frequency=None,
):
    """Updates one expense ledger row (this month only)."""
    if not _can_edit_expense_server_side(expense_id):
        return False
    record = _fetch_expense_flags(expense_id)
    target_table = get_budget_table("expenses")
    try:
        if pay_frequency:
            freq = normalize_expense_pay_frequency(pay_frequency)
        else:
            freq = "monthly" if is_recurring else "one_time"
        payload = {
            "amount": encrypt_data(float(amount)),
            "details": encrypt_data(details),
            "is_recurring": expense_is_recurring_frequency(freq),
            "pay_frequency": freq,
        }
        if date_logged:
            payload["date_logged"] = date_logged.strftime("%Y-%m-%d")
            payload["month_year"] = date_logged.strftime("%Y-%m")
        supabase.table(target_table).update(payload).eq("id", expense_id).execute()
        if (
            record
            and record.get("category_id")
            and is_allowance_subcategory_id(
                record["category_id"], household_id=record.get("household_id")
            )
        ):
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
        scope = _fetch_category_scope(category_id)
        if scope:
            sync_category_target_to_expense_streams(
                category_id,
                scope["household_id"],
                safe_target,
            )
        return True
    except Exception as e:
        print(f"Error updating category: {e}")
        return False
    
def _rollover_expense_streams(household_id, selected_month) -> bool:
    """Materialize active expense streams into selected_month.

    Batched: fetches all active streams, their versions, and the month's existing
    ledger rows in three queries total, then materializes in memory. Avoids the
    per-stream N+1 round-trips that made first-of-month views slow.
    """
    streams_table = get_expense_streams_table()
    versions_table = get_expense_stream_versions_table()
    expenses_table = get_budget_table("expenses")
    try:
        streams_res = (
            supabase.table(streams_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("is_active", True)
            .execute()
        )
        streams = streams_res.data or []
        if not streams:
            return False
        stream_ids = [str(s["id"]) for s in streams]

        versions_res = (
            supabase.table(versions_table)
            .select("*")
            .in_("stream_id", stream_ids)
            .order("effective_from", desc=False)
            .execute()
        )
        versions_by_stream: dict = {}
        for v in versions_res.data or []:
            versions_by_stream.setdefault(str(v["stream_id"]), []).append(v)

        existing_res = (
            supabase.table(expenses_table)
            .select("id, stream_id, date_logged, version_id, is_locked")
            .eq("household_id", household_id)
            .eq("month_year", selected_month)
            .in_("stream_id", stream_ids)
            .execute()
        )
        existing_by_stream: dict = {}
        for row in existing_res.data or []:
            existing_by_stream.setdefault(str(row["stream_id"]), {})[
                str(row.get("date_logged") or "")[:10]
            ] = row

        injected_any = False
        for stream in streams:
            sid = str(stream["id"])
            versions = versions_by_stream.get(sid)
            if not versions:
                continue
            expected = _expected_expense_occurrences(sid, versions, selected_month)
            existing_by_date = existing_by_stream.get(sid, {})
            expected_dates: set[str] = set()
            stream_changed = False
            for bill_date, version in expected:
                ds = bill_date.isoformat()
                expected_dates.add(ds)
                if _materialize_expense_occurrence(
                    stream=stream,
                    version=version,
                    month_year=selected_month,
                    date_logged=bill_date,
                    household_id=household_id,
                    existing=existing_by_date.get(ds),
                ):
                    stream_changed = True
            for ds, row in existing_by_date.items():
                if row.get("is_locked"):
                    continue
                if ds not in expected_dates:
                    supabase.table(expenses_table).delete().eq("id", row["id"]).execute()
                    stream_changed = True
            if stream_changed:
                injected_any = True
                _sync_allowance_for_stream_month(sid, selected_month, household_id)
        return injected_any
    except Exception as e:
        print(f"Error rolling over expense streams: {e}")
        return False


def _rollover_recurring_expenses_legacy(household_id, selected_month) -> bool:
    """Legacy rollover for recurring rows not yet linked to a stream."""
    target_table = get_budget_table("expenses")

    year, month = map(int, selected_month.split("-"))
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
    prev_month_str = f"{prev_year}-{prev_month:02d}"

    try:
        prev_res = (
            supabase.table(target_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("month_year", prev_month_str)
            .eq("is_recurring", True)
            .is_("stream_id", "null")
            .execute()
        )

        if not prev_res.data:
            return False

        curr_res = (
            supabase.table(target_table)
            .select("id, details, username, category_id, stream_id")
            .eq("household_id", household_id)
            .eq("month_year", selected_month)
            .eq("is_recurring", True)
            .execute()
        )

        existing_signatures = []
        if curr_res.data:
            for row in curr_res.data:
                if row.get("stream_id"):
                    continue
                decrypted_details = decrypt_text(row.get("details")) if row.get("details") else ""
                existing_signatures.append(
                    f"{decrypted_details}_{row.get('username')}_{row.get('category_id')}"
                )

        injected_any = False
        today = datetime.now().date()

        for row in prev_res.data:
            details = decrypt_text(row.get("details")) if row.get("details") else ""
            username = row.get("username")
            category_id = row.get("category_id")
            signature = f"{details}_{username}_{category_id}"

            if signature not in existing_signatures:
                amount = decrypt_float(row.get("amount")) if row.get("amount") is not None else 0.0
                prev_date_str = row.get("date_logged")
                if prev_date_str:
                    prev_date = datetime.strptime(str(prev_date_str)[:10], "%Y-%m-%d").date()
                    target_day = prev_date.day
                else:
                    target_day = 1

                _, last_day_of_new_month = calendar.monthrange(year, month)
                new_day = min(target_day, last_day_of_new_month)
                new_date_logged = date(year, month, new_day)

                if new_date_logged <= today:
                    freq = normalize_expense_pay_frequency(
                        row.get("pay_frequency") or "monthly"
                    )
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
                        is_recurring=True,
                        pay_frequency=freq,
                    )
                    injected_any = True

        return injected_any
    except Exception as e:
        print(f"Error rolling over legacy expenses: {e}")
        return False


def auto_rollover_recurring_expenses(household_id, selected_month):
    """Rolls recurring expenses via streams (preferred) or legacy copy."""
    stream_rolled = _rollover_expense_streams(household_id, selected_month)
    legacy_rolled = _rollover_recurring_expenses_legacy(household_id, selected_month)
    return stream_rolled or legacy_rolled


def _rollover_income_streams(household_id, selected_month) -> bool:
    """Materialize active income streams into selected_month from version history.

    Batched: fetches all active streams, their versions, and the month's existing
    ledger rows in three queries total, then materializes in memory. Avoids the
    per-stream N+1 round-trips that made first-of-month views slow.
    """
    streams_table = get_income_streams_table()
    versions_table = get_income_stream_versions_table()
    incomes_table = get_budget_table("household_incomes")
    try:
        streams_res = (
            supabase.table(streams_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("is_active", True)
            .execute()
        )
        streams = streams_res.data or []
        if not streams:
            return False
        stream_ids = [str(s["id"]) for s in streams]

        versions_res = (
            supabase.table(versions_table)
            .select("*")
            .in_("stream_id", stream_ids)
            .order("effective_from", desc=False)
            .execute()
        )
        versions_by_stream: dict = {}
        for v in versions_res.data or []:
            versions_by_stream.setdefault(str(v["stream_id"]), []).append(v)

        existing_res = (
            supabase.table(incomes_table)
            .select("id, stream_id, payment_date, version_id, is_locked")
            .eq("household_id", household_id)
            .eq("month_year", selected_month)
            .in_("stream_id", stream_ids)
            .execute()
        )
        existing_by_stream: dict = {}
        for row in existing_res.data or []:
            existing_by_stream.setdefault(str(row["stream_id"]), {})[
                str(row.get("payment_date") or "")[:10]
            ] = row

        injected_any = False
        for stream in streams:
            sid = str(stream["id"])
            versions = versions_by_stream.get(sid)
            if not versions:
                continue
            expected = _expected_income_occurrences(sid, versions, selected_month)
            existing_by_date = existing_by_stream.get(sid, {})
            expected_dates: set[str] = set()
            for payment_date, version in expected:
                ds = payment_date.isoformat()
                expected_dates.add(ds)
                if _materialize_income_occurrence(
                    stream=stream,
                    version=version,
                    month_year=selected_month,
                    payment_date=payment_date,
                    household_id=household_id,
                    existing=existing_by_date.get(ds),
                ):
                    injected_any = True
            for ds, row in existing_by_date.items():
                if row.get("is_locked"):
                    continue
                if ds not in expected_dates:
                    supabase.table(incomes_table).delete().eq("id", row["id"]).execute()
        return injected_any
    except Exception as e:
        print(f"Error rolling over income streams: {e}")
        return False


def _rollover_recurring_incomes_legacy(household_id, selected_month) -> bool:
    """Legacy rollover for monthly rows not yet linked to a stream."""
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
            .is_("stream_id", "null")
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
                    .is_("stream_id", "null")
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
            .select("id, source_name, owner_username, is_personal_income, stream_id")
            .eq("household_id", household_id)
            .eq("month_year", selected_month)
            .eq("is_recurring", True)
            .execute()
        )

        existing_signatures = []
        existing_stream_ids = set()
        if curr_res.data:
            for row in curr_res.data:
                if row.get("stream_id"):
                    existing_stream_ids.add(str(row["stream_id"]))
                    continue
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
        print(f"Error rolling over legacy incomes: {e}")
        return False


def auto_rollover_recurring_incomes(household_id, selected_month):
    """Rolls recurring income into a new month via streams (preferred) or legacy copy."""
    stream_rolled = _rollover_income_streams(household_id, selected_month)
    legacy_rolled = _rollover_recurring_incomes_legacy(household_id, selected_month)
    return stream_rolled or legacy_rolled


def get_recurring_schedule(household_id, month_year, is_personal=False, username=None):
    """Bill-day schedule per category from expense streams, with legacy fallback."""
    try:
        _, schedule = get_expense_stream_projections(
            household_id,
            month_year,
            is_personal_spend=is_personal,
            username=username,
        )
        if schedule:
            return schedule
    except Exception as e:
        print(f"Error fetching stream recurring schedule: {e}")

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