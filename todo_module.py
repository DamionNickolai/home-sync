import json

import pandas as pd
import streamlit as st
from zoneinfo import ZoneInfo

from database import (
    add_new_task,
    batch_update_tasks,
    delete_task,
    get_active_tasks,
    get_available_users,
    get_completed_tasks,
    update_task,
)
from ui_helpers import queue_rerun_reason, render_two_col_selector

FALLBACK_TIMEZONE = "America/Chicago"
RECURRENCE_OPTIONS = ["Daily", "Weekly", "Biweekly", "Monthly", "Quarterly", "Every 6 Months", "Yearly"]


def _get_app_timezone() -> ZoneInfo:
    tz_name = st.session_state.get("user_timezone", FALLBACK_TIMEZONE)
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo(FALLBACK_TIMEZONE)


def _central_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=_get_app_timezone())


def _to_central_timestamp(value):
    app_tz = _get_app_timezone()
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


def _parse_assignees(assigned_to_raw):
    try:
        if isinstance(assigned_to_raw, str) and assigned_to_raw.startswith("["):
            return json.loads(assigned_to_raw)
        return [assigned_to_raw]
    except Exception:
        return [assigned_to_raw]


def _get_due_status(target_date_str, priority):
    days_remaining = None
    if target_date_str:
        try:
            t_date = pd.to_datetime(target_date_str).tz_localize(None).date()
            today = _central_now().tz_localize(None).date()
            days_remaining = (t_date - today).days
        except Exception:
            days_remaining = None

    status_msg = "⚪ No Date"
    if days_remaining is not None:
        if days_remaining < 0:
            status_msg = f"🔴 Overdue by {abs(days_remaining)}d"
        elif days_remaining == 0:
            status_msg = "🟠 Due TODAY"
        elif days_remaining == 1:
            status_msg = "🟡 Due Tomorrow"
        else:
            status_msg = f"🟢 Due in {days_remaining}d"
    elif priority == "High":
        status_msg = "🔵 High Priority"
    elif priority == "Low":
        status_msg = "⚪ Low Priority"

    return status_msg


def _get_due_bucket(task):
    target_str = task.get("target_date")
    if not target_str:
        return None
    try:
        due_date = pd.to_datetime(target_str).tz_localize(None).date()
    except Exception:
        return None
    today_local = _central_now().tz_localize(None).date()
    delta_days = (due_date - today_local).days
    if delta_days < 0:
        return "overdue"
    if delta_days == 0:
        return "today"
    if 1 <= delta_days <= 7:
        return "week"
    return None


@st.fragment
def render_todo_view():
    st.subheader("📋 Active To-Do List")
    current_user = st.session_state.get("logged_in_user", "Unknown")
    user_role = st.session_state.get("user_role", "member")
    current_household = st.session_state.get("household_id", "unassigned")

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

    if "editing_task_id" not in st.session_state:
        st.session_state["editing_task_id"] = None

    all_active_tasks = get_active_tasks()
    active_tasks = []
    for task in all_active_tasks:
        assignees = _parse_assignees(task.get("assigned_to", "Unassigned"))
        if user_role in ["developer", "admin"] or current_user in assignees:
            active_tasks.append(task)

    overdue_count = sum(1 for t in active_tasks if _get_due_bucket(t) == "overdue")
    due_today_count = sum(1 for t in active_tasks if _get_due_bucket(t) == "today")
    due_week_count = sum(1 for t in active_tasks if _get_due_bucket(t) == "week")
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

    with st.expander("➕ Add New Task", expanded=False):
        with st.form("new_task_form", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            new_task = col1.text_input("Task", placeholder="e.g., Change HVAC filter")
            priority = col2.selectbox("Priority", ["Normal", "High", "Low"])
            new_notes = st.text_area("Notes", placeholder="Optional: tips, tools, or helper notes")

            col3, col4, col5 = st.columns(3)
            category = col3.selectbox("Category", ["House", "Yard", "Admin", "Errand"])

            if user_role in ["developer", "admin"]:
                available_users = get_available_users(current_household)
                default_users = (
                    [current_user]
                    if current_user in available_users
                    else (available_users[:1] if available_users else [])
                )
                assigned_to = col4.multiselect("Assign To", options=available_users, default=default_users)
            else:
                col4.text_input("Assign To", value=current_user, disabled=True)
                assigned_to = [current_user]

            target_date = col5.date_input("Target Date", value=None)

            rec1, rec2 = st.columns([1, 2])
            is_recurring = rec1.checkbox("Recurring task", value=False)
            recurrence_pattern = rec2.selectbox("Recurring", RECURRENCE_OPTIONS, index=3)

            submit = st.form_submit_button("Save Task", type="primary", width="stretch")
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

    if not active_tasks:
        st.info("🎉 You are all caught up! No active tasks.")
    else:
        single_user_tasks = {}
        multi_assigned_tasks = []
        for task in active_tasks:
            assignees = sorted([a for a in _parse_assignees(task.get("assigned_to", "Unassigned")) if str(a).strip()])
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
                    return (
                        pd.to_datetime(task_row.get("target_date")).date()
                        if task_row.get("target_date")
                        else pd.Timestamp.max.date()
                    )
                except Exception:
                    return pd.Timestamp.max.date()

            def render_task_item(task, assignees, key_scope):
                task_name = task.get("task_name", "Unnamed Task")
                priority = task.get("priority", "Normal")
                target_date_str = task.get("target_date")
                notes = (task.get("notes") or "").strip()
                is_recurring = bool(task.get("is_recurring", False))
                recurrence_pattern = task.get("recurrence_pattern") or ""

                status_msg = _get_due_status(target_date_str, priority)
                due_label = target_date_str if target_date_str else "No date"
                category = task.get("category", "Uncategorized")
                recurring_label = f" • 🔁 {recurrence_pattern}" if is_recurring and recurrence_pattern else ""
                detail_parts = [status_msg, category, f"📅 {due_label}{recurring_label}"]

                with st.container(border=True):
                    title_col, action_col = st.columns([6, 1])
                    title_col.write(f"**{task_name}**")
                    if user_role in ["developer", "admin"]:
                        if action_col.button("✏️ Edit", key=f"open_task_{task['id']}_{key_scope}", width="stretch"):
                            current_editing = st.session_state.get("editing_task_id")
                            st.session_state["editing_task_id"] = None if current_editing == task["id"] else task["id"]
                            queue_rerun_reason("task_edit_toggle")

                    st.caption(" • ".join(detail_parts))
                    if len(assignees) > 1:
                        st.caption(f"Assigned to multiple: {', '.join(assignees)}")
                    if notes:
                        st.caption(f"Notes: {notes}")

                if st.session_state.get("editing_task_id") == task["id"] and user_role in ["developer", "admin"]:
                    current_assignees = _parse_assignees(task.get("assigned_to", "Unassigned"))

                    with st.container(border=True):
                        st.caption("✏️ Edit Task")
                        with st.form(f"edit_task_form_{task['id']}_{key_scope}"):
                            edit_col1, edit_col2 = st.columns([3, 1])
                            edit_task_name = edit_col1.text_input(
                                "Task",
                                value=task.get("task_name", ""),
                                key=f"edit_task_name_{task['id']}_{key_scope}",
                            )

                            safe_priority = task.get("priority", "Normal")
                            p_index = (
                                ["Normal", "High", "Low"].index(safe_priority)
                                if safe_priority in ["Normal", "High", "Low"]
                                else 0
                            )
                            edit_priority = edit_col2.selectbox(
                                "Priority",
                                ["Normal", "High", "Low"],
                                index=p_index,
                                key=f"edit_priority_{task['id']}_{key_scope}",
                            )

                            edit_notes = st.text_area(
                                "Notes",
                                value=task.get("notes") or "",
                                key=f"edit_notes_{task['id']}_{key_scope}",
                            )

                            edit_col3, edit_col4, edit_col5 = st.columns(3)
                            safe_cat = task.get("category", "House")
                            c_index = (
                                ["House", "Yard", "Admin", "Errand"].index(safe_cat)
                                if safe_cat in ["House", "Yard", "Admin", "Errand"]
                                else 0
                            )
                            edit_category = edit_col3.selectbox(
                                "Category",
                                ["House", "Yard", "Admin", "Errand"],
                                index=c_index,
                                key=f"edit_cat_{task['id']}_{key_scope}",
                            )

                            available_users = get_available_users(current_household)
                            safe_defaults = [u for u in current_assignees if u in available_users]
                            edit_assigned_to = edit_col4.multiselect(
                                "Assign To",
                                options=available_users,
                                default=safe_defaults,
                                key=f"edit_assign_{task['id']}_{key_scope}",
                            )

                            current_target_date = (
                                pd.to_datetime(task.get("target_date")).date() if task.get("target_date") else None
                            )
                            has_target_date = edit_col5.checkbox(
                                "Has Target Date",
                                value=current_target_date is not None,
                                key=f"has_target_{task['id']}_{key_scope}",
                            )
                            edit_target_date = edit_col5.date_input(
                                "Target Date",
                                value=current_target_date or _central_now().tz_localize(None).date(),
                                key=f"edit_target_{task['id']}_{key_scope}",
                            )

                            edit_rec_col1, edit_rec_col2 = st.columns([1, 2])
                            safe_recurring = bool(task.get("is_recurring", False))
                            edit_is_recurring = edit_rec_col1.checkbox(
                                "Recurring task",
                                value=safe_recurring,
                                key=f"edit_recur_enabled_{task['id']}_{key_scope}",
                            )
                            existing_pattern = task.get("recurrence_pattern", "Monthly")
                            rec_index = (
                                RECURRENCE_OPTIONS.index(existing_pattern)
                                if existing_pattern in RECURRENCE_OPTIONS
                                else 3
                            )
                            edit_recurrence_pattern = edit_rec_col2.selectbox(
                                "Recurring",
                                RECURRENCE_OPTIONS,
                                index=rec_index,
                                key=f"edit_recur_pattern_{task['id']}_{key_scope}",
                            )

                            save_col, complete_col, del_col, cancel_col = st.columns([2, 1, 1, 1])
                            save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                            complete_clicked = complete_col.form_submit_button("✅ Complete", width="stretch")
                            delete_clicked = del_col.form_submit_button("🗑️ Delete", width="stretch")
                            cancel_clicked = cancel_col.form_submit_button("❌ Cancel", width="stretch")

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
                    assignees = sorted(
                        [a for a in _parse_assignees(task.get("assigned_to", "Unassigned")) if str(a).strip()]
                    )
                    render_task_item(task, assignees, f"multi_{task['id']}")
            else:
                username = selected_task_bucket.split("::", 1)[1]
                tasks_in_bucket = [x[0] for x in single_user_tasks.get(username, [])]
                tasks_in_bucket.sort(key=due_sort_value)
                for task in tasks_in_bucket:
                    assignees = sorted(
                        [a for a in _parse_assignees(task.get("assigned_to", "Unassigned")) if str(a).strip()]
                    )
                    render_task_item(task, assignees, f"user_{username}_{task['id']}")

    st.divider()

    with st.expander("✅ Recently Completed (Last 14 Days)"):
        all_completed = get_completed_tasks()
        completed_tasks = []
        cutoff_date = _central_now() - pd.Timedelta(days=14)

        for task in all_completed:
            date_str = task.get("target_date") or task.get("created_at")
            try:
                task_date = _to_central_timestamp(date_str)
                is_recent = task_date >= cutoff_date
            except Exception:
                is_recent = True
                task_date = None

            if not is_recent:
                continue

            assignees = _parse_assignees(task.get("assigned_to", "Unassigned"))
            if user_role in ["developer", "admin"] or current_user in assignees:
                task["_display_date"] = task_date.strftime("%b %d, %Y") if task_date else "No Date"
                completed_tasks.append(task)

        if completed_tasks:
            for task in completed_tasks:
                assignees = _parse_assignees(task.get("assigned_to", "Unassigned"))
                col_text, col_date, col_recall = st.columns([2.5, 1.5, 1])
                col_text.caption(f"~~{task['task_name']}~~")
                col_date.caption(f"📅 {task.get('_display_date')}")
                if user_role in ["developer", "admin"] or current_user in assignees:
                    if col_recall.button("🔄 Recall", key=f"recall_{task['id']}"):
                        batch_update_tasks([task["id"]], False)
                        queue_rerun_reason("task_write")
        else:
            st.caption("No recently completed tasks in the last 14 days.")
