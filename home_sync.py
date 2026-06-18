import streamlit as st
import pandas as pd
import time
import json
from auth import check_password
from home_assist_api import fetch_ha_state
from database import get_active_tasks, get_completed_tasks, add_new_task, batch_update_tasks, update_task, get_all_backlog_items, add_backlog_item, update_backlog_item, delete_backlog_item, delete_task
from supabase import create_client, Client

APP_VERSION = "1.0.0"

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

APP_VERSION = "0.0.1-alpha"

# 1. Page Config must ALWAYS be the very first Streamlit command
st.set_page_config(page_title="Home Sync Dashboard", page_icon="🏠", layout="wide")

# ==========================================
# 🛠️ STATIC UI STYLESHEET
# ==========================================
st.markdown("""
    <style>
    div[data-testid="InputInstructions"] { display: none !important; }
    header [data-testid="stToolbarActionButton"] { display: none !important; }
    header { background-color: transparent !important; }
    </style>
""", unsafe_allow_html=True
)

# ==========================================
# 🔒 SECURE LOGIN
# ==========================================
if not check_password():
    st.stop()

# 🟢 FIX: Make sure this matches the key from auth.py!
user_role = st.session_state.get("user_role", "member")

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

st.title("🏠 Home Sync Dashboard")

# ==========================================
#  SIDEBAR COMMAND CENTER
# ==========================================
st.sidebar.header("⚡ Quick Controls")
st.sidebar.info("Garage door local controls coming soon...")
st.sidebar.markdown("---")

# ==========================================
# ⚙️ SIDEBAR UTILITY FOOTER 
# ==========================================
st.sidebar.divider()

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
        submit_bug = st.form_submit_button("📤 Send to Developer", type="secondary", use_container_width=True)
        
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
if st.sidebar.button("🚪 Log Out", use_container_width=True):
    
    # Import our secure isolation function
    from auth import get_cookie_controller
    controller = get_cookie_controller()
    
    # 1. Safely nuke the browser cookie 
    # ⚠️ CHANGE THIS NAME FOR THE OTHER APP! ("get_fit_session")
    if controller.get("home_sync_session") is not None:
        controller.remove("home_sync_session")
    
    # 2. Nuke the temporary session state
    for key in list(st.session_state.keys()):
        del st.session_state[key]
        
    # 3. Leave the ghost flag to prevent auto-login
    st.session_state["logout_in_progress"] = True
        
    st.query_params.clear() 
    st.rerun()

# 🏷️ APPLICATION TAG
st.sidebar.caption("<div style='text-align: center; color: gray; padding-top: 10px;'>Home Sync Hub</div>", unsafe_allow_html=True)
    
# ==========================================
# 📋 MAIN DASHBOARD TABS
# ==========================================
if user_role == "developer":
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🏠 Household Hub",
        "☀️ Solar Production",
        "🛡️ Security",
        "🚗 Garage",        
        "⚙️ System Logs",
        "📝 Backlog"
    ])
else:
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏠 Household Hub",
        "☀️ Solar Production",
        "🛡️ Security",
        "🚗 Garage",        
        "⚙️ System Logs"
    ])

with tab1:
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
                if st.button("Open To-Do", type="primary", use_container_width=True):
                    st.session_state["active_hub_view"] = "todo"
                    st.rerun()
                    
            with st.container(border=True):
                st.markdown("### 💰 Budget")
                st.caption("Track monthly spending and financial goals.")
                if st.button("Open Budget", type="primary", use_container_width=True):
                    st.session_state["active_hub_view"] = "budget"
                    st.rerun()
                    
        with col2:
            with st.container(border=True):
                st.markdown("### 🛒 Groceries")
                st.caption("Shared family grocery list and meal prep.")
                if st.button("Open Groceries", type="primary", use_container_width=True):
                    st.session_state["active_hub_view"] = "groceries"
                    st.rerun()
                    
            with st.container(border=True):
                st.markdown("### 📅 Calendar")
                st.caption("Family schedule, appointments, and events.")
                if st.button("Open Calendar", type="primary", use_container_width=True):
                    st.session_state["active_hub_view"] = "calendar"
                    st.rerun()

    # ==========================================
    # VIEW: SUB-MODULES (What happens when you click a card)
    # ==========================================
    else:
        # Universal "Back" button to return to the grid
        if st.button("⬅️ Back to Hub Menu"):
            st.session_state["active_hub_view"] = "main_menu"
            st.rerun()
            
        st.divider()
        
        if current_view == "todo":
            st.subheader("📋 Active To-Do List")
            current_user = st.session_state.get("logged_in_user", "Unknown")            
            user_role = st.session_state.get("user_role", "member") 
            # 🟢 NEW: Grab the household ID for this session
            current_household = st.session_state.get("household_id", "unassigned")
            
            # --- 1. Form to Add a New Task (EVERYONE CAN ADD) ---
            with st.expander("➕ Add New Task", expanded=False):
                with st.form("new_task_form", clear_on_submit=True):
                    col1, col2 = st.columns([3, 1])
                    new_task = col1.text_input("Task Description", placeholder="e.g., Clean my room")
                    priority = col2.selectbox("Priority", ["Normal", "High", "Low"])
                    
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
                    
                    submit = st.form_submit_button("Save Task", type="primary", use_container_width=True)
                    if submit and new_task and assigned_to:
                        success = add_new_task(new_task, category, priority, json.dumps(assigned_to), target_date)
                        if success:
                            st.success("Task added!")
                            st.rerun()
                    elif submit and not assigned_to:
                        st.error("Please assign the task to at least one person.")

            st.write("")
            
            # --- 1.5 Edit Task Form (ADMINS & DEVS ONLY) ---
            if "editing_task_id" not in st.session_state:
                st.session_state["editing_task_id"] = None
            
            if st.session_state["editing_task_id"] is not None and user_role in ["developer", "admin"]:
                # Find the task being edited
                all_tasks = get_active_tasks()
                editing_task = next((t for t in all_tasks if t["id"] == st.session_state["editing_task_id"]), None)
                
                if editing_task:
                    with st.container(border=True):
                        st.markdown("### ✏️ Edit Task")
                        
                        with st.form("edit_task_form"):
                            # Parse current assignees securely
                            assigned_to_raw = editing_task.get("assigned_to", "Unassigned")
                            try:
                                current_assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                            except:
                                current_assignees = [assigned_to_raw]
                            
                            col1, col2 = st.columns([3, 1])
                            edit_task_name = col1.text_input("Task Description", value=editing_task.get("task_name", ""))
                            
                            # Safely set priority index
                            safe_priority = editing_task.get("priority", "Normal")
                            p_index = ["Normal", "High", "Low"].index(safe_priority) if safe_priority in ["Normal", "High", "Low"] else 0
                            edit_priority = col2.selectbox("Priority", ["Normal", "High", "Low"], index=p_index)
                            
                            col3, col4, col5 = st.columns(3)
                            
                            # Safely set category index
                            safe_cat = editing_task.get("category", "House")
                            c_index = ["House", "Yard", "Admin", "Errand"].index(safe_cat) if safe_cat in ["House", "Yard", "Admin", "Errand"] else 0
                            edit_category = col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"], index=c_index)
                            
                            # 🟢 NEW: Pull household-specific users for the dropdown!
                            available_users = get_available_users(current_household)
                            safe_defaults = [u for u in current_assignees if u in available_users]
                            edit_assigned_to = col4.multiselect("Assign To", options=available_users, default=safe_defaults)
                            
                            edit_target_date = col5.date_input("Target Date", value=pd.to_datetime(editing_task.get("target_date")) if editing_task.get("target_date") else None)
                            
                            col_save, col_del, col_cancel = st.columns([2, 1, 1])
                        
                        if col_save.form_submit_button("💾 Save", type="primary", use_container_width=True):
                            # Your existing update_task() code here...
                            st.session_state["editing_task_id"] = None
                            st.rerun()
                            
                        # 🟢 The new Delete Button
                        if col_del.form_submit_button("🗑️ Delete", use_container_width=True):
                            delete_task(st.session_state["editing_task_id"])
                            st.session_state["editing_task_id"] = None
                            st.rerun()
                            
                        if col_cancel.form_submit_button("❌ Cancel", use_container_width=True):
                            st.session_state["editing_task_id"] = None
                            st.rerun()
            
            # --- 2. Display Active Tasks ---
            all_active_tasks = get_active_tasks()
            
            # ROLE CHECK: Filter tasks for members so they only see their own
            active_tasks = []
            for task in all_active_tasks:
                assigned_to_raw = task.get("assigned_to", "Unassigned")
                try:
                    assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                except:
                    assignees = [assigned_to_raw]
                
                if user_role in ["developer", "admin"] or current_user in assignees:
                    active_tasks.append(task)
            
            selected_task_ids = [] 
            
            if not active_tasks:
                st.info("🎉 You are all caught up! No active tasks.")
            else:
                # Group the tasks dynamically by who they are assigned to
                grouped_tasks = {}
                for task in active_tasks:
                    assigned_to_raw = task.get("assigned_to", "Unassigned")
                    try:
                        assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                    except:
                        assignees = [assigned_to_raw]
                    
                    # Sort the names alphabetically
                    assignee_str = ", ".join(sorted(assignees))
                    
                    if assignee_str not in grouped_tasks:
                        grouped_tasks[assignee_str] = []
                    grouped_tasks[assignee_str].append(task)

                # Sort the groups
                sorted_groups = sorted(grouped_tasks.keys(), key=lambda x: (-len(x.split(", ")), x))

                # Render each group
                for group_name in sorted_groups:
                    header_icon = "👥" if "," in group_name else "👤"
                    st.markdown(f"##### {header_icon} {group_name}")
                    
                    tasks_in_group = grouped_tasks[group_name]
                    tasks_in_group.sort(key=lambda t: pd.to_datetime(t.get("target_date")).date() if t.get("target_date") else pd.Timestamp.max.date())
                    
                    for task in tasks_in_group:
                        col_check, col_text, col_meta, col_actions = st.columns([0.5, 4, 1.5, 0.8])
                        
                        if col_check.checkbox(" ", key=f"task_{task['id']}"):
                            st.session_state[f"to_complete_{task['id']}"] = True
                        
                        # ==========================================
                        # 🧠 THE SMART URGENCY ENGINE
                        # ==========================================
                        task_name = task.get("task_name", "Unnamed Task")
                        priority = task.get("priority", "Normal")
                        target_date_str = task.get("target_date")

                        days_remaining = None
                        if target_date_str:
                            try:
                                t_date = pd.to_datetime(target_date_str).tz_localize(None).date()
                                today = pd.Timestamp.now(tz='UTC').tz_localize(None).date()
                                days_remaining = (t_date - today).days
                            except:
                                pass

                        # Default Colors
                        border_color = "#475569" # Slate
                        status_msg = ""

                        if days_remaining is not None:
                            if days_remaining < 0:
                                border_color = "#EF4444" # Red
                                status_msg = f"🔴 Overdue by {abs(days_remaining)}d"
                            elif days_remaining == 0:
                                border_color = "#F97316" # Orange
                                status_msg = "🟠 Due TODAY"
                            elif days_remaining == 1:
                                border_color = "#EAB308" # Yellow
                                status_msg = "🟡 Due Tomorrow"
                            else:
                                border_color = "#22C55E" # Green
                                status_msg = f"🟢 Due in {days_remaining}d"
                        else:
                            # Priority Fallback
                            if priority == "High":
                                border_color = "#3B82F6" # Blue
                                status_msg = "🔵 High Priority"
                            elif priority == "Low":
                                border_color = "#64748B" # Gray
                                status_msg = "⚪ Low Priority"
                            else:
                                status_msg = "⚪ No Date"

                        # 🎨 THE UI INJECTION (Replaces old markdown)
                        col_text.markdown(f"""
                            <div style="border-left: 4px solid {border_color}; padding-left: 10px; margin-top: 4px;">
                                <div style="font-weight: 600; font-size: 1.05em; line-height: 1.2;">{task_name}</div>
                                <div style="font-size: 0.85em; color: gray; margin-top: 2px;">{status_msg}</div>
                            </div>
                        """, unsafe_allow_html=True)
                        
                        category = task.get("category", "Uncategorized")
                        due = task.get("target_date", "No date")
                        col_meta.caption(f"_{category}_ • 📅 {due}") 
                        
                        if user_role in ["developer", "admin"]:
                            if col_actions.button("✏️", key=f"edit_{task['id']}", help="Edit task"):
                                st.session_state["editing_task_id"] = task['id']
                                st.rerun()

                # Collect selected task IDs for the batch button
                selected_task_ids = [task['id'] for task in active_tasks if st.session_state.get(f"to_complete_{task['id']}")]

            # Batch Action Button
            if selected_task_ids:
                if st.button(f"✅ Mark {len(selected_task_ids)} Task(s) Complete"):
                    if batch_update_tasks(selected_task_ids, True):
                        st.success("Tasks updated!")
                        st.rerun()

            st.divider()
            
            # --- 3. Recently Completed (with Recall) ---
            with st.expander("✅ Recently Completed (Last 14 Days)"):
                all_completed = get_completed_tasks()
                
                completed_tasks = []
                # 🟢 NEW: Set our cutoff for "Recent" (Currently 14 days ago)
                cutoff_date = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=14)

                for task in all_completed:
                    # 1. TIME FILTER: Find the best date to use
                    date_str = task.get("target_date") or task.get("created_at")
                    try:
                        # Convert to standard Pandas datetime to do math
                        task_date = pd.to_datetime(date_str, utc=True)
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
                                st.rerun()
                else:
                    st.caption("No recently completed tasks in the last 14 days.")
                    
        elif current_view == "groceries":
            st.subheader("🛒 Grocery Manager")
            st.info("Checklist for the next store run will render here...")
            
        # ... and the rest of your routing (budget, calendar) ...
            
        elif current_view == "budget":
            st.subheader("💰 Financial Overview")
            st.info("Supabase financial_transactions table data will render here...")
            
        elif current_view == "calendar":
            st.subheader("📅 Family Calendar")
            st.info("Upcoming events will render here...")

with tab2:
    if st.button("🔄 Refresh Telemetry", type="primary", width='stretch'):
        st.rerun()

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
            st.dataframe(
                df.style.background_gradient(cmap="Greens", vmin=50, vmax=350),
                width='stretch'
            )
        else:
            st.warning("Panel data currently unavailable.")
    
with tab3:
    st.subheader("Security Overview")
    st.write("Camera and sensor feeds will render here...")

with tab4:
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

with tab5:
    st.subheader("Event History")
    st.write("Supabase database logs will render here...")

# ==========================================
# 📝 MASTER BACKLOG TAB (Developer Only)
# ==========================================
if user_role == "developer":
    with tab6:
        st.subheader("📝 Master Ecosystem Backlog")
        
        # --- 1. The New Add Form (Wrapped in an Expander) ---
        with st.expander("➕ Add New Backlog Ticket", expanded=False):
            with st.form("add_master_backlog_form", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                new_status = c1.selectbox("Status", ["Backlog", "In Progress", "Blocked", "Staged", "Done"])
                new_category = c2.selectbox("Category", ["Core", "UI", "Bug", "Ops"])
                new_priority = c3.selectbox("Priority", ["High", "Medium", "Low"], index=1)
                target_app = c4.selectbox("Target App", ["home_sync", "get_fit"])
                
                new_feature = st.text_input("Feature or Bug Name")
                new_notes = st.text_area("Notes / Description")
                
                if st.form_submit_button("Save Ticket", type="primary"):
                    if new_feature:
                        add_backlog_item(new_feature, new_notes, new_status, target_app, new_category, new_priority)
                        st.success(f"Added to {target_app}!")
                        st.rerun()
                        
        st.divider()
        
        # --- 2. Edit Backlog Form ---
        if "editing_backlog_id" not in st.session_state:
            st.session_state["editing_backlog_id"] = None
            
        raw_items = get_all_backlog_items()
        
        # 🟢 MULTI-LEVEL SORTING (Status -> Category -> Priority)
        if raw_items:
            # Convert to Pandas DataFrame for sorting
            df = pd.DataFrame(raw_items)
            
            # Clean and setup Priority
            df["priority"] = df["priority"].replace("", "Low").fillna("Low")
            df["priority"] = df["priority"].astype(str).str.title()
            
            # Clean and setup Category (Prevents Pandas4Warning)
            valid_cats = ["Core", "UI", "Bug", "Ops"]
            if "category" in df.columns:
                df["category"] = df["category"].apply(lambda x: x if x in valid_cats else "Core")
            
            # Apply Categorical Ordering
            status_order = ["In Progress", "Backlog", "Blocked", "Staged", "Done"]
            if "status" in df.columns:
                df["status"] = pd.Categorical(df["status"], categories=status_order, ordered=True)

            category_order = ["Core", "UI", "Bug", "Ops"]
            if "category" in df.columns:
                df["category"] = pd.Categorical(df["category"], categories=category_order, ordered=True)
            
            priority_order = ["High", "Medium", "Low"]
            if "priority" in df.columns:
                df["priority"] = pd.Categorical(df["priority"], categories=priority_order, ordered=True)

            # Sort by the ordered categorical columns
            sort_cols = [col for col in ["status", "category", "priority"] if col in df.columns]
            if sort_cols:
                df = df.sort_values(sort_cols)
            
            # Convert back to list of dictionaries for Streamlit rendering
            items = df.fillna("").to_dict("records")
        else:
            items = []

        # --- 3. Render Edit View ---
        if st.session_state["editing_backlog_id"] is not None:
            editing_item = next((i for i in items if i["id"] == st.session_state["editing_backlog_id"]), None)
            
            if editing_item:
                with st.container(border=True):
                    st.markdown("### ✏️ Edit Ticket")
                    with st.form("edit_backlog_form"):
                        c1, c2, c3, c4 = st.columns(4)
                        
                        s_idx = ["Backlog", "In Progress", "Blocked", "Staged", "Done"].index(editing_item.get("status", "Backlog")) if editing_item.get("status") in ["Backlog", "In Progress", "Blocked", "Staged", "Done"] else 0
                        e_status = c1.selectbox("Status", ["Backlog", "In Progress", "Blocked", "Staged", "Done"], index=s_idx)
                        
                        cat_idx = ["Core", "UI", "Bug", "Ops"].index(editing_item.get("category", "Core")) if editing_item.get("category") in ["Core", "UI", "Bug", "Ops"] else 0
                        e_category = c2.selectbox("Category", ["Core", "UI", "Bug", "Ops"], index=cat_idx)
                        
                        p_idx = ["High", "Medium", "Low"].index(editing_item.get("priority", "Medium")) if editing_item.get("priority") in ["High", "Medium", "Low"] else 1
                        e_priority = c3.selectbox("Priority", ["High", "Medium", "Low"], index=p_idx)
                        
                        app_idx = ["home_sync", "get_fit"].index(editing_item.get("app_name", "home_sync")) if editing_item.get("app_name") in ["home_sync", "get_fit"] else 0
                        e_app = c4.selectbox("Target App", ["home_sync", "get_fit"], index=app_idx)

                        e_feature = st.text_input("Feature or Bug Name", value=editing_item.get("feature", ""))
                        e_notes = st.text_area("Notes / Description", value=editing_item.get("notes", ""))
                        e_public_msg = st.text_area("Public Release Message", value=editing_item.get("public_message", ""))

                        # 🟢 Changed to 3 columns to fit the Delete button
                        col_save, col_del, col_cancel = st.columns([2, 1, 1])
                        
                        if col_save.form_submit_button("💾 Save", type="primary", use_container_width=True):
                            update_backlog_item(editing_item["id"], e_feature, e_notes, e_status, e_app, e_category, e_priority, e_public_msg)
                            st.session_state["editing_backlog_id"] = None
                            st.rerun()
                            
                        # 🟢 The new Delete Button
                        if col_del.form_submit_button("🗑️ Delete", use_container_width=True):
                            delete_backlog_item(editing_item["id"])
                            st.session_state["editing_backlog_id"] = None
                            st.rerun()
                            
                        if col_cancel.form_submit_button("❌ Cancel", use_container_width=True):
                            st.session_state["editing_backlog_id"] = None
                            st.rerun()
                            
        # --- 4. Display Items (Expanders) ---
        if items:
            apps = set([item.get("app_name") if item.get("app_name") else "unassigned" for item in items])
            
            # Sorts so "home_sync" is always index 0, followed by the rest alphabetically
            sorted_apps = sorted(apps, key=lambda x: (0 if x == "home_sync" else 1, x))
            
            for app in sorted_apps:
                clean_name = str(app).replace("_", " ").title()
                
                with st.expander(f"📱 {clean_name}", expanded=(app == "home_sync")):
                    app_items = [i for i in items if i.get("app_name") == app or (not i.get("app_name") and app == "unassigned")]
                    
                    for item in app_items:
                        col_text, col_act = st.columns([5, 1])
                        col_text.markdown(f"**{item.get('feature', 'Unnamed Feature')}**")
                        # Emphasize the Status visually to show the sorting order
                        col_text.caption(f"Status: **{item.get('status', 'N/A')}** | Category: {item.get('category', 'N/A')} | Priority: {item.get('priority', 'N/A')}")
                        
                        # 🟢 NEW: Explicit tags for Notes and Public Message
                        notes_text = item.get("notes", "").strip()
                        public_msg = item.get("public_message", "").strip()
                        
                        if notes_text:
                            col_text.markdown(f"**Notes / Description:** {notes_text}")
                            
                        if public_msg:
                            # Added a slight color tint to the public message so it stands out from internal notes!
                            col_text.markdown(f"**Public Message:** <span style='color: #10B981;'>{public_msg}</span>", unsafe_allow_html=True)
                        
                        if col_act.button("✏️ Edit", key=f"edit_bl_{item['id']}"):
                            st.session_state["editing_backlog_id"] = item["id"]
                            st.rerun()
                        st.divider()
        else:
            st.info("Your master backlog is currently empty.")