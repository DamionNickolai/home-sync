import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
from database import (
    get_project_budgets,
    update_project_budget_item,
    insert_project_budget_item,
    get_household_finance_settings,
    update_household_projects_funds,
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


def render_budget_module():
    st.title("Financial Hub 💰")
    st.caption("Manage household finances with quick cards and project-level visibility.")

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

    view = st.session_state["budget_view"]
    can_access_projects = _can_access_projects_module()
    can_edit_projects = _can_edit_projects_module()
    can_access_monthly = _can_access_monthly_module()

    if view == "menu":
        st.subheader("Budget Modules")
        st.caption("Pick a budget module to open.")

        card_col1, card_col2 = st.columns(2)

        with card_col1:
            with st.container(border=True):
                st.markdown("### 📅 Monthly Budget")
                if can_access_monthly:
                    st.caption("Track recurring monthly bills and spending goals.")
                    if st.button("Open Monthly Budget", key="budget_card_monthly", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "monthly"
                        st.rerun()
                else:
                    st.caption("Monthly Budget access is controlled by your household admin.")
                    st.button("Monthly Budget Locked", key="budget_card_monthly_locked", type="secondary", width="stretch", disabled=True)

        with card_col2:
            with st.container(border=True):
                st.markdown("### 🛠️ Projects")
                if can_access_projects:
                    st.caption("View active project estimates, spend, and execution notes.")
                    if st.button("Open Projects", key="budget_card_projects", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "projects"
                        st.rerun()
                else:
                    st.caption("Projects access is controlled by your household admin.")
                    st.button("Projects Locked", key="budget_card_projects_locked", type="secondary", width="stretch", disabled=True)

        return

    if st.button("⬅️ Back to Budget Modules", width="content"):
        st.session_state["budget_view"] = "menu"
        st.session_state["editing_project_budget_id"] = None
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

    if view == "monthly":
        st.subheader("📅 Monthly Budget")
        st.info("Monthly Budget planner is queued next. Projects data is still summarized below for context.")

        if active_projects:
            df = pd.DataFrame(active_projects)
            col1, col2, col3 = st.columns(3)
            col1.metric("Project Est. Low", f"${df.get('est_low_cost', pd.Series(dtype='float64')).fillna(0).sum():,.2f}")
            col2.metric("Project Est. High", f"${df.get('est_high_cost', pd.Series(dtype='float64')).fillna(0).sum():,.2f}")
            col3.metric("Project Actual", f"${df.get('actual_cost', pd.Series(dtype='float64')).fillna(0).sum():,.2f}")
        return

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