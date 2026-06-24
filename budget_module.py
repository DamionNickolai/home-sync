import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
from datetime import datetime

from database import (
    get_project_budgets,
    update_project_budget_item,
    insert_project_budget_item,
    get_household_finance_settings,
    update_household_projects_funds,
    get_household_users_for_admin,
    get_wish_list_items,
    insert_wish_list_item,
    update_wish_list_item,
    delete_wish_list_item,
    complete_wish_list_item,
    restore_wish_list_item,
    get_household_incomes, 
    get_monthly_expenses, 
    get_cash_flow_routing, 
    calculate_spend_money,
    get_user_finance_settings,
    log_expense_and_check_project,
    get_budget_categories,
    insert_budget_category,
    insert_household_income,
    get_individual_expenses,
    update_user_privacy_toggle,
    delete_budget_category,
    delete_household_income,
    initialize_default_categories,
    ensure_household_initialized
)

def _to_number(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _to_int(value, default=99):
    try:
        if value is None or str(value).strip() == "":
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _clean_text(value):
    return str(value or "").strip()


def _make_key_fragment(value):
    text = _clean_text(value).lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "item"


COMPLETED_TAG = "[COMPLETED]"
FALLBACK_TIMEZONE = "America/Chicago"
PROJECT_CATEGORIES = [
    "Garage & Shed",
    "Gym",
    "Home Improvement",
    "Home Interior",
    "Outdoor & Yard",
    "Pets",
    "Pool",
    "Tools & Hardware",
    "Vehicles",
]


def _is_completed_project(row):
    notes_text = _clean_text(row.get("notes"))
    status_text = _clean_text(row.get("status")).lower()
    return notes_text.startswith(COMPLETED_TAG) or status_text == "completed" or bool(row.get("is_completed", False))


def _mark_completed_notes(notes_text):
    clean_notes = _clean_text(notes_text)
    if clean_notes.startswith(COMPLETED_TAG):
        return clean_notes
    if clean_notes:
        return f"{COMPLETED_TAG} {clean_notes}"
    return COMPLETED_TAG


def _restore_active_notes(notes_text):
    clean_notes = _clean_text(notes_text)
    if clean_notes.startswith(COMPLETED_TAG):
        return clean_notes[len(COMPLETED_TAG):].strip() or None
    return clean_notes or None


def _parse_currency_input(raw_value):
    txt = _clean_text(raw_value).replace(",", "")
    if txt == "":
        return None
    try:
        return float(txt)
    except Exception:
        return "invalid"


def _format_currency_for_input(value):
    amount = _to_number(value, 0)
    if amount == 0:
        return ""
    if float(amount).is_integer():
        return str(int(amount))
    return str(amount)


def _format_money(amount):
    return f"${_to_number(amount, 0):,.2f}"


def _plotly_chart_locked(fig, **kwargs):
    fig.update_layout(dragmode=False)
    config = {
        "displaylogo": False,
        "scrollZoom": False,
        "modeBarButtonsToRemove": [
            "select2d",
            "lasso2d",
            "zoom2d",
            "pan2d",
            "autoScale2d",
            "resetScale2d",
            "zoomIn2d",
            "zoomOut2d",
        ],
    }
    st.plotly_chart(fig, config=config, **kwargs)


def _extract_project_year(row, fallback_year):
    app_tz_name = st.session_state.get("user_timezone", FALLBACK_TIMEZONE)
    try:
        app_tz = ZoneInfo(str(app_tz_name))
    except Exception:
        app_tz = ZoneInfo(FALLBACK_TIMEZONE)

    for key in ["created_at", "updated_at"]:
        raw_val = row.get(key)
        if raw_val:
            try:
                parsed = pd.to_datetime(raw_val, utc=True).tz_convert(app_tz)
                return parsed.year
            except Exception:
                pass
    return fallback_year


def _toggle_project_edit(project_id):
    current_id = st.session_state.get("editing_project_budget_id")
    st.session_state["editing_project_budget_id"] = None if current_id == project_id else project_id


def _can_access_projects_module():
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    return bool(st.session_state.get("can_view_projects", st.session_state.get("can_view_budget", False)))


def _can_edit_projects_module():
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    return bool(st.session_state.get("can_edit_projects", st.session_state.get("can_view_budget", False)))


def _can_access_monthly_module():
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    return bool(st.session_state.get("can_view_monthly_budget", st.session_state.get("can_view_budget", False)))


def render_budget_module(show_back_to_hub=False):
    ensure_household_initialized(st.session_state["household_id"])

    if "budget_view" not in st.session_state:
        st.session_state["budget_view"] = "menu"
    if "editing_project_budget_id" not in st.session_state:
        st.session_state["editing_project_budget_id"] = None
    if "projects_funds" not in st.session_state:
        st.session_state["projects_funds"] = None
    if "pending_restore_project_id" not in st.session_state:
        st.session_state["pending_restore_project_id"] = None
    if "projects_funds_input" not in st.session_state:
        st.session_state["projects_funds_input"] = ""
    if "projects_active_section" not in st.session_state:
        st.session_state["projects_active_section"] = "overview"
    if "projects_workspace_active_category" not in st.session_state:
        st.session_state["projects_workspace_active_category"] = "priority"
    if "editing_wishlist_id" not in st.session_state:
        st.session_state["editing_wishlist_id"] = None
    if "wishlist_active_owner" not in st.session_state:
        st.session_state["wishlist_active_owner"] = None
    if "wishlist_pending_owner" not in st.session_state:
        st.session_state["wishlist_pending_owner"] = None

    view = st.session_state["budget_view"]
    can_access_projects = _can_access_projects_module()
    can_edit_projects = _can_edit_projects_module()
    can_access_monthly = _can_access_monthly_module()

    if view == "menu":
        if show_back_to_hub:
            if st.button("⬅️ Back to Hub Menu", width="content"):
                st.session_state["active_hub_view"] = "main_menu"
                st.rerun()

        st.title("Financial Hub 💰")
        st.caption("Manage household finances with quick cards and project-level visibility.")
        st.subheader("Budget Modules")
        st.caption("Pick a budget module to open.")

        # --- ROW 1: The Dashboards ---
        r1c1, r1c2 = st.columns(2)
        with r1c1:
            with st.container(border=True):
                st.markdown("### 🏦 Household Budget")
                if can_access_monthly:
                    st.caption("Admin view for master ledger, bills, and routing.")
                    if st.button("Open Household", key="btn_household", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "household"
                        st.rerun()
                else:
                    st.caption("Household view is locked to Admins/Developers.")
                    st.button("Household Locked", disabled=True, width="stretch")

        with r1c2:
            with st.container(border=True):
                st.markdown("### 👤 Personal Budget")
                st.caption("Your private dashboard for personal 'spend money'.")
                if st.button("Open Personal", key="btn_personal", type="secondary", width="stretch"):
                    st.session_state["budget_view"] = "personal"
                    st.rerun()

        # --- ROW 2: Event Logging & Projects ---
        r2c1, r2c2 = st.columns(2)
        with r2c1:
            with st.container(border=True):
                st.markdown("### 💳 Expense Tracker")
                st.caption("Log a purchase to automatically update ledgers.")
                if st.button("Log Expense", key="btn_expense", type="primary", width="stretch"):
                    st.session_state["budget_view"] = "expense_tracker"
                    st.rerun()
                    
        with r2c2:
            with st.container(border=True):
                st.markdown("### 🛠️ Projects")
                if can_access_projects:
                    st.caption("View active project estimates and execution notes.")
                    if st.button("Open Projects", key="btn_projects", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "projects"
                        st.rerun()
                else:
                    st.caption("Projects access is restricted.")
                    st.button("Projects Locked", disabled=True, width="stretch")

        # --- ROW 3: Wish List ---
        r3c1, r3c2 = st.columns(2)
        with r3c1:
            with st.container(border=True):
                st.markdown("### 💝 Wish List")
                st.caption("Track purchase ideas in a shared household list.")
                if st.button("Open Wish List", key="btn_wish", type="secondary", width="stretch"):
                    st.session_state["budget_view"] = "wishlist"
                    st.rerun()

        return

    if st.button("⬅️ Back to Budget Modules", width="content"):
        st.session_state["budget_view"] = "menu"
        st.session_state["editing_project_budget_id"] = None
        st.session_state["editing_wishlist_id"] = None
        st.rerun()

    st.divider()

    if view == "projects" and not can_access_projects:
        st.warning("Projects access is currently disabled for your account. Ask your household admin to enable it.")
        if st.button("⬅️ Return to Budget Modules", key="projects_access_denied_return"):
            st.session_state["budget_view"] = "menu"
            st.rerun()
        return

    if view == "monthly" and not can_access_monthly:
        st.warning("Monthly Budget access is currently disabled for your account. Ask your household admin to enable it.")
        if st.button("⬅️ Return to Budget Modules", key="monthly_access_denied_return"):
            st.session_state["budget_view"] = "menu"
            st.rerun()
        return

    if view == "wishlist":
        st.subheader("💝 Wish List")
        st.caption("Visible to all roles. Entries are individually owned.")

        role = st.session_state.get("user_role", "member")
        active_auth_user_id = st.session_state.get("auth_user_id")
        active_username = _clean_text(st.session_state.get("username"))
        can_view_wishlist_members = bool(st.session_state.get("can_view_wishlist_members", True))
        can_view_wishlist_admin = bool(st.session_state.get("can_view_wishlist_admin", False))

        household_users = get_household_users_for_admin()
        username_to_auth = {
            _clean_text(u.get("username")): u.get("auth_user_id")
            for u in household_users
            if _clean_text(u.get("username"))
        }
        username_to_role = {
            _clean_text(u.get("username")): _clean_text(u.get("role")).lower()
            for u in household_users
            if _clean_text(u.get("username"))
        }

        wish_rows = get_wish_list_items()

        def get_row_owner_username(row):
            return _clean_text(row.get("owner_username")) or "unassigned"

        def get_row_owner_auth_user_id(row):
            return row.get("owner_auth_user_id")

        def can_edit_wish_row(row):
            if role in ["admin", "developer"]:
                return True
            row_auth_user = get_row_owner_auth_user_id(row)
            if active_auth_user_id and str(row_auth_user or "") == str(active_auth_user_id):
                return True
            row_user = get_row_owner_username(row)
            return bool(active_username and row_user and active_username == row_user)

        def is_row_visible_to_member(row):
            owner_name = get_row_owner_username(row)
            owner_role = username_to_role.get(owner_name, "member")
            if owner_role in ["admin", "developer"]:
                return can_view_wishlist_admin
            return can_view_wishlist_members

        if role in ["admin", "developer"]:
            visible_rows = wish_rows
        else:
            visible_rows = [r for r in wish_rows if is_row_visible_to_member(r)]

        all_owner_names = sorted({get_row_owner_username(r) for r in visible_rows if get_row_owner_username(r)})
        if role in ["admin", "developer"]:
            available_owner_names = sorted(set(list(username_to_auth.keys()) + all_owner_names))
        else:
            available_owner_names = sorted(set(all_owner_names + ([active_username] if active_username else [])))

        if not available_owner_names:
            available_owner_names = ["unassigned"]

        pending_owner = st.session_state.pop("wishlist_pending_owner", None)
        if pending_owner in available_owner_names:
            st.session_state["wishlist_active_owner"] = pending_owner

        if st.session_state.get("wishlist_active_owner") not in available_owner_names:
            st.session_state["wishlist_active_owner"] = active_username if active_username in available_owner_names else available_owner_names[0]

        selected_owner = st.radio(
            "Wish List Owner",
            options=available_owner_names,
            key="wishlist_active_owner",
            horizontal=True,
            label_visibility="collapsed",
            format_func=lambda owner: f"👤 {owner}",
        )

        active_rows = [r for r in visible_rows if not bool(r.get("is_completed", False))]
        completed_rows = [r for r in visible_rows if bool(r.get("is_completed", False))]

        filtered_rows = [r for r in active_rows if get_row_owner_username(r) == selected_owner]

        with st.expander("➕ Add Wish List Item", expanded=False):
            with st.form("add_wishlist_item_form", clear_on_submit=True):
                a1, a2 = st.columns([2, 1])
                new_item = a1.text_input("Item *", placeholder="e.g., New patio furniture")
                new_est_price_raw = a2.text_input("Estimated Price", placeholder="Enter amount")
                new_owner_username = active_username or "unassigned"

                new_description = st.text_area("Description", placeholder="Why this item is wanted")

                b1, b2, b3 = st.columns([1, 1, 2])
                new_actual_raw = b1.text_input("Actual Cost", placeholder="Enter amount")
                new_vet_discount = b2.checkbox("Veteran Discount", value=False)
                new_vendor = b3.text_input("Vendor", placeholder="Store or seller")

                new_notes = st.text_area("Notes", placeholder="Optional notes")

                add_save_col, add_complete_col = st.columns(2)
                add_clicked = add_save_col.form_submit_button("Save Wish Item", type="primary", width="stretch")
                complete_clicked = add_complete_col.form_submit_button("Complete", width="stretch")
                if add_clicked or complete_clicked:
                    parsed_est = _parse_currency_input(new_est_price_raw)
                    parsed_actual = _parse_currency_input(new_actual_raw)

                    if not new_item.strip():
                        st.warning("Item is required.")
                    elif "invalid" in [parsed_est, parsed_actual]:
                        st.warning("Estimated Price and Actual Cost must be valid numbers.")
                    else:
                        payload = {
                            "item": new_item.strip(),
                            "description": _clean_text(new_description) or None,
                            "estimated_price": float(parsed_est) if parsed_est is not None else None,
                            "actual_cost": float(parsed_actual) if parsed_actual is not None else None,
                            "veteran_discount": bool(new_vet_discount),
                            "vendor": _clean_text(new_vendor) or None,
                            "notes": _clean_text(new_notes) or None,
                            "owner_username": active_username or new_owner_username,
                            "owner_auth_user_id": active_auth_user_id or username_to_auth.get(new_owner_username),
                            "is_completed": bool(complete_clicked),
                        }
                        if insert_wish_list_item(payload):
                            st.session_state["wishlist_pending_owner"] = new_owner_username
                            st.success("Wish list item added.")
                            st.rerun()
                        else:
                            st.error("Could not add wish list item.")

        if not filtered_rows:
            st.info(f"No wish list items for {selected_owner} yet.")
            return

        for row in filtered_rows:
            row_id = row.get("id")
            if row_id is None:
                continue

            item_name = row.get("item") or "Unnamed Item"
            description = _clean_text(row.get("description"))
            est_price = row.get("estimated_price")
            actual_cost = row.get("actual_cost")
            vendor = _clean_text(row.get("vendor"))
            notes = _clean_text(row.get("notes"))
            veteran_discount = bool(row.get("veteran_discount", False))
            owner_name = get_row_owner_username(row)

            editable = can_edit_wish_row(row)

            with st.container(border=True):
                left, right = st.columns([6, 1])
                left.markdown(f"**{item_name}**")
                if description:
                    left.markdown(f"**Description:** {description}")
                if vendor:
                    left.markdown(f"**Brand or Vendor:** {vendor}")
                if est_price is not None:
                    left.markdown(
                        f"**Estimated:** <span style='color: #16A34A; font-weight: 700;'>{_format_money(est_price)}</span>",
                        unsafe_allow_html=True,
                    )
                if notes:
                    left.markdown(f"**Notes:** {notes}")
                if veteran_discount:
                    left.caption("Eligible for veteran discount.")

                if editable:
                    if right.button("✏️ Edit", key=f"edit_wishlist_{row_id}", width="stretch"):
                        current_edit = st.session_state.get("editing_wishlist_id")
                        st.session_state["editing_wishlist_id"] = None if current_edit == row_id else row_id
                        st.rerun()

            if editable and st.session_state.get("editing_wishlist_id") == row_id:
                with st.container(border=True):
                    st.markdown("### ✏️ Edit Wish Item")
                    with st.form(f"edit_wishlist_form_{row_id}"):
                        e1, e2 = st.columns([2, 1])
                        e_item = e1.text_input("Item *", value=item_name)
                        e_est_raw = e2.text_input("Estimated Price", value=_format_currency_for_input(est_price))

                        e_description = st.text_area("Description", value=description)

                        f1, f2, f3 = st.columns([1, 1, 2])
                        e_actual_raw = f1.text_input("Actual Cost", value=_format_currency_for_input(actual_cost))
                        e_vet_discount = f2.checkbox("Veteran Discount", value=veteran_discount)
                        e_vendor = f3.text_input("Vendor", value=vendor)

                        e_notes = st.text_area("Notes", value=notes)

                        save_col, complete_col, delete_col, cancel_col = st.columns([2, 1, 1, 1])
                        save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                        complete_clicked = complete_col.form_submit_button("✅ Complete", width="stretch")
                        delete_clicked = delete_col.form_submit_button("🗑️ Delete", width="stretch")
                        cancel_clicked = cancel_col.form_submit_button("❌ Cancel", width="stretch")

                    if save_clicked:
                        parsed_est = _parse_currency_input(e_est_raw)
                        parsed_actual = _parse_currency_input(e_actual_raw)

                        if not e_item.strip():
                            st.warning("Item is required.")
                        elif "invalid" in [parsed_est, parsed_actual]:
                            st.warning("Estimated Price and Actual Cost must be valid numbers.")
                        else:
                            update_payload = {
                                "item": e_item.strip(),
                                "description": _clean_text(e_description) or None,
                                "estimated_price": float(parsed_est) if parsed_est is not None else None,
                                "actual_cost": float(parsed_actual) if parsed_actual is not None else None,
                                "veteran_discount": bool(e_vet_discount),
                                "vendor": _clean_text(e_vendor) or None,
                                "notes": _clean_text(e_notes) or None,
                            }
                            if update_wish_list_item(str(row_id), update_payload):
                                st.session_state["wishlist_pending_owner"] = owner_name
                                st.session_state["editing_wishlist_id"] = None
                                st.success("Wish list item updated.")
                                st.rerun()
                            else:
                                st.error("Could not update this wish list item.")

                    if complete_clicked:
                        if complete_wish_list_item(str(row_id)):
                            st.session_state["editing_wishlist_id"] = None
                            st.success("Wish list item completed.")
                            st.rerun()
                        else:
                            st.error("Could not complete this wish list item.")

                    if delete_clicked:
                        if delete_wish_list_item(str(row_id)):
                            st.session_state["editing_wishlist_id"] = None
                            st.success("Wish list item deleted.")
                            st.rerun()
                        else:
                            st.error("Could not delete this wish list item.")

                    if cancel_clicked:
                        st.session_state["editing_wishlist_id"] = None
                        st.rerun()

            st.divider()

        if completed_rows:
            with st.expander("✅ Completed Items", expanded=False):
                for row in completed_rows:
                    completed_name = row.get("item") or "Unnamed Item"
                    completed_actual = row.get("actual_cost")
                    completed_editable = can_edit_wish_row(row)
                    col_text, col_action = st.columns([6, 1])
                    col_text.caption(f"{completed_name}")
                    if completed_actual is not None:
                        col_text.caption(f"Actual: {_format_money(completed_actual)}")
                    if completed_editable:
                        if col_action.button("↩️ Restore", key=f"restore_wishlist_{row.get('id')}", width="stretch"):
                            if restore_wish_list_item(str(row.get("id"))):
                                st.success("Wish list item restored.")
                                st.rerun()
                            else:
                                st.error("Could not restore this wish list item.")

        return

    raw_data = get_project_budgets()
    rows = raw_data if raw_data else []

    app_tz_name = st.session_state.get("user_timezone", FALLBACK_TIMEZONE)
    try:
        app_tz = ZoneInfo(str(app_tz_name))
    except Exception:
        app_tz = ZoneInfo(FALLBACK_TIMEZONE)

    current_year = pd.Timestamp.now(tz=app_tz).year

    normalized = []
    for row in rows:
        est_low = _to_number(row.get("est_low_cost"), 0)
        est_high = _to_number(row.get("est_high_cost"), 0)
        actual = _to_number(row.get("actual_cost"), 0)
        priority = _to_int(row.get("priority"), 99)
        completed = _is_completed_project(row)
        project_year = _extract_project_year(row, current_year)
        normalized.append(
            {
                **row,
                "_est_low": est_low,
                "_est_high": est_high,
                "_actual": actual,
                "_priority": priority,
                "_completed": completed,
                "_year": project_year,
            }
        )

    active_projects = [r for r in normalized if not r.get("_completed", False)]
    completed_projects = [r for r in normalized if r.get("_completed", False)]

    finance_settings = get_household_finance_settings()
    saved_projects_funds = finance_settings.get("projects_funds")
    saved_projects_funds_year = finance_settings.get("projects_funds_year")
    saved_projects_funds_updated_at = finance_settings.get("updated_at")

    if saved_projects_funds is not None and saved_projects_funds_year is None:
        update_household_projects_funds(saved_projects_funds, current_year)
        saved_projects_funds_year = current_year

    if saved_projects_funds_year not in [None, current_year]:
        update_household_projects_funds(None, current_year)
        saved_projects_funds = None
        saved_projects_funds_year = current_year
        saved_projects_funds_updated_at = None
        st.session_state["projects_funds_input"] = ""

    if st.session_state.get("projects_funds_input") == "" and saved_projects_funds is not None:
        st.session_state["projects_funds_input"] = _format_currency_for_input(saved_projects_funds)

    # ==========================================
    # 🏦 TOP-LEVEL: HOUSEHOLD BUDGET (Admin)
    # ==========================================
    if view == "household":
        if not can_access_monthly:
            st.warning("🔒 You do not have permission to view the Household Ledger.")
            if st.button("⬅️ Return to Menu"):
                st.session_state["budget_view"] = "menu"
                st.rerun()
            return
            
        st.subheader("🏦 Household Budget")
        household_id = st.session_state.get("household_id")
        
        current_month = datetime.now().strftime("%Y-%m")
        selected_month = st.selectbox("Select Month", [current_month, "2026-05", "2026-04"], index=0)
        
        incomes_df = get_household_incomes(household_id, selected_month)
        expenses_df = get_monthly_expenses(household_id, selected_month, include_private_members=True)
        routing_df = get_cash_flow_routing(household_id)
        
        total_take_home = incomes_df["take_home_amount"].sum() if not incomes_df.empty else 0.0
        taxable_income = incomes_df.loc[incomes_df["is_taxable"] == True, "gross_amount"].sum() if not incomes_df.empty else 0.0
        total_expenses = expenses_df["amount"].sum() if not expenses_df.empty else 0.0
        net_cash_flow = total_take_home - total_expenses
        
        household_view_mode = st.radio(
            "Household View Mode",
            ["📊 Master Ledger", "🔄 Cash Flow & Treasury", "⚙️ Settings & Setup"],
            horizontal=True,
            label_visibility="collapsed"
        )
        
        if household_view_mode == "📊 Master Ledger":
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Take-Home", f"${total_take_home:,.2f}")
            col2.metric("Total Expenses", f"${total_expenses:,.2f}")
            col3.metric("Net Cash Flow", f"${net_cash_flow:,.2f}", delta=f"${net_cash_flow:,.2f}", delta_color="normal")
            col4.metric("Annual Taxable Pace", f"${(taxable_income * 12):,.2f}")
            st.divider()
            
            st.markdown("#### Income Streams")
            if not incomes_df.empty:
                display_inc = incomes_df[["source_name", "take_home_amount", "gross_amount", "is_taxable", "is_windfall", "is_recurring"]].copy()
                display_inc.columns = ["Source", "Take-Home", "Gross", "Taxable?", "Windfall?", "Recurring?"]
                st.dataframe(display_inc, hide_index=True)
            else:
                st.info("No income logged for this month yet.")
        
            st.markdown("#### Household Expenses")
            if not expenses_df.empty:
                display_exp = expenses_df[["date_logged", "amount", "details"]].copy()
                display_exp.columns = ["Date", "Amount", "Details"]
                st.dataframe(display_exp, hide_index=True)
            else:
                st.info("No expenses logged for this month yet.")
                
        elif household_view_mode == "🔄 Cash Flow & Treasury":
            st.caption("Algorithms powered by legacy Excel routing.")
            if not routing_df.empty:
                st.markdown("#### Personal Spend Allocation")
                if not incomes_df.empty:
                    selected_paycheck = st.selectbox("Select Funding Source", incomes_df["source_name"].tolist())
                    paycheck_val = incomes_df.loc[incomes_df["source_name"] == selected_paycheck, "take_home_amount"].values[0]
                    spend_money = calculate_spend_money(paycheck_val, routing_df)
                    st.metric(f"Spend Money per Person (from {selected_paycheck})", f"${spend_money:,.2f}")
                else:
                    st.warning("Please add income to calculate Spend Money.")
                    
                st.divider()
                st.markdown("#### Destination Routing Targets")
                display_route = routing_df[["destination_account", "line_item", "annual_goal", "monthly_target"]].copy()
                display_route.columns = ["Destination Account", "Line Item", "Annual Goal", "Monthly Target"]
                display_route["Target (÷2)"] = display_route["Monthly Target"] / 2
                st.dataframe(display_route, hide_index=True)
            else:
                st.info("No routing targets configured yet.")

        elif household_view_mode == "⚙️ Settings & Setup":
            st.caption("Manage encrypted budget categories and monthly income streams.")
            
            # Fetch active household users for the dropdown
            household_users = get_household_users_for_admin()
            user_options = [_clean_text(u.get("username")) for u in household_users if _clean_text(u.get("username"))]
            if not user_options:
                user_options = ["unassigned"]
            
            set_col1, set_col2 = st.columns(2)
            
            # --- ADD CATEGORY ---
            with set_col1:
                with st.container(border=True):
                    st.markdown("#### 🏷️ Add New Category")
                    with st.form("add_category_form", clear_on_submit=True):
                        new_cat_name = st.text_input("Category Name *", placeholder="e.g., Auto, Home")
                        new_sub_cat = st.text_input("Sub-Category (Optional)", placeholder="e.g., Fuel, Groceries")
                        new_cat_type = st.selectbox("Type", ["Fixed Expense", "Variable Expense", "Project", "Income"])
                        
                        if st.form_submit_button("💾 Save Category", type="primary", width="stretch"):
                            if not new_cat_name.strip():
                                st.error("Category name is required.")
                            else:
                                if insert_budget_category(household_id, new_cat_name, new_cat_type, new_sub_cat):
                                    st.success(f"Added {new_cat_name}!")
                                    st.rerun()

                # --- INITIALIZE DEFAULTS ---
                with st.expander("🚀 Quick Setup: Load Default Categories"):
                    st.caption("Load a standard set of encrypted budget categories to get started quickly.")
                    if st.button("Load System Defaults", type="primary", width="stretch"):
                        if initialize_default_categories(household_id):
                            st.success("Default categories securely generated and encrypted!")
                            st.rerun()
                        else:
                            st.error("Failed to load defaults.")

                # Manage Existing Categories
                with st.expander("🛠️ Manage Categories"):
                    categories_df = get_budget_categories(household_id)
                    if not categories_df.empty:
                        for _, row in categories_df.iterrows():
                            cat_id = row["id"]
                            cat_name = row["category_name"]
                            sub_cat = row.get("sub_category_name")
                            display_name = f"{cat_name} - {sub_cat}" if pd.notnull(sub_cat) else cat_name
                            
                            c1, c2 = st.columns([4, 1])
                            c1.caption(f"{display_name} ({row['category_type']})")
                            if c2.button("❌", key=f"del_cat_{cat_id}"):
                                if delete_budget_category(cat_id):
                                    st.rerun()
                    else:
                        st.caption("No categories found.")
            
            # --- ADD INCOME ---
            with set_col2:
                with st.container(border=True):
                    st.markdown(f"#### 💵 Add Income ({selected_month})")
                    with st.form("add_income_form", clear_on_submit=True):
                        source_name = st.text_input("Source Name", placeholder="e.g., Paycheck, Other Income")
                        
                        # 🟢 Assign Ownership Dropdown
                        owner_username = st.selectbox("Assign to Earner", user_options)
                        
                        inc1, inc2 = st.columns(2)
                        take_home = inc1.text_input("Take-Home (Net) $")
                        gross = inc2.text_input("Gross $")
                        
                        tax1, tax2 = st.columns(2)
                        is_taxable = tax1.checkbox("Is Taxable?", value=True)
                        is_windfall = tax2.checkbox("Ad-hoc/Windfall?", value=False)
                        is_recurring = st.checkbox("🔄 Recurring Income? (Auto-roll to next month)", value=True)
                        
                        if st.form_submit_button("💾 Save Income", type="primary", width="stretch"):
                            th_val = _parse_currency_input(take_home)
                            g_val = _parse_currency_input(gross)
                            if not source_name.strip() or th_val == "invalid" or g_val == "invalid":
                                st.error("Please provide a valid source name and dollar amounts.")
                            else:
                                if insert_household_income(household_id, selected_month, source_name, th_val, g_val, is_taxable, owner_username, is_windfall, is_recurring):
                                    st.success(f"Added {source_name}!")
                                    st.rerun()
                                    
                # Manage Existing Income
                with st.expander(f"🛠️ Manage Income ({selected_month})"):
                    if not incomes_df.empty:
                        for _, row in incomes_df.iterrows():
                            inc_id = row["id"]
                            inc_name = row["source_name"]
                            inc_owner = row.get("owner_username", "unassigned")
                            
                            c1, c2 = st.columns([4, 1])
                            c1.caption(f"{inc_name} ({inc_owner}) - ${_to_number(row['take_home_amount']):,.2f}")
                            if c2.button("❌", key=f"del_inc_{inc_id}"):
                                if delete_household_income(inc_id):
                                    st.rerun()
                    else:
                        st.caption("No income found for this month.")
                                    
        return # Hard stop for Household

    # ==========================================
    # 👤 TOP-LEVEL: PERSONAL BUDGET (All Users)
    # ==========================================
    if view == "personal":
        st.subheader("👤 My Personal Budget")
        st.caption("Your private financial sandbox. Unrelated to master household bills.")
        
        household_id = st.session_state.get("household_id")
        username = st.session_state.get("username")
        auth_user_id = st.session_state.get("auth_user_id")
        
        current_month = datetime.now().strftime("%Y-%m")
        selected_month = st.selectbox("Select Month", [current_month, "2026-05", "2026-04"], index=0, key="personal_month")
        
        settings = get_user_finance_settings(household_id, username)
        incomes_df = get_household_incomes(household_id, selected_month)
        routing_df = get_cash_flow_routing(household_id)
        indiv_expenses_df = get_individual_expenses(household_id, auth_user_id, selected_month)
        
        # The Privacy Toggle
        current_share_status = settings.get("share_budget_with_admin", True)
        is_private = st.toggle(
            "🔒 Keep my personal ledger private (Hide from Household Rollup)", 
            value=not current_share_status
        )
        if (not is_private) != current_share_status:
            if update_user_privacy_toggle(household_id, username, not is_private):
                st.rerun()
                
        st.divider()
        
        if not incomes_df.empty and not routing_df.empty:
            selected_paycheck = st.selectbox("Select Funding Source", incomes_df["source_name"].tolist(), key="personal_fund_source")
            paycheck_val = incomes_df.loc[incomes_df["source_name"] == selected_paycheck, "take_home_amount"].values[0]
            
            total_spend_money = calculate_spend_money(paycheck_val, routing_df)
            indiv_spent = indiv_expenses_df["amount"].sum() if not indiv_expenses_df.empty else 0.0
            remaining = total_spend_money - indiv_spent
            
            p_col1, p_col2, p_col3 = st.columns(3)
            p_col1.metric(f"Total Allowance (from {selected_paycheck})", f"${total_spend_money:,.2f}")
            p_col2.metric("Personal Spend", f"${indiv_spent:,.2f}")
            p_col3.metric("Remaining Balance", f"${remaining:,.2f}", delta=f"${remaining:,.2f}", delta_color="normal" if remaining >= 0 else "inverse")
        else:
            st.info("Waiting on household admin to add income and routing targets to calculate your allowance.")
            
        st.divider()
        st.markdown("#### My Personal Ledger")
        if not indiv_expenses_df.empty:
            display_indiv = indiv_expenses_df[["date_logged", "amount", "details"]].copy()
            display_indiv.columns = ["Date", "Amount", "Details"]
            st.dataframe(display_indiv, hide_index=True)
        else:
            st.info("You haven't logged any personal expenses this month.")
            
        return # Hard stop for Personal

    # ==========================================
    # 💳 TOP-LEVEL: EXPENSE TRACKER
    # ==========================================
    if view == "expense_tracker":
        st.subheader("💳 Log an Expense")
        
        household_id = st.session_state.get("household_id")
        auth_user_id = st.session_state.get("auth_user_id")
        
        categories_df = get_budget_categories(household_id)
        if categories_df.empty:
            st.warning("No active categories found. Please ask an Admin to add categories in the Household Setup.")
            return
            
        # Optional: Format string to show Sub-Category if it exists
        categories_df["display_name"] = categories_df.apply(
            lambda row: f"{row['category_name']} - {row['sub_category_name']}" if pd.notnull(row.get('sub_category_name')) else row['category_name'], 
            axis=1
        )
        
        cat_options = categories_df["display_name"].tolist()
        selected_display_name = st.selectbox("Category", cat_options)
        
        cat_row = categories_df[categories_df["display_name"] == selected_display_name].iloc[0]
        category_id = cat_row["id"]
        category_type = cat_row["category_type"]
        
        selected_project_id = None
        
        if category_type == "Project":
            active_projects = [p for p in raw_data if not _is_completed_project(p)] 
            if not active_projects:
                st.warning("No active projects found.")
            else:
                proj_options = {p["id"]: p.get("item", "Unnamed") for p in active_projects}
                selected_project_id = st.selectbox("Assign to Active Project", options=list(proj_options.keys()), format_func=lambda x: proj_options[x])
                
        with st.form("expense_entry_form", clear_on_submit=True):
            a1, a2 = st.columns([1, 1])
            date_logged = a1.date_input("Date of Purchase")
            amount_raw = a2.text_input("Amount ($) *", placeholder="e.g., 45.00")
            details = st.text_input("Details & Vendor *")
            
            # 🟢 RECURRING BILL CHECKBOX (If it's a fixed expense)
            is_recurring_expense = False
            if category_type == "Fixed Expense":
                is_recurring_expense = st.checkbox("🔄 Recurring Bill? (Auto-logs this expense next month)")
                
            if st.form_submit_button("💾 Save Expense", type="primary", width="stretch"):
                parsed_amount = _parse_currency_input(amount_raw)
                if "invalid" == parsed_amount or parsed_amount is None:
                    st.error("Please enter a valid dollar amount.")
                elif not details.strip():
                    st.error("Please provide details/vendor.")
                else:
                    month_year_tag = date_logged.strftime("%Y-%m")
                    # We will update the log_expense_and_check_project function to handle the recurring flag next!
                    success = log_expense_and_check_project(
                        auth_user_id=auth_user_id, household_id=household_id,
                        month_year=month_year_tag, date_logged=date_logged,
                        category_id=category_id, category_type=category_type,
                        amount=parsed_amount, details=details, project_id=selected_project_id
                    )
                    if success:
                        st.success(f"Successfully logged ${_format_money(parsed_amount)} to {selected_display_name}.")
                    else:
                        st.error("Failed to log the expense.")
                        
        return # Hard stop for Expense Tracker

    st.subheader("🛠️ Projects")

    active_projects.sort(key=lambda x: (x.get("_priority", 99), -x.get("_est_high", 0), str(x.get("item", "")).lower()))
    completed_projects.sort(key=lambda x: (str(x.get("item", "")).lower(),))

    active_total_low = sum(r.get("_est_low", 0) for r in active_projects)
    active_total_high = sum(r.get("_est_high", 0) for r in active_projects)
    active_total_actual = sum(r.get("_actual", 0) for r in active_projects)
    completed_total_actual = sum(r.get("_actual", 0) for r in completed_projects)
    total_projects_actual = active_total_actual + completed_total_actual

    available_years = sorted({r.get("_year") for r in normalized if r.get("_year") is not None}, reverse=True)
    if current_year not in available_years:
        available_years.insert(0, current_year)

    if "projects_overview_year" not in st.session_state:
        st.session_state["projects_overview_year"] = current_year
    if st.session_state["projects_overview_year"] not in available_years:
        st.session_state["projects_overview_year"] = current_year
    selected_year = st.session_state["projects_overview_year"]

    yearly_projects = [r for r in normalized if r.get("_year") == selected_year]
    yearly_active_projects = [r for r in yearly_projects if not r.get("_completed", False)]
    yearly_completed_projects = [r for r in yearly_projects if r.get("_completed", False)]
    yearly_active_total_low = sum(r.get("_est_low", 0) for r in yearly_active_projects)
    yearly_active_total_high = sum(r.get("_est_high", 0) for r in yearly_active_projects)
    yearly_active_total_actual = sum(r.get("_actual", 0) for r in yearly_active_projects)
    yearly_completed_total_actual = sum(r.get("_actual", 0) for r in yearly_completed_projects)

    current_year_projects = [r for r in normalized if r.get("_year") == current_year]
    current_year_total_actual = sum(r.get("_actual", 0) for r in current_year_projects)

    archive_years = [y for y in available_years if y != selected_year]

    projects_section_keys = ["overview", "workspace", "completed"]

    if st.session_state.get("projects_active_section") not in projects_section_keys:
        st.session_state["projects_active_section"] = "overview"

    def projects_section_label(section_key):
        if section_key == "overview":
            return "📊 Projects Overview"
        if section_key == "workspace":
            return "🧭 Projects Workspace"
        return "✅ Completed Projects"

    selected_projects_section = st.radio(
        "Projects Section",
        options=projects_section_keys,
        key="projects_active_section",
        horizontal=True,
        label_visibility="collapsed",
        format_func=projects_section_label,
    )

    if selected_projects_section == "overview":
        with st.expander("🎛️ Overview Filters", expanded=False):
            selected_year = st.selectbox(
                "Overview Year",
                options=available_years,
                index=available_years.index(st.session_state.get("projects_overview_year", current_year)),
                key="projects_overview_year",
                width="stretch",
            )

            yearly_projects = [r for r in normalized if r.get("_year") == selected_year]
            category_options = sorted({r.get("category") or "Uncategorized" for r in yearly_projects})
            if "projects_overview_categories" not in st.session_state:
                st.session_state["projects_overview_categories"] = category_options
            else:
                current_categories = [c for c in st.session_state.get("projects_overview_categories", []) if c in category_options]
                st.session_state["projects_overview_categories"] = current_categories or category_options

            st.caption("Overview Categories")
            select_all_col, clear_all_col = st.columns(2)
            if select_all_col.button("Select All", key="overview_categories_select_all", width="stretch"):
                st.session_state["projects_overview_categories"] = list(category_options)
                for category_name in category_options:
                    st.session_state[f"overview_cat_{_make_key_fragment(category_name)}"] = True
                st.rerun()

            if clear_all_col.button("Clear All", key="overview_categories_clear_all", width="stretch"):
                st.session_state["projects_overview_categories"] = []
                for category_name in category_options:
                    st.session_state[f"overview_cat_{_make_key_fragment(category_name)}"] = False
                st.rerun()

            category_rows = []
            for idx, category_name in enumerate(category_options):
                key_name = f"overview_cat_{_make_key_fragment(category_name)}"
                if key_name not in st.session_state:
                    st.session_state[key_name] = category_name in st.session_state["projects_overview_categories"]
                category_rows.append((idx, category_name, key_name))

            category_columns = st.columns(2)
            for idx, category_name, key_name in category_rows:
                column = category_columns[idx % len(category_columns)]
                with column:
                    st.checkbox(category_name, key=key_name)

            selected_categories = [
                category_name
                for _, category_name, key_name in category_rows
                if st.session_state.get(key_name, False)
            ]
            st.session_state["projects_overview_categories"] = selected_categories or category_options

        yearly_projects = [r for r in normalized if r.get("_year") == selected_year]
        selected_categories = st.session_state.get("projects_overview_categories", category_options)

        yearly_projects = [r for r in yearly_projects if (r.get("category") or "Uncategorized") in selected_categories]
        yearly_active_projects = [r for r in yearly_projects if not r.get("_completed", False)]
        yearly_completed_projects = [r for r in yearly_projects if r.get("_completed", False)]
        yearly_active_total_low = sum(r.get("_est_low", 0) for r in yearly_active_projects)
        yearly_active_total_high = sum(r.get("_est_high", 0) for r in yearly_active_projects)
        yearly_active_total_actual = sum(r.get("_actual", 0) for r in yearly_active_projects)
        yearly_completed_total_actual = sum(r.get("_actual", 0) for r in yearly_completed_projects)
        archive_years = [y for y in available_years if y != selected_year]

        st.caption(f"Dashboard is scoped to calendar year {selected_year} ({app_tz.key}). Other years are available in archive below.")

        if yearly_active_projects:
            overview_df = pd.DataFrame(yearly_active_projects)
            category_df = overview_df.groupby("category", as_index=False)["_est_high"].sum()

            chart_col1, chart_col2 = st.columns(2)

            with chart_col1:
                if not category_df.empty and category_df["_est_high"].sum() > 0:
                    fig_donut = px.pie(
                        category_df,
                        values="_est_high",
                        names="category",
                        hole=0.45,
                        title="Active Est. High by Category",
                    )
                    fig_donut.update_traces(textposition="inside", textinfo="percent+label")
                    _plotly_chart_locked(fig_donut)
                else:
                    st.info("Add estimated high values to render category distribution.")

            with chart_col2:
                tree_df = overview_df[overview_df["_est_high"] > 0]
                if not tree_df.empty:
                    fig_tree = px.treemap(
                        tree_df,
                        path=["category", "item"],
                        values="_est_high",
                        title="Active Project Cost Hierarchy",
                        color="category",
                    )
                    _plotly_chart_locked(fig_tree)
                else:
                    st.info("Treemap appears once active projects have estimated high values.")

            category_costs = overview_df.groupby("category", as_index=False)[["_est_low", "_est_high", "_actual"]].sum()
            if not category_costs.empty:
                fig_bar = go.Figure(
                    data=[
                        go.Bar(name="Est. Low", x=category_costs["category"], y=category_costs["_est_low"], marker_color="#22C55E"),
                        go.Bar(name="Est. High", x=category_costs["category"], y=category_costs["_est_high"], marker_color="#DC2626"),
                        go.Bar(name="Actual", x=category_costs["category"], y=category_costs["_actual"], marker_color="#0EA5E9"),
                    ]
                )
                fig_bar.update_layout(title=f"Active Projects ({selected_year}): Estimates vs Actual", barmode="group")
                _plotly_chart_locked(fig_bar)
        else:
            st.info(f"No active projects found for {selected_year}.")

        status_counts_df = pd.DataFrame(
            [
                {"Status": "Active", "Count": len(yearly_active_projects)},
                {"Status": "Completed", "Count": len(yearly_completed_projects)},
            ]
        )
        if status_counts_df["Count"].sum() > 0:
            fig_status_counts = px.bar(
                status_counts_df,
                x="Status",
                y="Count",
                color="Status",
                text="Count",
                title=f"Project Status Count ({selected_year})",
                color_discrete_map={"Active": "#0EA5E9", "Completed": "#16A34A"},
            )
            fig_status_counts.update_traces(textposition="outside")
            fig_status_counts.update_layout(xaxis_title="", yaxis_title="Projects", showlegend=False)
            _plotly_chart_locked(fig_status_counts, width="stretch")
        else:
            st.info(f"No projects found for {selected_year}.")

        # Over-budget rollups for the selected year
        active_over_budget_rows = []
        for r in yearly_active_projects:
            over_amt = max(r.get("_actual", 0) - r.get("_est_high", 0), 0)
            if over_amt > 0:
                active_over_budget_rows.append({"Project": r.get("item") or "Unnamed", "Over Budget": over_amt})

        completed_over_budget_rows = []
        for r in yearly_completed_projects:
            over_amt = max(r.get("_actual", 0) - r.get("_est_high", 0), 0)
            if over_amt > 0:
                completed_over_budget_rows.append({"Project": r.get("item") or "Unnamed", "Over Budget": over_amt})

        over_col1, over_col2 = st.columns(2)
        with over_col1:
            st.markdown(f"#### Active Over Budget ({selected_year})")
            if active_over_budget_rows:
                active_over_df = pd.DataFrame(active_over_budget_rows).sort_values(by="Over Budget", ascending=False)
                fig_active_over = px.bar(
                    active_over_df,
                    x="Project",
                    y="Over Budget",
                    color_discrete_sequence=["#DC2626"],
                    title="Active Projects Over Budget",
                )
                fig_active_over.update_layout(xaxis_title="", yaxis_title="Over Budget ($)")
                _plotly_chart_locked(fig_active_over, width="stretch")
            else:
                st.info("No active projects are over budget for this year.")

        with over_col2:
            st.markdown(f"#### Completed Over Budget ({selected_year})")
            if completed_over_budget_rows:
                completed_over_df = pd.DataFrame(completed_over_budget_rows).sort_values(by="Over Budget", ascending=False)
                fig_completed_over = px.bar(
                    completed_over_df,
                    x="Project",
                    y="Over Budget",
                    color_discrete_sequence=["#B91C1C"],
                    title="Completed Projects Over Budget",
                )
                fig_completed_over.update_layout(xaxis_title="", yaxis_title="Over Budget ($)")
                _plotly_chart_locked(fig_completed_over, width="stretch")
            else:
                st.info("No completed projects are over budget for this year.")

        st.divider()
        o1, o2, o3 = st.columns(3)
        o1.metric(f"{selected_year} Active Est. Low", f"${yearly_active_total_low:,.2f}")
        o2.metric(f"{selected_year} Active Est. High", f"${yearly_active_total_high:,.2f}")
        o3.metric(f"{selected_year} Active Actual Spent", f"${yearly_active_total_actual:,.2f}")
        st.metric(f"{selected_year} Completed Projects (Final Dollars)", f"${yearly_completed_total_actual:,.2f}")

        with st.expander("🗂️ Previous Years Archive", expanded=False):
            st.caption("Review historical calendar-year totals.")

            if not archive_years:
                st.info("No archived years found yet.")
            else:
                for year_value in archive_years:
                    year_rows = [r for r in normalized if r.get("_year") == year_value]
                    year_project_count = len(year_rows)
                    year_spent = sum(r.get("_actual", 0) for r in year_rows)

                    y1, y2 = st.columns([2, 2])
                    y1.metric(f"{year_value} Projects", f"{year_project_count}")
                    y2.metric(f"{year_value} Spent", f"${year_spent:,.2f}")

    elif selected_projects_section == "workspace":
        st.caption("Open this section to add and manage project records.")

        if not can_edit_projects:
            st.info("You have view-only access to Projects. Editing is restricted by your household admin.")

        with st.expander("💼 Project Funds", expanded=False):
            funds_input = st.text_input(
                "Projects Funds",
                key="projects_funds_input",
                placeholder="Enter available project funds",
                disabled=not can_edit_projects,
            )
            parsed_funds = _parse_currency_input(funds_input)
            if parsed_funds == "invalid":
                st.warning("Projects Funds must be a valid dollar amount.")
                active_funds_value = _to_number(saved_projects_funds, 0)
            else:
                active_funds_value = _to_number(parsed_funds if parsed_funds is not None else saved_projects_funds, 0)

            if st.button("💾 Save Funds", key="save_projects_funds", width="stretch", disabled=not can_edit_projects):
                if parsed_funds == "invalid":
                    st.error("Cannot save: Projects Funds must be a valid dollar amount.")
                else:
                    value_to_save = parsed_funds if parsed_funds is not None else None
                    if update_household_projects_funds(value_to_save, current_year):
                        st.success("Projects Funds saved.")
                        st.rerun()
                    else:
                        st.error("Could not save Projects Funds.")

            st.caption(f"Projects Funds is annual and automatically resets on Jan 1. Remaining Funds is based on {current_year} actual project spend.")

        remaining_funds = float(active_funds_value or 0.0) - float(current_year_total_actual)

        if saved_projects_funds is not None:
            history_line = f"Last saved funds ({current_year}): {_format_money(saved_projects_funds)}"
            if saved_projects_funds_updated_at:
                try:
                    updated_local = pd.to_datetime(saved_projects_funds_updated_at, utc=True).tz_convert(app_tz)
                    history_line = f"{history_line} on {updated_local.strftime('%b %d, %Y %I:%M %p %Z')}"
                except Exception:
                    pass
            st.caption(history_line)

        st.divider()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Est. Total Low", f"${active_total_low:,.2f}")
        k2.metric("Est. Total High", f"${active_total_high:,.2f}")
        k3.metric(f"{current_year} Actual Spent", f"${current_year_total_actual:,.2f}")
        remaining_color = "#16A34A" if remaining_funds >= 0 else "#DC2626"
        k4.markdown("**Remaining Funds**")
        k4.markdown(
            f"<div style='font-size:1.5rem; font-weight:700; color:{remaining_color};'>{_format_money(remaining_funds)}</div>",
            unsafe_allow_html=True,
        )

        st.divider()

        if not active_projects:
            st.info("No active projects found.")

        st.markdown("### Projects")

        with st.expander("➕ Add New Project", expanded=False):
            if not can_edit_projects:
                st.caption("Edit permission required to add new projects.")
            with st.form("add_project_budget_item", clear_on_submit=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                new_item = c1.text_input("Project Name *", placeholder="e.g., Front Deck Repair", disabled=not can_edit_projects)
                new_category = c2.selectbox("Category", PROJECT_CATEGORIES, index=2, disabled=not can_edit_projects)
                new_priority = c3.number_input("Priority", min_value=1, step=1, value=3, disabled=not can_edit_projects)

                new_description = st.text_area("Description", placeholder="Scope, intent, or major deliverables", disabled=not can_edit_projects)

                b1, b2, b3 = st.columns(3)
                new_est_low_raw = b1.text_input("Est. Low", value="", placeholder="Enter amount", disabled=not can_edit_projects)
                new_est_high_raw = b2.text_input("Est. High", value="", placeholder="Enter amount", disabled=not can_edit_projects)
                new_actual_raw = b3.text_input("Actual Spent", value="", placeholder="Enter amount", disabled=not can_edit_projects)

                n1, n2 = st.columns(2)
                new_vendors = n1.text_input("Vendors", placeholder="Contractor names, stores, links", disabled=not can_edit_projects)
                new_vet_discount = n2.checkbox("Veteran Discount", value=False, disabled=not can_edit_projects)
                new_notes = st.text_area("Notes", placeholder="Execution notes, blockers, follow-ups", disabled=not can_edit_projects)

                save_col, complete_col = st.columns(2)
                save_clicked = save_col.form_submit_button("Save Project", type="primary", width="stretch", disabled=not can_edit_projects)
                complete_clicked = complete_col.form_submit_button("✅ Complete Project", width="stretch", disabled=not can_edit_projects)

                if save_clicked or complete_clicked:
                    parsed_low = _parse_currency_input(new_est_low_raw)
                    parsed_high = _parse_currency_input(new_est_high_raw)
                    parsed_actual = _parse_currency_input(new_actual_raw)

                    if not new_item.strip():
                        st.warning("Project Name is required.")
                    elif "invalid" in [parsed_low, parsed_high, parsed_actual]:
                        st.warning("Est. Low, Est. High, and Actual Spent must be valid numbers.")
                    else:
                        payload = {
                            "item": new_item.strip(),
                            "category": new_category,
                            "priority": int(new_priority),
                            "description": _clean_text(new_description) or None,
                            "est_low_cost": float(parsed_low) if parsed_low is not None else 0.0,
                            "est_high_cost": float(parsed_high) if parsed_high is not None else 0.0,
                            "actual_cost": float(parsed_actual) if parsed_actual is not None else 0.0,
                            "veteran_discount": bool(new_vet_discount),
                            "vendors": _clean_text(new_vendors) or None,
                            "notes": _mark_completed_notes(new_notes) if complete_clicked else (_clean_text(new_notes) or None),
                        }
                        if insert_project_budget_item(payload):
                            st.success("Project completed and archived." if complete_clicked else "Project added.")
                            st.rerun()
                        else:
                            st.error("Could not save project.")

        priority_projects = [p for p in active_projects if p.get("_priority", 99) == 1]
        non_priority_projects = [p for p in active_projects if p.get("_priority", 99) != 1]

        # Group non-priority active projects by category.
        grouped_active = {}
        for item in non_priority_projects:
            cat_key = item.get("category") or "Uncategorized"
            grouped_active.setdefault(cat_key, []).append(item)

        sorted_categories = sorted(grouped_active.keys())
        if priority_projects or sorted_categories:
            def render_project_item(item):
                project_id = item.get("id")
                title = item.get("item") or "Unnamed Project"
                category = item.get("category") or "Uncategorized"
                priority = item.get("_priority", 99)
                description = _clean_text(item.get("description"))
                vendors = _clean_text(item.get("vendors"))
                notes = _clean_text(item.get("notes"))
                est_low = item.get("_est_low", 0)
                est_high = item.get("_est_high", 0)
                actual = item.get("_actual", 0)
                budget_cap = est_high if est_high > 0 else est_low
                remaining_balance = budget_cap - actual if budget_cap > 0 else None
                has_vet_discount = bool(item.get("veteran_discount", False))

                budget_status = "On Track"
                if est_high > 0 and actual > est_high:
                    budget_status = "Over Budget"
                elif est_high > 0 and actual >= (est_high * 0.85):
                    budget_status = "At Risk"

                with st.container(border=True):
                    left_col, right_col = st.columns([6, 1])
                    left_col.markdown(f"**{title}**")
                    left_col.caption(f"Priority: {priority} | Category: {category} | Status: {budget_status}")
                    left_col.markdown(
                        "Estimated: "
                        f"<span style='color:#16A34A; font-weight:600;'>&#36;{est_low:,.2f}</span> - "
                        f"<span style='color:#DC2626; font-weight:600;'>&#36;{est_high:,.2f}</span> | "
                        f"Actual: {_format_money(actual)}",
                        unsafe_allow_html=True,
                    )
                    if remaining_balance is not None:
                        remaining_color = "#16A34A" if remaining_balance >= 0 else "#DC2626"
                        left_col.markdown(
                            f"**Remaining Budget:** <span style='color:{remaining_color}; font-weight:700;'>{_format_money(remaining_balance)}</span>",
                            unsafe_allow_html=True,
                        )

                    if est_high > 0:
                        left_col.progress(min(actual / est_high, 1.0))

                    if description:
                        left_col.markdown(f"**Description:** {description}")
                    if vendors:
                        left_col.markdown(f"**Vendors:** {vendors}")
                    if notes and notes != COMPLETED_TAG:
                        left_col.markdown(f"**Notes:** {notes.replace(COMPLETED_TAG, '').strip()}")
                    if has_vet_discount:
                        left_col.caption("Eligible for veteran discount.")

                    if can_edit_projects:
                        right_col.button(
                            "✏️ Edit",
                            key=f"edit_project_budget_{project_id}",
                            width="stretch",
                            on_click=_toggle_project_edit,
                            args=(project_id,),
                        )

                if can_edit_projects and st.session_state.get("editing_project_budget_id") == project_id:
                    with st.container(border=True):
                        st.markdown("### ✏️ Edit Project")
                        with st.form(f"edit_project_budget_form_{project_id}"):
                            e1, e2, e3 = st.columns([2, 1, 1])
                            e_item = e1.text_input("Project Name *", value=title)
                            safe_category = category if category in PROJECT_CATEGORIES else "Home Improvement"
                            e_category = e2.selectbox(
                                "Category",
                                PROJECT_CATEGORIES,
                                index=PROJECT_CATEGORIES.index(safe_category),
                                key=f"edit_cat_{project_id}",
                            )
                            e_priority = e3.number_input("Priority", min_value=1, step=1, value=max(priority, 1))

                            e_description = st.text_area("Description", value=description)

                            eb1, eb2, eb3 = st.columns(3)
                            e_est_low_raw = eb1.text_input(
                                "Est. Low ($)",
                                value=_format_currency_for_input(est_low),
                                placeholder="Enter amount",
                                key=f"edit_est_low_{project_id}",
                            )
                            e_est_high_raw = eb2.text_input(
                                "Est. High ($)",
                                value=_format_currency_for_input(est_high),
                                placeholder="Enter amount",
                                key=f"edit_est_high_{project_id}",
                            )
                            e_actual_raw = eb3.text_input(
                                "Actual Spent ($)",
                                value=_format_currency_for_input(actual),
                                placeholder="Enter amount",
                                key=f"edit_actual_{project_id}",
                            )

                            if budget_cap > 0:
                                remaining_preview = budget_cap - _to_number(e_actual_raw if _clean_text(e_actual_raw) else actual, 0)
                                preview_color = "#16A34A" if remaining_preview >= 0 else "#DC2626"
                                st.markdown(
                                    f"**Remaining Budget:** <span style='color:{preview_color}; font-weight:700;'>{_format_money(remaining_preview)}</span>",
                                    unsafe_allow_html=True,
                                )

                            en1, en2 = st.columns(2)
                            e_vendors = en1.text_input("Vendors", value=vendors)
                            e_vet_discount = en2.checkbox("Veteran Discount", value=has_vet_discount)
                            cleaned_edit_notes = notes.replace(COMPLETED_TAG, "").strip()
                            e_notes = st.text_area("Notes", value=cleaned_edit_notes)

                            save_col, complete_col, cancel_col = st.columns([2, 2, 1])
                            save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                            complete_clicked = complete_col.form_submit_button("✅ Complete Project", width="stretch")
                            cancel_clicked = cancel_col.form_submit_button("❌ Cancel", width="stretch")

                        if save_clicked or complete_clicked:
                            parsed_low = _parse_currency_input(e_est_low_raw)
                            parsed_high = _parse_currency_input(e_est_high_raw)
                            parsed_actual = _parse_currency_input(e_actual_raw)

                            if not e_item.strip():
                                st.warning("Project Name is required.")
                            elif "invalid" in [parsed_low, parsed_high, parsed_actual]:
                                st.warning("Est. Low, Est. High, and Actual Spent must be valid numbers.")
                            else:
                                update_payload = {
                                    "item": e_item.strip(),
                                    "category": e_category,
                                    "priority": int(e_priority),
                                    "description": _clean_text(e_description) or None,
                                    "est_low_cost": float(parsed_low) if parsed_low is not None else float(est_low),
                                    "est_high_cost": float(parsed_high) if parsed_high is not None else float(est_high),
                                    "actual_cost": float(parsed_actual) if parsed_actual is not None else float(actual),
                                    "veteran_discount": bool(e_vet_discount),
                                    "vendors": _clean_text(e_vendors) or None,
                                    "notes": _mark_completed_notes(e_notes) if complete_clicked else (_clean_text(e_notes) or None),
                                }
                                if update_project_budget_item(project_id, update_payload):
                                    st.session_state["editing_project_budget_id"] = None
                                    st.success("Project completed and moved out of active totals." if complete_clicked else "Project updated.")
                                    st.rerun()
                                else:
                                    st.error("Could not update project.")

                        if cancel_clicked:
                            st.session_state["editing_project_budget_id"] = None
                            st.rerun()

            def render_tab_totals(project_rows):
                tab_est_low = sum(p.get("_est_low", 0) for p in project_rows)
                tab_est_high = sum(p.get("_est_high", 0) for p in project_rows)
                tab_spent = sum(p.get("_actual", 0) for p in project_rows)
                t1, t2, t3 = st.columns(3)
                t1.metric("Est Low", _format_money(tab_est_low))
                t2.metric("Estimated High", _format_money(tab_est_high))
                t3.metric("Spent", _format_money(tab_spent))
                st.divider()

            priority_projects.sort(key=lambda x: (str(x.get("category") or "Uncategorized").lower(), -x.get("_est_high", 0), str(x.get("item", "")).lower()))

            priority_grouped = {}
            for p_item in priority_projects:
                p_cat = p_item.get("category") or "Uncategorized"
                priority_grouped.setdefault(p_cat, []).append(p_item)

            workspace_section_keys = ["priority"] + [f"category::{cat_name}" for cat_name in sorted_categories]
            if st.session_state.get("projects_workspace_active_category") not in workspace_section_keys:
                st.session_state["projects_workspace_active_category"] = workspace_section_keys[0]

            def workspace_section_label(section_key):
                if section_key == "priority":
                    return f"🔴 Priority ({len(priority_projects)})"
                category_name = section_key.split("::", 1)[1]
                return f"📁 {category_name} ({len(grouped_active.get(category_name, []))})"

            selected_workspace_section = st.radio(
                "Project Workspace Section",
                options=workspace_section_keys,
                key="projects_workspace_active_category",
                horizontal=True,
                label_visibility="collapsed",
                format_func=workspace_section_label,
            )

            if selected_workspace_section == "priority":
                render_tab_totals(priority_projects)
                if not priority_projects:
                    st.caption("No priority 1 projects right now.")
                else:
                    for p_cat in sorted(priority_grouped.keys()):
                        st.markdown(f"#### {p_cat}")
                        cat_items = priority_grouped.get(p_cat, [])
                        cat_items.sort(key=lambda x: (-x.get("_est_high", 0), str(x.get("item", "")).lower()))
                        for item in cat_items:
                            render_project_item(item)
            else:
                cat_name = selected_workspace_section.split("::", 1)[1]
                cat_projects = grouped_active.get(cat_name, [])
                cat_projects.sort(key=lambda x: (x.get("_priority", 99), -x.get("_est_high", 0), str(x.get("item", "")).lower()))
                render_tab_totals(cat_projects)
                for item in cat_projects:
                    render_project_item(item)

    else:
        st.caption("Completed items are excluded from active totals. Restore any project back to Active.")

        if not completed_projects:
            st.info("No completed projects yet.")
        else:
            for item in completed_projects:
                project_id = item.get("id")
                title = item.get("item") or "Unnamed Project"
                category = item.get("category") or "Uncategorized"
                actual = item.get("_actual", 0)
                notes = _clean_text(item.get("notes"))
                restored_notes = _restore_active_notes(notes)
                completed_date_label = "Unknown"
                completed_raw = item.get("updated_at") or item.get("created_at")
                if completed_raw:
                    try:
                        completed_ts = pd.to_datetime(completed_raw, utc=True).tz_convert(app_tz)
                        completed_date_label = completed_ts.strftime("%b %d, %Y")
                    except Exception:
                        pass

                with st.container(border=True):
                    c_left, c_right = st.columns([6, 1])
                    c_left.markdown(f"**{title}**")
                    c_left.caption(f"Category: {category} | Date Completed: {completed_date_label} | Final Actual: ${actual:,.2f}")
                    if restored_notes:
                        c_left.markdown(f"**Notes:** {restored_notes}")

                    if can_edit_projects and c_right.button("↩️ Restore", key=f"restore_project_{project_id}", width="stretch"):
                        st.session_state["pending_restore_project_id"] = project_id

                    if st.session_state.get("pending_restore_project_id") == project_id:
                        c_left.warning("Confirm restore? This project will be moved back to Active.")
                        confirm_col, cancel_col = c_left.columns(2)

                        if confirm_col.button("✅ Confirm Restore", key=f"confirm_restore_project_{project_id}", width="stretch"):
                            restore_payload = {"notes": restored_notes}
                            restored = update_project_budget_item(project_id, restore_payload)
                            st.session_state["pending_restore_project_id"] = None
                            if restored:
                                st.success("Project restored to Active.")
                                st.rerun()
                            else:
                                st.error("Could not restore this project.")

                        if cancel_col.button("❌ Cancel", key=f"cancel_restore_project_{project_id}", width="stretch"):
                            st.session_state["pending_restore_project_id"] = None
                            st.rerun()