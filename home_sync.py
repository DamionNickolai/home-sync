import streamlit as st
import pandas as pd
import time
import json
from auth import check_password
from home_assist_api import fetch_ha_state
from database import get_active_tasks, get_completed_tasks, add_new_task, batch_update_tasks, update_task
from supabase import create_client, Client

@st.cache_resource
def get_supabase_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

@st.cache_data(ttl=3600)
def get_available_users():
    """Fetch list of available users from the database."""
    try:
        supabase = get_supabase_client()
        response = supabase.table("users").select("username").execute()
        return [user["username"] for user in response.data] if response.data else []
    except Exception as e:
        st.warning(f"Could not fetch users: {e}")
        return []

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
# This barrier stops the app from running until the user logs in
if not check_password():
    st.stop()

role = st.session_state.get("role", "user")

# ==========================================
#  SIDEBAR COMMAND CENTER
# ==========================================
st.sidebar.header("⚡ Quick Controls")
st.sidebar.info("Garage door local controls coming soon...")

st.sidebar.markdown("---")

# The Developer Lock
if role == "developer":
    with st.sidebar.expander("🛠️ Developer Tools"):
        st.caption("Home Assistant API Status: Standby")
        # We can put API raw payloads and cache clear buttons here later

if st.sidebar.button("🚪 Log Out", width='stretch'):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.query_params.clear() 
    st.rerun()
    
# ==========================================
# 🚧 ENVIRONMENT DETECTION & BANNER
# ==========================================
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
# 📋 MAIN DASHBOARD TABS
# ==========================================
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
                              
            # --- 1. Form to Add a New Task ---
            with st.expander("➕ Add New Task", expanded=False):
                with st.form("new_task_form", clear_on_submit=True):
                    col1, col2 = st.columns([3, 1])
                    new_task = col1.text_input("Task Description", placeholder="e.g., Clean the gutters")
                    priority = col2.selectbox("Priority", ["Normal", "High", "Low"])
                    
                    col3, col4, col5 = st.columns(3)
                    category = col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"])
                    
                    # Multi-select for Assign To
                    available_users = get_available_users()
                    default_users = [current_user] if current_user in available_users else (available_users[:1] if available_users else [])
                    assigned_to = col4.multiselect("Assign To", options=available_users, default=default_users)
                    
                    target_date = col5.date_input("Target Date", value=None)
                    
                    submit = st.form_submit_button("Save Task", type="primary", use_container_width=True)
                    if submit and new_task and assigned_to:
                        # Call the updated function with the list of assignees as JSON
                        success = add_new_task(new_task, category, priority, json.dumps(assigned_to), target_date)
                        if success:
                            st.success("Task added!")
                            st.rerun()
                    elif submit and not assigned_to:
                        st.error("Please assign the task to at least one person.")

            st.write("")
            
            # --- 1.5 Edit Task Form (shown when editing) ---
            if "editing_task_id" not in st.session_state:
                st.session_state["editing_task_id"] = None
            
            if st.session_state["editing_task_id"] is not None:
                # Find the task being edited
                all_tasks = get_active_tasks()
                editing_task = next((t for t in all_tasks if t["id"] == st.session_state["editing_task_id"]), None)
                
                if editing_task:
                    with st.container(border=True):
                        st.markdown("### ✏️ Edit Task")
                        
                        with st.form("edit_task_form"):
                            # Parse current assignees
                            assigned_to_raw = editing_task.get("assigned_to", "Unassigned")
                            try:
                                current_assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                            except:
                                current_assignees = [assigned_to_raw]
                            
                            col1, col2 = st.columns([3, 1])
                            edit_task_name = col1.text_input("Task Description", value=editing_task.get("task_name", ""))
                            edit_priority = col2.selectbox("Priority", ["Normal", "High", "Low"], index=["Normal", "High", "Low"].index(editing_task.get("priority", "Normal")))
                            
                            col3, col4, col5 = st.columns(3)
                            edit_category = col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"], index=["House", "Yard", "Admin", "Errand"].index(editing_task.get("category", "House")))
                            
                            available_users = get_available_users()
                            edit_assigned_to = col4.multiselect("Assign To", options=available_users, default=current_assignees)
                            
                            edit_target_date = col5.date_input("Target Date", value=pd.to_datetime(editing_task.get("target_date")) if editing_task.get("target_date") else None)
                            
                            col_save, col_cancel = st.columns(2)
                            save_edit = col_save.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
                            cancel_edit = col_cancel.form_submit_button("❌ Cancel", use_container_width=True)
                            
                            if save_edit and edit_task_name and edit_assigned_to:
                                success = update_task(
                                    st.session_state["editing_task_id"],
                                    task_name=edit_task_name,
                                    category=edit_category,
                                    priority=edit_priority,
                                    assigned_to=json.dumps(edit_assigned_to),
                                    target_date=edit_target_date
                                )
                                if success:
                                    st.success("Task updated!")
                                    st.session_state["editing_task_id"] = None
                                    st.rerun()
                                else:
                                    st.error("Failed to update task.")
                            elif save_edit and not edit_assigned_to:
                                st.error("Please assign the task to at least one person.")
                            
                            if cancel_edit:
                                st.session_state["editing_task_id"] = None
                                st.rerun()
            
            # --- 2. Display Active Tasks and Batch Processing ---
            active_tasks = get_active_tasks()
            
            if not active_tasks:
                st.info("🎉 You are all caught up! No active tasks.")
            else:
                # Define sort key function that handles multi-user tasks and dates
                def get_task_sort_key(task):
                    # Parse assignees
                    assigned_to_raw = task.get("assigned_to", "Unassigned")
                    try:
                        assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                    except:
                        assignees = [assigned_to_raw]
                    
                    # Check if current user is assigned (False sorts before True, so NOT in list comes after)
                    is_not_assigned_to_current = current_user not in assignees
                    
                    # Parse date for calendar ordering
                    due_date_str = task.get("target_date")
                    has_no_date = not due_date_str
                    
                    try:
                        if due_date_str:
                            due_date = pd.to_datetime(due_date_str).date()
                        else:
                            due_date = pd.Timestamp.max.date()  # Push no-date tasks to end
                    except:
                        due_date = pd.Timestamp.max.date()
                    
                    # Return tuple: (current_user not assigned, no date, date value)
                    # This prioritizes: current user's tasks -> tasks with dates in order -> others
                    return (is_not_assigned_to_current, has_no_date, due_date)
                
                active_tasks.sort(key=get_task_sort_key)

                for task in active_tasks:
                    col_check, col_text, col_meta, col_actions = st.columns([0.5, 4, 1.5, 0.8])
                    
                    # The Checkbox
                    if col_check.checkbox(" ", key=f"task_{task['id']}"):
                        st.session_state[f"to_complete_{task['id']}"] = True
                    
                    # Render Text with Defensive .get()
                    # Using .get() prevents KeyError if the field is missing
                    task_name = task.get("task_name", "Unnamed Task")
                    priority = task.get("priority", "Normal")
                    
                    display_name = f"**🔴 {task_name}**" if priority == "High" else f"**{task_name}**"
                    
                    # Check if current user is in the assignees list
                    assigned_to_raw = task.get("assigned_to", "Unassigned")
                    try:
                        assignees = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                    except:
                        assignees = [assigned_to_raw]
                    
                    if current_user in assignees:
                        display_name += " *(👉 Yours)*"
                    col_text.markdown(display_name)
                    
                    # Render Metadata with Defensive .get()
                    category = task.get("category", "Uncategorized")
                    
                    # Parse assigned_to for display - handle both JSON array and legacy string format
                    try:
                        assignees_display = json.loads(assigned_to_raw) if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith('[') else [assigned_to_raw]
                        assignee = ", ".join(assignees_display)
                    except:
                        assignee = assigned_to_raw
                    
                    due = task.get("target_date", "No date")
                    
                    col_meta.caption(f"_{category}_ • 👤 {assignee} • 📅 {due}")
                    
                    # Edit Button
                    if col_actions.button("✏️", key=f"edit_{task['id']}", help="Edit task"):
                        st.session_state["editing_task_id"] = task['id']
                        st.rerun()

                # Collect selected task IDs (not full task objects)
                selected_task_ids = [task['id'] for task in active_tasks if st.session_state.get(f"to_complete_{task['id']}")]

            # Batch Action Button
            if selected_task_ids:
                if st.button(f"✅ Mark {len(selected_task_ids)} Task(s) Complete"):
                    if batch_update_tasks(selected_task_ids, True):
                        st.success("Tasks updated!")
                        st.rerun()

            st.divider()
            
            # --- 3. Recently Completed (with Recall) ---
            with st.expander("✅ Recently Completed"):
                completed = get_completed_tasks()
                if completed:
                    for task in completed:
                        col_text, col_recall = st.columns([4, 1])
                        col_text.caption(f"~~{task['task_name']}~~")
                        
                        # Recall Button
                        if col_recall.button("🔄 Recall", key=f"recall_{task['id']}"):
                            batch_update_tasks([task['id']], False) # Flip back to False
                            st.rerun()
                else:
                    st.caption("No recently completed tasks.")
                    
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