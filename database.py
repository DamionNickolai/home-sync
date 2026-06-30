import streamlit as st
from supabase import create_client, Client
import pandas as pd
import json
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
    OBLIGATION_SUPPORT_INCOME_SOURCE_NAME,
    RECEIPT_UNCATEGORIZED,
    TRANSFER_ALLOWANCE_EXPENSE_DETAILS,
    PROJECT_EXPENSE_CATEGORY,
    TAXES_EXPENSE_CATEGORY,
    allowance_recipient_username,
    is_allowance_category,
    is_allowance_subcategory,
    is_system_managed_allowance_category,
    is_system_project_expense_category,
    member_transfer_income_link_key,
    parse_member_transfer_income_link_key,
)
from household_obligations import (
    aggregate_member_obligations,
    build_assignment_maps,
    build_parent_summaries,
    compute_allowance_coverage,
    compute_supplement_gap,
    find_allowance_category_id,
    is_assignable_household_category,
    reconcile_displacement,
    resolve_obligation_lines,
)
from household_disbursements import (
    build_paycheck_disbursement_schedule,
    compute_member_bundled_amounts,
    compute_member_transfer_needs,
    compute_surplus_pool,
    compute_surplus_shares,
    disbursement_allowance_surplus_flags,
    disbursement_review_flags,
    filter_disbursement_eligible_usernames,
    sum_transfer_allowance_total,
    summarize_monthly_disbursement,
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


# Paycheck frequencies that count toward monthly obligation take-home (excludes
# annual/quarterly bonuses and one-time windfalls shown in Cash Flow separately).
OBLIGATION_REGULAR_PAY_FREQUENCIES = frozenset({
    "monthly",
    "school_year_monthly",
    "bi_weekly",
    "semi_monthly",
    "weekly",
})


def _freq_is_obligation_regular_pay(pay_frequency) -> bool:
    return normalize_income_pay_frequency(pay_frequency) in OBLIGATION_REGULAR_PAY_FREQUENCIES


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

_TRANSIENT_DB_ERROR_MARKERS = (
    "10054",
    "forcibly closed",
    "connection reset",
    "connection aborted",
    "remotedisconnected",
    "econnreset",
)


def _is_transient_db_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_DB_ERROR_MARKERS)


def _refresh_supabase_client() -> Client:
    """Drop cached client and open a fresh connection (stale keep-alive recovery)."""
    global supabase
    clear_fn = getattr(init_connection, "clear", None)
    if callable(clear_fn):
        clear_fn()
    supabase = init_connection()
    return supabase


def supabase_execute(builder, *, retries: int = 2):
    """Run a Supabase query; reconnect once on transient network drops."""
    global supabase
    last_exc = None
    for attempt in range(retries):
        try:
            return builder(supabase).execute()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1 and _is_transient_db_error(exc):
                supabase = _refresh_supabase_client()
                continue
            raise last_exc


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


def get_income_suppressions_table():
    return get_budget_table("income_occurrence_suppressions")


def _fetch_income_suppressions_for_month(household_id, month_year) -> dict[str, set[str]]:
    """Map stream_id -> suppressed payment_date strings for one month."""
    table = get_income_suppressions_table()
    try:
        response = (
            supabase.table(table)
            .select("stream_id, payment_date")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .execute()
        )
    except Exception as e:
        print(f"Error fetching income suppressions for {month_year}: {e}")
        return {}
    out: dict[str, set[str]] = {}
    for row in response.data or []:
        stream_id = str(row.get("stream_id") or "")
        payment_date = str(row.get("payment_date") or "")[:10]
        if stream_id and payment_date:
            out.setdefault(stream_id, set()).add(payment_date)
    return out


def _record_income_occurrence_suppression(
    *,
    household_id,
    stream_id,
    month_year,
    payment_date,
) -> None:
    """Remember that a stream paycheck was intentionally removed for this month."""
    date_str = str(payment_date or "")[:10]
    if not (household_id and stream_id and month_year and date_str):
        return
    table = get_income_suppressions_table()
    try:
        supabase.table(table).upsert(
            {
                "household_id": household_id,
                "stream_id": str(stream_id),
                "month_year": month_year,
                "payment_date": date_str,
            },
            on_conflict="household_id,stream_id,month_year,payment_date",
        ).execute()
    except Exception as e:
        print(f"Error recording income suppression ({stream_id}, {month_year}, {date_str}): {e}")


def _clear_income_suppressions_for_stream(stream_id) -> None:
    if not stream_id:
        return
    table = get_income_suppressions_table()
    try:
        supabase.table(table).delete().eq("stream_id", str(stream_id)).execute()
    except Exception as e:
        print(f"Error clearing income suppressions for stream {stream_id}: {e}")


def _payment_date_for_income_row(row, stream_id=None) -> str | None:
    payment_date = row.get("payment_date")
    if payment_date:
        if hasattr(payment_date, "isoformat"):
            return payment_date.isoformat()[:10]
        return str(payment_date)[:10]
    month_year = row.get("month_year")
    if stream_id and month_year:
        versions = _fetch_stream_versions_raw(stream_id)
        expected = _expected_income_occurrences(stream_id, versions, month_year)
        if expected:
            return expected[0][0].isoformat()
        return f"{month_year}-01"
    return None


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
    force: bool = False,
) -> bool:
    """Insert/update one paycheck ledger row using a pre-fetched existing row.

    Skips the write when the row is locked or already reflects the current
    version (idempotent re-materialization). ``force=True`` rewrites the row even
    when the version id is unchanged, which is required after an in-place version
    edit (same effective_from, new amount).
    """
    today = datetime.now().date()
    if payment_date > today:
        return False
    if existing and existing.get("is_locked"):
        return False
    if (
        not force
        and existing
        and str(existing.get("version_id") or "") == str(version.get("id") or "")
    ):
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


def materialize_income_month(stream_id, month_year, household_id=None, force=False) -> bool:
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
    suppressed_dates = _fetch_income_suppressions_for_month(house_id, month_year).get(str(stream_id), set())

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
        if date_str in suppressed_dates:
            continue
        if _materialize_income_occurrence(
            stream=stream,
            version=version,
            month_year=month_year,
            payment_date=payment_date,
            household_id=house_id,
            existing=existing_by_date.get(date_str),
            force=force,
        ):
            injected_any = True

    # Cleanup stale rows using the already-fetched data (no extra query).
    for date_str, row in existing_by_date.items():
        if row.get("is_locked"):
            continue
        if date_str in suppressed_dates:
            supabase.table(incomes_table).delete().eq("id", row["id"]).execute()
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


def _rematerialize_stream_from_month(stream_id, from_month_year: str, household_id: str, force: bool = False) -> None:
    """Re-apply versions to unlocked monthly rows at or after from_month_year."""
    incomes_table = get_budget_table("household_incomes")
    materialize_income_month(stream_id, from_month_year, household_id, force=force)
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
        materialize_income_month(stream_id, month_year, household_id, force=force)


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

    # "From effective date forward" means these terms govern everything on or
    # after effective_from. Remove stale later-dated versions left by previous
    # edits, otherwise they shadow this change for occurrences after their date.
    supabase.table(get_income_stream_versions_table()).delete().eq(
        "stream_id", stream_id
    ).gt("effective_from", effective_from.isoformat()).execute()

    # Editing a stream "forward" implies it should be live again: clear any
    # prior end-stream state so re-materialization is not refused as inactive.
    stream_update = {
        "display_name": encrypt_data(source_name),
        "owner_username": owner_username,
        "is_active": True,
        "ended_on": None,
    }
    supabase.table(get_income_streams_table()).update(stream_update).eq("id", stream_id).execute()

    from_month = _month_year_from_date(effective_from)
    _rematerialize_stream_from_month(stream_id, from_month, household_id, force=True)
    return True


def end_income_stream(income_id, end_date=None) -> bool:
    """Stop future rollover and remove the current/future unlocked occurrences.

    Past months (and any locked rows) are preserved as history; the selected
    income's month and everything after it are cleared so the recurring paycheck
    visibly stops in the ledger.
    """
    if not _can_edit_household_income_server_side(income_id):
        return False
    stream_id = ensure_income_stream_for_row(income_id)
    if not stream_id:
        return False
    if end_date is None:
        end_date = date.today()
    elif isinstance(end_date, str):
        end_date = datetime.strptime(end_date[:10], "%Y-%m-%d").date()

    row = _fetch_household_income_row(income_id)
    from_month = (row or {}).get("month_year") or end_date.strftime("%Y-%m")

    supabase.table(get_income_streams_table()).update(
        {"is_active": False, "ended_on": end_date.isoformat()}
    ).eq("id", stream_id).execute()
    _clear_income_suppressions_for_stream(stream_id)

    incomes_table = get_budget_table("household_incomes")
    occ = (
        supabase.table(incomes_table)
        .select("id, is_locked")
        .eq("stream_id", stream_id)
        .gte("month_year", from_month)
        .execute()
    )
    for occ_row in occ.data or []:
        if not occ_row.get("is_locked"):
            delete_household_income(occ_row["id"])
    return True


def delete_household_income_month_only(income_id) -> bool:
    """Remove one monthly ledger row only (does not end the stream).

    Records a suppression so auto-materialization does not recreate this paycheck.
    """
    if _household_income_is_allowance_linked(income_id):
        return False
    if not _can_edit_household_income_server_side(income_id):
        return False

    row = _fetch_household_income_row(income_id)
    if not row:
        return False

    stream_id = row.get("stream_id")
    if not stream_id:
        stream_id = ensure_income_stream_for_row(income_id)

    household_id = row.get("household_id")
    month_year = row.get("month_year")
    payment_date = _payment_date_for_income_row(row, stream_id=stream_id)

    if not delete_household_income(income_id):
        return False

    if stream_id and month_year and payment_date:
        _record_income_occurrence_suppression(
            household_id=household_id,
            stream_id=str(stream_id),
            month_year=month_year,
            payment_date=payment_date,
        )
    return True


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
    """Bill-day schedule per category from expense streams (for ledger due-date hints).

    The projections dict in the return tuple is legacy; category **Projected**
    columns use target_budget from category management only.
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
    """Deprecated: category target_budget is managed only via category edit UI."""
    return False


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
    force: bool = False,
) -> str | None:
    """Insert/update one expense ledger row using a pre-fetched existing row.

    Skips the write when the row is locked or already reflects the current
    version (idempotent re-materialization). ``force=True`` rewrites the row even
    when the version id is unchanged, which is required after an in-place version
    edit (same effective_from, new amount/details).
    """
    today = datetime.now().date()
    if date_logged > today:
        return None
    if existing and existing.get("is_locked"):
        return None
    if (
        not force
        and existing
        and str(existing.get("version_id") or "") == str(version.get("id") or "")
    ):
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


def materialize_expense_month(stream_id, month_year, household_id=None, force=False) -> bool:
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
            force=force,
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


def _rematerialize_expense_stream_from_month(stream_id, from_month_year: str, household_id: str, force: bool = False) -> None:
    expenses_table = get_budget_table("expenses")
    materialize_expense_month(stream_id, from_month_year, household_id, force=force)
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
        materialize_expense_month(stream_id, month_year, household_id, force=force)


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

    # "From effective date forward" means these terms govern everything on or
    # after effective_from. Remove stale later-dated versions left by previous
    # edits, otherwise they shadow this change for occurrences after their date.
    supabase.table(get_expense_stream_versions_table()).delete().eq(
        "stream_id", stream_id
    ).gt("effective_from", effective_from.isoformat()).execute()

    # Editing a stream "forward" implies it should be live again: clear any
    # prior end-stream state so re-materialization is not refused as inactive.
    stream_update = {
        "display_name": encrypt_data(details),
        "is_active": True,
        "ended_on": None,
    }
    if category_id is not None:
        stream_update["category_id"] = category_id
    supabase.table(get_expense_streams_table()).update(stream_update).eq("id", stream_id).execute()

    from_month = _month_year_from_date(effective_from)
    _rematerialize_expense_stream_from_month(stream_id, from_month, household_id, force=True)
    _sync_allowance_for_stream_month(stream_id, from_month, household_id)
    return True


def end_expense_stream(expense_id, end_date=None) -> bool:
    """Stop future rollover and remove the current/future unlocked occurrences.

    Past months (and any locked rows) are preserved as history; the selected
    expense's month and everything after it are cleared so the recurring charge
    visibly stops in the ledger.
    """
    if not _can_edit_expense_server_side(expense_id):
        return False
    stream_id = ensure_expense_stream_for_row(expense_id)
    if not stream_id:
        return False
    if end_date is None:
        end_date = date.today()
    elif isinstance(end_date, str):
        end_date = datetime.strptime(end_date[:10], "%Y-%m-%d").date()

    row = _fetch_expense_flags(expense_id)
    from_month = (row or {}).get("month_year") or end_date.strftime("%Y-%m")

    supabase.table(get_expense_streams_table()).update(
        {"is_active": False, "ended_on": end_date.isoformat()}
    ).eq("id", stream_id).execute()

    expenses_table = get_budget_table("expenses")
    occ = (
        supabase.table(expenses_table)
        .select("id, is_locked")
        .eq("stream_id", stream_id)
        .gte("month_year", from_month)
        .execute()
    )
    for occ_row in occ.data or []:
        if not occ_row.get("is_locked"):
            delete_expense(occ_row["id"])
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
            "is_recurring, pay_frequency, details"
        )
        .eq("household_id", household_id)
        .eq("stream_id", stream_id)
        .eq("month_year", month_year)
        .eq("is_personal_spend", False)
        .execute()
    )
    for row in response.data or []:
        if is_transfer_allowance_expense_record(row):
            continue
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
        return {"share_budget_with_admin": False, "default_view": "Household", "show_obligation_transfers_on_personal": False, "integrate_household_on_personal": False}
    except Exception as e:
        print(f"Error fetching finance settings: {e}")
        return {"share_budget_with_admin": False, "default_view": "Household", "show_obligation_transfers_on_personal": False, "integrate_household_on_personal": False}

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
        def build_query(client):
            query = (
                client.table(target_table)
                .select("*")
                .eq("household_id", household_id)
                .eq("is_active", True)
                .eq("is_personal", is_personal)
            )
            if is_personal and username:
                query = query.eq("username", username)
            return query

        response = supabase_execute(build_query)
        
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
                    
            df = pd.DataFrame(response.data)
            # When the new transfer-based allowance rows (stream_id IS NULL) exist
            # alongside old stream-materialized allowance rows (stream_id set), suppress
            # the stream-based ones so they don't double-count on the personal ledger.
            if is_personal_income and not df.empty and "stream_id" in df.columns:
                _dedup_source_names = [ALLOWANCE_INCOME_SOURCE_NAME, OBLIGATION_SUPPORT_INCOME_SOURCE_NAME]
                mask_these = df["source_name"].isin(_dedup_source_names)
                if mask_these.any():
                    for _src in _dedup_source_names:
                        src_mask = df["source_name"] == _src
                        has_transfer_row = (src_mask & df["stream_id"].isna()).any()
                        has_stream_row = (src_mask & df["stream_id"].notna()).any()
                        if has_transfer_row and has_stream_row:
                            # Keep only the transfer-based (new-system) rows; drop stream-based ones
                            df = df[~(src_mask & df["stream_id"].notna())]
                # Drop expense-mirror Allowance rows when transfer-path rows exist.
                # Never collapse multiple transfer-path rows (same-day paychecks).
                if ALLOWANCE_INCOME_SOURCE_NAME in df["source_name"].values and "source_expense_id" in df.columns:
                    allow_mask = df["source_name"] == ALLOWANCE_INCOME_SOURCE_NAME
                    allow_df = df[allow_mask].copy()
                    if not allow_df.empty:
                        allow_df["_pay"] = allow_df["payment_date"].astype(str).str[:10]
                        allow_df["_amt"] = allow_df["take_home_amount"].apply(
                            lambda v: round(float(v or 0), 2)
                        )
                        drop_idx: list = []
                        group_cols = ["owner_username", "_pay", "_amt"]
                        for _, grp in allow_df.groupby(group_cols, dropna=False):
                            if len(grp) <= 1:
                                continue
                            transfer_path = grp[
                                grp["source_expense_id"].isna()
                                | (grp["source_expense_id"].astype(str).str.strip() == "")
                            ]
                            if transfer_path.empty:
                                continue
                            mirrors = grp[
                                grp["source_expense_id"].notna()
                                & (grp["source_expense_id"].astype(str).str.strip() != "")
                            ]
                            drop_idx.extend(mirrors.index.tolist())
                        if drop_idx:
                            df = df.drop(drop_idx)
            return df
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
    project_id=None,
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
            if project_id and not is_personal_spend:
                payload["project_budget_id"] = str(project_id)
            response = supabase.table(target_table).insert(payload).execute()
            if not response.data:
                return False
            expense_id = response.data[0].get("id")
            if project_id and not is_personal_spend:
                if _increment_project_actual_from_purchase(
                    project_id,
                    float(amount),
                    date_logged,
                    product_or_service=details,
                ) is None:
                    return False
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
                "date_logged, month_year, is_recurring, pay_frequency, stream_id, "
                "project_budget_id"
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
    if is_transfer_allowance_expense_record(record):
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

    Skipped when disbursement member transfers own allowance for that month.
    """
    if _is_transfer_allowance_expense_id(expense_id):
        return False
    if _disbursement_transfers_cover_allowance(household_id, month_year, recipient_username):
        return False

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


def _fetch_household_expenses_for_aggregation(household_id, year=None):
    """Lightweight expense rows for project-fund and year-spend aggregation."""
    expenses_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(expenses_table)
            .select("amount, month_year, project_budget_id, category_id")
            .eq("household_id", household_id)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        print(f"Error fetching expenses for aggregation: {e}")
        return []

    if year is None:
        return rows

    year_prefix = f"{int(year)}-"
    return [
        row
        for row in rows
        if str(row.get("month_year") or "").startswith(year_prefix)
    ]


def _expense_counts_toward_project_pool(row, project_category_id):
    """True when a ledger row reduces the household project-fund pool for a year."""
    if row.get("project_budget_id"):
        return True
    if project_category_id is None:
        return False
    return str(row.get("category_id") or "") == str(project_category_id)


def sum_project_pool_expenses_for_year(household_id, year):
    """Sum project-linked ledger spend for a calendar year (fund pool deduction)."""
    project_category_id = _resolve_project_expense_category_id(household_id)
    total = 0.0
    for row in _fetch_household_expenses_for_aggregation(household_id, year):
        if not _expense_counts_toward_project_pool(row, project_category_id):
            continue
        total += decrypt_float(row.get("amount")) or 0.0
    return total


def get_project_expense_totals_for_year(household_id, year):
    """
    Returns {"pool_total": float, "by_project_id": {str: float}} for a calendar year.
    pool_total includes all project-category and project-linked expenses.
    by_project_id only includes rows with project_budget_id set.
    """
    project_category_id = _resolve_project_expense_category_id(household_id)
    pool_total = 0.0
    by_project_id: dict[str, float] = {}
    for row in _fetch_household_expenses_for_aggregation(household_id, year):
        if not _expense_counts_toward_project_pool(row, project_category_id):
            continue
        amount = decrypt_float(row.get("amount")) or 0.0
        pool_total += amount
        project_id = row.get("project_budget_id")
        if project_id:
            key = str(project_id)
            by_project_id[key] = by_project_id.get(key, 0.0) + amount
    return {"pool_total": pool_total, "by_project_id": by_project_id}


def _increment_project_actual_from_purchase(
    project_id,
    amount,
    purchase_date,
    *,
    product_or_service=None,
):
    """Update a project's lifetime actual_cost from a purchase."""
    house_id = get_current_household_id()
    project_res = (
        supabase.table(PROJECT_BUDGETS_TABLE)
        .select("*")
        .eq("id", project_id)
        .eq("household_id", house_id)
        .limit(1)
        .execute()
    )
    if not project_res.data:
        return None

    row = project_res.data[0]
    safe_amount = float(amount)
    current_actual = decrypt_float(row.get("actual_cost")) or 0.0
    new_actual = current_actual + safe_amount

    if not update_project_budget_item(
        project_id,
        {"actual_cost": new_actual},
    ):
        return None
    return decrypt_text(row.get("item")) or "Project"


def format_project_purchase_expense_line(purchase_date, amount, product_or_service=None) -> str:
    """Display line for a project purchase (matches legacy notes audit format)."""
    if isinstance(purchase_date, str):
        date_str = purchase_date[:10]
    elif hasattr(purchase_date, "isoformat"):
        date_str = purchase_date.isoformat()[:10]
    else:
        date_str = str(purchase_date)[:10]
    line = f"[{date_str}] Expense logged: ${float(amount):,.2f}"
    product_label = str(product_or_service or "").strip()
    if product_label:
        line = f"{line} — {product_label}"
    return line


def strip_expense_audit_lines_from_notes(notes_text) -> str:
    """Remove purchase audit lines from project notes (now shown on the Expenses row)."""
    if not notes_text:
        return ""
    kept = []
    for line in str(notes_text).splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "Expense logged:" in stripped:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def get_project_purchase_expenses(household_id, project_id) -> pd.DataFrame:
    """Ledger expense rows linked to a project budget item."""
    expenses_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(expenses_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("project_budget_id", str(project_id))
            .order("date_logged")
            .execute()
        )
        if not response.data:
            return pd.DataFrame()
        for row in response.data:
            row["amount"] = decrypt_float(row.get("amount"))
            row["details"] = decrypt_text(row.get("details"))
        return pd.DataFrame(response.data)
    except Exception as e:
        print(f"Error fetching project purchase expenses for {project_id}: {e}")
        return pd.DataFrame()


def _project_expense_product_from_details(details, project_name) -> str:
    details_text = str(details or "").strip()
    project_prefix = f"{project_name} — "
    if details_text.startswith(project_prefix):
        return details_text[len(project_prefix):].strip()
    if " — " in details_text:
        return details_text.split(" — ", 1)[1].strip()
    if details_text.endswith(" — project purchase"):
        return ""
    return details_text


def _reconcile_project_actual_cost(project_id) -> bool:
    """Set project actual_cost to the sum of linked ledger expenses."""
    house_id = get_current_household_id()
    expenses_df = get_project_purchase_expenses(house_id, project_id)
    total = float(expenses_df["amount"].sum()) if not expenses_df.empty else 0.0
    return update_project_budget_item(project_id, {"actual_cost": round(total, 2)})


def parse_project_purchase_expense_line(line: str) -> dict | None:
    """Parse a display/legacy audit line into structured purchase data."""
    text = (line or "").strip()
    if not text.startswith("[") or "Expense logged:" not in text:
        return None
    try:
        close_idx = text.index("]")
        date_str = text[1:close_idx]
        rest = text[close_idx + 1 :].strip()
        if not rest.startswith("Expense logged:"):
            return None
        rest = rest[len("Expense logged:") :].strip()
        product = ""
        if " — " in rest:
            amount_text, product = rest.split(" — ", 1)
            product = product.strip()
        else:
            amount_text = rest
        amount = float(str(amount_text).strip().lstrip("$").replace(",", ""))
        display = format_project_purchase_expense_line(date_str, amount, product)
        return {
            "date": date_str,
            "amount": amount,
            "product": product,
            "display": display,
        }
    except (ValueError, IndexError):
        return None


def get_project_purchase_expense_entries(
    household_id,
    project_id,
    legacy_notes=None,
) -> list[dict]:
    """Ledger purchases plus legacy note audit lines (deduped, chronological)."""
    entries: list[dict] = []
    seen_display: set[str] = set()

    expenses_df = get_project_purchase_expenses(household_id, project_id)
    project_name = ""
    if not expenses_df.empty:
        try:
            project_res = (
                supabase.table(PROJECT_BUDGETS_TABLE)
                .select("item")
                .eq("id", str(project_id))
                .eq("household_id", household_id)
                .limit(1)
                .execute()
            )
            if project_res.data:
                project_name = decrypt_text(project_res.data[0].get("item")) or ""
        except Exception:
            project_name = ""

    if not expenses_df.empty:
        for _, row in expenses_df.iterrows():
            date_str = str(row.get("date_logged") or "")[:10]
            amt = float(row.get("amount") or 0)
            product = _project_expense_product_from_details(row.get("details"), project_name)
            display = format_project_purchase_expense_line(date_str, amt, product)
            if display in seen_display:
                continue
            seen_display.add(display)
            entries.append(
                {
                    "date": date_str,
                    "amount": amt,
                    "product": product,
                    "display": display,
                    "is_legacy": False,
                    "expense_id": row.get("id"),
                }
            )

    if legacy_notes:
        for line in str(legacy_notes).splitlines():
            parsed = parse_project_purchase_expense_line(line)
            if not parsed or parsed["display"] in seen_display:
                continue
            seen_display.add(parsed["display"])
            entries.append(
                {
                    **parsed,
                    "is_legacy": True,
                    "expense_id": None,
                }
            )

    entries.sort(key=lambda entry: entry.get("date") or "")
    return entries


def get_project_purchase_expense_lines(household_id, project_id, legacy_notes=None) -> list[str]:
    """Purchase lines for a project from ledger rows plus legacy notes audit entries."""
    return [
        entry["display"]
        for entry in get_project_purchase_expense_entries(
            household_id, project_id, legacy_notes=legacy_notes
        )
    ]


def sum_project_purchase_expenses_for_year(entries: list[dict], year) -> float:
    """Sum purchase amounts whose date falls in the given calendar year."""
    year_prefix = f"{int(year)}-"
    return round(
        sum(
            float(entry.get("amount") or 0)
            for entry in entries
            if str(entry.get("date") or "").startswith(year_prefix)
        ),
        2,
    )


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


def _resolve_taxes_category_id(categories_df, *, name: str, sub: str) -> str | None:
    if categories_df is None or categories_df.empty:
        return None
    match = categories_df[
        (categories_df["category_name"] == name)
        & (categories_df["sub_category_name"].fillna("") == sub)
    ]
    if match.empty:
        return None
    return str(match.iloc[0]["id"])


def ensure_household_taxes_category(household_id: str) -> str | None:
    """Ensure HH shared Taxes / General exists; return category id."""
    cats = get_budget_categories(household_id, is_personal=False)
    existing = _resolve_taxes_category_id(
        cats,
        name=TAXES_EXPENSE_CATEGORY["name"],
        sub=TAXES_EXPENSE_CATEGORY["sub"],
    )
    if existing:
        return existing

    target_table = get_budget_table("budget_categories")
    try:
        payload = {
            "household_id": household_id,
            "category_name": encrypt_data(TAXES_EXPENSE_CATEGORY["name"]),
            "sub_category_name": encrypt_data(TAXES_EXPENSE_CATEGORY["sub"]),
            "category_type": "Variable Expense",
            "is_active": True,
            "is_personal": False,
            "target_budget": encrypt_data(0.0),
        }
        response = supabase.table(target_table).insert(payload).execute()
        if response.data:
            return str(response.data[0].get("id"))
    except Exception as e:
        print(f"Error ensuring household taxes category: {e}")

    cats = get_budget_categories(household_id, is_personal=False)
    return _resolve_taxes_category_id(
        cats,
        name=TAXES_EXPENSE_CATEGORY["name"],
        sub=TAXES_EXPENSE_CATEGORY["sub"],
    )


def ensure_personal_taxes_category(household_id: str, username: str) -> str | None:
    """Ensure personal Taxes / General exists for a member; return category id."""
    if not username:
        return None

    cats = get_budget_categories(household_id, is_personal=True, username=username)
    existing = _resolve_taxes_category_id(
        cats,
        name=TAXES_EXPENSE_CATEGORY["name"],
        sub=TAXES_EXPENSE_CATEGORY["sub"],
    )
    if existing:
        return existing

    ok = insert_budget_category(
        household_id,
        TAXES_EXPENSE_CATEGORY["name"],
        sub_category_name=TAXES_EXPENSE_CATEGORY["sub"],
        is_personal=True,
        username=username,
        target_budget=0.0,
    )
    if not ok:
        return None

    cats = get_budget_categories(household_id, is_personal=True, username=username)
    return _resolve_taxes_category_id(
        cats,
        name=TAXES_EXPENSE_CATEGORY["name"],
        sub=TAXES_EXPENSE_CATEGORY["sub"],
    )


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
                "is_recurring, pay_frequency, details"
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
            if is_transfer_allowance_expense_record(row):
                continue
            category_id = row.get("category_id")
            recipient = allowance_recipient_by_cat.get(category_id) if category_id else None
            if not recipient:
                continue
            payment_date = row.get("date_logged")
            month_year = row.get("month_year")
            if not payment_date or not month_year:
                continue
            if _disbursement_transfers_cover_allowance(household_id, month_year, recipient):
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

        project_name = _increment_project_actual_from_purchase(
            project_id,
            safe_amount,
            purchase_date,
            product_or_service=product_or_service,
        )
        if project_name is None:
            return False

        category_id = ensure_project_expense_category(house_id)
        if not category_id:
            print("Could not resolve Projects budget category; project actual updated but expense not logged.")
            return True

        if isinstance(purchase_date, str):
            purchase_date = datetime.strptime(purchase_date[:10], "%Y-%m-%d").date()
        month_year = purchase_date.strftime("%Y-%m")
        product_label = str(product_or_service or "").strip()
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
            "project_budget_id": str(project_id),
        }
        supabase.table(expenses_table).insert(expense_payload).execute()
        return True
    except Exception as e:
        print(f"Error adding project purchase expense: {e}")
        return False


def update_project_purchase_expense(
    expense_id,
    purchase_date,
    amount,
    product_or_service=None,
) -> bool:
    """Update a project-linked purchase and reconcile the project's actual_cost."""
    if not _can_edit_projects_server_side():
        return False
    expenses_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(expenses_table)
            .select("*")
            .eq("id", expense_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return False
        row = response.data[0]
        project_id = row.get("project_budget_id")
        if not project_id or str(row.get("household_id")) != str(get_current_household_id()):
            return False

        project_res = (
            supabase.table(PROJECT_BUDGETS_TABLE)
            .select("item")
            .eq("id", str(project_id))
            .eq("household_id", row.get("household_id"))
            .limit(1)
            .execute()
        )
        if not project_res.data:
            return False
        project_name = decrypt_text(project_res.data[0].get("item")) or "Project"

        if isinstance(purchase_date, str):
            purchase_date = datetime.strptime(purchase_date[:10], "%Y-%m-%d").date()
        safe_amount = float(amount)
        product_label = str(product_or_service or "").strip()
        details = (
            f"{project_name} — {product_label}"
            if product_label
            else f"{project_name} — project purchase"
        )
        supabase.table(expenses_table).update(
            {
                "amount": encrypt_data(safe_amount),
                "details": encrypt_data(details),
                "date_logged": purchase_date.isoformat(),
                "month_year": purchase_date.strftime("%Y-%m"),
                "is_recurring": False,
                "pay_frequency": "one_time",
            }
        ).eq("id", expense_id).execute()
        return _reconcile_project_actual_cost(str(project_id))
    except Exception as e:
        print(f"Error updating project purchase expense {expense_id}: {e}")
        return False


def delete_project_purchase_expense(expense_id) -> bool:
    """Delete a project-linked purchase and reconcile the project's actual_cost."""
    if not _can_edit_projects_server_side():
        return False
    expenses_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(expenses_table)
            .select("household_id, project_budget_id")
            .eq("id", expense_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return False
        row = response.data[0]
        project_id = row.get("project_budget_id")
        if not project_id or str(row.get("household_id")) != str(get_current_household_id()):
            return False
        supabase.table(expenses_table).delete().eq("id", expense_id).execute()
        return _reconcile_project_actual_cost(str(project_id))
    except Exception as e:
        print(f"Error deleting project purchase expense {expense_id}: {e}")
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
            .select(
                "household_id, projects_funds, projects_funds_opening, "
                "projects_funds_year, updated_at"
            )
            .eq("household_id", house_id)
            .limit(1)
            .execute()
        )
        if response.data:
            data = response.data[0]
            data["projects_funds"] = decrypt_float(data.get("projects_funds"))
            data["projects_funds_opening"] = decrypt_float(data.get("projects_funds_opening"))
            return data
        return {}
    except Exception as e:
        print(f"Error fetching household finance settings: {e}")
        return {}


_PROJECT_FUNDS_OPENING_UNSET = object()


def _upsert_household_projects_funds_row(
    *,
    projects_funds,
    projects_funds_year=None,
    projects_funds_opening=_PROJECT_FUNDS_OPENING_UNSET,
):
    """Internal upsert for project fund balances (rollover/backfill; no permission gate)."""
    try:
        house_id = get_current_household_id()
        payload = {
            "household_id": house_id,
            "projects_funds_year": projects_funds_year,
            "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        }
        if projects_funds is not None:
            payload["projects_funds"] = encrypt_data(projects_funds)
        else:
            payload["projects_funds"] = None
        if projects_funds_opening is not _PROJECT_FUNDS_OPENING_UNSET:
            if projects_funds_opening is not None:
                payload["projects_funds_opening"] = encrypt_data(projects_funds_opening)
            else:
                payload["projects_funds_opening"] = None
        (
            supabase
            .table(HOUSEHOLD_FINANCE_SETTINGS_TABLE)
            .upsert(payload, on_conflict="household_id")
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error upserting projects funds row: {e}")
        return False


def reconstruct_projects_funds_opening(working_balance, ytd_pool_spend):
    """
    Infer a Jan 1 opening snapshot mid-year: working balance plus YTD pool spend.
    Assumes no mid-year fund Add/Subtract adjustments since Jan 1.
    """
    return max(0.0, float(working_balance or 0.0) + float(ytd_pool_spend or 0.0))


def _projects_funds_opening_needs_backfill(saved_opening, working_balance, ytd_pool_spend):
    """True when opening was never captured (NULL/0) but the pool is in use."""
    working = float(working_balance or 0.0)
    spent = float(ytd_pool_spend or 0.0)
    if saved_opening is None:
        return working > 0 or spent > 0
    if float(saved_opening) == 0.0:
        return working > 0 or spent > 0
    return False


def apply_projects_funds_year_rollover(current_year: int) -> dict:
    """
    Carry forward unspent project funds into a new calendar year.
    Sets both opening snapshot and working balance to max(0, prior_remaining).
    Also backfills opening mid-year when the Jan 1 snapshot was never stored.
    """
    result = {
        "applied": False,
        "backfilled": False,
        "opening": None,
        "prior_year": None,
        "prior_spend": 0.0,
        "prior_remaining": None,
    }
    try:
        house_id = get_current_household_id()
        settings = get_household_finance_settings()
        saved_year = settings.get("projects_funds_year")
        saved_funds = settings.get("projects_funds")
        saved_opening = settings.get("projects_funds_opening")
        ytd_spend = sum_project_pool_expenses_for_year(house_id, current_year)

        if saved_year is None:
            if saved_funds is not None or ytd_spend > 0:
                opening_val = (
                    float(saved_opening)
                    if saved_opening is not None and float(saved_opening) != 0.0
                    else reconstruct_projects_funds_opening(saved_funds, ytd_spend)
                )
                if _upsert_household_projects_funds_row(
                    projects_funds=saved_funds,
                    projects_funds_opening=opening_val,
                    projects_funds_year=current_year,
                ):
                    result["backfilled"] = True
                    result["opening"] = opening_val
            return result

        if saved_year == current_year:
            if _projects_funds_opening_needs_backfill(saved_opening, saved_funds, ytd_spend):
                opening_val = reconstruct_projects_funds_opening(saved_funds, ytd_spend)
                if _upsert_household_projects_funds_row(
                    projects_funds=saved_funds,
                    projects_funds_opening=opening_val,
                    projects_funds_year=current_year,
                ):
                    result["backfilled"] = True
                    result["opening"] = opening_val
            return result

        prior_year = int(saved_year)
        prior_working = float(saved_funds or 0.0)
        prior_spend = sum_project_pool_expenses_for_year(house_id, prior_year)
        prior_remaining = prior_working - prior_spend
        new_balance = max(0.0, prior_remaining)

        if _upsert_household_projects_funds_row(
            projects_funds=new_balance,
            projects_funds_opening=new_balance,
            projects_funds_year=current_year,
        ):
            result.update(
                {
                    "applied": True,
                    "opening": new_balance,
                    "prior_year": prior_year,
                    "prior_spend": prior_spend,
                    "prior_remaining": prior_remaining,
                }
            )
        return result
    except Exception as e:
        print(f"Error applying projects funds year rollover: {e}")
        return result


def adjust_household_projects_funds(delta, projects_funds_year=None) -> bool:
    """Add or subtract from the household project fund balance."""
    try:
        if not _can_edit_projects_server_side():
            return False
        safe_delta = float(delta)
        if safe_delta == 0:
            return False

        settings = get_household_finance_settings()
        current = settings.get("projects_funds")
        current_val = float(current) if current is not None else 0.0
        new_val = current_val + safe_delta
        if new_val < 0:
            return False

        return update_household_projects_funds(new_val, projects_funds_year)
    except Exception as e:
        print(f"Error adjusting projects funds: {e}")
        return False


def update_household_projects_funds(
    projects_funds,
    projects_funds_year=None,
    *,
    projects_funds_opening=None,
    set_opening=False,
):
    """Encrypts and upserts projects_funds for the active household."""
    try:
        if not _can_edit_projects_server_side():
            return False
        opening_arg = (
            projects_funds_opening
            if set_opening
            else _PROJECT_FUNDS_OPENING_UNSET
        )
        return _upsert_household_projects_funds_row(
            projects_funds=projects_funds,
            projects_funds_year=projects_funds_year,
            projects_funds_opening=opening_arg,
        )
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
        and not is_transfer_allowance_expense_record(record)
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

        suppressions_by_stream = _fetch_income_suppressions_for_month(household_id, selected_month)
        injected_any = False
        for stream in streams:
            sid = str(stream["id"])
            versions = versions_by_stream.get(sid)
            if not versions:
                continue
            expected = _expected_income_occurrences(sid, versions, selected_month)
            existing_by_date = existing_by_stream.get(sid, {})
            suppressed_dates = suppressions_by_stream.get(sid, set())
            expected_dates: set[str] = set()
            for payment_date, version in expected:
                ds = payment_date.isoformat()
                expected_dates.add(ds)
                if ds in suppressed_dates:
                    continue
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
                if ds in suppressed_dates:
                    supabase.table(incomes_table).delete().eq("id", row["id"]).execute()
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


# ==========================================
# HOUSEHOLD OBLIGATION ASSIGNMENTS & SUPPLEMENTS
# ==========================================


def get_obligation_assignments_table():
    return get_budget_table("household_obligation_assignments")


def get_supplement_snapshots_table():
    return get_budget_table("household_supplement_snapshots")


def get_disbursement_settings_table():
    return get_budget_table("household_disbursement_settings")


def _decrypt_obligation_assignment(row: dict) -> dict:
    out = dict(row)
    if out.get("parent_category_name"):
        out["parent_category_name"] = decrypt_text(out["parent_category_name"])
    if out.get("label"):
        out["label"] = decrypt_text(out["label"])
    if out.get("category_id"):
        out["category_id"] = str(out["category_id"])
    return out


def get_obligation_assignments(household_id):
    if not household_id:
        return []
    try:
        response = (
            supabase.table(get_obligation_assignments_table())
            .select("*")
            .eq("household_id", household_id)
            .eq("is_active", True)
            .execute()
        )
        return [_decrypt_obligation_assignment(row) for row in (response.data or [])]
    except Exception as e:
        print(f"Error fetching obligation assignments: {e}")
        return []


def _deactivate_obligation_assignments(household_id, *, assignment_level, parent_category_name=None, category_id=None):
    table = get_obligation_assignments_table()
    query = (
        supabase.table(table)
        .update({"is_active": False, "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat()})
        .eq("household_id", household_id)
        .eq("assignment_level", assignment_level)
        .eq("is_active", True)
    )
    if parent_category_name is not None:
        query = query.eq("parent_category_name", encrypt_data(parent_category_name))
    if category_id is not None:
        query = query.eq("category_id", category_id)
    query.execute()


def upsert_parent_assignment(household_id, parent_category_name, member_username) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    parent = (parent_category_name or "").strip()
    member = (member_username or "").strip()
    if not parent or not member:
        return False
    try:
        _deactivate_obligation_assignments(
            household_id, assignment_level="parent", parent_category_name=parent
        )
        now = datetime.now(ZoneInfo("America/Chicago")).isoformat()
        supabase.table(get_obligation_assignments_table()).insert(
            {
                "household_id": household_id,
                "member_username": member,
                "parent_category_name": encrypt_data(parent),
                "category_id": None,
                "assignment_level": "parent",
                "label": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ).execute()
        return True
    except Exception as e:
        print(f"Error upserting parent assignment: {e}")
        return False


def clear_parent_assignment(household_id, parent_category_name) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    try:
        _deactivate_obligation_assignments(
            household_id,
            assignment_level="parent",
            parent_category_name=(parent_category_name or "").strip(),
        )
        return True
    except Exception as e:
        print(f"Error clearing parent assignment: {e}")
        return False


def upsert_subcategory_override(household_id, category_id, member_username) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    member = (member_username or "").strip()
    if not category_id or not member:
        return False
    try:
        _deactivate_obligation_assignments(
            household_id, assignment_level="subcategory", category_id=str(category_id)
        )
        now = datetime.now(ZoneInfo("America/Chicago")).isoformat()
        supabase.table(get_obligation_assignments_table()).insert(
            {
                "household_id": household_id,
                "member_username": member,
                "parent_category_name": None,
                "category_id": str(category_id),
                "assignment_level": "subcategory",
                "label": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ).execute()
        return True
    except Exception as e:
        print(f"Error upserting subcategory override: {e}")
        return False


def clear_subcategory_override(household_id, category_id) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    try:
        _deactivate_obligation_assignments(
            household_id, assignment_level="subcategory", category_id=str(category_id)
        )
        return True
    except Exception as e:
        print(f"Error clearing subcategory override: {e}")
        return False


def _income_row_allowance_linked(row) -> bool:
    val = row.get("source_expense_id")
    if val is None:
        return False
    text = str(val).strip().lower()
    return text not in ("", "none", "nan")


def _username_key(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("", "none", "nan", "unassigned"):
        return ""
    return text


def _income_row_stream_id(row) -> str:
    sid = row.get("stream_id")
    if sid is None or (isinstance(sid, float) and pd.isna(sid)):
        return ""
    text = str(sid).strip()
    if text.lower() in ("", "none", "nan"):
        return ""
    return text


def _fetch_household_income_stream_owners(household_id) -> dict[str, str]:
    """Map stream_id -> owner_username for active household income streams."""
    try:
        response = (
            supabase.table(get_income_streams_table())
            .select("id, owner_username")
            .eq("household_id", household_id)
            .eq("is_personal_income", False)
            .eq("is_active", True)
            .execute()
        )
        return {
            str(row["id"]): row.get("owner_username")
            for row in (response.data or [])
            if row.get("id")
        }
    except Exception as e:
        print(f"Error fetching income stream owners: {e}")
        return {}


def _member_recurring_take_home_from_streams(
    household_id,
    member_key: str,
    month_year: str,
    member_stream_ids: list[str],
    *,
    obligation_pay_only: bool = False,
) -> float:
    """One normalized monthly take-home per active recurring stream for this member."""
    if not member_stream_ids:
        return 0.0

    try:
        versions_res = (
            supabase.table(get_income_stream_versions_table())
            .select("*")
            .in_("stream_id", member_stream_ids)
            .order("effective_from", desc=False)
            .execute()
        )
    except Exception as e:
        print(f"Error fetching income stream versions: {e}")
        return 0.0

    versions_by_stream: dict[str, list] = {}
    for version in versions_res.data or []:
        versions_by_stream.setdefault(str(version["stream_id"]), []).append(version)

    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    month_end = date(year, month, last_day)

    total = 0.0
    for stream_id in member_stream_ids:
        versions = versions_by_stream.get(stream_id) or []
        if not versions:
            continue
        version = resolve_version_at_date(versions, month_end)
        if not version:
            version = resolve_version_at_date(versions, date(year, month, 1))
        if not version:
            continue
        effective_from = _parse_iso_date(version.get("effective_from"))
        if effective_from and _month_year_from_date(effective_from) > month_year:
            continue
        take_home = decrypt_float(version.get("take_home_amount"))
        freq = normalize_income_pay_frequency(version.get("pay_frequency") or "monthly")
        if obligation_pay_only and not _freq_is_obligation_regular_pay(freq):
            continue
        monthly = normalize_income_amount_for_month(
            take_home, freq, month_year=month_year
        )
        total += monthly
    return total


def _member_household_take_home(household_id, member_username, month_year) -> float:
    """Regular monthly take-home for one member (matches Cash Flow paycheck rows).

    Uses ledger rows for regular paycheck frequencies (monthly, school-year, bi-weekly,
    etc.) assigned to the member. Annual/quarterly/one-time entries are excluded from
    obligation math. Falls back to configured streams when no ledger rows exist yet.
    """
    member_key = _username_key(member_username)
    if not member_key:
        return 0.0

    incomes_df = get_household_incomes(household_id, month_year, is_personal_income=False)
    obligation_rows = []
    if incomes_df is not None and not incomes_df.empty:
        for _, row in incomes_df.iterrows():
            if _income_row_allowance_linked(row):
                continue
            if _username_key(row.get("owner_username")) != member_key:
                continue
            if not _freq_is_obligation_regular_pay(_income_row_frequency(row)):
                continue
            obligation_rows.append(row)

    if obligation_rows:
        return sum_income_for_month(pd.DataFrame(obligation_rows), month_year)

    stream_owners = _fetch_household_income_stream_owners(household_id)
    member_stream_ids = [
        sid for sid, owner in stream_owners.items() if _username_key(owner) == member_key
    ]
    return _member_recurring_take_home_from_streams(
        household_id,
        member_key,
        month_year,
        member_stream_ids,
        obligation_pay_only=True,
    )


def _member_allowance_logged(household_id, member_username, month_year, category_rows) -> float:
    cat_id = find_allowance_category_id(category_rows, member_username)
    if not cat_id:
        return 0.0
    expenses_df = get_monthly_expenses(household_id, month_year, include_private_members=True)
    if expenses_df is None or expenses_df.empty:
        return 0.0
    allowance_rows = expenses_df[expenses_df["category_id"].astype(str) == str(cat_id)]
    if allowance_rows.empty:
        return 0.0
    return float(allowance_rows["amount"].sum())


def _member_recurring_allowance_amount(household_id, member_username, category_rows) -> float:
    """Configured monthly amount on the member's recurring Allowance expense stream."""
    cat_id = find_allowance_category_id(category_rows, member_username)
    if not cat_id:
        return 0.0
    existing = _find_recurring_allowance_expense(household_id, cat_id)
    if not existing or not existing.get("stream_id"):
        return 0.0
    versions = get_expense_stream_versions(existing["stream_id"])
    if not versions:
        return 0.0
    version = resolve_version_at_date(versions, date.today())
    if not version:
        version = versions[0]
    if version.get("amount") is None:
        return 0.0
    return float(decrypt_float(version.get("amount")))


def compute_household_obligations(household_id, month_year):
    categories_df = get_budget_categories(household_id, is_personal=False)
    if categories_df is None or categories_df.empty:
        return {
            "lines": [],
            "displacement": reconcile_displacement([]),
            "parent_summaries": [],
            "by_member": {},
            "assignments": [],
        }

    category_rows = [
        row.to_dict() for _, row in categories_df.iterrows() if is_assignable_household_category(row)
    ]
    assignments = get_obligation_assignments(household_id)
    parent_map, override_map = build_assignment_maps(assignments)
    lines = resolve_obligation_lines(category_rows, parent_map, override_map)
    displacement = reconcile_displacement(lines)
    parent_summaries = build_parent_summaries(lines, parent_map)
    obligation_totals = aggregate_member_obligations(lines)

    members = set(obligation_totals.keys())
    for row in assignments:
        member = (row.get("member_username") or "").strip()
        if member:
            members.add(member)

    by_member = {}
    for member in sorted(members):
        total_obligation = round(obligation_totals.get(member, 0.0), 2)
        take_home = round(_member_household_take_home(household_id, member, month_year), 2)
        gap = round(compute_supplement_gap(total_obligation, take_home), 2)
        allowance_logged = round(
            _member_allowance_logged(household_id, member, month_year, category_rows), 2
        )
        recurring_allowance = round(
            _member_recurring_allowance_amount(household_id, member, category_rows), 2
        )
        if recurring_allowance <= 0 and allowance_logged > 0:
            recurring_allowance = allowance_logged
        coverage = compute_allowance_coverage(
            total_obligation, take_home, recurring_allowance
        )
        member_lines = [line for line in lines if line.get("member_username") == member]
        by_member[member] = {
            "total_obligation": total_obligation,
            "member_take_home": take_home,
            "supplement_gap": gap,
            "allowance_logged": allowance_logged,
            "current_recurring_allowance": coverage["current_recurring_allowance"],
            "target_recurring_allowance": coverage["target_recurring_allowance"],
            "total_available": coverage["total_available"],
            "shortfall": coverage["shortfall"],
            "allowance_adjustment": coverage["allowance_adjustment"],
            "is_covered": coverage["is_covered"],
            "needs_allowance_update": coverage["needs_allowance_update"],
            "recommended_allowance": coverage["target_recurring_allowance"],
            "obligation_lines": member_lines,
            "warnings": [],
        }
        if total_obligation > 0 and take_home == 0 and recurring_allowance <= 0:
            by_member[member]["warnings"].append(
                f"No household income found for '{member}' — verify the Earner "
                "on Cash Flow & Treasury income matches this username."
            )
        if coverage["needs_allowance_update"] and coverage["target_recurring_allowance"] > 0:
            cur = coverage["current_recurring_allowance"]
            tgt = coverage["target_recurring_allowance"]
            by_member[member]["warnings"].append(
                f"Recurring Allowance is ${cur:,.2f}; set to ${tgt:,.2f} to cover assigned obligations."
            )

    return {
        "lines": lines,
        "displacement": displacement,
        "parent_summaries": parent_summaries,
        "by_member": by_member,
        "assignments": assignments,
    }


def _total_household_regular_income(household_id, month_year) -> float:
    users = _fetch_household_users_cached(household_id) or []
    total = 0.0
    for row in users:
        username = row.get("username")
        if username:
            total += _member_household_take_home(household_id, username, month_year)
    return round(total, 2)


def get_household_income_stream_options(household_id) -> list[dict]:
    """Active household income streams for disbursement funding picker."""
    try:
        response = (
            supabase.table(get_income_streams_table())
            .select("id, owner_username, display_name, is_active")
            .eq("household_id", household_id)
            .eq("is_personal_income", False)
            .eq("is_active", True)
            .execute()
        )
        options = []
        for row in response.data or []:
            versions = get_income_stream_versions(str(row["id"]))
            if not versions:
                continue
            version = resolve_version_at_date(versions, date.today()) or versions[-1]
            freq = normalize_income_pay_frequency(version.get("pay_frequency") or "monthly")
            label_name = decrypt_text(row.get("display_name")) if row.get("display_name") else "Income"
            owner = row.get("owner_username") or "—"
            options.append(
                {
                    "stream_id": str(row["id"]),
                    "owner_username": owner,
                    "label": f"{label_name} ({owner}) · {income_pay_frequency_label(freq)}",
                    "pay_frequency": freq,
                }
            )
        return sorted(options, key=lambda item: item.get("label") or "")
    except Exception as e:
        print(f"Error listing income streams: {e}")
        return []


def get_disbursement_settings(household_id) -> dict:
    if not household_id:
        return {}
    try:
        response = (
            supabase.table(get_disbursement_settings_table())
            .select("*")
            .eq("household_id", household_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"Error fetching disbursement settings: {e}")
        return {}


def upsert_disbursement_settings(household_id, funding_income_stream_id) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    stream_id = str(funding_income_stream_id).strip() if funding_income_stream_id else None
    if stream_id in ("", "none", "None"):
        stream_id = None
    try:
        existing = get_disbursement_settings(household_id)
        payload = {
            "household_id": household_id,
            "funding_income_stream_id": stream_id,
            "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
            "updated_by": st.session_state.get("username"),
        }
        if existing.get("id"):
            supabase.table(get_disbursement_settings_table()).update(payload).eq(
                "id", existing["id"]
            ).execute()
        else:
            supabase.table(get_disbursement_settings_table()).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error saving disbursement settings: {e}")
        return False


def get_member_disbursement_streams_table():
    return get_budget_table("user_disbursement_funding_streams")


def get_member_funding_streams(household_id, username) -> list[str]:
    """Return all disbursement funding stream_ids selected by a member."""
    try:
        rows = (
            supabase.table(get_member_disbursement_streams_table())
            .select("stream_id")
            .eq("household_id", household_id)
            .eq("username", username)
            .execute()
        ).data or []
        return [str(r["stream_id"]) for r in rows if r.get("stream_id")]
    except Exception as e:
        print(f"Error fetching funding streams for {username}: {e}")
        return []


def set_member_funding_streams(household_id, username, stream_ids: list[str]) -> bool:
    """Replace the full set of funding streams for a member (delete + insert)."""
    if not _can_edit_monthly_budget_server_side():
        return False
    table = get_member_disbursement_streams_table()
    try:
        supabase.table(table).delete().eq("household_id", household_id).eq("username", username).execute()
        for sid in stream_ids or []:
            safe = str(sid).strip()
            if safe:
                supabase.table(table).insert({
                    "household_id": household_id,
                    "username": username,
                    "stream_id": safe,
                }).execute()
        return True
    except Exception as e:
        print(f"Error setting funding streams for {username}: {e}")
        return False


# Keep the singular helpers as thin wrappers for backward compat.
def get_member_funding_stream(household_id, username) -> str | None:
    streams = get_member_funding_streams(household_id, username)
    return streams[0] if streams else None


def set_member_funding_stream(household_id, username, stream_id) -> bool:
    ids = [str(stream_id)] if stream_id else []
    return set_member_funding_streams(household_id, username, ids)


def _fetch_stream_info(stream_id: str) -> dict:
    """Return label and owner for a single income stream."""
    try:
        stream_res = (
            supabase.table(get_income_streams_table())
            .select("id, owner_username, display_name")
            .eq("id", stream_id)
            .limit(1)
            .execute()
        )
        if not stream_res.data:
            return {}
        row = stream_res.data[0]
        label = decrypt_text(row.get("display_name")) if row.get("display_name") else None
        return {
            "stream_id": stream_id,
            "owner_username": row.get("owner_username"),
            "label": label,
        }
    except Exception as e:
        print(f"Error fetching stream info for {stream_id}: {e}")
        return {}


def _stream_pay_dates_for_month(stream_id: str, month_year: str) -> tuple[list[date], str]:
    """Return (sorted pay_dates, normalized_frequency) for one stream in month_year."""
    try:
        versions = get_income_stream_versions(stream_id)
        if not versions:
            return [], "monthly"
        occurrences = paycheck_occurrences_in_month(versions, month_year)
        pay_dates = sorted(occ["payment_date"] for occ in occurrences)
        version = resolve_version_at_date(versions, date.today()) or versions[-1]
        freq = normalize_income_pay_frequency(version.get("pay_frequency") or "monthly")
        return pay_dates, freq
    except Exception as e:
        print(f"Error getting pay dates for stream {stream_id}: {e}")
        return [], "monthly"


def _member_combined_pay_dates(household_id: str, username: str, month_year: str) -> tuple[list[tuple], list[dict]]:
    """Collect all paycheck occurrences across a member's selected funding streams.

    Returns (occurrences, stream_details) where occurrences is a list of
    (pay_date, stream_id) tuples sorted by date. Each paycheck occurrence from every
    stream is counted separately — two streams landing on the same calendar date
    produce two occurrences. Total paycheck count = len(occurrences).
    """
    stream_ids = get_member_funding_streams(household_id, username)
    if not stream_ids:
        return [], []

    occurrences: list[tuple] = []
    stream_details: list[dict] = []

    for sid in stream_ids:
        info = _fetch_stream_info(sid)
        pay_dates, freq = _stream_pay_dates_for_month(sid, month_year)
        for d in pay_dates:
            occurrences.append((d, sid))
        stream_details.append({
            "stream_id": sid,
            "label": info.get("label") or sid,
            "frequency": freq,
            "paycheck_count": len(pay_dates),
        })

    occurrences.sort(key=lambda x: x[0])
    return occurrences, stream_details


def compute_household_disbursement_plan(household_id, month_year) -> dict:
    """Transfer needs, admin/dev surplus split, and per-member paycheck schedule.

    Each member's bundle (obligation gap + surplus share) is split across that
    member's own funding income stream paychecks for the month.
    """
    obligations = compute_household_obligations(household_id, month_year)
    by_member = obligations.get("by_member") or {}
    displacement = obligations.get("displacement") or {}

    users = _fetch_household_users_cached(household_id) or []
    eligible = filter_disbursement_eligible_usernames(users)

    member_transfer_needs = compute_member_transfer_needs(by_member)
    total_regular_income = _total_household_regular_income(household_id, month_year)
    total_assigned = float(displacement.get("total_assigned") or 0)
    surplus_pool = compute_surplus_pool(total_regular_income, total_assigned)
    surplus_shares = compute_surplus_shares(surplus_pool, eligible)
    monthly_summary = summarize_monthly_disbursement(member_transfer_needs, surplus_shares)
    member_bundled_amounts = compute_member_bundled_amounts(member_transfer_needs, surplus_shares)

    per_member_monthly: dict[str, float] = {
        m: round(bundle["total_amount"], 2)
        for m, bundle in member_bundled_amounts.items()
    }

    # ── Per-member paycheck schedule ──────────────────────────────────────
    # Each (pay_date, stream_id) occurrence is a separate schedule slot so that
    # two streams landing on the same date produce distinct transfer rows.
    # Keys: f"{pay_date}|{stream_id}" — sorted by date when building final list.

    # Pre-fetch every unique stream once to avoid repeated socket calls when
    # multiple members share the same funding stream.
    all_stream_ids: set[str] = set()
    member_stream_ids: dict[str, list[str]] = {}
    for member in member_bundled_amounts:
        sids = get_member_funding_streams(household_id, member)
        member_stream_ids[member] = sids
        all_stream_ids.update(sids)

    stream_info_cache: dict[str, dict] = {sid: _fetch_stream_info(sid) for sid in all_stream_ids}
    stream_dates_cache: dict[str, tuple] = {
        sid: _stream_pay_dates_for_month(sid, month_year) for sid in all_stream_ids
    }

    schedule_by_slot: dict[str, dict] = {}
    per_member_stream_info: dict[str, dict] = {}

    for member, bundle in member_bundled_amounts.items():
        sids = member_stream_ids.get(member) or []
        occurrences: list[tuple] = []
        stream_details: list[dict] = []

        for sid in sids:
            info = stream_info_cache.get(sid) or {}
            pay_dates, freq = stream_dates_cache.get(sid) or ([], "monthly")
            for d in pay_dates:
                occurrences.append((d, sid))
            stream_details.append({
                "stream_id": sid,
                "label": info.get("label") or sid,
                "frequency": freq,
                "paycheck_count": len(pay_dates),
            })

        occurrences.sort(key=lambda x: x[0])
        paycheck_count = len(occurrences)

        stream_label_map = {s["stream_id"]: s["label"] for s in stream_details}
        per_member_stream_info[member] = {
            "stream_ids": [s["stream_id"] for s in stream_details],
            "streams": stream_details,
            "paycheck_count": paycheck_count,
            "display": " + ".join(s["label"] for s in stream_details) if stream_details else "",
        }

        if not occurrences:
            continue

        for pay_date_val, stream_id in occurrences:
            pay_date_str = pay_date_val.isoformat() if hasattr(pay_date_val, "isoformat") else str(pay_date_val)[:10]
            slot_key = f"{pay_date_str}|{stream_id or ''}"
            if slot_key not in schedule_by_slot:
                schedule_by_slot[slot_key] = {
                    "payment_date": pay_date_str,
                    "stream_id": stream_id,
                    "stream_label": stream_label_map.get(stream_id, ""),
                    "payouts": {},
                    "total": 0.0,
                }
            obl = round(float(bundle["obligation_amount"]) / paycheck_count, 2)
            allow = round(float(bundle["allowance_amount"]) / paycheck_count, 2)
            schedule_by_slot[slot_key]["payouts"][member] = {
                "obligation": obl,
                "allowance": allow,
                "total": round(obl + allow, 2),
            }

    # Sort by pay date, recalculate per-slot totals
    paycheck_schedule = []
    for slot_key in sorted(schedule_by_slot, key=lambda k: k.split("|")[0]):
        entry = schedule_by_slot[slot_key]
        entry["total"] = round(sum(p["total"] for p in entry["payouts"].values()), 2)
        paycheck_schedule.append(entry)

    review_flags = disbursement_review_flags(per_member_stream_info)

    saved_transfers = get_member_transfers(household_id, month_year)
    planned_allowance_total = sum_transfer_allowance_total(saved_transfers)
    recommended_allowance_total = round(sum((surplus_shares or {}).values()), 2)
    allowance_surplus_flags = disbursement_allowance_surplus_flags(
        current_surplus_pool=surplus_pool,
        planned_allowance_total=planned_allowance_total,
        recommended_allowance_total=recommended_allowance_total,
    )

    return {
        "month_year": month_year,
        "total_regular_income": total_regular_income,
        "total_assigned_obligations": round(total_assigned, 2),
        "surplus_pool": round(surplus_pool, 2),
        "eligible_members": eligible,
        "member_transfer_needs": member_transfer_needs,
        "surplus_shares": surplus_shares,
        "per_member_monthly": per_member_monthly,
        "member_bundled_amounts": member_bundled_amounts,
        "monthly_summary": monthly_summary,
        "per_member_stream_info": per_member_stream_info,
        "paycheck_schedule": paycheck_schedule,
        "review_flags": review_flags,
        "allowance_surplus_flags": allowance_surplus_flags,
        "planned_allowance_total": planned_allowance_total,
        "recommended_allowance_total": recommended_allowance_total,
        "obligations": obligations,
    }


def get_disbursement_allowance_surplus_flags(household_id, month_year) -> list[dict]:
    """Lightweight allowance-vs-surplus check (no paycheck schedule build)."""
    if not household_id or not month_year:
        return []

    obligations = compute_household_obligations(household_id, month_year)
    displacement = obligations.get("displacement") or {}
    users = _fetch_household_users_cached(household_id) or []
    eligible = filter_disbursement_eligible_usernames(users)

    total_regular_income = _total_household_regular_income(household_id, month_year)
    total_assigned = float(displacement.get("total_assigned") or 0)
    surplus_pool = compute_surplus_pool(total_regular_income, total_assigned)
    surplus_shares = compute_surplus_shares(surplus_pool, eligible)
    recommended_allowance_total = round(sum((surplus_shares or {}).values()), 2)
    planned_allowance_total = sum_transfer_allowance_total(
        get_member_transfers(household_id, month_year)
    )

    return disbursement_allowance_surplus_flags(
        current_surplus_pool=surplus_pool,
        planned_allowance_total=planned_allowance_total,
        recommended_allowance_total=recommended_allowance_total,
    )


def record_supplement_snapshot(
    household_id,
    month_year,
    member_username,
    member_totals: dict,
    *,
    displacement_summary=None,
    applied_to_allowance=False,
    allowance_expense_id=None,
) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    try:
        payload = {
            "household_id": household_id,
            "month_year": month_year,
            "member_username": member_username,
            "total_obligation": encrypt_data(member_totals.get("total_obligation", 0)),
            "member_take_home": encrypt_data(member_totals.get("member_take_home", 0)),
            "supplement_gap": encrypt_data(member_totals.get("supplement_gap", 0)),
            "allowance_logged": encrypt_data(member_totals.get("allowance_logged", 0)),
            "recommended_allowance": encrypt_data(member_totals.get("recommended_allowance", 0)),
            "obligation_breakdown": encrypt_data(
                json.dumps(member_totals.get("obligation_lines") or [])
            ),
            "displacement_summary": encrypt_data(json.dumps(displacement_summary or {})),
            "applied_to_allowance": bool(applied_to_allowance),
            "allowance_expense_id": str(allowance_expense_id) if allowance_expense_id else None,
            "applied_at": datetime.now(ZoneInfo("America/Chicago")).isoformat()
            if applied_to_allowance
            else None,
            "applied_by": st.session_state.get("username") if applied_to_allowance else None,
            "computed_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        }
        supabase.table(get_supplement_snapshots_table()).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error recording supplement snapshot: {e}")
        return False


def get_supplement_snapshots(household_id, month_year=None, *, limit=20):
    try:
        query = (
            supabase.table(get_supplement_snapshots_table())
            .select("*")
            .eq("household_id", household_id)
            .order("computed_at", desc=True)
            .limit(limit)
        )
        if month_year:
            query = query.eq("month_year", month_year)
        response = query.execute()
        rows = []
        for row in response.data or []:
            rows.append(
                {
                    **row,
                    "total_obligation": decrypt_float(row.get("total_obligation")),
                    "member_take_home": decrypt_float(row.get("member_take_home")),
                    "supplement_gap": decrypt_float(row.get("supplement_gap")),
                    "allowance_logged": decrypt_float(row.get("allowance_logged")),
                    "recommended_allowance": decrypt_float(row.get("recommended_allowance")),
                }
            )
        return rows
    except Exception as e:
        print(f"Error fetching supplement snapshots: {e}")
        return []


def _find_recurring_allowance_expense(household_id, allowance_category_id):
    expenses_table = get_budget_table("expenses")
    response = (
        supabase.table(expenses_table)
        .select("id, stream_id, date_logged")
        .eq("household_id", household_id)
        .eq("category_id", allowance_category_id)
        .eq("is_personal_spend", False)
        .eq("is_recurring", True)
        .order("date_logged", desc=True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def apply_supplement_to_allowance(household_id, member_username, month_year, amount) -> bool:
    if not _can_edit_monthly_budget_server_side():
        return False
    ensure_allowance_categories(household_id)
    categories_df = get_budget_categories(household_id, is_personal=False)
    category_rows = categories_df.to_dict("records") if categories_df is not None and not categories_df.empty else []
    allowance_category_id = find_allowance_category_id(category_rows, member_username)
    if not allowance_category_id:
        return False

    safe_amount = round(float(amount or 0), 2)
    if safe_amount <= 0:
        return False

    auth_user_id = st.session_state.get("auth_user_id")
    username = st.session_state.get("username")
    effective = date.today()
    expense_id = None

    existing = _find_recurring_allowance_expense(household_id, allowance_category_id)
    if existing:
        expense_id = existing.get("id")
        if not schedule_expense_change(
            expense_id,
            effective,
            safe_amount,
            ALLOWANCE_INCOME_SOURCE_NAME,
            "monthly",
            category_id=allowance_category_id,
        ):
            return False
        # schedule_expense_change rematerializes HH expense; re-sync personal allowance income.
        expense_flags = _fetch_expense_flags(expense_id)
        stream_id = (expense_flags or {}).get("stream_id")
        if stream_id:
            _sync_allowance_for_stream_month(stream_id, month_year, household_id)
    else:
        if not log_expense_and_check_project(
            auth_user_id=auth_user_id,
            username=username,
            household_id=household_id,
            month_year=month_year,
            date_logged=effective,
            category_id=allowance_category_id,
            amount=safe_amount,
            details=ALLOWANCE_INCOME_SOURCE_NAME,
            is_personal_spend=False,
            is_recurring=True,
            pay_frequency="monthly",
        ):
            return False
        created = _find_recurring_allowance_expense(household_id, allowance_category_id)
        expense_id = created.get("id") if created else None

    obligations = compute_household_obligations(household_id, month_year)
    member_totals = obligations.get("by_member", {}).get(member_username, {})
    record_supplement_snapshot(
        household_id,
        month_year,
        member_username,
        member_totals,
        displacement_summary=obligations.get("displacement"),
        applied_to_allowance=True,
        allowance_expense_id=expense_id,
    )
    return True


# ---------------------------------------------------------------------------
# Member transfer ledger (migration 032)
# ---------------------------------------------------------------------------

def get_member_transfers_table():
    return get_budget_table("household_member_transfers")


def get_member_transfers(household_id, month_year) -> list[dict]:
    """Return all transfer rows for a household/month, decrypted."""
    try:
        rows = (
            supabase.table(get_member_transfers_table())
            .select("*")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .order("payment_date")
            .execute()
        ).data or []
        return [_decrypt_member_transfer(r) for r in rows]
    except Exception as e:
        print(f"Error fetching member transfers: {e}")
        return []


def get_due_planned_member_transfers(household_id, as_of=None) -> list[dict]:
    """Planned transfers whose payment_date is on or before as_of (default: today, US Central)."""
    if not household_id:
        return []
    as_of = as_of or datetime.now(ZoneInfo("America/Chicago")).date()
    try:
        rows = (
            supabase.table(get_member_transfers_table())
            .select("*")
            .eq("household_id", household_id)
            .eq("status", "planned")
            .lte("payment_date", as_of.isoformat())
            .order("payment_date")
            .execute()
        ).data or []
        return [_decrypt_member_transfer(r) for r in rows]
    except Exception as e:
        print(f"Error fetching due planned transfers: {e}")
        return []


def _decrypt_member_transfer(row: dict) -> dict:
    out = dict(row)
    for field in ("allowance_amount", "obligation_amount", "total_amount"):
        raw = out.get(field)
        if raw is not None:
            out[field] = decrypt_float(raw)
    return out


def is_transfer_allowance_expense_record(record) -> bool:
    """True for auto-created HH allowance expenses tied to disbursement transfers."""
    if not record:
        return False
    raw_details = record.get("details")
    if raw_details is None:
        return False
    details = decrypt_text(raw_details) if not isinstance(raw_details, str) else raw_details
    return (details or "").strip() == TRANSFER_ALLOWANCE_EXPENSE_DETAILS


def _is_transfer_allowance_expense_id(expense_id) -> bool:
    if not expense_id:
        return False
    target_table = get_budget_table("expenses")
    try:
        response = (
            supabase.table(target_table)
            .select("details")
            .eq("id", str(expense_id))
            .limit(1)
            .execute()
        )
        return is_transfer_allowance_expense_record(response.data[0]) if response.data else False
    except Exception:
        return False


def _disbursement_transfers_cover_allowance(
    household_id: str,
    month_year: str,
    recipient_username: str,
) -> bool:
    """True when disbursement transfers (not legacy HH allowance expenses) own allowance."""
    member_key = _username_key(recipient_username)
    if not member_key:
        return False
    for transfer in get_member_transfers(household_id, month_year):
        if _username_key(transfer.get("recipient_username")) != member_key:
            continue
        if round(float(transfer.get("allowance_amount") or 0), 2) > 0:
            return True
    return False


def _household_disbursement_months(household_id: str) -> list[str]:
    """Distinct month_year values that have member transfer rows."""
    if not household_id:
        return []
    try:
        rows = (
            supabase.table(get_member_transfers_table())
            .select("month_year")
            .eq("household_id", household_id)
            .execute()
        ).data or []
        return sorted({str(r["month_year"]) for r in rows if r.get("month_year")})
    except Exception as e:
        print(f"Error listing disbursement months: {e}")
        return []


def _prune_legacy_allowance_superseded_by_transfers(household_id: str, month_year: str) -> int:
    """Remove legacy stream/expense-mirror Allowance rows when transfers own allowance.

    Only deletes rows clearly from the old HH-expense path (income stream or
    non-transfer-auto source_expense_id). Transfer-path rows are never removed here.
    """
    if not household_id or not month_year:
        return 0

    transfers = get_member_transfers(household_id, month_year)
    covered_recipients = {
        _username_key(t["recipient_username"])
        for t in transfers
        if t.get("status") == "completed"
        and round(float(t.get("allowance_amount") or 0), 2) > 0
    }
    if not covered_recipients:
        return 0

    linked_income_ids = {
        str(t["personal_allowance_income_id"])
        for t in transfers
        if t.get("personal_allowance_income_id")
    }

    expenses_table = get_budget_table("expenses")
    incomes_table = get_budget_table("household_incomes")
    try:
        expense_rows = (
            supabase.table(expenses_table)
            .select("id, details")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_spend", False)
            .execute()
        ).data or []
        transfer_auto_ids = {
            str(row["id"])
            for row in expense_rows
            if is_transfer_allowance_expense_record(row)
        }
        income_rows = (
            supabase.table(incomes_table)
            .select("id, source_name, owner_username, stream_id, source_expense_id")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .execute()
        ).data or []
    except Exception as e:
        print(f"Error pruning legacy allowance incomes: {e}")
        return 0

    removed = 0
    for row in income_rows:
        income_id = str(row["id"])
        if income_id in linked_income_ids:
            continue
        source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
        if source != ALLOWANCE_INCOME_SOURCE_NAME:
            continue
        if _username_key(row.get("owner_username")) not in covered_recipients:
            continue
        src_exp = str(row.get("source_expense_id") or "").strip()
        stream_id = row.get("stream_id")
        is_legacy = bool(stream_id) or (bool(src_exp) and src_exp not in transfer_auto_ids)
        if not is_legacy:
            continue
        if _delete_personal_transfer_income(income_id):
            removed += 1
    return removed


def _reconcile_transfer_allowance_incomes(household_id: str, month_year: str) -> int:
    """Give every completed allowance transfer its own valid personal income row."""
    if not household_id or not month_year:
        return 0

    transfers = get_member_transfers(household_id, month_year)
    completed = sorted(
        [
            t for t in transfers
            if t.get("status") == "completed"
            and round(float(t.get("allowance_amount") or 0), 2) > 0
        ],
        key=lambda t: (
            str(t.get("payment_date") or ""),
            str(t.get("funding_income_stream_id") or ""),
            str(t.get("id") or ""),
        ),
    )
    if not completed:
        return 0

    incomes_table = get_budget_table("household_incomes")
    table = get_member_transfers_table()
    now_ts = datetime.now(ZoneInfo("America/Chicago")).isoformat()
    fixed = 0

    for transfer in completed:
        transfer_id = str(transfer.get("id") or "")
        recipient = transfer["recipient_username"]
        pay_date_str = str(transfer.get("payment_date") or "")[:10]
        allowance_amt = float(transfer.get("allowance_amount") or 0)
        link_key = member_transfer_income_link_key(transfer_id, ALLOWANCE_INCOME_SOURCE_NAME)
        linked_str = transfer.get("personal_allowance_income_id")
        by_link = _find_income_id_by_member_transfer_link(link_key)
        if by_link and _income_id_is_allowance_expense_mirror(by_link):
            by_link = None
        preferred_id = by_link
        if not preferred_id and linked_str:
            linked_text = str(linked_str)
            if not _income_id_is_allowance_expense_mirror(linked_text):
                preferred_id = linked_text

        new_id = _upsert_personal_transfer_income(
            household_id=household_id,
            month_year=month_year,
            recipient=recipient,
            pay_date_str=pay_date_str,
            source_name=ALLOWANCE_INCOME_SOURCE_NAME,
            amount=allowance_amt,
            existing_id=preferred_id,
            transfer_id=transfer_id,
        )
        if not new_id:
            continue
        new_id = str(new_id)
        if new_id != str(linked_str or ""):
            try:
                supabase.table(table).update({
                    "personal_allowance_income_id": new_id,
                    "updated_at": now_ts,
                }).eq("id", transfer_id).execute()
                fixed += 1
            except Exception as e:
                print(f"Error relinking transfer {transfer_id} income: {e}")
    return fixed


def _reconcile_shared_transfer_allowance_incomes(household_id: str, month_year: str) -> int:
    """Backward-compatible alias."""
    return _reconcile_transfer_allowance_incomes(household_id, month_year)


def _prune_transfer_auto_expense_mirror_incomes(household_id, month_year) -> int:
    """Delete personal Allowance rows mirrored from transfer-auto HH expenses.

    Disbursement transfers create personal income via personal_allowance_income_id.
    Expense-mirror rows (source_expense_id → transfer-auto expense) are duplicates.
    """
    if not household_id or not month_year:
        return 0
    expenses_table = get_budget_table("expenses")
    incomes_table = get_budget_table("household_incomes")
    try:
        expense_rows = (
            supabase.table(expenses_table)
            .select("id, details")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_spend", False)
            .execute()
        ).data or []
        transfer_auto_ids = {
            str(row["id"])
            for row in expense_rows
            if is_transfer_allowance_expense_record(row)
        }
        if not transfer_auto_ids:
            return 0
        income_rows = (
            supabase.table(incomes_table)
            .select("id, source_expense_id")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .execute()
        ).data or []
        removed = 0
        for row in income_rows:
            src = row.get("source_expense_id")
            if src and str(src) in transfer_auto_ids:
                if _delete_personal_transfer_income(row["id"]):
                    removed += 1
        return removed
    except Exception as e:
        print(f"Error pruning transfer-auto mirror incomes: {e}")
        return 0


def _household_budget_actor(household_id) -> tuple[str | None, str | None]:
    users = _fetch_household_users_cached(household_id) or []
    for user in users:
        if user.get("role") in ("admin", "developer"):
            return user.get("auth_user_id"), user.get("username")
    if users:
        return users[0].get("auth_user_id"), users[0].get("username")
    return None, None


def _allowance_category_id_for_member(household_id, member_username) -> str | None:
    ensure_allowance_categories(household_id)
    categories_df = get_budget_categories(household_id, is_personal=False)
    if categories_df is None or categories_df.empty:
        return None
    member_key = _username_key(member_username)
    for _, row in categories_df.iterrows():
        if not is_allowance_subcategory(row.get("category_name"), row.get("sub_category_name")):
            continue
        linked = allowance_recipient_username(
            row.get("category_name"),
            row.get("sub_category_name"),
            username_field=row.get("username"),
        )
        if _username_key(linked) == member_key:
            return str(row.get("id"))
    return None


def _delete_transfer_allowance_household_expense(expense_id) -> bool:
    if not expense_id:
        return False
    target_table = get_budget_table("expenses")
    try:
        supabase.table(target_table).delete().eq("id", str(expense_id)).execute()
        return True
    except Exception as e:
        print(f"Error deleting transfer allowance expense {expense_id}: {e}")
        return False


def _find_existing_transfer_allowance_expense(
    household_id,
    month_year,
    category_id,
    pay_date_str,
) -> str | None:
    """Reuse an unlinked auto allowance expense before inserting a duplicate."""
    expenses_table = get_budget_table("expenses")
    try:
        rows = (
            supabase.table(expenses_table)
            .select("id, details")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("category_id", str(category_id))
            .eq("date_logged", pay_date_str)
            .eq("is_personal_spend", False)
            .execute()
        ).data or []
        for row in rows:
            if is_transfer_allowance_expense_record(row):
                return str(row["id"])
    except Exception as e:
        print(f"Error finding existing transfer allowance expense: {e}")
    return None


def _clear_transfer_side_effects(transfer: dict) -> None:
    """Remove HH allowance expense and personal incomes linked to a transfer row."""
    expense_id = transfer.get("household_allowance_expense_id")
    if expense_id:
        _delete_allowance_income_for_expense(expense_id)
        _delete_transfer_allowance_household_expense(expense_id)
    if transfer.get("personal_allowance_income_id"):
        _delete_personal_transfer_income(transfer["personal_allowance_income_id"])
    if transfer.get("personal_obligation_income_id"):
        _delete_personal_transfer_income(transfer["personal_obligation_income_id"])


def cleanup_orphan_disbursement_artifacts(household_id, month_year) -> dict:
    """Delete transfer-generated expenses/incomes no longer referenced by transfer rows."""
    stats = {"expenses": 0, "incomes": 0}
    if not household_id:
        return stats

    transfers_table = get_member_transfers_table()
    expenses_table = get_budget_table("expenses")
    incomes_table = get_budget_table("household_incomes")

    try:
        transfer_rows = (
            supabase.table(transfers_table)
            .select(
                "household_allowance_expense_id, personal_allowance_income_id, personal_obligation_income_id"
            )
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .execute()
        ).data or []
    except Exception as e:
        print(f"Error loading transfers for orphan cleanup: {e}")
        return stats

    linked_expense_ids = {
        str(row["household_allowance_expense_id"])
        for row in transfer_rows
        if row.get("household_allowance_expense_id")
    }
    linked_income_ids = set()
    for row in transfer_rows:
        for field in ("personal_allowance_income_id", "personal_obligation_income_id"):
            if row.get(field):
                linked_income_ids.add(str(row[field]))

    try:
        expense_rows = (
            supabase.table(expenses_table)
            .select("id, details")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_spend", False)
            .execute()
        ).data or []
        for row in expense_rows:
            if not is_transfer_allowance_expense_record(row):
                continue
            expense_id = str(row["id"])
            if expense_id in linked_expense_ids:
                continue
            _delete_allowance_income_for_expense(expense_id)
            if _delete_transfer_allowance_household_expense(expense_id):
                stats["expenses"] += 1
    except Exception as e:
        print(f"Error cleaning orphan transfer expenses: {e}")

    valid_expense_ids = {str(row["id"]) for row in expense_rows}
    transfer_auto_expense_ids = {
        str(row["id"])
        for row in expense_rows
        if is_transfer_allowance_expense_record(row)
    }

    transfer_income_sources = {ALLOWANCE_INCOME_SOURCE_NAME, OBLIGATION_SUPPORT_INCOME_SOURCE_NAME}
    try:
        income_rows = (
            supabase.table(incomes_table)
            .select("id, source_name, source_expense_id")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .execute()
        ).data or []
        keeper_by_expense: dict[str, str] = {}
        for row in income_rows:
            source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
            if source not in transfer_income_sources:
                continue
            income_id = str(row["id"])
            src_exp = row.get("source_expense_id")
            if src_exp:
                src_key = str(src_exp)
                if src_key not in valid_expense_ids:
                    if _delete_personal_transfer_income(income_id):
                        stats["incomes"] += 1
                    continue
                if src_key in transfer_auto_expense_ids:
                    if _delete_personal_transfer_income(income_id):
                        stats["incomes"] += 1
                    continue
                existing_keeper = keeper_by_expense.get(src_key)
                if existing_keeper and existing_keeper != income_id:
                    drop_id = income_id
                    if income_id in linked_income_ids and existing_keeper not in linked_income_ids:
                        keeper_by_expense[src_key] = income_id
                        drop_id = existing_keeper
                    if _delete_personal_transfer_income(drop_id):
                        stats["incomes"] += 1
                    continue
                keeper_by_expense[src_key] = income_id
                continue
            if income_id in linked_income_ids:
                continue
            # Transfer-path rows (no source_expense_id) are assigned by repair;
            # do not delete as orphans or dedupe will remove needed same-day rows.
            if source == ALLOWANCE_INCOME_SOURCE_NAME:
                continue
            if _delete_personal_transfer_income(income_id):
                stats["incomes"] += 1
    except Exception as e:
        print(f"Error cleaning orphan transfer incomes: {e}")

    mirror_pruned = _prune_transfer_auto_expense_mirror_incomes(household_id, month_year)
    stats["incomes"] += mirror_pruned

    return stats


def dedupe_transfer_allowance_personal_incomes(household_id: str, month_year: str) -> int:
    """Remove orphan/mirror Allowance incomes without collapsing separate transfers.

    Multiple completed transfers may share the same payment date and allowance
    amount (e.g. two paychecks on the 1st). Rows linked on transfer records are
    always kept. Only unlinked expense-mirror or stray duplicate rows are removed.
    """
    if not household_id or not month_year:
        return 0

    incomes_table = get_budget_table("household_incomes")
    transfers = get_member_transfers(household_id, month_year)
    linked_income_ids = {
        str(t["personal_allowance_income_id"])
        for t in transfers
        if t.get("personal_allowance_income_id")
    }
    allowance_link_keys = _allowance_link_keys_for_month(household_id, month_year)
    transfer_needs: dict[tuple, int] = {}
    for transfer in transfers:
        if transfer.get("status") != "completed":
            continue
        allowance_amt = round(float(transfer.get("allowance_amount") or 0), 2)
        if allowance_amt <= 0:
            continue
        pay = str(transfer.get("payment_date") or "")[:10]
        key = (transfer.get("recipient_username"), pay, allowance_amt)
        transfer_needs[key] = transfer_needs.get(key, 0) + 1

    try:
        income_rows = (
            supabase.table(incomes_table)
            .select(
                "id, source_name, owner_username, payment_date, "
                "source_expense_id, take_home_amount, source_member_transfer_id"
            )
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .execute()
        ).data or []
    except Exception as e:
        print(f"Error loading allowance incomes for dedupe: {e}")
        return 0

    allowance_rows = []
    for row in income_rows:
        source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
        if source != ALLOWANCE_INCOME_SOURCE_NAME:
            continue
        pay = str(row.get("payment_date") or "")[:10]
        amount = decrypt_float(row.get("take_home_amount")) if row.get("take_home_amount") is not None else 0.0
        has_src_exp = bool(str(row.get("source_expense_id") or "").strip())
        link_key = str(row.get("source_member_transfer_id") or "").strip()
        transfer_linked = (
            str(row["id"]) in linked_income_ids
            or bool(link_key and link_key in allowance_link_keys)
        )
        allowance_rows.append({
            "id": str(row["id"]),
            "owner": row.get("owner_username"),
            "payment_date": pay,
            "amount": round(float(amount), 2),
            "has_source_expense": has_src_exp,
            "transfer_linked": transfer_linked,
        })

    groups: dict[tuple, list[dict]] = {}
    for row in allowance_rows:
        key = (row["owner"], row["payment_date"], row["amount"])
        groups.setdefault(key, []).append(row)

    removed = 0
    for key, group in groups.items():
        if len(group) <= 1:
            continue

        linked = [r for r in group if r["transfer_linked"]]
        unlinked = [r for r in group if not r["transfer_linked"]]
        needed = transfer_needs.get(key, 0)

        # Same-day paychecks: keep unlinked rows until every transfer has a link.
        if needed > len(linked):
            spare_slots = needed - len(linked)
            mirrors = [r for r in unlinked if r["has_source_expense"]]
            transfer_path = [r for r in unlinked if not r["has_source_expense"]]
            for row in mirrors:
                if _delete_personal_transfer_income(row["id"]):
                    removed += 1
            for row in transfer_path[spare_slots:]:
                if _delete_personal_transfer_income(row["id"]):
                    removed += 1
            continue

        # Enough transfer links — drop mirrors and extra unlinked rows only.
        if len(linked) >= 1:
            for row in unlinked:
                if _delete_personal_transfer_income(row["id"]):
                    removed += 1
            continue

        # No transfer links — collapse stray duplicates (prefer non-mirror row).
        keeper = next((r for r in unlinked if not r["has_source_expense"]), unlinked[0])
        for row in unlinked:
            if row["id"] == keeper["id"]:
                continue
            if _delete_personal_transfer_income(row["id"]):
                removed += 1
    return removed


def repair_disbursement_allowance_incomes(household_id: str, month_year: str) -> dict:
    """Re-sync transfer-linked allowance incomes and remove duplicates for a month."""
    stats = {"deduped": 0, "mirrors_pruned": 0, "legacy_pruned": 0, "reconciled": 0, "link_stamped": 0, "mirror_links_cleared": 0}
    if not household_id or not month_year:
        return stats

    stats["mirror_links_cleared"] = _clear_mirror_transfer_link_keys(household_id, month_year)
    stats["link_stamped"] = _backfill_member_transfer_income_link_keys(household_id, month_year)
    stats["reconciled"] = _reconcile_transfer_allowance_incomes(household_id, month_year)

    stats["legacy_pruned"] = _prune_legacy_allowance_superseded_by_transfers(
        household_id, month_year
    )
    stats["mirrors_pruned"] = _prune_transfer_auto_expense_mirror_incomes(household_id, month_year)
    stats["deduped"] = dedupe_transfer_allowance_personal_incomes(household_id, month_year)
    return stats


def repair_all_disbursement_allowance_incomes(household_id: str) -> dict:
    """Run allowance income repair for every month that has member transfers."""
    totals = {"deduped": 0, "mirrors_pruned": 0, "legacy_pruned": 0, "reconciled": 0, "link_stamped": 0, "mirror_links_cleared": 0, "months": 0}
    if not household_id:
        return totals
    months = set(_household_disbursement_months(household_id))
    tz = ZoneInfo("America/Chicago")
    months.add(datetime.now(tz).strftime("%Y-%m"))
    for month_year in sorted(months):
        stats = repair_disbursement_allowance_incomes(household_id, month_year)
        totals["months"] += 1
        for key in ("deduped", "mirrors_pruned", "legacy_pruned", "reconciled", "link_stamped", "mirror_links_cleared"):
            totals[key] += int(stats.get(key) or 0)
    return totals


def _count_duplicate_allowance_incomes(household_id: str, month_year: str) -> int:
    """Count stray or missing Allowance rows relative to completed transfers."""
    incomes_table = get_budget_table("household_incomes")
    transfers = get_member_transfers(household_id, month_year)
    completed_allowance = [
        t for t in transfers
        if t.get("status") == "completed"
        and round(float(t.get("allowance_amount") or 0), 2) > 0
    ]
    linked_income_ids = {
        str(t["personal_allowance_income_id"])
        for t in transfers
        if t.get("personal_allowance_income_id")
    }
    try:
        income_rows = (
            supabase.table(incomes_table)
            .select(
                "id, source_name, owner_username, payment_date, "
                "source_expense_id, take_home_amount, source_member_transfer_id"
            )
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .execute()
        ).data or []
    except Exception:
        return 0

    groups: dict[tuple, dict] = {}
    for row in income_rows:
        source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
        if source != ALLOWANCE_INCOME_SOURCE_NAME:
            continue
        pay = str(row.get("payment_date") or "")[:10]
        amount = decrypt_float(row.get("take_home_amount")) if row.get("take_home_amount") is not None else 0.0
        key = (row.get("owner_username"), pay, round(float(amount), 2))
        bucket = groups.setdefault(key, {"linked": 0, "unlinked": 0})
        if str(row["id"]) in linked_income_ids:
            bucket["linked"] += 1
        else:
            bucket["unlinked"] += 1

    extras = 0
    for bucket in groups.values():
        if bucket["linked"] >= 1:
            extras += bucket["unlinked"]
        elif bucket["unlinked"] > 1:
            extras += bucket["unlinked"] - 1

    missing_links = sum(
        1 for t in completed_allowance if not t.get("personal_allowance_income_id")
    )
    shared_links = 0
    by_income: dict[str, int] = {}
    for transfer in completed_allowance:
        income_id = transfer.get("personal_allowance_income_id")
        if income_id:
            by_income[str(income_id)] = by_income.get(str(income_id), 0) + 1
    for count in by_income.values():
        if count > 1:
            shared_links += count - 1

    return extras + missing_links + shared_links


def get_disbursement_automation_audit_flags(household_id, month_year) -> list[dict]:
    """Audit flags for hands-off disbursement automation (surplus, overdue, duplicates)."""
    if not household_id or not month_year:
        return []

    flags: list[dict] = []
    flags.extend(get_disbursement_allowance_surplus_flags(household_id, month_year))

    due_transfers = get_due_planned_member_transfers(household_id)
    due_this_month = [t for t in due_transfers if str(t.get("month_year") or "") == month_year]
    if due_this_month:
        flags.append({
            "kind": "overdue_transfers",
            "severity": "warning",
            "message": (
                f"Household Budget: {len(due_this_month)} planned transfer(s) are due "
                "and will auto-complete on next app load."
            ),
        })

    dup_count = _count_duplicate_allowance_incomes(household_id, month_year)
    if dup_count:
        flags.append({
            "kind": "duplicate_allowance_income",
            "severity": "error",
            "message": (
                f"Household Budget: {dup_count} duplicate Allowance income row(s) detected "
                f"for {month_year}. Budget automation will repair on load."
            ),
        })

    transfers = get_member_transfers(household_id, month_year)
    missing_income = 0
    missing_expense = 0
    for row in transfers:
        if row.get("status") != "completed":
            continue
        if round(float(row.get("allowance_amount") or 0), 2) <= 0:
            continue
        if not row.get("personal_allowance_income_id"):
            missing_income += 1
        if not row.get("household_allowance_expense_id"):
            missing_expense += 1
    if missing_income:
        flags.append({
            "kind": "transfer_missing_income",
            "severity": "warning",
            "message": (
                f"Household Budget: {missing_income} completed transfer(s) are missing "
                "linked personal Allowance income."
            ),
        })
    if missing_expense:
        flags.append({
            "kind": "transfer_missing_expense",
            "severity": "warning",
            "message": (
                f"Household Budget: {missing_expense} completed transfer(s) are missing "
                "linked household Allowance expenses."
            ),
        })

    return flags


def _sync_transfer_allowance_household_expense(row: dict) -> str | None:
    """Create or update the household allowance expense when a transfer completes."""
    household_id = row.get("household_id")
    month_year = row.get("month_year")
    recipient = row.get("recipient_username")
    pay_date_str = str(row.get("payment_date") or "")[:10]
    amount = round(float(row.get("allowance_amount") or 0), 2)
    existing_expense_id = row.get("household_allowance_expense_id")

    if amount <= 0:
        if existing_expense_id:
            _delete_transfer_allowance_household_expense(existing_expense_id)
        return None

    category_id = _allowance_category_id_for_member(household_id, recipient)
    if not category_id:
        return str(existing_expense_id) if existing_expense_id else None

    auth_user_id, username = _household_budget_actor(household_id)
    if not auth_user_id or not username:
        return str(existing_expense_id) if existing_expense_id else None

    expenses_table = get_budget_table("expenses")
    payload = {
        "household_id": household_id,
        "auth_user_id": auth_user_id,
        "username": username,
        "month_year": month_year,
        "date_logged": pay_date_str,
        "category_id": category_id,
        "amount": encrypt_data(amount),
        "details": encrypt_data(TRANSFER_ALLOWANCE_EXPENSE_DETAILS),
        "is_personal_spend": False,
        "is_recurring": False,
        "pay_frequency": "one_time",
    }
    try:
        if existing_expense_id:
            supabase.table(expenses_table).update(payload).eq("id", str(existing_expense_id)).execute()
            return str(existing_expense_id)

        reuse_id = _find_existing_transfer_allowance_expense(
            household_id, month_year, category_id, pay_date_str
        )
        if reuse_id:
            supabase.table(expenses_table).update(payload).eq("id", reuse_id).execute()
            return reuse_id

        response = supabase.table(expenses_table).insert(payload).execute()
        new_id = (response.data or [{}])[0].get("id")
        return str(new_id) if new_id else None
    except Exception as e:
        print(f"Error syncing transfer allowance expense for transfer {row.get('id')}: {e}")
        return str(existing_expense_id) if existing_expense_id else None


def _income_id_is_allowance_expense_mirror(income_id) -> bool:
    """True when personal Allowance income was mirrored from a HH expense, not a transfer."""
    if not income_id:
        return False
    incomes_table = get_budget_table("household_incomes")
    try:
        rows = (
            supabase.table(incomes_table)
            .select("source_name, source_expense_id")
            .eq("id", str(income_id))
            .limit(1)
            .execute()
        ).data or []
    except Exception:
        return False
    if not rows:
        return False
    row = rows[0]
    source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
    if source != ALLOWANCE_INCOME_SOURCE_NAME:
        return False
    src_exp = str(row.get("source_expense_id") or "").strip()
    if not src_exp:
        return False
    return not _is_transfer_allowance_expense_id(src_exp)


def _find_income_id_by_member_transfer_link(link_key: str) -> str | None:
    """Lookup personal income by plaintext transfer link (no decryption)."""
    if not link_key:
        return None
    incomes_table = get_budget_table("household_incomes")
    try:
        rows = (
            supabase.table(incomes_table)
            .select("id")
            .eq("source_member_transfer_id", str(link_key))
            .limit(1)
            .execute()
        ).data or []
        return str(rows[0]["id"]) if rows else None
    except Exception as e:
        print(f"Error loading income by transfer link {link_key}: {e}")
        return None


def _allowance_link_keys_for_month(household_id: str, month_year: str) -> set[str]:
    keys: set[str] = set()
    for transfer in get_member_transfers(household_id, month_year):
        if transfer.get("status") != "completed":
            continue
        if round(float(transfer.get("allowance_amount") or 0), 2) <= 0:
            continue
        keys.add(
            member_transfer_income_link_key(transfer["id"], ALLOWANCE_INCOME_SOURCE_NAME)
        )
    return keys


def _clear_mirror_transfer_link_keys(household_id: str, month_year: str) -> int:
    """Remove plaintext transfer links mistakenly stamped on expense-mirror incomes."""
    if not household_id or not month_year:
        return 0
    incomes_table = get_budget_table("household_incomes")
    try:
        rows = (
            supabase.table(incomes_table)
            .select("id, source_member_transfer_id")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("is_personal_income", True)
            .not_.is_("source_member_transfer_id", "null")
            .execute()
        ).data or []
    except Exception as e:
        print(f"Error loading transfer link keys: {e}")
        return 0
    cleared = 0
    for row in rows:
        income_id = str(row["id"])
        if not _income_id_is_allowance_expense_mirror(income_id):
            continue
        try:
            supabase.table(incomes_table).update(
                {"source_member_transfer_id": None}
            ).eq("id", income_id).execute()
            cleared += 1
        except Exception as e:
            print(f"Error clearing mirror link key on income {income_id}: {e}")
    return cleared


def _backfill_member_transfer_income_link_keys(household_id: str, month_year: str) -> int:
    """Stamp plaintext transfer link keys onto existing linked income rows."""
    if not household_id or not month_year:
        return 0

    incomes_table = get_budget_table("household_incomes")
    stamped = 0
    for transfer in get_member_transfers(household_id, month_year):
        if transfer.get("status") != "completed":
            continue
        pairs = [
            ("personal_allowance_income_id", ALLOWANCE_INCOME_SOURCE_NAME),
            ("personal_obligation_income_id", OBLIGATION_SUPPORT_INCOME_SOURCE_NAME),
        ]
        for field, source_name in pairs:
            income_id = transfer.get(field)
            if not income_id:
                continue
            if _income_id_is_allowance_expense_mirror(income_id):
                continue
            link_key = member_transfer_income_link_key(transfer["id"], source_name)
            existing_link = _find_income_id_by_member_transfer_link(link_key)
            if existing_link and str(existing_link) != str(income_id):
                continue
            try:
                supabase.table(incomes_table).update(
                    {"source_member_transfer_id": link_key}
                ).eq("id", str(income_id)).execute()
                stamped += 1
            except Exception as e:
                print(f"Error stamping transfer link on income {income_id}: {e}")
    return stamped


def _other_transfer_linked_income_ids(
    household_id: str,
    month_year: str,
    *,
    exclude_transfer_id=None,
) -> set[str]:
    """Income ids already claimed by a different member transfer row."""
    claimed: set[str] = set()
    for transfer in get_member_transfers(household_id, month_year):
        if exclude_transfer_id and str(transfer.get("id")) == str(exclude_transfer_id):
            continue
        income_id = transfer.get("personal_allowance_income_id")
        if income_id:
            claimed.add(str(income_id))
    return claimed


def _upsert_personal_transfer_income(
    *,
    household_id: str,
    month_year: str,
    recipient: str,
    pay_date_str: str,
    source_name: str,
    amount: float,
    existing_id=None,
    transfer_id=None,
) -> str | None:
    """Insert or update a one-time personal income row for a member transfer."""
    incomes_table = get_budget_table("household_incomes")
    safe_amt = round(float(amount), 2)
    if safe_amt <= 0:
        return existing_id
    link_key = (
        member_transfer_income_link_key(transfer_id, source_name)
        if transfer_id
        else None
    )
    payload = {
        "household_id": household_id,
        "month_year": month_year,
        "source_name": encrypt_data(source_name),
        "take_home_amount": encrypt_data(safe_amt),
        "gross_amount": encrypt_data(safe_amt),
        "is_taxable": False,
        "owner_username": recipient,
        "is_windfall": False,
        "is_recurring": False,
        "pay_frequency": "one_time",
        "is_personal_income": True,
        "payment_date": pay_date_str,
        "stream_id": None,
        "version_id": None,
    }
    if link_key:
        payload["source_member_transfer_id"] = link_key
    try:
        if link_key:
            by_link = _find_income_id_by_member_transfer_link(link_key)
            if by_link and not _income_id_is_allowance_expense_mirror(by_link):
                supabase.table(incomes_table).update(payload).eq("id", by_link).execute()
                return by_link

        claimed_elsewhere = _other_transfer_linked_income_ids(
            household_id,
            month_year,
            exclude_transfer_id=transfer_id,
        )

        if existing_id and str(existing_id) in claimed_elsewhere:
            existing_id = None

        if existing_id:
            still_exists = (
                supabase.table(incomes_table)
                .select("id")
                .eq("id", str(existing_id))
                .limit(1)
                .execute()
            ).data
            if still_exists and not (transfer_id and _income_id_is_allowance_expense_mirror(existing_id)):
                supabase.table(incomes_table).update(payload).eq("id", str(existing_id)).execute()
                return str(existing_id)

        if transfer_id:
            res = supabase.table(incomes_table).insert(payload).execute()
            return (res.data or [{}])[0].get("id")

        candidates = (
            supabase.table(incomes_table)
            .select("id, source_name, take_home_amount")
            .eq("household_id", household_id)
            .eq("month_year", month_year)
            .eq("owner_username", recipient)
            .eq("is_personal_income", True)
            .eq("payment_date", pay_date_str)
            .execute()
        ).data or []
        matching_ids: list[str] = []
        for candidate in candidates:
            cand_id = str(candidate["id"])
            if cand_id in claimed_elsewhere:
                continue
            dec_name = decrypt_text(candidate.get("source_name")) if candidate.get("source_name") else ""
            if dec_name != source_name:
                continue
            cand_amt = (
                decrypt_float(candidate.get("take_home_amount"))
                if candidate.get("take_home_amount") is not None
                else 0.0
            )
            if round(float(cand_amt), 2) != safe_amt:
                continue
            matching_ids.append(cand_id)
        if len(matching_ids) == 1:
            keeper = matching_ids[0]
            supabase.table(incomes_table).update(payload).eq("id", keeper).execute()
            return keeper

        res = supabase.table(incomes_table).insert(payload).execute()
        return (res.data or [{}])[0].get("id")
    except Exception as e:
        print(f"Error upserting personal income ({source_name}) for {recipient}: {e}")
        return str(existing_id) if existing_id else None


def _delete_personal_transfer_income(income_id) -> bool:
    """Delete a transfer-synced personal income row (system-managed, no UI permission gate)."""
    if not income_id:
        return False
    target_table = get_budget_table("household_incomes")
    try:
        supabase.table(target_table).delete().eq("id", str(income_id)).execute()
        return True
    except Exception as e:
        print(f"Error deleting personal transfer income {income_id}: {e}")
        return False


def _get_completed_transfers_for_recipient(household_id: str, username: str) -> list[dict]:
    try:
        rows = (
            supabase.table(get_member_transfers_table())
            .select("*")
            .eq("household_id", household_id)
            .eq("recipient_username", username)
            .eq("status", "completed")
            .order("payment_date")
            .execute()
        ).data or []
        return [_decrypt_member_transfer(r) for r in rows]
    except Exception as e:
        print(f"Error fetching completed transfers for {username}: {e}")
        return []


def _sync_allowance_transfer_income_for_member(household_id: str, username: str) -> None:
    """Ensure personal Allowance income exists for all completed transfers (always on)."""
    table = get_member_transfers_table()
    now_ts = datetime.now(ZoneInfo("America/Chicago")).isoformat()
    for transfer in _get_completed_transfers_for_recipient(household_id, username):
        allowance_amt = float(transfer.get("allowance_amount") or 0)
        if allowance_amt <= 0:
            continue
        pay_date_str = str(transfer.get("payment_date") or "")[:10]
        month_year = transfer.get("month_year")
        allowance_income_id = transfer.get("personal_allowance_income_id")
        new_id = _upsert_personal_transfer_income(
            household_id=household_id,
            month_year=month_year,
            recipient=username,
            pay_date_str=pay_date_str,
            source_name=ALLOWANCE_INCOME_SOURCE_NAME,
            amount=allowance_amt,
            existing_id=allowance_income_id,
            transfer_id=transfer.get("id"),
        )
        if new_id and str(new_id) != str(allowance_income_id or ""):
            supabase.table(table).update({
                "personal_allowance_income_id": str(new_id),
                "updated_at": now_ts,
            }).eq("id", transfer.get("id")).execute()


def _sync_transfer_personal_income_for_member(household_id: str, username: str, *, enabled: bool) -> None:
    """Apply or remove Obligation Support personal income for completed transfers.

    Allowance transfer income is always synced separately and is not gated by integration.
    """
    _sync_allowance_transfer_income_for_member(household_id, username)
    table = get_member_transfers_table()
    now_ts = datetime.now(ZoneInfo("America/Chicago")).isoformat()
    for transfer in _get_completed_transfers_for_recipient(household_id, username):
        transfer_id = transfer.get("id")
        pay_date_str = str(transfer.get("payment_date") or "")[:10]
        month_year = transfer.get("month_year")
        obl_amt = float(transfer.get("obligation_amount") or 0)
        obl_income_id = transfer.get("personal_obligation_income_id")
        updates = {}

        if enabled and obl_amt > 0:
            new_id = _upsert_personal_transfer_income(
                household_id=household_id,
                month_year=month_year,
                recipient=username,
                pay_date_str=pay_date_str,
                source_name=OBLIGATION_SUPPORT_INCOME_SOURCE_NAME,
                amount=obl_amt,
                existing_id=obl_income_id,
                transfer_id=transfer_id,
            )
            if new_id and str(new_id) != str(obl_income_id or ""):
                updates["personal_obligation_income_id"] = str(new_id)
        elif not enabled and obl_income_id:
            if _delete_personal_transfer_income(obl_income_id):
                updates["personal_obligation_income_id"] = None

        if updates:
            updates["updated_at"] = now_ts
            supabase.table(table).update(updates).eq("id", transfer_id).execute()


def _sync_obligation_personal_income_for_member(household_id: str, username: str, *, enabled: bool) -> None:
    """Deprecated alias — use _sync_transfer_personal_income_for_member."""
    _sync_transfer_personal_income_for_member(household_id, username, enabled=enabled)


def _transfer_rows_from_disbursement_schedule(household_id, month_year) -> list[dict]:
    """Build planned transfer row dicts from the computed paycheck schedule."""
    plan = compute_household_disbursement_plan(household_id, month_year)
    schedule = plan.get("paycheck_schedule") or []
    rows: list[dict] = []
    for entry in schedule:
        pay_date_str = str(entry["payment_date"])[:10]
        stream_id = entry.get("stream_id")
        for member, parts in (entry.get("payouts") or {}).items():
            rows.append({
                "payment_date": pay_date_str,
                "stream_id": stream_id,
                "recipient_username": member,
                "obligation": float(parts.get("obligation") or 0),
                "allowance": float(parts.get("allowance") or 0),
            })
    return rows


def upsert_planned_transfers_from_schedule(
    household_id,
    month_year,
    *,
    override_rows: list[dict] | None = None,
    force: bool = False,
) -> int:
    """Materialize planned transfer rows from the disbursement plan.

    Idempotent per (household, month, payment_date, recipient, stream_id).
    Returns the number of rows inserted or updated.

    Args:
        override_rows: Optional list of dicts with keys
            {payment_date, stream_id, recipient_username, obligation, allowance}.
            When provided, these amounts are used instead of the auto-computed plan.
            Useful when the user edits amounts in the UI before saving.
        force: When True (disbursement reset), replace existing rows for the month
            instead of skipping completed or already-planned matches.
    """
    if not _can_edit_monthly_budget_server_side():
        return 0

    if override_rows is not None:
        rows_to_insert = override_rows
    else:
        rows_to_insert = _transfer_rows_from_disbursement_schedule(household_id, month_year)

    if not rows_to_insert:
        return 0

    table = get_member_transfers_table()
    existing_rows = get_member_transfers(household_id, month_year)
    # Key → full row so we can compare amounts and check status before updating
    existing_by_key = {
        (r["payment_date"][:10], r["recipient_username"], str(r.get("funding_income_stream_id") or "")): r
        for r in existing_rows
        if r.get("payment_date") and r.get("recipient_username")
    }

    inserted = 0
    updated = 0
    for row in rows_to_insert:
        pay_date_str = str(row["payment_date"])[:10]
        member = row["recipient_username"]
        stream_id = row.get("stream_id")
        key = (pay_date_str, member, str(stream_id or ""))
        obl = round(float(row.get("obligation") or 0), 2)
        allow = round(float(row.get("allowance") or 0), 2)
        total = round(obl + allow, 2)

        existing = existing_by_key.get(key)
        if existing:
            if existing.get("status") == "completed" and not force:
                continue
            if override_rows is None and not force:
                continue
            if force:
                _clear_transfer_side_effects(existing)
                try:
                    supabase.table(table).delete().eq("id", str(existing["id"])).execute()
                except Exception as e:
                    print(f"Error replacing transfer ({pay_date_str}, {member}): {e}")
                    continue
                existing_by_key.pop(key, None)
                existing = None
            else:
                # override_rows mode: update planned rows if amounts changed
                existing_obl = round(float(existing.get("obligation_amount") or 0), 2)
                existing_allow = round(float(existing.get("allowance_amount") or 0), 2)
                if existing_obl == obl and existing_allow == allow:
                    continue  # No change — skip
                update_payload = {
                    "obligation_amount": encrypt_data(obl),
                    "allowance_amount": encrypt_data(allow),
                    "total_amount": encrypt_data(total),
                    "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
                }
                try:
                    supabase.table(table).update(update_payload).eq("id", str(existing["id"])).execute()
                    updated += 1
                except Exception as e:
                    print(f"Error updating planned transfer ({pay_date_str}, {member}): {e}")
                continue

        # Row doesn't exist (or was deleted for force replace) — insert it
        payload = {
            "household_id": household_id,
            "month_year": month_year,
            "payment_date": pay_date_str,
            "recipient_username": member,
            "funding_income_stream_id": str(stream_id) if stream_id else None,
            "allowance_amount": encrypt_data(allow),
            "obligation_amount": encrypt_data(obl),
            "total_amount": encrypt_data(total),
            "status": "planned",
        }
        try:
            supabase.table(table).insert(payload).execute()
            inserted += 1
        except Exception as e:
            print(f"Error inserting planned transfer ({pay_date_str}, {member}, {stream_id}): {e}")

    return inserted + updated


def reset_disbursement_plan_transfers(household_id, month_year) -> dict:
    """Reset a month's disbursement plan from the current computed schedule.

    Clears completed/planned transfer rows for the month (and linked side effects),
    removes orphan transfer expenses/incomes, then materializes a fresh planned schedule.
    """
    empty = {
        "cleared": 0,
        "inserted": 0,
        "orphan_expenses": 0,
        "orphan_incomes": 0,
        "permission_denied": False,
    }
    if not _can_edit_monthly_budget_server_side():
        return {**empty, "permission_denied": True}

    table = get_member_transfers_table()
    existing_rows = get_member_transfers(household_id, month_year)
    cleared = 0
    for transfer in existing_rows:
        _clear_transfer_side_effects(transfer)
        try:
            supabase.table(table).delete().eq("id", str(transfer["id"])).execute()
            cleared += 1
        except Exception as e:
            print(f"Error deleting transfer {transfer.get('id')}: {e}")

    orphan_stats = cleanup_orphan_disbursement_artifacts(household_id, month_year)
    inserted = upsert_planned_transfers_from_schedule(household_id, month_year, force=True)

    return {
        "cleared": cleared,
        "inserted": inserted,
        "orphan_expenses": orphan_stats.get("expenses", 0),
        "orphan_incomes": orphan_stats.get("incomes", 0),
    }


def auto_materialize_disbursement_plan(household_id, month_year) -> int:
    """Create missing planned transfer rows for a month from the computed schedule.

    Idempotent: never overwrites existing planned or completed rows. Intended to run
    automatically when a new month is opened so users do not need to click Plan transfers
    every month.
    """
    if not _can_edit_monthly_budget_server_side():
        return 0
    return upsert_planned_transfers_from_schedule(household_id, month_year)


def _apply_member_transfer_completion(row: dict, *, actor_username: str | None = None) -> bool:
    """Mark a transfer completed and sync linked personal income rows."""
    table = get_member_transfers_table()
    transfer_id = row.get("id")
    if not transfer_id:
        return False

    recipient = row["recipient_username"]
    month_year = row["month_year"]
    pay_date_str = row["payment_date"][:10]
    household_id = row["household_id"]
    allowance_amt = float(row.get("allowance_amount") or 0)
    obligation_amt = float(row.get("obligation_amount") or 0)

    now_ts = datetime.now(ZoneInfo("America/Chicago")).isoformat()
    actor = actor_username
    if actor is None:
        try:
            actor = st.session_state.get("username")
        except Exception:
            actor = None
    if not actor:
        actor = "auto"

    personal_allowance_income_id = row.get("personal_allowance_income_id")
    personal_obligation_income_id = row.get("personal_obligation_income_id")

    integrated = get_personal_household_integration(household_id, recipient)

    if allowance_amt > 0:
        personal_allowance_income_id = _upsert_personal_transfer_income(
            household_id=household_id,
            month_year=month_year,
            recipient=recipient,
            pay_date_str=pay_date_str,
            source_name=ALLOWANCE_INCOME_SOURCE_NAME,
            amount=allowance_amt,
            existing_id=personal_allowance_income_id,
            transfer_id=transfer_id,
        )

    if integrated and obligation_amt > 0:
        personal_obligation_income_id = _upsert_personal_transfer_income(
            household_id=household_id,
            month_year=month_year,
            recipient=recipient,
            pay_date_str=pay_date_str,
            source_name=OBLIGATION_SUPPORT_INCOME_SOURCE_NAME,
            amount=obligation_amt,
            existing_id=personal_obligation_income_id,
            transfer_id=transfer_id,
        )
    elif not integrated and personal_obligation_income_id:
        if _delete_personal_transfer_income(personal_obligation_income_id):
            personal_obligation_income_id = None

    household_allowance_expense_id = _sync_transfer_allowance_household_expense(row)
    if household_allowance_expense_id:
        _delete_allowance_income_for_expense(household_allowance_expense_id)

    update_payload = {
        "status": "completed",
        "transferred_at": now_ts,
        "transferred_by": actor,
        "personal_allowance_income_id": str(personal_allowance_income_id) if personal_allowance_income_id else None,
        "personal_obligation_income_id": str(personal_obligation_income_id) if personal_obligation_income_id else None,
        "household_allowance_expense_id": str(household_allowance_expense_id) if household_allowance_expense_id else None,
        "updated_at": now_ts,
    }
    try:
        supabase.table(table).update(update_payload).eq("id", transfer_id).execute()
        return True
    except Exception as e:
        print(f"Error completing transfer {transfer_id}: {e}")
        return False


def ensure_completed_transfer_allowance_expenses(household_id, month_year) -> int:
    """Backfill HH allowance expenses for completed transfers missing a linked expense row."""
    if not household_id:
        return 0
    table = get_member_transfers_table()
    updated = 0
    for transfer in get_member_transfers(household_id, month_year):
        if transfer.get("status") != "completed":
            continue
        if round(float(transfer.get("allowance_amount") or 0), 2) <= 0:
            continue
        if transfer.get("household_allowance_expense_id"):
            continue
        expense_id = _sync_transfer_allowance_household_expense(transfer)
        if not expense_id:
            continue
        _delete_allowance_income_for_expense(expense_id)
        try:
            supabase.table(table).update(
                {
                    "household_allowance_expense_id": str(expense_id),
                    "updated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
                }
            ).eq("id", str(transfer["id"])).execute()
            updated += 1
        except Exception as e:
            print(f"Error linking allowance expense to transfer {transfer.get('id')}: {e}")
    return updated


def auto_complete_due_member_transfers(household_id, as_of=None) -> int:
    """Complete planned transfers on or after their payment date (session automation)."""
    if not household_id:
        return 0
    completed = 0
    for row in get_due_planned_member_transfers(household_id, as_of=as_of):
        if _apply_member_transfer_completion(row, actor_username="auto"):
            completed += 1
    return completed


def complete_due_member_transfers(household_id, as_of=None) -> int:
    """Admin-triggered bulk completion for planned transfers on or before today.

    Returns the number completed, 0 when none are due, or -1 when not permitted.
    """
    if not _can_edit_monthly_budget_server_side():
        return -1
    return auto_complete_due_member_transfers(household_id, as_of=as_of)


def complete_member_transfer(transfer_id: str) -> bool:
    """Mark a planned transfer as completed and sync personal income rows.

    Allowance income is always created on the recipient's personal ledger.
    Obligation Support income is created only when integrate_household_on_personal is on.
    """
    if not _can_edit_monthly_budget_server_side():
        return False
    table = get_member_transfers_table()
    try:
        rows = (
            supabase.table(table).select("*").eq("id", transfer_id).limit(1).execute()
        ).data or []
    except Exception as e:
        print(f"Error fetching transfer {transfer_id}: {e}")
        return False
    if not rows:
        return False

    row = _decrypt_member_transfer(rows[0])
    return _apply_member_transfer_completion(row)


def get_personal_household_integration(household_id, username) -> bool:
    """Return whether household is integrated into this member's personal budget."""
    try:
        rows = (
            supabase.table(get_budget_table("user_finance_settings"))
            .select("integrate_household_on_personal, show_obligation_transfers_on_personal")
            .eq("household_id", household_id)
            .eq("username", username)
            .limit(1)
            .execute()
        ).data or []
        if rows:
            row = rows[0]
            if row.get("integrate_household_on_personal") is not None:
                return bool(row["integrate_household_on_personal"])
            return bool(row.get("show_obligation_transfers_on_personal", False))
    except Exception as e:
        print(f"Error fetching household integration for {username}: {e}")
    return False


def _get_obligation_transfer_visibility(household_id, username) -> bool:
    """Deprecated alias — use get_personal_household_integration."""
    return get_personal_household_integration(household_id, username)


def _can_edit_own_finance_settings(username) -> bool:
    return bool(username) and username == st.session_state.get("username")


def update_personal_household_integration(household_id, username, enabled: bool) -> bool:
    """Toggle integrate_household_on_personal for a member (self-service or admin)."""
    if not (_can_edit_monthly_budget_server_side() or _can_edit_own_finance_settings(username)):
        return False
    settings_table = get_budget_table("user_finance_settings")
    try:
        existing = (
            supabase.table(settings_table)
            .select("id")
            .eq("household_id", household_id)
            .eq("username", username)
            .limit(1)
            .execute()
        ).data or []
        flag = bool(enabled)
        payload = {
            "integrate_household_on_personal": flag,
            "show_obligation_transfers_on_personal": flag,
        }
        if existing:
            supabase.table(settings_table).update(payload).eq("id", existing[0]["id"]).execute()
        else:
            payload.update({
                "household_id": household_id,
                "username": username,
                "share_budget_with_admin": False,
            })
            supabase.table(settings_table).insert(payload).execute()
        _sync_transfer_personal_income_for_member(household_id, username, enabled=flag)
        return True
    except Exception as e:
        print(f"Error updating household integration for {username}: {e}")
        return False


def update_obligation_transfer_visibility(household_id, username, enabled: bool) -> bool:
    """Deprecated alias — use update_personal_household_integration."""
    return update_personal_household_integration(household_id, username, enabled)


_TRANSFER_INCOME_SOURCES = {
    OBLIGATION_SUPPORT_INCOME_SOURCE_NAME,
}


def get_personal_ledger_incomes(household_id, month_year, username) -> pd.DataFrame:
    """Personal ledger incomes: native personal rows plus optional household mirror.

    Allowance income always appears (personal spend, not household-linked).
    Obligation Support and household paycheck mirror require integration to be on.
    """
    integrated = get_personal_household_integration(household_id, username)
    personal_df = get_household_incomes(
        household_id, month_year, is_personal_income=True, username=username
    )
    frames = []

    if personal_df is not None and not personal_df.empty:
        work = personal_df.copy()
        if not integrated:
            work = work[~work["source_name"].isin(_TRANSFER_INCOME_SOURCES)]
        if not work.empty:
            work["ledger_source"] = "personal"
            obl_mask = work["source_name"] == OBLIGATION_SUPPORT_INCOME_SOURCE_NAME
            work.loc[obl_mask, "ledger_source"] = "transfer"
            frames.append(work)

    if integrated:
        hh_df = get_household_incomes(household_id, month_year, is_personal_income=False)
        if hh_df is not None and not hh_df.empty:
            member_key = _username_key(username)
            mirror_rows = []
            for _, row in hh_df.iterrows():
                if _income_row_allowance_linked(row):
                    continue
                if _username_key(row.get("owner_username")) != member_key:
                    continue
                if not _freq_is_obligation_regular_pay(_income_row_frequency(row)):
                    continue
                mirror = row.to_dict()
                mirror["ledger_source"] = "household_mirror"
                mirror["id"] = f"mirror_{row.get('id')}"
                mirror_rows.append(mirror)
            if mirror_rows:
                frames.append(pd.DataFrame(mirror_rows))

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_member_obligation_parent_names(household_id, username) -> list[str]:
    """Parent category names assigned to a member for obligation spending."""
    member_key = _username_key(username)
    if not member_key:
        return []
    parents = set()
    for assignment in get_obligation_assignments(household_id):
        if assignment.get("assignment_level") != "parent":
            continue
        if not assignment.get("is_active", True):
            continue
        if _username_key(assignment.get("member_username")) != member_key:
            continue
        parent = (assignment.get("parent_category_name") or "").strip()
        if parent:
            parents.add(parent)
    return sorted(parents)


def get_member_obligation_expense_categories(household_id, username) -> pd.DataFrame:
    """Household expense categories assigned to a member for obligation spending."""
    member_key = _username_key(username)
    if not member_key:
        return pd.DataFrame()

    categories_df = get_budget_categories(household_id, is_personal=False)
    if categories_df is None or categories_df.empty:
        return pd.DataFrame()

    category_rows = [
        row.to_dict()
        for _, row in categories_df.iterrows()
        if is_assignable_household_category(row)
    ]
    if not category_rows:
        return pd.DataFrame()

    parent_map, override_map = build_assignment_maps(get_obligation_assignments(household_id))
    lines = resolve_obligation_lines(category_rows, parent_map, override_map)
    member_cat_ids = {
        str(line["category_id"])
        for line in lines
        if _username_key(line.get("member_username")) == member_key
        and line.get("source") in ("parent", "override")
    }
    if not member_cat_ids:
        return pd.DataFrame()

    return categories_df[categories_df["id"].astype(str).isin(member_cat_ids)].copy()


def insert_obligation_subcategory(
    household_id,
    username,
    parent_category_name,
    sub_category_name,
    target_budget=0.0,
) -> bool:
    """Add a sub-category under an assigned obligation parent (integration must be on).

    Reactivates a previously removed sub-category when the same parent/sub name exists.
    """
    if not get_personal_household_integration(household_id, username):
        return False
    if not (_can_edit_own_finance_settings(username) or _is_budget_privileged()):
        return False
    parent = (parent_category_name or "").strip()
    sub = (sub_category_name or "").strip()
    if not parent or not sub:
        return False
    if parent not in set(get_member_obligation_parent_names(household_id, username)):
        return False
    if is_system_project_expense_category(parent, sub):
        return False
    if is_system_managed_allowance_category(parent, sub):
        return False

    inactive_id = _find_inactive_obligation_subcategory_id(household_id, parent, sub)
    if inactive_id:
        return reactivate_obligation_subcategory(household_id, username, inactive_id, target_budget=target_budget)

    target_table = get_budget_table("budget_categories")
    try:
        safe_target = float(target_budget) if target_budget not in [None, ""] else 0.0
        payload = {
            "household_id": household_id,
            "category_name": encrypt_data(parent),
            "sub_category_name": encrypt_data(sub),
            "is_active": True,
            "is_personal": False,
            "username": None,
            "target_budget": encrypt_data(safe_target),
        }
        supabase.table(target_table).insert(payload).execute()
        return True
    except Exception as e:
        print(f"Error inserting obligation sub-category: {e}")
        return False


def _find_inactive_obligation_subcategory_id(household_id, parent_category_name, sub_category_name) -> str | None:
    inactive = get_member_obligation_inactive_subcategories(household_id, username=None)
    if inactive is None or inactive.empty:
        return None
    parent = (parent_category_name or "").strip()
    sub = (sub_category_name or "").strip()
    for _, row in inactive.iterrows():
        if row.get("category_name") == parent and row.get("sub_category_name") == sub:
            return str(row.get("id"))
    return None


def get_member_obligation_inactive_subcategories(household_id, username=None) -> pd.DataFrame:
    """Inactive household sub-categories under obligation-assigned parents."""
    if username:
        parents = set(get_member_obligation_parent_names(household_id, username))
    else:
        parents = set()
        for assignment in get_obligation_assignments(household_id):
            if assignment.get("assignment_level") != "parent":
                continue
            parent = (assignment.get("parent_category_name") or "").strip()
            if parent:
                parents.add(parent)
    if not parents:
        return pd.DataFrame()

    target_table = get_budget_table("budget_categories")
    try:
        response = (
            supabase.table(target_table)
            .select("*")
            .eq("household_id", household_id)
            .eq("is_active", False)
            .eq("is_personal", False)
            .execute()
        )
        if not response.data:
            return pd.DataFrame()
        rows = []
        for row in response.data:
            if row.get("category_name"):
                row["category_name"] = decrypt_text(row.get("category_name"))
            if row.get("sub_category_name"):
                row["sub_category_name"] = decrypt_text(row.get("sub_category_name"))
            if row.get("target_budget") is not None:
                row["target_budget"] = decrypt_float(row.get("target_budget"))
            else:
                row["target_budget"] = 0.0
            sub = row.get("sub_category_name")
            if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
                continue
            if row.get("category_name") not in parents:
                continue
            rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        print(f"Error fetching inactive obligation sub-categories: {e}")
        return pd.DataFrame()


def reactivate_obligation_subcategory(
    household_id,
    username,
    category_id,
    *,
    target_budget=None,
) -> bool:
    """Restore a previously removed obligation sub-category."""
    if not get_personal_household_integration(household_id, username):
        return False
    if not (_can_edit_own_finance_settings(username) or _is_budget_privileged()):
        return False

    inactive = get_member_obligation_inactive_subcategories(household_id, username)
    if inactive is None or inactive.empty:
        return False
    match = inactive[inactive["id"].astype(str) == str(category_id)]
    if match.empty:
        return False

    target_table = get_budget_table("budget_categories")
    payload = {"is_active": True}
    if target_budget is not None and target_budget != "":
        payload["target_budget"] = encrypt_data(float(target_budget))
    try:
        supabase.table(target_table).update(payload).eq("id", category_id).execute()
        return True
    except Exception as e:
        print(f"Error reactivating obligation sub-category: {e}")
        return False


def deactivate_obligation_subcategory(household_id, username, category_id) -> bool:
    """Soft-delete a sub-category under an assigned obligation parent."""
    if not get_personal_household_integration(household_id, username):
        return False
    if not (_can_edit_own_finance_settings(username) or _is_budget_privileged()):
        return False
    cats = get_member_obligation_expense_categories(household_id, username)
    if cats is None or cats.empty:
        return False
    match = cats[cats["id"].astype(str) == str(category_id)]
    if match.empty:
        return False
    row = match.iloc[0]
    sub = row.get("sub_category_name")
    if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
        return False
    if is_system_project_expense_category(row.get("category_name"), sub):
        return False
    if is_system_managed_allowance_category(row.get("category_name"), sub):
        return False

    target_table = get_budget_table("budget_categories")
    try:
        supabase.table(target_table).update({"is_active": False}).eq("id", category_id).execute()
        return True
    except Exception as e:
        print(f"Error deactivating obligation sub-category: {e}")
        return False


def get_personal_ledger_expenses(household_id, auth_user_id, month_year, username) -> pd.DataFrame:
    """Personal ledger expenses: native personal rows plus obligation household rows when integrated."""
    all_df = get_individual_expenses(household_id, auth_user_id, month_year)
    if all_df is None or all_df.empty:
        return pd.DataFrame()

    integrated = get_personal_household_integration(household_id, username)
    frames = []

    personal = all_df[all_df["is_personal_spend"] == True].copy()
    if not personal.empty:
        personal["ledger_source"] = "personal"
        frames.append(personal)

    if integrated:
        obl_cats = get_member_obligation_expense_categories(household_id, username)
        if obl_cats is not None and not obl_cats.empty:
            obl_ids = set(obl_cats["id"].astype(str))
            household = all_df[all_df["is_personal_spend"] == False].copy()
            if not household.empty:
                household = household[household["category_id"].astype(str).isin(obl_ids)]
                if not household.empty:
                    household["ledger_source"] = "household_obligation"
                    frames.append(household)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def log_household_expense_from_personal(
    auth_user_id,
    username,
    household_id,
    month_year,
    date_logged,
    category_id,
    amount,
    details,
    *,
    is_recurring=False,
    pay_frequency=None,
) -> bool:
    """Log a household expense against a member's assigned obligation categories."""
    allowed = get_member_obligation_expense_categories(household_id, username)
    if allowed is None or allowed.empty:
        return False
    if str(category_id) not in allowed["id"].astype(str).tolist():
        return False
    return log_expense_and_check_project(
        auth_user_id=auth_user_id,
        username=username,
        household_id=household_id,
        month_year=month_year,
        date_logged=date_logged,
        category_id=category_id,
        amount=amount,
        details=details,
        is_personal_spend=False,
        is_recurring=is_recurring,
        pay_frequency=pay_frequency,
    )


# ===========================================================================
# Receipt upload CRUD
# ===========================================================================

def get_receipt_uploads_table():
    return get_budget_table("receipt_uploads")


def get_receipt_line_items_table():
    return get_budget_table("receipt_line_items")


def create_receipt_upload(
    household_id: str,
    file_name: str,
    mime_type: str,
    storage_path: str | None = None,
) -> str | None:
    """Insert a receipt_uploads row and return its UUID."""
    auth_user_id = st.session_state.get("auth_user_id")
    username = st.session_state.get("username", "")
    if not auth_user_id or not username:
        return None
    table = get_receipt_uploads_table()
    try:
        payload = {
            "household_id": household_id,
            "uploaded_by_username": username,
            "uploaded_by_auth_user_id": auth_user_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "status": "draft",
            "ocr_status": "pending",
        }
        if storage_path:
            payload["storage_path"] = storage_path
        resp = supabase.table(table).insert(payload).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as e:
        print(f"Error creating receipt upload: {e}")
        return None


def update_receipt_upload(receipt_id: str, **fields) -> bool:
    """Partial update of a receipt_uploads row (merchant, receipt_date, total_amount, etc.)."""
    table = get_receipt_uploads_table()
    try:
        payload = {}
        for k, v in fields.items():
            if k == "total_amount" and v is not None:
                payload[k] = encrypt_data(float(v))
            else:
                payload[k] = v
        supabase.table(table).update(payload).eq("id", receipt_id).execute()
        return True
    except Exception as e:
        print(f"Error updating receipt upload {receipt_id}: {e}")
        return False


def _decrypt_receipt_upload(row: dict) -> dict:
    out = dict(row)
    raw = out.get("total_amount")
    if raw is not None:
        out["total_amount"] = decrypt_float(raw)
    return out


def get_receipt_upload(receipt_id: str) -> dict | None:
    """Fetch a single receipt row; returns None when not found or not visible."""
    table = get_receipt_uploads_table()
    try:
        resp = supabase.table(table).select("*").eq("id", receipt_id).limit(1).execute()
        if not resp.data:
            return None
        row = _decrypt_receipt_upload(resp.data[0])
        # Visibility: uploader or admin/dev
        username = st.session_state.get("username", "")
        if row.get("uploaded_by_username") != username and not _is_budget_privileged():
            return None
        return row
    except Exception as e:
        print(f"Error fetching receipt upload {receipt_id}: {e}")
        return None


def get_draft_receipt_uploads(household_id: str, username: str) -> list[dict]:
    """Return this user's draft receipt uploads for the current month."""
    table = get_receipt_uploads_table()
    current_month = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m")
    try:
        query = (
            supabase.table(table)
            .select("*")
            .eq("household_id", household_id)
            .eq("status", "draft")
        )
        if not _is_budget_privileged():
            query = query.eq("uploaded_by_username", username)
        resp = query.order("created_at", desc=True).limit(20).execute()
        rows = []
        for r in resp.data or []:
            d = _decrypt_receipt_upload(r)
            # Filter client-side by month for non-admins
            created = str(d.get("created_at") or "")[:7]
            if not _is_budget_privileged() and created != current_month:
                continue
            rows.append(d)
        return rows
    except Exception as e:
        print(f"Error fetching draft receipts: {e}")
        return []


def delete_receipt_upload(receipt_id: str) -> bool:
    """Soft-delete (archive) a receipt. Hard delete via cascade is admin only."""
    row = get_receipt_upload(receipt_id)
    if row is None:
        return False
    table = get_receipt_uploads_table()
    try:
        supabase.table(table).update({"status": "archived"}).eq("id", receipt_id).execute()
        return True
    except Exception as e:
        print(f"Error archiving receipt {receipt_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Receipt line items
# ---------------------------------------------------------------------------

def _decrypt_line_item(row: dict) -> dict:
    out = dict(row)
    raw = out.get("line_amount")
    if raw is not None:
        out["line_amount"] = decrypt_float(raw)
    return out


def upsert_receipt_line_items(receipt_id: str, lines: list[dict]) -> bool:
    """Replace all line items for a receipt with the provided list.

    Each line dict: {line_index, description, line_amount, ledger_target,
                     category_id (opt), project_budget_id (opt), status}
    """
    table = get_receipt_line_items_table()
    try:
        supabase.table(table).delete().eq("receipt_upload_id", receipt_id).execute()
        if not lines:
            return True
        rows = []
        for i, ln in enumerate(lines):
            amt = ln.get("line_amount")
            rows.append({
                "receipt_upload_id": receipt_id,
                "line_index": int(ln.get("line_index", i)),
                "description": str(ln.get("description") or ""),
                "line_amount": encrypt_data(float(amt)) if amt is not None else None,
                "ledger_target": ln.get("ledger_target", "personal"),
                "category_id": ln.get("category_id"),
                "project_budget_id": str(ln["project_budget_id"]) if ln.get("project_budget_id") else None,
                "status": ln.get("status", "draft"),
            })
        supabase.table(table).insert(rows).execute()
        return True
    except Exception as e:
        print(f"Error upserting line items for {receipt_id}: {e}")
        return False


def get_receipt_line_items(receipt_id: str) -> list[dict]:
    """Return decrypted line items for a receipt ordered by line_index."""
    table = get_receipt_line_items_table()
    try:
        resp = (
            supabase.table(table)
            .select("*")
            .eq("receipt_upload_id", receipt_id)
            .order("line_index")
            .execute()
        )
        return [_decrypt_line_item(r) for r in (resp.data or [])]
    except Exception as e:
        print(f"Error fetching line items for {receipt_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# Uncategorized category helpers
# ---------------------------------------------------------------------------

def ensure_personal_uncategorized_category(household_id: str, username: str) -> str | None:
    """Return category_id for 'Receipt / Uncategorized' (personal), creating if needed."""
    cats = get_budget_categories(household_id, is_personal=True, username=username)
    if cats is not None and not cats.empty:
        match = cats[
            (cats["category_name"] == RECEIPT_UNCATEGORIZED["name"])
            & (cats["sub_category_name"].fillna("") == RECEIPT_UNCATEGORIZED["sub"])
        ]
        if not match.empty:
            return str(match.iloc[0]["id"])
    ok = insert_budget_category(
        household_id,
        RECEIPT_UNCATEGORIZED["name"],
        sub_category_name=RECEIPT_UNCATEGORIZED["sub"],
        is_personal=True,
        username=username,
        target_budget=0.0,
    )
    if not ok:
        return None
    cats = get_budget_categories(household_id, is_personal=True, username=username)
    if cats is None or cats.empty:
        return None
    match = cats[
        (cats["category_name"] == RECEIPT_UNCATEGORIZED["name"])
        & (cats["sub_category_name"].fillna("") == RECEIPT_UNCATEGORIZED["sub"])
    ]
    return str(match.iloc[0]["id"]) if not match.empty else None


def ensure_household_uncategorized_category(household_id: str) -> str | None:
    """Return category_id for 'Receipt / Uncategorized' (HH shared), creating if needed."""
    if not _can_edit_monthly_budget_server_side():
        return None
    cats = get_budget_categories(household_id, is_personal=False)
    if cats is not None and not cats.empty:
        match = cats[
            (cats["category_name"] == RECEIPT_UNCATEGORIZED["name"])
            & (cats["sub_category_name"].fillna("") == RECEIPT_UNCATEGORIZED["sub"])
        ]
        if not match.empty:
            return str(match.iloc[0]["id"])
    ok = insert_budget_category(
        household_id,
        RECEIPT_UNCATEGORIZED["name"],
        sub_category_name=RECEIPT_UNCATEGORIZED["sub"],
        is_personal=False,
        target_budget=0.0,
    )
    if not ok:
        return None
    cats = get_budget_categories(household_id, is_personal=False)
    if cats is None or cats.empty:
        return None
    match = cats[
        (cats["category_name"] == RECEIPT_UNCATEGORIZED["name"])
        & (cats["sub_category_name"].fillna("") == RECEIPT_UNCATEGORIZED["sub"])
    ]
    return str(match.iloc[0]["id"]) if not match.empty else None


# ---------------------------------------------------------------------------
# Receipt line post router
# ---------------------------------------------------------------------------

def post_receipt_line_item(
    line: dict,
    *,
    receipt_date,
    household_id: str,
    allow_uncategorized: bool = True,
) -> tuple[bool, str]:
    """Post one receipt line item to the appropriate ledger.

    Returns (success, human-readable message).
    On success also updates the line row's status and posted_expense_id.
    """
    auth_user_id = st.session_state.get("auth_user_id")
    username = st.session_state.get("username", "")
    line_id = line.get("id")
    amount = float(line.get("line_amount") or 0)
    description = str(line.get("description") or "").strip() or "Receipt item"
    ledger_target = str(line.get("ledger_target") or "personal")
    category_id = line.get("category_id")
    project_budget_id = line.get("project_budget_id")

    if amount <= 0:
        return False, "Amount must be greater than zero."

    month_year = receipt_date.strftime("%Y-%m") if hasattr(receipt_date, "strftime") else str(receipt_date)[:7]

    # --- Project ---
    if ledger_target == "project":
        if not project_budget_id:
            return False, "No project selected for this line."
        if not _can_edit_projects_server_side():
            return False, "You do not have permission to log project expenses."
        ok = add_project_purchase_expense(project_budget_id, receipt_date, amount, product_or_service=description)
        if ok and line_id:
            _mark_line_posted(line_id, posted_expense_id=None)
        return (ok, f"Logged ${amount:,.2f} to project.") if ok else (False, "Failed to log project expense.")

    # --- Resolve category or use uncategorized fallback ---
    if not category_id:
        if not allow_uncategorized:
            return False, "Category required. Select a category or enable uncategorized posting."
        if ledger_target in ("hh_obligation", "project"):
            return False, f"Category required for {ledger_target} expenses."
        if ledger_target == "personal":
            category_id = ensure_personal_uncategorized_category(household_id, username)
        elif ledger_target == "hh_shared":
            category_id = ensure_household_uncategorized_category(household_id)
        if not category_id:
            return False, "Could not create uncategorized placeholder category."

    # --- HH obligation ---
    if ledger_target == "hh_obligation":
        ok = log_household_expense_from_personal(
            auth_user_id=auth_user_id,
            username=username,
            household_id=household_id,
            month_year=month_year,
            date_logged=receipt_date,
            category_id=category_id,
            amount=amount,
            details=description,
        )
        if ok and line_id:
            _mark_line_posted(line_id)
        return (ok, f"Logged ${amount:,.2f} to Household (obligation).") if ok else (False, "Failed to log obligation expense. Check your category assignments.")

    # --- HH shared ---
    if ledger_target == "hh_shared":
        ok = log_expense_and_check_project(
            auth_user_id=auth_user_id,
            username=username,
            household_id=household_id,
            month_year=month_year,
            date_logged=receipt_date,
            category_id=category_id,
            amount=amount,
            details=description,
            is_personal_spend=False,
        )
        if ok and line_id:
            _mark_line_posted(line_id)
        return (ok, f"Logged ${amount:,.2f} to Household.") if ok else (False, "Failed to log household expense.")

    # --- Personal (default) ---
    ok = log_expense_and_check_project(
        auth_user_id=auth_user_id,
        username=username,
        household_id=household_id,
        month_year=month_year,
        date_logged=receipt_date,
        category_id=category_id,
        amount=amount,
        details=description,
        is_personal_spend=True,
    )
    if ok and line_id:
        _mark_line_posted(line_id)
    return (ok, f"Logged ${amount:,.2f} to Personal Ledger.") if ok else (False, "Failed to log personal expense.")


def _mark_line_posted(line_id: str, posted_expense_id=None) -> None:
    table = get_receipt_line_items_table()
    try:
        payload = {"status": "posted"}
        if posted_expense_id:
            payload["posted_expense_id"] = str(posted_expense_id)
        supabase.table(table).update(payload).eq("id", line_id).execute()
    except Exception as e:
        print(f"Error marking line {line_id} posted: {e}")


def post_all_receipt_lines(
    receipt_id: str,
    lines: list[dict],
    *,
    receipt_date,
    household_id: str,
    allow_uncategorized: bool = True,
) -> dict:
    """Post every line; returns {posted, failed, messages}."""
    posted = 0
    failed = 0
    messages = []
    for line in lines:
        if line.get("status") == "posted":
            continue
        ok, msg = post_receipt_line_item(
            line,
            receipt_date=receipt_date,
            household_id=household_id,
            allow_uncategorized=allow_uncategorized,
        )
        if ok:
            posted += 1
        else:
            failed += 1
        messages.append(msg)

    if failed == 0 and posted > 0:
        update_receipt_upload(receipt_id, status="posted",
                              posted_at=datetime.now(ZoneInfo("America/Chicago")).isoformat())
    return {"posted": posted, "failed": failed, "messages": messages}


def upload_receipt_file_to_storage(
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
    household_id: str,
    receipt_id: str,
) -> str | None:
    """Upload image/PDF bytes to Supabase Storage; return storage path or None."""
    bucket = "household-receipts"
    path = f"{household_id}/{receipt_id}/{file_name}"
    try:
        supabase.storage.from_(bucket).upload(
            path,
            file_bytes,
            {"content-type": mime_type, "upsert": "true"},
        )
        return path
    except Exception as e:
        print(f"Error uploading receipt to storage: {e}")
        return None


def get_receipt_file_url(storage_path: str) -> str | None:
    """Return a short-lived signed URL for a receipt image."""
    bucket = "household-receipts"
    try:
        resp = supabase.storage.from_(bucket).create_signed_url(storage_path, 3600)
        return resp.get("signedURL") or resp.get("signedUrl")
    except Exception as e:
        print(f"Error generating receipt signed URL: {e}")
        return None


def download_receipt_file_bytes(storage_path: str) -> bytes | None:
    """Download receipt blob bytes from Supabase Storage."""
    bucket = "household-receipts"
    try:
        return supabase.storage.from_(bucket).download(storage_path)
    except Exception as e:
        print(f"Error downloading receipt file: {e}")
        return None
