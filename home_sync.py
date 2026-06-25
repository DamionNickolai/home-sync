import streamlit as st
import pandas as pd
import time
import json
import re
from zoneinfo import ZoneInfo
try:
    from streamlit_js_eval import streamlit_js_eval
except Exception:
    streamlit_js_eval = None
from auth import check_password
from home_assist_api import fetch_ha_state
from admin_module import render_admin_sidebar_panel
from budget_module import render_budget_module
from database import get_active_tasks, get_completed_tasks, add_new_task, batch_update_tasks, update_task, get_all_backlog_items, get_current_app_version, add_backlog_item, update_backlog_item, delete_backlog_item, delete_task, cut_release, get_current_user_permissions
from utils import calculate_next_version
from supabase import create_client, Client

APP_VERSION = "0.3.0"
GET_FIT_BASELINE_VERSION = "2.1.0"
FALLBACK_TIMEZONE = "America/Chicago"


def queue_rerun_reason(reason: str) -> None:
    if reason:
        st.session_state["pending_rerun_reason"] = reason


def rerun_with_reason(reason: str) -> None:
    queue_rerun_reason(reason)
    st.rerun()


def track_rerun_diagnostics() -> None:
    diagnostics = st.session_state.get("rerun_diagnostics") or {
        "total_runs": 0,
        "reasons": {},
        "last_reason": "initial_load",
    }

    diagnostics["total_runs"] = int(diagnostics.get("total_runs", 0)) + 1
    pending_reason = st.session_state.pop("pending_rerun_reason", None)

    if pending_reason:
        reasons = diagnostics.setdefault("reasons", {})
        reasons[pending_reason] = int(reasons.get(pending_reason, 0)) + 1
        diagnostics["last_reason"] = pending_reason
    elif diagnostics["total_runs"] > 1:
        reasons = diagnostics.setdefault("reasons", {})
        reasons["widget_or_streamlit"] = int(reasons.get("widget_or_streamlit", 0)) + 1
        diagnostics["last_reason"] = "widget_or_streamlit"

    st.session_state["rerun_diagnostics"] = diagnostics


def render_rerun_debug_panel() -> None:
    if st.session_state.get("user_role") != "developer":
        return

    diagnostics = st.session_state.get("rerun_diagnostics") or {}
    reasons = diagnostics.get("reasons") or {}

    ordered_reasons = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
    reason_text = ", ".join([f"{name}:{count}" for name, count in ordered_reasons[:5]]) or "none"

    st.sidebar.caption(
        f"Reruns total: {diagnostics.get('total_runs', 0)} | "
        f"Last: {diagnostics.get('last_reason', 'initial_load')}"
    )
    st.sidebar.caption(f"Rerun buckets: {reason_text}")


def mark_top_nav_change() -> None:
    st.session_state.pop("main_dashboard_lock", None)
    queue_rerun_reason("top_nav")


def set_main_dashboard_view(view_name: str) -> None:
    if st.session_state.get("main_dashboard_view") == view_name:
        return
    st.session_state["main_dashboard_view"] = view_name
    mark_top_nav_change()
    rerun_with_reason("top_nav")


def render_main_dashboard_selector(options: list[str]) -> None:
    if not options:
        return

    current_view = st.session_state.get("main_dashboard_view", options[0])

    for idx in range(0, len(options), 2):
        row_options = options[idx:idx + 2]

        if len(row_options) == 2:
            left_opt, right_opt = row_options
            col_left, col_right = st.columns(2)

            if col_left.button(
                left_opt,
                key=f"main_dash_btn_{idx}_left",
                type="primary" if current_view == left_opt else "secondary",
                width="stretch",
            ):
                set_main_dashboard_view(left_opt)

            if col_right.button(
                right_opt,
                key=f"main_dash_btn_{idx}_right",
                type="primary" if current_view == right_opt else "secondary",
                width="stretch",
            ):
                set_main_dashboard_view(right_opt)
        else:
            only_opt = row_options[0]
            if st.button(
                only_opt,
                key=f"main_dash_btn_{idx}_full",
                type="primary" if current_view == only_opt else "secondary",
                width="stretch",
            ):
                set_main_dashboard_view(only_opt)


def render_two_col_selector(key: str, options: list, format_func=None):
    if not options:
        return None

    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]

    selected_value = st.session_state.get(key)

    for idx in range(0, len(options), 2):
        row_options = options[idx:idx + 2]

        if len(row_options) == 2:
            left_opt, right_opt = row_options
            left_label = format_func(left_opt) if format_func else str(left_opt)
            right_label = format_func(right_opt) if format_func else str(right_opt)
            col_left, col_right = st.columns(2)

            if col_left.button(
                left_label,
                key=f"{key}_btn_{idx}_left",
                type="primary" if selected_value == left_opt else "secondary",
                width="stretch",
            ):
                if selected_value != left_opt:
                    st.session_state[key] = left_opt
                    rerun_with_reason("selector_change")

            if col_right.button(
                right_label,
                key=f"{key}_btn_{idx}_right",
                type="primary" if selected_value == right_opt else "secondary",
                width="stretch",
            ):
                if selected_value != right_opt:
                    st.session_state[key] = right_opt
                    rerun_with_reason("selector_change")
        else:
            only_opt = row_options[0]
            only_label = format_func(only_opt) if format_func else str(only_opt)
            if st.button(
                only_label,
                key=f"{key}_btn_{idx}_full",
                type="primary" if selected_value == only_opt else "secondary",
                width="stretch",
            ):
                if selected_value != only_opt:
                    st.session_state[key] = only_opt
                    rerun_with_reason("selector_change")

    return st.session_state.get(key)


def is_dashboard_drilldown_active(selected_dashboard: str) -> bool:
    if selected_dashboard == "🏠 Household Hub":
        return st.session_state.get("active_hub_view", "main_menu") != "main_menu"
    if selected_dashboard == "🛠️ Developer Dashboard":
        return st.session_state.get("developer_dashboard_view", "menu") != "menu"
    return False


def request_main_dashboard_view(view_name: str) -> None:
    st.session_state["main_dashboard_lock"] = view_name
    st.session_state["pending_main_dashboard_view"] = view_name


def get_app_timezone() -> ZoneInfo:
    tz_name = st.session_state.get("user_timezone", FALLBACK_TIMEZONE)
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo(FALLBACK_TIMEZONE)


def central_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=get_app_timezone())


def to_central_timestamp(value):
    app_tz = get_app_timezone()
    try:
        ts = pd.to_datetime(value, utc=True)
        return ts.tz_convert(app_tz)
    except Exception:
        try:
            ts = pd.to_datetime(value)
            if getattr(ts, "tzinfo", None) is None:
                return ts.tz_localize(app_tz)
            return ts.tz_convert(app_tz)
        except Exception:
            return None


def initialize_user_timezone():
    if "user_timezone" not in st.session_state:
        st.session_state["user_timezone"] = FALLBACK_TIMEZONE

    # Guard against repeated mobile rerun loops from JS timezone probing.
    if st.session_state.get("user_timezone_initialized", False):
        return

    if streamlit_js_eval is None:
        st.session_state["user_timezone_initialized"] = True
        return

    detected_tz = streamlit_js_eval(
        js_expressions="Intl.DateTimeFormat().resolvedOptions().timeZone",
        key="user_timezone_detector",
    )

    if isinstance(detected_tz, str) and detected_tz.strip():
        detected_tz = detected_tz.strip()
        if st.session_state.get("user_timezone") != detected_tz:
            st.session_state["user_timezone"] = detected_tz
            st.session_state["user_timezone_initialized"] = True
            rerun_with_reason("timezone_bootstrap")

    st.session_state["user_timezone_initialized"] = True

@st.cache_resource
def get_supabase_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

@st.cache_data(ttl=3600)
def get_available_users(household_id):
    """Fetch list of available users from the database for a specific household."""
    try:
        supabase = get_supabase_client()
        # 🟢 NEW: Filter the users table by the provided household_id
        response = supabase.table("users").select("username").eq("household_id", household_id).execute()
        return [user["username"] for user in response.data] if response.data else []
    except Exception as e:
        st.warning(f"Could not fetch users: {e}")
        return []

# 1. Page Config must ALWAYS be the very first Streamlit command
st.set_page_config(page_title="Home Sync Dashboard", page_icon="🏠", layout="wide")
track_rerun_diagnostics()

# Initialize per-user timezone (device/browser) with US Central fallback.
initialize_user_timezone()

# ==========================================
# 🛠️ STATIC UI STYLESHEET
# ==========================================
st.markdown("""
    <style>
    div[data-testid="InputInstructions"] { display: none !important; }
    header [data-testid="stToolbarActionButton"] { display: none !important; }
    header { background-color: transparent !important; }
    /* Conservative readability bump: body text and captions only. */
    [data-testid="stAppViewContainer"] p,
    [data-testid="stAppViewContainer"] li,
    [data-testid="stAppViewContainer"] .stCaption,
    [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] {
        font-size: calc(1em + 2px);
    }

    /* Mobile polish: readable tap targets for any remaining radio controls. */
    @media (max-width: 768px) {
        div[data-testid="stRadio"] [role="radiogroup"] > label {
            min-height: 2.05rem;
            padding: 0.12rem 0.28rem;
            border: 1px solid rgba(148, 163, 184, 0.35);
            border-radius: 0.5rem;
            margin: 0.08rem 0;
            background: rgba(15, 23, 42, 0.08);
            box-sizing: border-box;
        }

    }
    </style>
""", unsafe_allow_html=True
)

# ==========================================
# 🔒 SECURE LOGIN
# ==========================================
is_authenticated = check_password()
if is_authenticated and st.session_state.pop("post_login_clean_rerun", False):
    rerun_with_reason("post_login_clean")

if not is_authenticated:
    st.stop()

# 🟢 FIX: Make sure this matches the key from auth.py!
user_role = st.session_state.get("user_role", "member")


def refresh_permissions_from_db() -> bool:
    """Keeps role/module access in sync with admin toggles."""
    latest = get_current_user_permissions()
    if not latest:
        return False

    st.session_state["user_role"] = latest.get("role", st.session_state.get("user_role", "member"))
    st.session_state["can_view_budget"] = bool(latest.get("can_view_budget", False))
    legacy_budget_view = bool(latest.get("can_view_budget", False))
    st.session_state["can_view_projects"] = bool(latest.get("can_view_projects", legacy_budget_view))
    st.session_state["can_edit_projects"] = bool(latest.get("can_edit_projects", legacy_budget_view))
    st.session_state["can_view_monthly_budget"] = bool(latest.get("can_view_monthly_budget", legacy_budget_view))
    st.session_state["can_edit_monthly_budget"] = bool(latest.get("can_edit_monthly_budget", False))
    st.session_state["can_view_wishlist_members"] = bool(latest.get("can_view_wishlist_members", True))
    st.session_state["can_view_wishlist_admin"] = bool(latest.get("can_view_wishlist_admin", False))
    st.session_state["permissions_refreshed_at"] = central_now().strftime("%I:%M:%S %p")
    return True


def maybe_refresh_permissions(min_interval_seconds: int = 90) -> bool:
    now_ts = int(time.time())
    last_refresh_ts = int(st.session_state.get("permissions_refreshed_ts", 0))
    should_refresh = (
        "permissions_refreshed_ts" not in st.session_state
        or now_ts - last_refresh_ts >= min_interval_seconds
        or "user_role" not in st.session_state
    )

    if not should_refresh:
        return False

    refreshed = refresh_permissions_from_db()
    if refreshed:
        st.session_state["permissions_refreshed_ts"] = now_ts
    return refreshed


maybe_refresh_permissions()

# ==========================================
# 🚧 ENVIRONMENT DETECTION & BANNER
# ==========================================
# 🟢 Moved to the very top so it renders instantly!
env = st.secrets.get("app_config", {}).get("environment", "production")
is_local_env = (env == "local")

if is_local_env:
    st.markdown(
        """
        <div style="background-color: #fef08a; padding: 12px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #facc15;">
            <h3 style="color: #b91c1c; margin: 0px; text-align: center;">
                🚧 DEV MODE ACTIVE: Writing to DEV Tables
            </h3>
        </div>
        """, 
        unsafe_allow_html=True
    )

# ==========================================
#  SIDEBAR COMMAND CENTER
# ==========================================
user_role = st.session_state.get("user_role", "member")

with st.sidebar:
    st.header("Navigation")

    # ==========================================
    # ⚙️ ADMIN PANEL (Called from our new module!)
    # ==========================================
    render_admin_sidebar_panel()

# ==========================================
# ⚙️ SIDEBAR UTILITY FOOTER 
# ==========================================
st.sidebar.divider()
if st.session_state.get("permissions_refreshed_at"):
    st.sidebar.caption(f"Permissions refreshed: {st.session_state['permissions_refreshed_at']}")

# 🟢 1. THE PANIC BUTTON (Available to everyone)
with st.sidebar.expander("🐛 Report an Issue"):
    with st.form(key="home_sync_bug_report", clear_on_submit=True):
        st.caption("Did something break or do you have an idea? Tell the developer!")
        
        if user_role in ["developer", "admin"]:
            issue_categories = ["Bug", "UI", "Core", "Ops"]
        else:
            issue_categories = ["Bug", "UI"]
            
        selected_category = st.selectbox("Type of Issue", options=issue_categories)
        bug_text = st.text_area("What happened?", placeholder="e.g., I cannot check off the trash task.")
        submit_bug = st.form_submit_button("📤 Send to Developer", type="secondary", width='stretch')
        
        if submit_bug:
            if not bug_text.strip():
                st.warning("Please type a message first.")
            else:
                with st.spinner("Sending..."):
                    active_user = st.session_state.get("username", "Unknown User")
                    feature_title = f"User Reported: {active_user}"
                    
                    success = add_backlog_item(
                        feature=feature_title, 
                        notes=bug_text.strip(), 
                        status="Backlog", 
                        app_name="home_sync", 
                        category=selected_category, 
                        priority="High"
                    )
                    
                    if success:
                        st.success("✅ Sent! Thanks for the feedback.")
                    else:
                        st.error("Failed to send the bug report.")

# 🛠️ 2. DEVELOPER TOOLS (Restricted to Devs Only)
if user_role == "developer":
    with st.sidebar.expander("🛠️ Developer Tools"):
        st.caption("Home Assistant API Status: Standby")
        # We can put API raw payloads and cache clear buttons here later

# 🔄 Public Log Out Button
if st.sidebar.button("🚪 Log Out", width='stretch'):
    from auth import clear_auth_session
    clear_auth_session()
    
    # Nuke the temporary session state
    for key in list(st.session_state.keys()):
        del st.session_state[key]
        
    # Leave the ghost flag to prevent immediate session recreation on rerun
    st.session_state["logout_in_progress"] = True
        
    st.query_params.clear() 
    rerun_with_reason("logout_action")

render_rerun_debug_panel()

# 🏷️ APPLICATION TAG
st.sidebar.caption(f"<div style='text-align: center; color: gray; padding-top: 10px;'>Home Sync Hub v{APP_VERSION}</div>", unsafe_allow_html=True)

# ==========================================
# 📋 MAIN DASHBOARD TABS
# ==========================================
if user_role == "developer":
    dashboard_sections = [
        "🏠 Household Hub",
        "☀️ Solar Production",
        "🛡️ Security",
        "🚗 Garage",        
        "⚙️ System Logs",
        "🆕 What's New",
        "🛠️ Developer Dashboard"
    ]
else:
    dashboard_sections = [
        "🏠 Household Hub",
        "☀️ Solar Production",
        "🛡️ Security",
        "🚗 Garage",        
        "⚙️ System Logs",
        "🆕 What's New"
    ]

pending_main_dashboard_view = st.session_state.pop("pending_main_dashboard_view", None)
locked_main_dashboard_view = st.session_state.get("main_dashboard_lock")

if locked_main_dashboard_view and locked_main_dashboard_view not in dashboard_sections:
    st.session_state.pop("main_dashboard_lock", None)
    locked_main_dashboard_view = None

if locked_main_dashboard_view in dashboard_sections:
    st.session_state["main_dashboard_view"] = locked_main_dashboard_view

if pending_main_dashboard_view in dashboard_sections:
    st.session_state["main_dashboard_view"] = pending_main_dashboard_view

if st.session_state.get("main_dashboard_view") not in dashboard_sections:
    st.session_state["main_dashboard_view"] = dashboard_sections[0]

active_hub_view = st.session_state.get("active_hub_view", "main_menu")
current_main_view = st.session_state.get("main_dashboard_view", dashboard_sections[0])
hide_main_dashboard_selector = is_dashboard_drilldown_active(current_main_view)

if hide_main_dashboard_selector:
    selected_dashboard_view = st.session_state.get("main_dashboard_view", dashboard_sections[0])
else:
    render_main_dashboard_selector(dashboard_sections)
    selected_dashboard_view = st.session_state.get("main_dashboard_view", dashboard_sections[0])

show_app_header = not is_dashboard_drilldown_active(selected_dashboard_view)

if show_app_header:
    st.title("🏠 Home Sync Dashboard")

if selected_dashboard_view == "🏠 Household Hub":
    # 1. Initialize the session state for this tab
    if "active_hub_view" not in st.session_state:
        st.session_state["active_hub_view"] = "main_menu"
        
    current_view = st.session_state["active_hub_view"]
    
    # ==========================================
    # VIEW: MAIN MENU (The 2x2 Card Grid)
    # ==========================================
    if current_view == "main_menu":
        st.subheader("🏠 Household Hub")
        st.caption("Select a module below to manage your household.")
        st.write("") # Spacer
        
        # Create the 2x2 Grid
        col1, col2 = st.columns(2)
        
        with col1:
            with st.container(border=True):
                st.markdown("### 📋 To-Do List")
                st.caption("Manage daily chores and household projects.")
                if st.button("Open To-Do List", type="secondary", width='stretch'):
                    st.session_state["active_hub_view"] = "todo"
                    rerun_with_reason("hub_nav")
                    
            with st.container(border=True):
                st.markdown("### 💰 Budget")
                st.caption("Track monthly spending and financial goals.")
                if st.button("Open Budget", type="secondary", width='stretch'):
                    st.session_state["active_hub_view"] = "budget"
                    rerun_with_reason("hub_nav")
                    
        with col2:
            with st.container(border=True):
                st.markdown("### 🛒 Groceries")
                st.caption("Shared family grocery list and meal prep.")
                if st.button("Open Groceries", type="secondary", width='stretch'):
                    st.session_state["active_hub_view"] = "groceries"
                    rerun_with_reason("hub_nav")
                    
            with st.container(border=True):
                st.markdown("### 📅 Calendar")
                st.caption("Family schedule, appointments, and events.")
                if st.button("Open Calendar", type="secondary", width='stretch'):
                    st.session_state["active_hub_view"] = "calendar"
                    rerun_with_reason("hub_nav")

    # ==========================================
    # VIEW: SUB-MODULES (What happens when you click a card)
    # ==========================================
    else:
        show_hub_back = current_view != "budget"
        if show_hub_back:
            # Universal "Back" button to return to the grid
            if st.button("⬅️ Back to Hub Menu"):
                st.session_state["active_hub_view"] = "main_menu"
                rerun_with_reason("hub_nav")
            st.divider()
        
        if current_view == "todo":
            st.subheader("📋 Active To-Do List")
            current_user = st.session_state.get("logged_in_user", "Unknown")            
            user_role = st.session_state.get("user_role", "member") 
            # 🟢 NEW: Grab the household ID for this session
            current_household = st.session_state.get("household_id", "unassigned")

            # Slightly larger tap targets for mobile task controls.
            st.markdown(
                """
                <style>
                div[data-testid="stCheckbox"] label {
                    min-height: 2rem;
                    align-items: center;
                }
                div[data-testid="stButton"] button {
                    min-height: 2rem;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            recurrence_options = ["Daily", "Weekly", "Biweekly", "Monthly", "Quarterly", "Every 6 Months", "Yearly"]

            def parse_assignees(assigned_to_raw):
                try:
                    if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('['):
                        return json.loads(assigned_to_raw)
                    return [assigned_to_raw]
                except Exception:
                    return [assigned_to_raw]

            def get_due_status(target_date_str, priority):
                days_remaining = None
                if target_date_str:
                    try:
                        t_date = pd.to_datetime(target_date_str).tz_localize(None).date()
                        today = central_now().tz_localize(None).date()
                        days_remaining = (t_date - today).days
                    except Exception:
                        days_remaining = None

                border_color = "#475569"
                status_msg = "⚪ No Date"

                if days_remaining is not None:
                    if days_remaining < 0:
                        border_color = "#EF4444"
                        status_msg = f"🔴 Overdue by {abs(days_remaining)}d"
                    elif days_remaining == 0:
                        border_color = "#F97316"
                        status_msg = "🟠 Due TODAY"
                    elif days_remaining == 1:
                        border_color = "#EAB308"
                        status_msg = "🟡 Due Tomorrow"
                    else:
                        border_color = "#22C55E"
                        status_msg = f"🟢 Due in {days_remaining}d"
                else:
                    if priority == "High":
                        border_color = "#3B82F6"
                        status_msg = "🔵 High Priority"
                    elif priority == "Low":
                        border_color = "#64748B"
                        status_msg = "⚪ Low Priority"

                return border_color, status_msg

            def get_due_bucket(task):
                target_str = task.get("target_date")
                if not target_str:
                    return None
                try:
                    due_date = pd.to_datetime(target_str).tz_localize(None).date()
                except Exception:
                    return None
                today_local = central_now().tz_localize(None).date()
                delta_days = (due_date - today_local).days
                if delta_days < 0:
                    return "overdue"
                if delta_days == 0:
                    return "today"
                if 1 <= delta_days <= 7:
                    return "week"
                return None

            # --- 1.5 Edit Task Form (ADMINS & DEVS ONLY) ---
            if "editing_task_id" not in st.session_state:
                st.session_state["editing_task_id"] = None

            # --- 2. Load and filter active tasks first so notifications can stay at the top ---
            all_active_tasks = get_active_tasks()
            active_tasks = []
            for task in all_active_tasks:
                assigned_to_raw = task.get("assigned_to", "Unassigned")
                assignees = parse_assignees(assigned_to_raw)
                if user_role in ["developer", "admin"] or current_user in assignees:
                    active_tasks.append(task)

            # Notifications now sit at the top of the To-Do page.
            overdue_count = sum(1 for t in active_tasks if get_due_bucket(t) == "overdue")
            due_today_count = sum(1 for t in active_tasks if get_due_bucket(t) == "today")
            due_week_count = sum(1 for t in active_tasks if get_due_bucket(t) == "week")
            with st.container(border=True):
                st.markdown("#### 🔔 Task Notifications")
                st.markdown(
                    f"""
                    <div style=\"display:flex; gap:14px; flex-wrap:nowrap; overflow-x:auto;\">
                        <div style=\"min-width:140px;\">
                            <div style=\"font-size:0.82em; color:#64748B;\">Overdue</div>
                            <div style=\"font-weight:700; color:#B91C1C; font-size:1.35em;\">{overdue_count}</div>
                        </div>
                        <div style=\"min-width:140px;\">
                            <div style=\"font-size:0.82em; color:#64748B;\">Due Today</div>
                            <div style=\"font-weight:700; color:#C2410C; font-size:1.35em;\">{due_today_count}</div>
                        </div>
                        <div style=\"min-width:160px;\">
                            <div style=\"font-size:0.82em; color:#64748B;\">Due in One Week</div>
                            <div style=\"font-weight:700; font-size:1.35em;\">{due_week_count}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            
            # --- 1. Form to Add a New Task (EVERYONE CAN ADD) ---
            with st.expander("➕ Add New Task", expanded=False):
                with st.form("new_task_form", clear_on_submit=True):
                    col1, col2 = st.columns([3, 1])
                    new_task = col1.text_input("Task", placeholder="e.g., Change HVAC filter")
                    priority = col2.selectbox("Priority", ["Normal", "High", "Low"])

                    new_notes = st.text_area("Notes", placeholder="Optional: tips, tools, or helper notes")
                    
                    col3, col4, col5 = st.columns(3)
                    category = col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"])
                    
                    # ROLE CHECK: How to handle the "Assign To" field
                    if user_role in ["developer", "admin"]:
                        # Admins/Devs can assign to anyone
                        available_users = get_available_users(current_household)
                        default_users = [current_user] if current_user in available_users else (available_users[:1] if available_users else [])
                        assigned_to = col4.multiselect("Assign To", options=available_users, default=default_users)
                    else:
                        # Members can only assign to themselves (we lock it in behind the scenes)
                        col4.text_input("Assign To", value=current_user, disabled=True)
                        assigned_to = [current_user]
                    
                    target_date = col5.date_input("Target Date", value=None)

                    rec1, rec2 = st.columns([1, 2])
                    is_recurring = rec1.checkbox("Recurring task", value=False)
                    recurrence_pattern = rec2.selectbox(
                        "Recurring",
                        recurrence_options,
                        index=3,
                    )
                    
                    submit = st.form_submit_button("Save Task", type="primary", width='stretch')
                    if submit and new_task and assigned_to:
                        success = add_new_task(
                            new_task,
                            category,
                            priority,
                            json.dumps(assigned_to),
                            target_date,
                            new_notes,
                            is_recurring,
                            recurrence_pattern,
                        )
                        if success:
                            st.success("Task added!")
                            queue_rerun_reason("task_write")
                        else:
                            st.error("Could not save task.")
                    elif submit and not new_task:
                        st.error("Task is required.")
                    elif submit and not assigned_to:
                        st.error("Please assign the task to at least one person.")

            st.write("")

            # --- 3. Display Active Tasks ---
            if not active_tasks:
                st.info("🎉 You are all caught up! No active tasks.")
            else:
                # Build non-duplicating tab buckets: single-assignee tabs + one multi-assigned tab.
                single_user_tasks = {}
                multi_assigned_tasks = []
                for task in active_tasks:
                    assigned_to_raw = task.get("assigned_to", "Unassigned")
                    assignees = sorted([a for a in parse_assignees(assigned_to_raw) if str(a).strip()])
                    if len(assignees) > 1:
                        multi_assigned_tasks.append((task, assignees))
                        if current_user in assignees:
                            single_user_tasks.setdefault(current_user, []).append((task, assignees))
                        else:
                            for assignee in assignees:
                                single_user_tasks.setdefault(assignee, []).append((task, assignees))
                    else:
                        bucket_user = assignees[0] if assignees else "Unassigned"
                        single_user_tasks.setdefault(bucket_user, []).append((task, assignees))

                def user_tab_sort(name):
                    if name == current_user:
                        return (0, name)
                    return (1, name)

                sorted_users = sorted(single_user_tasks.keys(), key=user_tab_sort)
                task_bucket_keys = [f"user::{u}" for u in sorted_users]
                if multi_assigned_tasks:
                    task_bucket_keys.append("multi")

                if not task_bucket_keys:
                    st.info("No active tasks found for the selected visibility.")
                else:
                    if "todo_active_bucket" not in st.session_state:
                        st.session_state["todo_active_bucket"] = task_bucket_keys[0]
                    if st.session_state.get("todo_active_bucket") not in task_bucket_keys:
                        st.session_state["todo_active_bucket"] = task_bucket_keys[0]

                    def task_bucket_label(bucket_key):
                        if bucket_key == "multi":
                            return f"👥 Multi-Assigned ({len(multi_assigned_tasks)})"
                        bucket_user = bucket_key.split("::", 1)[1]
                        return f"👤 {bucket_user} ({len(single_user_tasks.get(bucket_user, []))})"

                    selected_task_bucket = render_two_col_selector(
                        key="todo_active_bucket",
                        options=task_bucket_keys,
                        format_func=task_bucket_label,
                    )

                    def due_sort_value(task_row):
                        try:
                            return pd.to_datetime(task_row.get("target_date")).date() if task_row.get("target_date") else pd.Timestamp.max.date()
                        except Exception:
                            return pd.Timestamp.max.date()

                    def render_task_item(task, assignees, key_scope):
                        task_name = task.get("task_name", "Unnamed Task")
                        priority = task.get("priority", "Normal")
                        target_date_str = task.get("target_date")
                        notes = (task.get("notes") or "").strip()
                        is_recurring = bool(task.get("is_recurring", False))
                        recurrence_pattern = task.get("recurrence_pattern") or ""

                        border_color, status_msg = get_due_status(target_date_str, priority)
                        due_label = target_date_str if target_date_str else "No date"
                        category = task.get("category", "Uncategorized")
                        recurring_label = f" • 🔁 {recurrence_pattern}" if is_recurring and recurrence_pattern else ""
                        detail_parts = [status_msg, category, f"📅 {due_label}{recurring_label}"]

                        with st.container(border=True):
                            card_label = f"{task_name}"
                            title_col, action_col = st.columns([6, 1])
                            title_col.write(f"**{card_label}**")
                            if user_role in ["developer", "admin"]:
                                if action_col.button("✏️ Edit", key=f"open_task_{task['id']}_{key_scope}", width='stretch'):
                                    current_editing = st.session_state.get("editing_task_id")
                                    st.session_state["editing_task_id"] = None if current_editing == task['id'] else task['id']
                                    queue_rerun_reason("task_edit_toggle")

                            st.caption(" • ".join(detail_parts))
                            if len(assignees) > 1:
                                st.caption(f"Assigned to multiple: {', '.join(assignees)}")
                            if notes:
                                st.caption(f"Notes: {notes}")

                        if st.session_state.get("editing_task_id") == task['id'] and user_role in ["developer", "admin"]:
                            current_assignees = parse_assignees(task.get("assigned_to", "Unassigned"))

                            with st.container(border=True):
                                st.caption("✏️ Edit Task")
                                with st.form(f"edit_task_form_{task['id']}_{key_scope}"):
                                    edit_col1, edit_col2 = st.columns([3, 1])
                                    edit_task_name = edit_col1.text_input("Task", value=task.get("task_name", ""), key=f"edit_task_name_{task['id']}_{key_scope}")

                                    safe_priority = task.get("priority", "Normal")
                                    p_index = ["Normal", "High", "Low"].index(safe_priority) if safe_priority in ["Normal", "High", "Low"] else 0
                                    edit_priority = edit_col2.selectbox("Priority", ["Normal", "High", "Low"], index=p_index, key=f"edit_priority_{task['id']}_{key_scope}")

                                    edit_notes = st.text_area("Notes", value=task.get("notes") or "", key=f"edit_notes_{task['id']}_{key_scope}")

                                    edit_col3, edit_col4, edit_col5 = st.columns(3)
                                    safe_cat = task.get("category", "House")
                                    c_index = ["House", "Yard", "Admin", "Errand"].index(safe_cat) if safe_cat in ["House", "Yard", "Admin", "Errand"] else 0
                                    edit_category = edit_col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"], index=c_index, key=f"edit_cat_{task['id']}_{key_scope}")

                                    available_users = get_available_users(current_household)
                                    safe_defaults = [u for u in current_assignees if u in available_users]
                                    edit_assigned_to = edit_col4.multiselect("Assign To", options=available_users, default=safe_defaults, key=f"edit_assign_{task['id']}_{key_scope}")

                                    current_target_date = pd.to_datetime(task.get("target_date")).date() if task.get("target_date") else None
                                    has_target_date = edit_col5.checkbox("Has Target Date", value=current_target_date is not None, key=f"has_target_{task['id']}_{key_scope}")
                                    edit_target_date = edit_col5.date_input(
                                        "Target Date",
                                        value=current_target_date or central_now().tz_localize(None).date(),
                                        key=f"edit_target_{task['id']}_{key_scope}",
                                    )

                                    edit_rec_col1, edit_rec_col2 = st.columns([1, 2])
                                    safe_recurring = bool(task.get("is_recurring", False))
                                    edit_is_recurring = edit_rec_col1.checkbox("Recurring task", value=safe_recurring, key=f"edit_recur_enabled_{task['id']}_{key_scope}")
                                    existing_pattern = task.get("recurrence_pattern", "Monthly")
                                    rec_index = recurrence_options.index(existing_pattern) if existing_pattern in recurrence_options else 3
                                    edit_recurrence_pattern = edit_rec_col2.selectbox(
                                        "Recurring",
                                        recurrence_options,
                                        index=rec_index,
                                        key=f"edit_recur_pattern_{task['id']}_{key_scope}",
                                    )

                                    save_col, complete_col, del_col, cancel_col = st.columns([2, 1, 1, 1])
                                    save_clicked = save_col.form_submit_button("💾 Save", type="primary", width='stretch')
                                    complete_clicked = complete_col.form_submit_button("✅ Complete", width='stretch')
                                    delete_clicked = del_col.form_submit_button("🗑️ Delete", width='stretch')
                                    cancel_clicked = cancel_col.form_submit_button("❌ Cancel", width='stretch')

                                if save_clicked:
                                    if not edit_task_name.strip():
                                        st.error("Task is required.")
                                    elif not edit_assigned_to:
                                        st.error("Please assign the task to at least one person.")
                                    else:
                                        success = update_task(
                                            task_id=task["id"],
                                            task_name=edit_task_name,
                                            notes=edit_notes,
                                            category=edit_category,
                                            priority=edit_priority,
                                            assigned_to=json.dumps(edit_assigned_to),
                                            target_date=edit_target_date if has_target_date else None,
                                            clear_target_date=not has_target_date,
                                            is_recurring=edit_is_recurring,
                                            recurrence_pattern=edit_recurrence_pattern if edit_is_recurring else None,
                                        )
                                        if success:
                                            st.session_state["editing_task_id"] = None
                                            st.success("Task updated.")
                                            queue_rerun_reason("task_write")
                                        else:
                                            st.error("Could not update task.")

                                if delete_clicked:
                                    delete_task(task["id"])
                                    st.session_state["editing_task_id"] = None
                                    queue_rerun_reason("task_write")

                                if complete_clicked:
                                    if batch_update_tasks([task["id"]], True):
                                        st.session_state["editing_task_id"] = None
                                        queue_rerun_reason("task_write")
                                    else:
                                        st.error("Could not complete task.")

                                if cancel_clicked:
                                    st.session_state["editing_task_id"] = None
                                    queue_rerun_reason("task_edit_cancel")

                    if selected_task_bucket == "multi":
                        tasks_in_bucket = [x[0] for x in multi_assigned_tasks]
                        tasks_in_bucket.sort(key=due_sort_value)
                        for task in tasks_in_bucket:
                            assigned_to_raw = task.get("assigned_to", "Unassigned")
                            assignees = sorted([a for a in parse_assignees(assigned_to_raw) if str(a).strip()])
                            render_task_item(task, assignees, f"multi_{task['id']}")
                    else:
                        username = selected_task_bucket.split("::", 1)[1]
                        tasks_in_bucket = [x[0] for x in single_user_tasks.get(username, [])]
                        tasks_in_bucket.sort(key=due_sort_value)
                        for task in tasks_in_bucket:
                            assigned_to_raw = task.get("assigned_to", "Unassigned")
                            assignees = sorted([a for a in parse_assignees(assigned_to_raw) if str(a).strip()])
                            render_task_item(task, assignees, f"user_{username}_{task['id']}")

            st.divider()
            
            # --- 3. Recently Completed (with Recall) ---
            with st.expander("✅ Recently Completed (Last 14 Days)"):
                all_completed = get_completed_tasks()
                
                completed_tasks = []
                # 🟢 NEW: Set our cutoff for "Recent" (Currently 14 days ago)
                cutoff_date = central_now() - pd.Timedelta(days=14)

                for task in all_completed:
                    # 1. TIME FILTER: Find the best date to use
                    date_str = task.get("target_date") or task.get("created_at")
                    try:
                        # Convert to standard Pandas datetime to do math
                        task_date = to_central_timestamp(date_str)
                        is_recent = task_date >= cutoff_date
                    except:
                        is_recent = True 
                        task_date = None

                    # If the task is older than 14 days, skip it entirely!
                    if not is_recent:
                        continue
                        
                    # 2. ROLE FILTER:
                    assigned_to_raw = task.get("assigned_to", "Unassigned")
                    try:
                        assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                    except:
                        assignees = [assigned_to_raw]
                        
                    if user_role in ["developer", "admin"] or current_user in assignees:
                        # 🟢 NEW: Save a beautifully formatted version of the date to display
                        task['_display_date'] = task_date.strftime('%b %d, %Y') if task_date else "No Date"
                        completed_tasks.append(task)

                if completed_tasks:
                    for task in completed_tasks:
                        assigned_to_raw = task.get("assigned_to", "Unassigned")
                        try:
                            assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                        except:
                            assignees = [assigned_to_raw]

                        # 🟢 NEW: We break this into 3 columns now so the date has a dedicated home
                        col_text, col_date, col_recall = st.columns([2.5, 1.5, 1])
                        
                        col_text.caption(f"~~{task['task_name']}~~")
                        
                        # Display the Date!
                        col_date.caption(f"📅 {task.get('_display_date')}")
                        
                        # Recall Button
                        if user_role in ["developer", "admin"] or current_user in assignees:
                            if col_recall.button("🔄 Recall", key=f"recall_{task['id']}"):
                                batch_update_tasks([task['id']], False) 
                                queue_rerun_reason("task_write")
                else:
                    st.caption("No recently completed tasks in the last 14 days.")
                    
        elif current_view == "groceries":
            st.subheader("🛒 Grocery Manager")
            st.info("Checklist for the next store run will render here...")
            
        # ... and the rest of your routing (budget, calendar) ...
            
        elif current_view == "budget":
            # 💰 Financial Overview is now entirely handled by new module!
            render_budget_module(show_back_to_hub=True)
            
        elif current_view == "calendar":
            st.subheader("📅 Family Calendar")
            st.info("Upcoming events will render here...")

if selected_dashboard_view == "☀️ Solar Production":
    if st.button("🔄 Refresh Telemetry", type="primary", width='stretch'):
        queue_rerun_reason("telemetry_refresh")

    st.subheader("☀️ Live Energy Flow")
    
    # 1. Fetch fresh data by calling the function directly
    solar_data = fetch_ha_state("sensor.solaredge_current_power")
    net_data = fetch_ha_state("sensor.solaredge_meter_power")
    inv1_data = fetch_ha_state("sensor.solaredge_inverter_1")
    inv2_data = fetch_ha_state("sensor.solaredge_inverter_2")
                        
    # 2. Extract and Calculate
    try:
        cur_solar_w = float(solar_data.get("state", 0))
        net_w = float(net_data.get("state", 0))
        inv1_w = float(inv1_data.get("state", 0))
        inv2_w = float(inv2_data.get("state", 0))
    except ValueError:
        cur_solar_w, net_w, inv1_w, inv2_w = 0.0, 0.0, 0.0, 0.0
        
    home_cons_w = cur_solar_w + net_w
    
    # 3. Aggregates UI (Top Level)
    col1, col2, col3 = st.columns(3)
    col1.metric("Panels Generating", f"{(cur_solar_w/1000):.2f} kW", 
                "Producing" if cur_solar_w > 0 else "Offline")
    col2.metric("Home Consuming", f"{((home_cons_w)/1000):.2f} kW", "Load", delta_color="off")
    col3.metric("Grid Status", f"{abs(net_w/1000):.2f} kW", 
                "Exporting" if net_w < 0 else "Importing", 
                delta_color="inverse" if net_w < 0 else "normal")
    
    st.divider()
    
    # 4. Inverter Breakdown
    st.markdown("#### 🔌 Inverter Performance")
    inv_col1, inv_col2 = st.columns(2)
    inv_col1.metric("Inverter 1", f"{(inv1_w/1000):.2f} kW", 
                    "Active" if inv1_w > 0 else "Offline")
    inv_col2.metric("Inverter 2", f"{(inv2_w/1000):.2f} kW", 
                    "Active" if inv2_w > 0 else "Offline")
    
    st.write("")
    
    # 5. The 67 Panel Heatmap
    with st.expander("🔍 View Individual Panel Optimizers (67)"):
        # Pass the nonce here as well!
        panel_data = fetch_ha_state("sensor.solaredge_panel_array")
        panels = panel_data.get("attributes", {}).get("panels", {})
        
        if panels:
            df = pd.DataFrame(list(panels.items()), columns=["Panel ID", "Power (W)"])
            df.set_index("Panel ID", inplace=True)
            try:
                st.dataframe(
                    df.style.background_gradient(cmap="Greens", vmin=50, vmax=350),
                    width='stretch'
                )
            except ImportError:
                st.dataframe(df, width='stretch')
                st.caption("Install matplotlib to enable heatmap styling for panel power.")
        else:
            st.warning("Panel data currently unavailable.")
    
if selected_dashboard_view == "🛡️ Security":
    st.subheader("Security Overview")
    st.write("Camera and sensor feeds will render here...")

if selected_dashboard_view == "🚗 Garage":
    st.subheader("🚗 Garage Access")
    
    # 1. Establish the "Fake" Garage State in memory
    # (When you get HA hardware, we will replace this with fetch_ha_state)
    if "mock_garage_state" not in st.session_state:
        st.session_state["mock_garage_state"] = "closed"
        
    current_state = st.session_state["mock_garage_state"]

    # 2. Dynamic UI rendering based on the state
    with st.container(border=True):
        if current_state == "closed":
            st.markdown("### 🟢 Status: **CLOSED**")
            st.caption("The garage is secured.")
            action_text = "📤 OPEN Garage Door"
            btn_type = "secondary"
        else:
            st.markdown("### 🔴 Status: **OPEN**")
            st.caption("Warning: The garage is exposed.")
            action_text = "📥 CLOSE Garage Door"
            btn_type = "primary" # Highlights the button in red/accent color when open
        
        st.write("") # Spacer
        
        # 3. The Action Button
        if st.button(action_text, type=btn_type, width='stretch'):
            # Simulate the delay of the ratgdo hardware processing the command
            with st.spinner("Transmitting local command via ratgdo..."):
                import time
                time.sleep(1.5) # 1.5 second fake delay
                
                # Flip the fake state
                if current_state == "closed":
                    st.session_state["mock_garage_state"] = "open"
                else:
                    st.session_state["mock_garage_state"] = "closed"
                    
                # Rerun the app to show the new state
                st.rerun()

if selected_dashboard_view == "⚙️ System Logs":
    st.subheader("Event History")
    st.write("Supabase database logs will render here...")

if selected_dashboard_view == "🆕 What's New":
    st.subheader("🆕 What's New")
    st.caption("Release notes for Home Sync and Global items only.")

    cat_display = {
        "Core": "Core Features",
        "UI": "User Interface / Experience",
        "Bug": "Bug Fixes",
        "Ops": "Operations",
    }

    def parse_home_sync_version(version_value):
        try:
            raw_version = str(version_value or "").strip().lower().replace("v", "")
            if not raw_version:
                return ""

            if "home_sync" in raw_version:
                match = re.search(r"home_sync\s*:\s*([0-9]+(?:\.[0-9]+){1,2})", raw_version)
                return match.group(1) if match else ""

            if "|" in raw_version:
                # Allow future global payloads like "home_sync:1.2.3 | get_fit:2.1.0"
                match = re.search(r"home_sync\s*:\s*([0-9]+(?:\.[0-9]+){1,2})", raw_version)
                if match:
                    return match.group(1)

            parts = [int(p) for p in raw_version.split(".") if p != ""]
            while len(parts) < 3:
                parts.append(0)
            return ".".join(str(p) for p in parts[:3])
        except Exception:
            return ""

    # ==========================================
    # DEV ONLY: DRAFT RELEASE PREVIEW
    # ==========================================
    supabase_client = get_supabase_client()

    if user_role == "developer" and is_local_env:
        staged_response = (
            supabase_client
            .table("backlog")
            .select("*")
            .eq("status", "Staged")
            .in_("app_name", ["home_sync", "Global"])
            .execute()
        )

        if staged_response.data:
            categories = [r.get("category", "") for r in staged_response.data]
            current_v = st.session_state.get("APP_VERSION", APP_VERSION)

            try:
                major, minor, patch = map(int, current_v.replace("v", "").strip().split("."))
                if "Core" in categories:
                    major += 1; minor = 0; patch = 0
                elif "UI" in categories:
                    minor += 1; patch = 0
                elif "Bug" in categories:
                    patch += 1
                proposed_v = f"{major}.{minor}.{patch}"
            except Exception:
                proposed_v = current_v

            st.markdown(f"""
            <div style="background-color: #fef08a; padding: 12px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #facc15;">
                <h4 style="color: #b91c1c; margin: 0px; text-align: center;">
                    🚧 DRAFT PREVIEW: Proposed Release v{proposed_v}
                </h4>
            </div>
            """, unsafe_allow_html=True)

            batch_cats = sorted(
                set(categories),
                key=lambda x: ["Core", "UI", "Bug", "Ops"].index(x) if x in ["Core", "UI", "Bug", "Ops"] else 99
            )

            for cat in batch_cats:
                st.markdown(f"#### {cat_display.get(cat, cat)}")
                cat_items = [r for r in staged_response.data if r.get("category") == cat]

                for item in cat_items:
                    task = item.get("feature", "System Update")
                    pub_msg = item.get("public_message", "")
                    app_badge = "(Global) " if item.get("app_name") == "Global" else ""
                    st.markdown(f"**• {app_badge}{task}**")
                    if pub_msg and str(pub_msg).strip() not in ["", "None"]:
                        st.caption(f"&emsp; *{pub_msg}*")
                st.write("")
            st.divider()

    # ==========================================
    # PROD FEED (The Formal History)
    # ==========================================
    try:
        response = (
            supabase_client
            .table("backlog")
            .select("*")
            .eq("status", "Done")
            .in_("app_name", ["home_sync", "Global"])
            .execute()
        )

        if response.data:
            df = pd.DataFrame(response.data)

            df = df.rename(columns={
                "feature": "Feature", "category": "Category",
                "public_message": "Public Message", "release_date": "Release Date",
                "version": "Version"
            })

            for col in ["Release Date", "Version", "Public Message", "app_name"]:
                if col not in df.columns:
                    df[col] = ""
                df[col] = df[col].fillna("").astype(str)

            df["Release Date"] = pd.to_datetime(df["Release Date"], errors="coerce").fillna(pd.Timestamp("2000-01-01"))

            def extract_home_sync_version(row):
                raw_v = str(row.get("Version", "")).strip()
                app_name = str(row.get("app_name", "")).strip()
                if app_name == "Global":
                    match = re.search(r"home_sync\s*:\s*([0-9]+(?:\.[0-9]+){1,2})", raw_v, re.IGNORECASE)
                    return match.group(1) if match else ""
                return parse_home_sync_version(raw_v)

            df["Display Version"] = df.apply(extract_home_sync_version, axis=1)
            df = df[df["Display Version"].astype(str).str.strip() != ""]

            def parse_version(v_str):
                try:
                    clean_v = str(v_str).lower().replace('v', '').strip()
                    parts = [int(p) for p in clean_v.split('.') if p != ""]
                    while len(parts) < 3:
                        parts.append(0)
                    return tuple(parts[:3])
                except Exception:
                    return (0, 0, 0)

            current_app_v = parse_version(APP_VERSION)
            df = df[df["Display Version"].apply(parse_version) <= current_app_v]

            df = df.sort_values(by=["Release Date"], ascending=[False])
            unique_versions = [v for v in df["Display Version"].unique() if str(v).strip() != ""]

            recent_versions = unique_versions[:3]
            older_versions = unique_versions[3:]

            for v_val in recent_versions:
                group = df[df["Display Version"] == v_val]
                date_val = group["Release Date"].iloc[0]
                date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d") if date_val > pd.Timestamp("2000-01-01") else "Archive"

                st.markdown(f"### 🚀 Update: {date_str} | v{v_val}")

                version_cats = group["Category"].fillna("Ops").unique().tolist()
                batch_cats = sorted(version_cats, key=lambda x: ["Core", "UI", "Bug", "Ops"].index(x) if x in ["Core", "UI", "Bug", "Ops"] else 99)

                for cat in batch_cats:
                    st.markdown(f"#### {cat_display.get(cat, cat)}")
                    cat_df = group[group["Category"] == cat]

                    for _, row in cat_df.iterrows():
                        task = row.get("Feature", "System Update")
                        pub_msg = row.get("Public Message", "")
                        app_badge = "(Global) " if row.get("app_name") == "Global" else ""
                        st.markdown(f"**• {app_badge}{task}**")
                        if pd.notna(pub_msg) and str(pub_msg).strip() not in ["", "None"]:
                            st.caption(f"&emsp; *{pub_msg}*")
                    st.write("")
                st.divider()

            if len(older_versions) > 0:
                with st.expander("🕰️ View Older Updates"):
                    for v_val in older_versions:
                        group = df[df["Display Version"] == v_val]
                        date_val = group["Release Date"].iloc[0]
                        date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d") if date_val > pd.Timestamp("2000-01-01") else "Archive"

                        st.markdown(f"### 🚀 Update: {date_str} | v{v_val}")

                        version_cats = group["Category"].fillna("Ops").unique().tolist()
                        batch_cats = sorted(version_cats, key=lambda x: ["Core", "UI", "Bug", "Ops"].index(x) if x in ["Core", "UI", "Bug", "Ops"] else 99)

                        for cat in batch_cats:
                            st.markdown(f"#### {cat_display.get(cat, cat)}")
                            cat_df = group[group["Category"] == cat]

                            for _, row in cat_df.iterrows():
                                task = row.get("Feature", "System Update")
                                pub_msg = row.get("Public Message", "")
                                app_badge = "(Global) " if row.get("app_name") == "Global" else ""
                                st.markdown(f"**• {app_badge}{task}**")
                                if pd.notna(pub_msg) and str(pub_msg).strip() not in ["", "None"]:
                                    st.caption(f"&emsp; *{pub_msg}*")
                            st.write("")
                        st.divider()
        else:
            st.info("No released updates yet.")

    except Exception as e:
        st.error(f"Could not load the changelog: {e}")

if user_role == "developer" and selected_dashboard_view == "🛠️ Developer Dashboard":
        if "developer_dashboard_view" not in st.session_state:
            st.session_state["developer_dashboard_view"] = "menu"
        if "backlog_flash" not in st.session_state:
            st.session_state["backlog_flash"] = None
        if "editing_backlog_id" not in st.session_state:
            st.session_state["editing_backlog_id"] = None
        if "backlog_active_section" not in st.session_state:
            st.session_state["backlog_active_section"] = None

        dev_view = st.session_state.get("developer_dashboard_view", "menu")

        if dev_view == "menu":
            st.subheader("🛠️ Developer Dashboard")
            st.caption("Backlog management plus future-facing operational tooling for your app ecosystem.")

            try:
                radar_response = (
                    get_supabase_client()
                    .table("backlog")
                    .select("app_name, status")
                    .eq("category", "Bug")
                    .neq("status", "Staged")
                    .neq("status", "Done")
                    .execute()
                )
                bug_rows = radar_response.data or []
                bug_counts = {"home_sync": 0, "get_fit": 0, "Global": 0, "unassigned": 0}

                for row in bug_rows:
                    app_name = row.get("app_name") or "unassigned"
                    if app_name not in bug_counts:
                        bug_counts[app_name] = 0
                    bug_counts[app_name] += 1

                total_bugs = sum(bug_counts.values())
                if total_bugs > 0:
                    app_labels = {
                        "home_sync": "Home Sync",
                        "get_fit": "Get Fit Together",
                        "Global": "Global",
                        "unassigned": "Unassigned",
                    }
                    breakdown = " | ".join(
                        f"{app_labels.get(app, app)}: {count}"
                        for app, count in bug_counts.items()
                        if count > 0
                    )
                    st.markdown(
                        f"""
                        <div style="background-color: #fff7ed; border: 1px solid #fb923c; color: #9a3412; padding: 12px 14px; border-radius: 10px; margin: 8px 0 16px 0;">
                            <strong>⚠️ Bug Radar:</strong> {total_bugs} open bugs across the ecosystem.<br/>
                            <span style="font-size: 0.92em;">{breakdown}</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            except Exception:
                pass

            card_col1, card_col2 = st.columns(2)

            with card_col1:
                with st.container(border=True):
                    st.markdown("### 🧭 Developer Overview")
                    st.caption("System health, technical debt, analytics, and migration posture.")
                    if st.button("Open Developer Overview", key="open_dev_overview", width='stretch'):
                        request_main_dashboard_view("🛠️ Developer Dashboard")
                        st.session_state["active_hub_view"] = "main_menu"
                        st.session_state["developer_dashboard_view"] = "overview"
                        rerun_with_reason("dev_nav")

            with card_col2:
                with st.container(border=True):
                    st.markdown("### 🎟️ Backlog & Release Management")
                    st.caption("Create/edit backlog items, review staged work, and cut releases.")
                    if st.button("Open Backlog & Release", key="open_dev_backlog", width='stretch'):
                        request_main_dashboard_view("🛠️ Developer Dashboard")
                        st.session_state["active_hub_view"] = "main_menu"
                        st.session_state["developer_dashboard_view"] = "backlog_release"
                        rerun_with_reason("dev_nav")

        else:
            if st.button("⬅️ Back to Developer Modules", key="back_dev_modules"):
                request_main_dashboard_view("🛠️ Developer Dashboard")
                st.session_state["active_hub_view"] = "main_menu"
                st.session_state["developer_dashboard_view"] = "menu"
                rerun_with_reason("dev_nav")

            st.divider()

            st.subheader("🎟️ Backlog & Release Management")
            if dev_view == "overview":
                st.markdown("### Developer Overview")
                dev_col1, dev_col2 = st.columns(2)
                with dev_col1.container(border=True):
                    st.markdown("#### System Health")
                    st.caption("Planned")
                    st.write("Supabase connection status, database size, and API request headroom.")

                with dev_col2.container(border=True):
                    st.markdown("#### Technical Debt")
                    st.caption("Planned")
                    st.write("Python/runtime version tracking, library drift, and refactor backlog.")

                dev_col3, dev_col4 = st.columns(2)
                with dev_col3.container(border=True):
                    st.markdown("#### Environment Audit")
                    st.caption("Prep")
                    st.write("Track which secrets and env values are deployed to each app before releases.")

                with dev_col4.container(border=True):
                    st.markdown("#### Active Analytics")
                    st.caption("Prep")
                    st.write("Usage metrics like households, logins, and creation activity across apps.")

                dev_col5, dev_col6 = st.columns(2)
                with dev_col5.container(border=True):
                    st.markdown("#### Vulnerability Management")
                    st.caption("Planned")
                    st.write("Placeholder for dependency CVEs, secret leak checks, and remediation status across HS/GFT.")

                with dev_col6.container(border=True):
                    st.markdown("#### Database Migrations")
                    st.caption("Prep")
                    st.write("Central history of schema changes, backfills, and environment-specific database updates.")
                    st.caption("Current tracked migrations: user sessions, backlog release management, release ledger, to-do metadata/recurrence.")

            elif dev_view == "backlog_release":
                backlog_status_options = ["In Progress", "Blocked", "Backlog", "Staged", "Done"]
                backlog_category_options = ["Bug", "Core", "UI", "Ops"]
                backlog_priority_options = ["High", "Medium", "Low"]

                flash = st.session_state.get("backlog_flash")
                if flash:
                    level = flash.get("level", "info")
                    message = flash.get("message", "")
                    if level == "success":
                        st.success(message)
                    elif level == "error":
                        st.error(message)
                    elif level == "warning":
                        st.warning(message)
                    else:
                        st.info(message)
                    st.session_state["backlog_flash"] = None

                def render_add_backlog_ticket_form(default_target_app):
                    app_labels = {
                        "home_sync": "Home Sync",
                        "get_fit": "Get Fit Together",
                        "Global": "Global",
                    }
                    app_label = app_labels.get(default_target_app, default_target_app)

                    with st.expander(f"➕ Add New {app_label} Ticket", expanded=False):
                        with st.form(f"add_backlog_form_{default_target_app}", clear_on_submit=True):
                            c1, c2, c3 = st.columns(3)
                            new_status = c1.selectbox(
                                "Status",
                                backlog_status_options,
                                index=2,
                                key=f"add_status_{default_target_app}",
                            )
                            new_category = c2.selectbox(
                                "Category",
                                backlog_category_options,
                                index=1,
                                key=f"add_category_{default_target_app}",
                            )
                            new_priority = c3.selectbox(
                                "Priority",
                                backlog_priority_options,
                                index=2,
                                key=f"add_priority_{default_target_app}",
                            )

                            st.caption(f"Target App: {app_label}")
                            st.caption("Fields marked with * are required.")

                            new_feature = st.text_input(
                                "Feature or Bug Name *",
                                key=f"add_feature_{default_target_app}",
                            )
                            new_notes = st.text_area(
                                "Description",
                                help="External-facing description of the feature",
                                key=f"add_desc_{default_target_app}",
                            )
                            new_work_notes = st.text_area(
                                "Work Notes",
                                help="Internal notes about implementation",
                                key=f"add_work_{default_target_app}",
                            )

                            submit_label = f"Save {app_label} Ticket"
                            create_clicked = st.form_submit_button(submit_label, type="primary")

                        if create_clicked:
                            if not new_feature.strip():
                                st.session_state["backlog_flash"] = {
                                    "level": "warning",
                                    "message": "Create blocked: Feature or Bug Name is required.",
                                }
                                rerun_with_reason("backlog_write")
                            else:
                                created = add_backlog_item(
                                    new_feature,
                                    new_notes,
                                    new_status,
                                    default_target_app,
                                    new_category,
                                    new_priority,
                                    new_work_notes,
                                )
                                if created:
                                    st.session_state["backlog_flash"] = {
                                        "level": "success",
                                        "message": f"Ticket created successfully in {app_label}.",
                                    }
                                    rerun_with_reason("backlog_write")
                                else:
                                    st.session_state["backlog_flash"] = {
                                        "level": "error",
                                        "message": "Failed to create ticket. Check logs and try again.",
                                    }
                                    rerun_with_reason("backlog_write")

                raw_items = get_all_backlog_items()
                if raw_items:
                    df = pd.DataFrame(raw_items)
                    df["priority"] = df["priority"].replace("", "Low").fillna("Low")
                    df["priority"] = df["priority"].astype(str).str.title()

                    valid_cats = backlog_category_options
                    if "category" in df.columns:
                        df["category"] = df["category"].apply(lambda x: x if x in valid_cats else "Core")

                    if "status" in df.columns:
                        df["status"] = pd.Categorical(df["status"], categories=backlog_status_options, ordered=True)
                    if "category" in df.columns:
                        df["category"] = pd.Categorical(df["category"], categories=backlog_category_options, ordered=True)
                    if "priority" in df.columns:
                        df["priority"] = pd.Categorical(df["priority"], categories=backlog_priority_options, ordered=True)

                    sort_cols = [col for col in ["status", "category", "priority"] if col in df.columns]
                    if sort_cols:
                        df = df.sort_values(sort_cols)
                    items = df.fillna("").to_dict("records")
                else:
                    items = []

                def render_edit_form(item, form_key_suffix):
                    with st.container(border=True):
                        st.markdown("### ✏️ Edit Ticket")
                    
                        # Form inputs plus Save/Delete submit buttons
                        with st.form(f"edit_backlog_form_{form_key_suffix}"):
                            c1, c2, c3, c4 = st.columns(4)

                            s_idx = backlog_status_options.index(item.get("status", "Backlog")) if item.get("status") in backlog_status_options else 0
                            e_status = c1.selectbox("Status", backlog_status_options, index=s_idx, key=f"s_{form_key_suffix}")

                            cat_idx = backlog_category_options.index(item.get("category", "Core")) if item.get("category") in backlog_category_options else 0
                            e_category = c2.selectbox("Category", backlog_category_options, index=cat_idx, key=f"c_{form_key_suffix}")

                            p_idx = backlog_priority_options.index(item.get("priority", "Medium")) if item.get("priority") in backlog_priority_options else 1
                            e_priority = c3.selectbox("Priority", backlog_priority_options, index=p_idx, key=f"p_{form_key_suffix}")

                            app_opts = ["home_sync", "get_fit", "Global"]
                            app_idx = app_opts.index(item.get("app_name", "home_sync")) if item.get("app_name") in app_opts else 0
                            e_app = c4.selectbox("Target App", app_opts, index=app_idx, key=f"a_{form_key_suffix}")

                            st.caption("Fields marked with * are required.")

                            e_feature = st.text_input("Feature or Bug Name *", value=item.get("feature", ""), key=f"f_{form_key_suffix}")
                            e_notes = st.text_area("Description", value=item.get("notes", ""), help="External-facing description", key=f"n_{form_key_suffix}")
                            e_work_notes = st.text_area("Work Notes", value=item.get("work_notes", ""), help="Internal implementation notes", key=f"w_{form_key_suffix}")
                            e_public_msg = st.text_area("Public Release Message", value=item.get("public_message", ""), key=f"pm_{form_key_suffix}")

                            save_col, delete_col = st.columns([3, 1])
                            save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                            delete_clicked = delete_col.form_submit_button("🗑️ Delete", width="stretch")

                        if save_clicked:
                            if not e_feature.strip():
                                st.session_state["backlog_flash"] = {
                                    "level": "warning",
                                    "message": "Save blocked: Feature or Bug Name is required.",
                                }
                            else:
                                updated = update_backlog_item(
                                    item["id"],
                                    e_feature,
                                    e_notes,
                                    e_status,
                                    e_app,
                                    e_category,
                                    e_priority,
                                    e_public_msg,
                                    e_work_notes,
                                )
                                if updated:
                                    st.session_state["backlog_flash"] = {
                                        "level": "success",
                                        "message": f"Ticket updated successfully for {e_app}.",
                                    }
                                    st.session_state["editing_backlog_id"] = None
                                else:
                                    st.session_state["backlog_flash"] = {
                                        "level": "error",
                                        "message": "Failed to update ticket. Check logs and try again.",
                                    }
                            rerun_with_reason("backlog_write")

                        if delete_clicked:
                            deleted = delete_backlog_item(item["id"])
                            if deleted:
                                st.session_state["backlog_flash"] = {
                                    "level": "success",
                                    "message": "Ticket deleted successfully.",
                                }
                                st.session_state["editing_backlog_id"] = None
                            else:
                                st.session_state["backlog_flash"] = {
                                    "level": "error",
                                    "message": "Failed to delete ticket. Check logs and try again.",
                                }
                            rerun_with_reason("backlog_write")

                def begin_backlog_edit(item_id, app_name, is_staged=False):
                    current_id = st.session_state.get("editing_backlog_id")
                    st.session_state["editing_backlog_id"] = None if current_id == item_id else item_id
                    st.session_state["backlog_active_section"] = "staged" if is_staged else f"app::{app_name}"

                def render_backlog_item(item, app_name, is_staged=False):
                    col_text, col_act = st.columns([5, 1])
                    col_text.markdown(f"**{item.get('feature', 'Unnamed Feature')}**")
                    col_text.caption(
                        f"Status: **{item.get('status', 'N/A')}** | Category: **{item.get('category', 'N/A')}** | Priority: **{item.get('priority', 'N/A')}**"
                    )

                    notes_text = item.get("notes", "").strip()
                    work_notes_text = item.get("work_notes", "").strip()
                    public_msg = item.get("public_message", "").strip()
                    version_info = item.get("version", "")

                    if notes_text:
                        col_text.markdown(f"**Description:** {notes_text}")
                    if work_notes_text:
                        col_text.markdown(f"**Work Notes:** {work_notes_text}")
                    if public_msg:
                        col_text.markdown(f"**Public Message:** <span style='color: #10B981;'>{public_msg}</span>", unsafe_allow_html=True)
                    if version_info:
                        col_text.caption(f"🏷️ Released as v{version_info}")

                    col_act.button(
                        "✏️ Edit",
                        key=f"edit_{'staged' if is_staged else 'bl'}_{item['id']}",
                        on_click=begin_backlog_edit,
                        args=(item["id"], app_name, is_staged),
                    )

                    if st.session_state["editing_backlog_id"] == item["id"]:
                        render_edit_form(item, f"{'staged' if is_staged else 'app'}_{item['id']}")

                    st.divider()

                def sort_key(app):
                    if app == "home_sync":
                        return (0, app)
                    if app == "get_fit":
                        return (1, app)
                    if app == "Global":
                        return (2, app)
                    return (3, app)

                apps = set([item.get("app_name") if item.get("app_name") else "unassigned" for item in items])
                sorted_apps = sorted(apps, key=sort_key)
                staged_items = [i for i in items if i.get("status") == "Staged"]

                backlog_section_keys = [f"app::{app}" for app in sorted_apps] + ["staged", "release"]
                if st.session_state.get("backlog_active_section") not in backlog_section_keys:
                    st.session_state["backlog_active_section"] = backlog_section_keys[0] if backlog_section_keys else "release"

                def backlog_section_label(section_key):
                    if section_key == "staged":
                        return f"🚀 Staged ({len(staged_items)})"
                    if section_key == "release":
                        return "🚀 Release Management"
                    app_name = section_key.split("::", 1)[1]
                    return f"📱 {str(app_name).replace('_', ' ').title()}"

                selected_backlog_section = render_two_col_selector(
                    key="backlog_active_section",
                    options=backlog_section_keys,
                    format_func=backlog_section_label,
                )

                if selected_backlog_section.startswith("app::"):
                    app = selected_backlog_section.split("::", 1)[1]

                    if app in ["home_sync", "get_fit", "Global"]:
                        render_add_backlog_ticket_form(app)

                    app_items = [
                        i for i in items
                        if (i.get("app_name") == app or (not i.get("app_name") and app == "unassigned"))
                        and i.get("status") != "Staged"
                    ]
                    if not app_items:
                        st.caption("No non-staged items in this app section.")

                    for item in app_items:
                        render_backlog_item(item, app, False)

                elif selected_backlog_section == "staged":
                    if staged_items:
                        staged_apps = sorted(
                            set(i.get("app_name") if i.get("app_name") else "unassigned" for i in staged_items),
                            key=sort_key
                        )
                        for staged_app in staged_apps:
                            staged_app_items = [
                                i for i in staged_items
                                if (i.get("app_name") == staged_app or (not i.get("app_name") and staged_app == "unassigned"))
                            ]

                            staged_app_name = str(staged_app).replace("_", " ").title()
                            st.markdown(f"### {staged_app_name}")

                            for item in staged_app_items:
                                render_backlog_item(item, staged_app, True)
                    else:
                        st.caption("No staged items currently.")

                else:
                    st.markdown("### 🚀 Release Management")
                    staged_count = len(staged_items)

                    staged_home_sync_items = [i for i in staged_items if i.get("app_name") == "home_sync"]
                    staged_get_fit_items = [i for i in staged_items if i.get("app_name") == "get_fit"]
                    staged_global_items = [i for i in staged_items if i.get("app_name") == "Global"]

                    home_sync_categories = [i.get("category", "") for i in staged_home_sync_items]
                    get_fit_categories = [i.get("category", "") for i in staged_get_fit_items]
                    home_sync_all_categories = [i.get("category", "") for i in staged_items if i.get("app_name") in ["home_sync", "Global"]]
                    get_fit_all_categories = [i.get("category", "") for i in staged_items if i.get("app_name") in ["get_fit", "Global"]]

                    current_home_sync_version = get_current_app_version("home_sync", fallback_version=APP_VERSION)
                    current_get_fit_version = get_current_app_version("get_fit", fallback_version=GET_FIT_BASELINE_VERSION)

                    next_home_sync_version = (
                        calculate_next_version(current_home_sync_version, home_sync_categories)
                        if home_sync_categories else current_home_sync_version
                    )
                    next_get_fit_version = (
                        calculate_next_version(current_get_fit_version, get_fit_categories)
                        if get_fit_categories else current_get_fit_version
                    )
                    next_home_sync_all_version = (
                        calculate_next_version(current_home_sync_version, home_sync_all_categories)
                        if home_sync_all_categories else current_home_sync_version
                    )
                    next_get_fit_all_version = (
                        calculate_next_version(current_get_fit_version, get_fit_all_categories)
                        if get_fit_all_categories else current_get_fit_version
                    )

                    col_home_preview, col_get_fit_preview = st.columns(2)
                    with col_home_preview:
                        st.caption(f"Home Sync: Current v{current_home_sync_version} -> Next v{next_home_sync_version}")
                    with col_get_fit_preview:
                        st.caption(f"Get Fit Together: Current v{current_get_fit_version} -> Next v{next_get_fit_version}")

                    if staged_count > 0:
                        col_home_cut, col_get_fit_cut = st.columns(2)

                        with col_home_cut:
                            if st.button("🚀 Cut Home Sync Release", type="primary", key="cut_home_sync_release", width='stretch'):
                                success, versions, message = cut_release(
                                    current_home_sync_version,
                                    current_get_fit_version,
                                    release_target="home_sync"
                                )
                                if success:
                                    st.success(message)
                                    st.session_state["APP_VERSION"] = versions.get("home_sync", current_home_sync_version)
                                    st.info(f"📝 Next step: Use Home Sync v{versions.get('home_sync', current_home_sync_version)} for deployment.")
                                    queue_rerun_reason("release_cut")
                                else:
                                    st.error(message)

                        with col_get_fit_cut:
                            if st.button("🚀 Cut Get Fit Together Release", key="cut_get_fit_release", width='stretch'):
                                success, versions, message = cut_release(
                                    current_home_sync_version,
                                    current_get_fit_version,
                                    release_target="get_fit"
                                )
                                if success:
                                    st.success(message)
                                    st.info(f"📝 Next step: Use Get Fit Together v{versions.get('get_fit', current_get_fit_version)} for deployment.")
                                    queue_rerun_reason("release_cut")
                                else:
                                    st.error(message)

                        if staged_global_items:
                            st.caption(
                                f"Global staged items: {len(staged_global_items)}. "
                                f"Use All Apps cut when you want Global changes released to both apps."
                            )
                            st.caption(
                                f"All Apps preview -> Home Sync: v{current_home_sync_version} -> v{next_home_sync_all_version} | "
                                f"Get Fit Together: v{current_get_fit_version} -> v{next_get_fit_all_version}"
                            )
                            if st.button("🚀 Cut All Apps Release (Includes Global)", key="cut_all_apps_release", width='stretch'):
                                success, versions, message = cut_release(
                                    current_home_sync_version,
                                    current_get_fit_version,
                                    release_target="all"
                                )
                                if success:
                                    st.success(message)
                                    st.session_state["APP_VERSION"] = versions.get("home_sync", current_home_sync_version)
                                    st.info(
                                        f"📝 Next step: Use Home Sync v{versions.get('home_sync', current_home_sync_version)} "
                                        f"and Get Fit Together v{versions.get('get_fit', current_get_fit_version)} in deployment scripts."
                                    )
                                    queue_rerun_reason("release_cut")
                                else:
                                    st.error(message)
                    else:
                        st.caption("No staged items currently. Next versions match current versions.")
