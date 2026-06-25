import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import calendar
from zoneinfo import ZoneInfo
from datetime import datetime, date

from database import (
    get_project_budgets,
    update_project_budget_item,
    insert_project_budget_item,
    add_project_purchase_expense,
    ensure_project_expense_category,
    ensure_allowance_categories,
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
    get_members_sharing_personal_budget,
    get_budget_categories,
    insert_budget_category,
    insert_household_income,
    update_household_income,
    sum_income_for_month,
    compute_annual_income_totals,
    income_pay_frequency_label,
    INCOME_PAY_FREQUENCY_LABELS,
    normalize_income_pay_frequency,
    school_year_active_month,
    get_individual_expenses,
    update_user_privacy_toggle,
    delete_budget_category,
    delete_household_income,
    delete_expense,
    update_expense,
    update_budget_category,
    auto_rollover_recurring_expenses,
    auto_rollover_recurring_incomes,
    get_recurring_schedule
)
from ui_helpers import rerun_app_with_reason, manage_popover_key, finish_manage_popover
from constants import is_system_project_expense_category, is_system_managed_allowance_category, is_allowance_subcategory


def _maybe_auto_rollover(household_id, selected_month):
    """Run recurring rollover once per session/month to avoid repeat DB scans on every rerun."""
    if not household_id:
        return
    guard_key = f"rollover_checked_{household_id}_{selected_month}"
    if st.session_state.get(guard_key):
        return
    expense_rolled = auto_rollover_recurring_expenses(household_id, selected_month)
    income_rolled = auto_rollover_recurring_incomes(household_id, selected_month)
    st.session_state[guard_key] = True
    if expense_rolled or income_rolled:
        rerun_app_with_reason("recurring_rollover")


def _recurring_due_date_in_month(expense_row, selected_month):
    year, month = map(int, selected_month.split("-"))
    date_str = expense_row.get("date_logged")
    if not date_str:
        return date(year, month, 1)
    logged = datetime.strptime(date_str, "%Y-%m-%d").date()
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, min(logged.day, last_day))


def _expense_counts_toward_actual(expense_row, selected_month, as_of=None):
    if not expense_row.get("is_recurring", False):
        return True
    as_of = as_of or date.today()
    return as_of >= _recurring_due_date_in_month(expense_row, selected_month)


def _filter_expenses_for_actual_totals(expenses_df, selected_month, as_of=None):
    if expenses_df is None or expenses_df.empty:
        return expenses_df
    as_of = as_of or date.today()
    mask = expenses_df.apply(
        lambda row: _expense_counts_toward_actual(row, selected_month, as_of),
        axis=1,
    )
    return expenses_df[mask]


def _income_due_date_in_month(income_row, selected_month):
    year, month = map(int, selected_month.split("-"))
    date_str = income_row.get("payment_date")
    if not date_str:
        month_year = income_row.get("month_year") or selected_month
        date_str = f"{month_year}-01"
    logged = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, min(logged.day, last_day))


def _income_counts_toward_actual(income_row, selected_month, as_of=None):
    freq = normalize_income_pay_frequency(
        income_row.get("pay_frequency")
        or ("monthly" if income_row.get("is_recurring") else "one_time")
    )
    if freq == "one_time":
        return True
    _, month = map(int, selected_month.split("-"))
    if freq == "school_year_monthly" and not school_year_active_month(month):
        return False
    as_of = as_of or date.today()
    return as_of >= _income_due_date_in_month(income_row, selected_month)


def _filter_incomes_for_actual_totals(incomes_df, selected_month, as_of=None):
    if incomes_df is None or incomes_df.empty:
        return incomes_df
    as_of = as_of or date.today()
    mask = incomes_df.apply(
        lambda row: _income_counts_toward_actual(row, selected_month, as_of),
        axis=1,
    )
    return incomes_df[mask]


def _split_recurring_expenses(expenses_df):
    if expenses_df is None or expenses_df.empty:
        empty = expenses_df if expenses_df is not None else pd.DataFrame()
        return empty, empty
    if "is_recurring" not in expenses_df.columns:
        return expenses_df.iloc[0:0], expenses_df
    is_recurring = expenses_df["is_recurring"].fillna(False).astype(bool)
    return expenses_df[is_recurring].copy(), expenses_df[~is_recurring].copy()


def _split_project_household_expenses(expenses_df, categories_df):
    """Split household expenses into shared (non-project) vs system project-category rows."""
    if expenses_df is None or expenses_df.empty:
        empty = expenses_df if expenses_df is not None else pd.DataFrame()
        return empty, empty
    if categories_df is None or categories_df.empty or "category_id" not in expenses_df.columns:
        return expenses_df.copy(), pd.DataFrame()

    project_ids = _system_project_category_ids(categories_df)
    if not project_ids:
        return expenses_df.copy(), pd.DataFrame()

    project_mask = expenses_df["category_id"].isin(project_ids)
    return expenses_df[~project_mask].copy(), expenses_df[project_mask].copy()


def _system_project_category_ids(categories_df):
    if categories_df is None or categories_df.empty:
        return set()
    ids = set()
    for _, row in categories_df.iterrows():
        if is_system_project_expense_category(row.get("category_name"), row.get("sub_category_name")):
            cat_id = row.get("id")
            if cat_id is not None:
                ids.add(cat_id)
    return ids


def _exclude_system_categories(categories_df):
    if categories_df is None or categories_df.empty:
        return categories_df
    mask = ~categories_df.apply(
        lambda row: is_system_project_expense_category(
            row.get("category_name"),
            row.get("sub_category_name"),
        ),
        axis=1,
    )
    return categories_df[mask].copy()


def _exclude_system_category_expenses(expenses_df, categories_df):
    if expenses_df is None or expenses_df.empty:
        return expenses_df
    system_ids = _system_project_category_ids(categories_df)
    if not system_ids or "category_id" not in expenses_df.columns:
        return expenses_df
    return expenses_df[~expenses_df["category_id"].isin(system_ids)].copy()


def _is_budget_admin():
    return st.session_state.get("user_role", "member") in ["admin", "developer"]


def _is_allowance_linked_income_row(row) -> bool:
    val = row.get("source_expense_id")
    if val is None:
        return False
    try:
        return not pd.isna(val)
    except Exception:
        return bool(val)


def _household_submodule_options():
    return ["📊 Master Ledger", "💳 Expenses", "🔄 Cash Flow & Treasury"]


def _personal_submodule_options(username):
    options = [
        f"📊 {username.title()}'s Ledger",
        "💳 Expenses",
        "🔄 Cash Flow & Treasury",
    ]
    if _is_budget_admin():
        options.append("👨‍👩‍👧 Family Member Budgets")
    return options


def _sync_selector_option(key, allowed_options):
    if not allowed_options:
        return
    if st.session_state.get(key) not in allowed_options:
        st.session_state[key] = allowed_options[0]


def _enrich_expenses_with_categories(expenses_df, categories_df):
    if expenses_df is None or expenses_df.empty:
        return expenses_df
    if categories_df is None or categories_df.empty or "category_id" not in expenses_df.columns:
        enriched = expenses_df.copy()
        enriched["category_name"] = "—"
        enriched["sub_category_name"] = "—"
        return enriched

    lookup = categories_df.set_index("id")
    enriched = expenses_df.copy()

    def _cat_name(category_id):
        if category_id not in lookup.index:
            return "—"
        return lookup.loc[category_id].get("category_name") or "—"

    def _sub_name(category_id):
        if category_id not in lookup.index:
            return "—"
        sub = lookup.loc[category_id].get("sub_category_name")
        if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
            return "—"
        return str(sub)

    enriched["category_name"] = enriched["category_id"].map(_cat_name)
    enriched["sub_category_name"] = enriched["category_id"].map(_sub_name)
    return enriched


LEDGER_COL_WIDTHS = [3.5, 0.65, 1.25, 1.25, 1.35]
SINKING_COL_WIDTHS = [2.6, 1.5, 1.5]
INCOME_COL_WIDTHS = [1.7, 1.0, 1.0, 1.0, 1.0, 1.0]
INCOME_COL_WIDTHS_PERSONAL = [2.2, 1.1, 1.1, 1.1, 1.1]
EXPENSE_COL_WIDTHS = [1.0, 1.2, 1.2, 2.1, 0.9]
EXPENSE_ACTION_COL_WIDTH = 0.65
INCOME_FREQUENCY_OPTIONS = list(INCOME_PAY_FREQUENCY_LABELS.keys())


def _grid_cell(column, text, *, align="left", emphasize=False):
    content = f"**{text}**" if emphasize else str(text)
    if align == "right":
        column.markdown(
            f'<div style="text-align:right;font-size:0.875rem;line-height:1.4;">{content}</div>',
            unsafe_allow_html=True,
        )
    else:
        column.caption(content)


def _render_plain_grid_header(labels, widths, *, right_from_index=1):
    cols = st.columns(widths)
    for idx, (col, label) in enumerate(zip(cols, labels)):
        align = "right" if idx >= right_from_index else "left"
        _grid_cell(col, label, align=align, emphasize=False)


def _render_plain_grid_row(values, widths, *, right_from_index=1, emphasize=False):
    cols = st.columns(widths)
    for idx, (col, value) in enumerate(zip(cols, values)):
        align = "right" if idx >= right_from_index else "left"
        _grid_cell(col, value, align=align, emphasize=emphasize)


def _format_ledger_amount(amount) -> str:
    return f"${float(amount):,.2f}"


def _format_ledger_diff(projected, actual) -> str:
    diff = float(projected) - float(actual)
    if diff > 0:
        return f"🟢 +{_format_ledger_amount(diff)}"
    if diff < 0:
        return f"🔴 -{_format_ledger_amount(abs(diff))}"
    return "➖ $0.00"


def _compact_divider() -> None:
    st.markdown(
        '<hr style="margin:0.3rem 0;border:none;border-top:1px solid rgba(128,128,128,0.35);" />',
        unsafe_allow_html=True,
    )


def _format_purchase_count(count) -> str:
    if count is None or int(count) == 0:
        return "—"
    return str(int(count))


def _purchase_counts_by_category(expenses_df, selected_month) -> dict:
    """Number of expense entries per category_id that count toward this month's actuals."""
    if expenses_df is None or expenses_df.empty or "category_id" not in expenses_df.columns:
        return {}
    filtered = _filter_expenses_for_actual_totals(expenses_df, selected_month)
    if filtered.empty:
        return {}
    return filtered.groupby("category_id").size().to_dict()


def _render_ledger_column_header() -> None:
    _render_plain_grid_header(
        ["Category", "Qty", "Projected", "Actual", "Difference"],
        LEDGER_COL_WIDTHS,
    )


def _render_ledger_line(name, projected, actual, *, purchase_count=0, indent=False, emphasize=False) -> None:
    prefix = "↳ " if indent else ""
    _render_plain_grid_row(
        [
            f"{prefix}{name}",
            _format_purchase_count(purchase_count),
            _format_ledger_amount(projected),
            _format_ledger_amount(actual),
            _format_ledger_diff(projected, actual),
        ],
        LEDGER_COL_WIDTHS,
        right_from_index=1,
        emphasize=emphasize,
    )


def _render_household_budget_breakdown(
    merged_df,
    hh_expenses_df,
    recurring_schedule,
    selected_month,
    *,
    filter_key: str,
) -> None:
    if merged_df.empty:
        st.info("No categories setup yet. Add some to build your ledger!")
        return

    year, month = map(int, selected_month.split("-"))
    purchase_counts = _purchase_counts_by_category(hh_expenses_df, selected_month)

    parent_groups = []
    for parent in merged_df["category_name"].unique():
        parent_mask = merged_df["category_name"] == parent
        parent_target = float(merged_df.loc[parent_mask, "target_budget"].sum())
        parent_actual = float(merged_df.loc[parent_mask, "actual_amount"].sum())
        if parent_target == 0 and parent_actual == 0:
            continue

        sub_rows = []
        parent_purchase_count = 0
        for _, row in merged_df[parent_mask].iterrows():
            target = float(row["target_budget"])
            actual = float(row["actual_amount"])
            if target == 0 and actual == 0:
                continue

            cat_id = row["id"]
            sub_purchase_count = int(purchase_counts.get(cat_id, 0))
            parent_purchase_count += sub_purchase_count

            sub_name = row["sub_category_name"]
            if sub_name is None or (isinstance(sub_name, float) and pd.isna(sub_name)) or str(sub_name).strip() == "":
                sub_name = "(General)"
            else:
                sub_name = str(sub_name)

            recurring_items = hh_expenses_df[
                (hh_expenses_df["category_id"] == cat_id) & (hh_expenses_df["is_recurring"] == True)
            ]
            recurring_due = _filter_expenses_for_actual_totals(recurring_items, selected_month)
            if recurring_due.empty and cat_id in recurring_schedule:
                target_day = recurring_schedule[cat_id]
                _, last_day = calendar.monthrange(year, month)
                safe_day = min(target_day, last_day)
                scheduled_date = date(year, month, safe_day).strftime("%B %d")
                sub_name = f"{sub_name} · {scheduled_date}"

            sub_rows.append(
                {
                    "name": sub_name,
                    "projected": target,
                    "actual": actual,
                    "purchase_count": sub_purchase_count,
                }
            )

        parent_groups.append(
            {
                "name": parent,
                "projected": parent_target,
                "actual": parent_actual,
                "purchase_count": parent_purchase_count,
                "subs": sub_rows,
            }
        )

    parent_groups.sort(key=lambda group: group["name"].lower())
    if not parent_groups:
        st.info("No active budget targets or expenses for this month.")
        return

    category_options = ["All categories"] + [group["name"] for group in parent_groups]
    selected_category = st.selectbox("Category", category_options, key=filter_key)
    visible_groups = (
        parent_groups
        if selected_category == "All categories"
        else [group for group in parent_groups if group["name"] == selected_category]
    )
    if not visible_groups:
        st.info("No budget data for the selected category.")
        return

    _render_ledger_column_header()
    _compact_divider()

    for index, group in enumerate(visible_groups):
        _render_ledger_line(
            group["name"],
            group["projected"],
            group["actual"],
            purchase_count=group.get("purchase_count", 0),
            emphasize=False,
        )
        for sub in group["subs"]:
            _render_ledger_line(
                sub["name"],
                sub["projected"],
                sub["actual"],
                purchase_count=sub.get("purchase_count", 0),
                indent=True,
            )

        if index < len(visible_groups) - 1:
            _compact_divider()

    total_projected = sum(float(group["projected"]) for group in visible_groups)
    total_actual = sum(float(group["actual"]) for group in visible_groups)
    total_purchases = sum(int(group.get("purchase_count", 0)) for group in visible_groups)
    _compact_divider()
    _render_ledger_line(
        "Total",
        total_projected,
        total_actual,
        purchase_count=total_purchases,
        emphasize=True,
    )


def _format_payment_date(value) -> str:
    if not value:
        return "—"
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%b %d, %Y")


def _render_signed_currency_metric(column, label: str, amount: float) -> None:
    """Metric-style display with green for positive and red for negative amounts."""
    color = "#21c354" if amount >= 0 else "#ff4b4b"
    column.markdown(f"**{label}**")
    column.markdown(
        f'<p style="color:{color};font-size:1.75rem;font-weight:600;margin:0;line-height:1.2;">'
        f"${amount:,.2f}</p>",
        unsafe_allow_html=True,
    )


def _render_annual_income_metrics(annual_totals: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Annual Take-Home", f"${annual_totals['annual_takehome']:,.2f}")
    c2.metric("Annual Gross", f"${annual_totals['annual_gross']:,.2f}")
    c3.metric("Annual Taxable", f"${annual_totals['annual_taxable']:,.2f}")
    c4.metric("Annual Non-Taxable", f"${annual_totals['annual_non_taxable']:,.2f}")


def _render_income_streams_list(incomes_df, *, is_personal=False, annual_totals=None):
    if incomes_df is None or incomes_df.empty:
        st.info("No income logged for this month.")
        if annual_totals:
            st.divider()
            _render_annual_income_metrics(annual_totals)
        return

    if is_personal:
        headers = ["Source", "Net (Per Payment)", "Gross (Per Payment)", "Frequency", "Payment Date"]
        widths = INCOME_COL_WIDTHS_PERSONAL
    else:
        headers = ["Source", "Earner", "Net (Per Payment)", "Gross (Per Payment)", "Frequency", "Payment Date"]
        widths = INCOME_COL_WIDTHS

    _render_plain_grid_header(headers, widths, right_from_index=1)
    _compact_divider()

    for _, row in incomes_df.iterrows():
        values = [row.get("source_name", "—")]
        if not is_personal:
            values.append(row.get("owner_username", "—"))
        values.extend([
            _format_ledger_amount(row.get("take_home_amount", 0)),
            _format_ledger_amount(row.get("gross_amount", 0)),
            income_pay_frequency_label(row.get("pay_frequency")),
            _format_payment_date(row.get("payment_date")),
        ])
        _render_plain_grid_row(values, widths, right_from_index=1)

    if annual_totals:
        st.divider()
        _render_annual_income_metrics(annual_totals)


def _render_sinking_funds_list(annual_df) -> None:
    if annual_df.empty:
        return

    _render_plain_grid_header(["Subscription", "Monthly set-aside", "Annual cost"], SINKING_COL_WIDTHS)
    st.caption(
        "Monthly set-aside is the amount budgeted each month. Annual cost is that monthly amount × 12."
    )
    st.divider()

    for _, row in annual_df.iterrows():
        sub = row["sub_category_name"]
        if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
            sub = "(General)"
        else:
            sub = str(sub)
        monthly_target = float(row["target_budget"])
        annual_target = monthly_target * 12
        _render_plain_grid_row(
            [sub, _format_ledger_amount(monthly_target), _format_ledger_amount(annual_target)],
            SINKING_COL_WIDTHS,
            right_from_index=1,
        )


def _render_expense_column_header(*, can_edit: bool) -> None:
    widths = EXPENSE_COL_WIDTHS + ([EXPENSE_ACTION_COL_WIDTH] if can_edit else [])
    cols = st.columns(widths)
    headers = ["Date", "Category", "Sub-category", "Details", "Amount"]
    for col, label in zip(cols[:5], headers):
        _grid_cell(col, label, emphasize=False)
    if can_edit:
        _grid_cell(cols[5], "", emphasize=False)


def _render_expense_manage_rows(
    expenses_df,
    key_prefix,
    empty_caption,
    categories_df=None,
    can_edit=True,
):
    if expenses_df.empty:
        st.caption(empty_caption)
        return

    enriched = _enrich_expenses_with_categories(expenses_df, categories_df)
    sorted_df = enriched.sort_values("date_logged", ascending=False)

    _render_expense_column_header(can_edit=can_edit)
    _compact_divider()

    row_widths = EXPENSE_COL_WIDTHS + ([EXPENSE_ACTION_COL_WIDTH] if can_edit else [])

    for _, row in sorted_df.iterrows():
        exp_id = row["id"]
        recurring_tag = " 🔄" if row.get("is_recurring", False) else ""
        cols = st.columns(row_widths)
        _grid_cell(cols[0], row["date_logged"])
        _grid_cell(cols[1], row.get("category_name", "—"))
        _grid_cell(cols[2], row.get("sub_category_name", "—"))
        _grid_cell(cols[3], f"{row['details']}{recurring_tag}")
        _grid_cell(cols[4], _format_ledger_amount(row["amount"]))

        if can_edit:
            expense_popover_key = f"{key_prefix}_{exp_id}"
            form_key = f"edit_form_{key_prefix}_{exp_id}"
            with cols[5].popover("⚙️ Manage", key=manage_popover_key(expense_popover_key)):
                st.markdown(f"**Edit: {row['details']}**")
                with st.form(form_key):
                    new_date = st.date_input("Date", value=datetime.strptime(row["date_logged"], "%Y-%m-%d"))
                    new_amt = st.text_input("Amount ($)", value=str(row["amount"]))
                    new_det = st.text_input("Details", value=row["details"])
                    new_recur = st.checkbox("🔄 Is Recurring?", value=bool(row.get("is_recurring", False)))
                    save_clicked = st.form_submit_button("💾 Save Changes", type="primary", width="stretch")

                if save_clicked:
                    parsed_amt = _parse_currency_input(new_amt)
                    if parsed_amt != "invalid" and new_det.strip():
                        if update_expense(exp_id, parsed_amt, new_det, new_recur, date_logged=new_date):
                            finish_manage_popover("expense_write", expense_popover_key)
                        else:
                            st.error("Could not update expense.")
                    else:
                        st.error("Invalid input.")

                if st.button("❌ Delete Expense", key=f"del_{key_prefix}_{exp_id}", type="secondary", width="stretch"):
                    if delete_expense(exp_id):
                        finish_manage_popover("expense_write", expense_popover_key)


def _render_income_management(
    *,
    expander_title,
    incomes_df,
    household_id,
    selected_month,
    form_key_prefix,
    is_personal,
    fixed_owner=None,
    earner_options=None,
):
    with st.expander(expander_title):
        tab_add, tab_edit = st.tabs(["➕ Add New", "🛠️ Edit Existing"])

        with tab_add:
            with st.form(f"add_{form_key_prefix}_income_form", clear_on_submit=True):
                source_name = st.text_input(
                    "Source Name",
                    placeholder="e.g., Paycheck, Side Gig, Bonus" if not is_personal else "e.g., Side Hustle, Dividends",
                )

                owner_username = fixed_owner
                if not is_personal:
                    owner_username = st.selectbox("Assign to Earner", earner_options or ["unassigned"])

                inc1, inc2 = st.columns(2)
                take_home = inc1.text_input("Take-Home (Net) $ per payment")
                gross = inc2.text_input("Gross $ per payment")

                is_taxable = st.checkbox("Is Taxable?", value=True)
                pay_frequency = st.selectbox(
                    "Pay frequency",
                    INCOME_FREQUENCY_OPTIONS,
                    format_func=income_pay_frequency_label,
                    index=INCOME_FREQUENCY_OPTIONS.index("monthly"),
                )
                if pay_frequency == "school_year_monthly":
                    st.caption(
                        "Regular paychecks run Sep–Jun on this day each month. "
                        "Add two **One-time** incomes in July and August for summer checks."
                    )
                payment_date = st.date_input(
                    "Payment / recurrence start date",
                    value=date.today(),
                    help=(
                        "Recurring income counts toward the ledger once this day of the month arrives."
                        if pay_frequency != "school_year_monthly"
                        else "Day of month for each Sep–Jun paycheck."
                    ),
                )

                if st.form_submit_button("💾 Save Income", type="primary", width="stretch"):
                    th_val = _parse_currency_input(take_home)
                    g_val = _parse_currency_input(gross)
                    if not source_name.strip() or th_val == "invalid" or g_val == "invalid":
                        st.error("Please provide a valid source name and dollar amounts.")
                    elif insert_household_income(
                        household_id,
                        selected_month,
                        source_name,
                        th_val,
                        g_val,
                        is_taxable,
                        owner_username,
                        False,
                        pay_frequency,
                        is_personal_income=is_personal,
                        payment_date=payment_date,
                    ):
                        st.rerun()

        with tab_edit:
            st.markdown(f"**✏️ Edit or Delete Income ({selected_month})**")
            if incomes_df.empty:
                st.caption("No income found for this month.")
                return

            if is_personal and "source_expense_id" in incomes_df.columns:
                linked = incomes_df[incomes_df.apply(_is_allowance_linked_income_row, axis=1)]
                if not linked.empty:
                    st.caption(
                        "Allowance income is managed via Household Budget expenses "
                        "(Allowance category). Edit or delete the household expense instead."
                    )

            editable_df = (
                incomes_df[~incomes_df.apply(_is_allowance_linked_income_row, axis=1)]
                if "source_expense_id" in incomes_df.columns
                else incomes_df
            )
            if editable_df.empty:
                st.caption("No editable income streams for this month.")
                return

            def _income_label(row):
                owner = row.get("owner_username", "unassigned")
                amount = _to_number(row.get("take_home_amount"))
                freq = income_pay_frequency_label(row.get("pay_frequency"))
                if is_personal:
                    return f"{row.get('source_name')} · {freq} · ${_format_money(amount)}"
                return f"{row.get('source_name')} ({owner}) · {freq} · ${_format_money(amount)}"

            edit_options = editable_df.apply(_income_label, axis=1).tolist()
            selected_edit_str = st.selectbox(
                "Select Income Stream to Edit",
                edit_options,
                key=f"edit_{form_key_prefix}_income_select",
            )
            selected_edit_idx = edit_options.index(selected_edit_str)
            target_row = editable_df.iloc[selected_edit_idx]
            target_income_id = target_row["id"]

            with st.form(f"edit_{form_key_prefix}_income_form", clear_on_submit=True):
                edit_source = st.text_input("Source Name", value=target_row.get("source_name", ""))

                edit_owner = fixed_owner
                if not is_personal:
                    current_owner = target_row.get("owner_username") or (earner_options or ["unassigned"])[0]
                    owner_index = (earner_options or ["unassigned"]).index(current_owner) if current_owner in (earner_options or []) else 0
                    edit_owner = st.selectbox("Assign to Earner", earner_options or ["unassigned"], index=owner_index)

                e1, e2 = st.columns(2)
                edit_take_home = e1.text_input("Take-Home (Net) $ per payment", value=f"{_to_number(target_row.get('take_home_amount')):.2f}")
                edit_gross = e2.text_input("Gross $ per payment", value=f"{_to_number(target_row.get('gross_amount')):.2f}")

                edit_taxable = st.checkbox("Is Taxable?", value=bool(target_row.get("is_taxable", True)))
                current_freq = normalize_income_pay_frequency(target_row.get("pay_frequency"))
                edit_pay_frequency = st.selectbox(
                    "Pay frequency",
                    INCOME_FREQUENCY_OPTIONS,
                    format_func=income_pay_frequency_label,
                    index=INCOME_FREQUENCY_OPTIONS.index(current_freq),
                    key=f"edit_pay_freq_{form_key_prefix}_{target_income_id}",
                )
                if edit_pay_frequency == "school_year_monthly":
                    st.caption(
                        "Regular paychecks run Sep–Jun on this day each month. "
                        "Add two **One-time** incomes in July and August for summer checks."
                    )
                payment_default = date.today()
                if target_row.get("payment_date"):
                    payment_default = datetime.strptime(str(target_row["payment_date"])[:10], "%Y-%m-%d").date()
                edit_payment_date = st.date_input(
                    "Payment / recurrence start date",
                    value=payment_default,
                    key=f"edit_pay_date_{form_key_prefix}_{target_income_id}",
                )

                u1, u2 = st.columns(2)
                update_clicked = u1.form_submit_button("💾 Update Income", type="primary", width="stretch")
                delete_clicked = u2.form_submit_button("🗑️ Delete Income", type="secondary", width="stretch")

            if update_clicked:
                parsed_take_home = _parse_currency_input(edit_take_home)
                parsed_gross = _parse_currency_input(edit_gross)
                if not edit_source.strip() or parsed_take_home == "invalid" or parsed_gross == "invalid":
                    st.error("Please provide a valid source name and dollar amounts.")
                elif update_household_income(
                    target_income_id,
                    edit_source,
                    parsed_take_home,
                    parsed_gross,
                    edit_taxable,
                    edit_owner,
                    False,
                    edit_pay_frequency,
                    payment_date=edit_payment_date,
                ):
                    st.rerun()

            if delete_clicked:
                if delete_household_income(target_income_id):
                    st.rerun()


def _render_family_member_budgets(household_id, selected_month, household_users):
    st.markdown("#### 👨‍👩‍👧 Family Member Budgets")
    st.caption(
        "View-only access to personal budgets shared by household members and other family admins."
    )

    sharing_members = get_members_sharing_personal_budget(household_id)
    if not sharing_members:
        st.info("No one has opted to share their personal budget yet.")
        return

    user_map = {u.get("username"): u for u in household_users if u.get("username")}
    available = [name for name in sorted(sharing_members) if name in user_map]
    if not available:
        st.info("No shareable member profiles are available right now.")
        return

    selected_member = st.selectbox("Select family member", available, key="family_member_select")
    member_profile = user_map[selected_member]
    member_auth_id = member_profile.get("auth_user_id")

    member_incomes = get_household_incomes(
        household_id, selected_month, is_personal_income=True, username=selected_member
    )
    member_incomes_actual = _filter_incomes_for_actual_totals(member_incomes, selected_month)
    total_member_income = sum_income_for_month(member_incomes_actual, selected_month)
    member_annual_income_totals = compute_annual_income_totals(member_incomes_actual)

    member_expenses = get_individual_expenses(household_id, member_auth_id, selected_month)
    if not member_expenses.empty and "is_personal_spend" in member_expenses.columns:
        member_personal_df = member_expenses[member_expenses["is_personal_spend"] == True]
    else:
        member_personal_df = member_expenses

    member_actual_df = _filter_expenses_for_actual_totals(member_personal_df, selected_month)
    total_member_spend = member_actual_df["amount"].sum() if not member_actual_df.empty else 0.0
    net_member_cash = total_member_income - total_member_spend

    m1, m2, m3 = st.columns(3)
    m1.metric("Est. Monthly Income", f"${total_member_income:,.2f}")
    m2.metric("Total Personal Spend", f"${total_member_spend:,.2f}")
    _render_signed_currency_metric(m3, "Net Personal Cash Flow", net_member_cash)
    st.divider()

    st.markdown(f"##### {selected_member.title()}'s Budget Breakdown")
    categories_df = get_budget_categories(household_id, is_personal=True, username=selected_member)
    if categories_df.empty:
        st.info("No personal categories set up yet.")
    else:
        if not member_actual_df.empty:
            exp_summary = member_actual_df.groupby("category_id")["amount"].sum().reset_index()
        else:
            exp_summary = pd.DataFrame(columns=["category_id", "amount"])

        merged_df = pd.merge(categories_df, exp_summary, left_on="id", right_on="category_id", how="left")
        merged_df["actual_amount"] = merged_df["amount"].fillna(0.0)

        year, month = map(int, selected_month.split("-"))
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        prev_month_str = f"{prev_year}-{prev_month:02d}"
        recurring_schedule = get_recurring_schedule(household_id, prev_month_str, is_personal=True)

        _render_household_budget_breakdown(
            merged_df,
            member_personal_df,
            recurring_schedule,
            selected_month,
            filter_key=f"family_breakdown_{selected_member}",
        )

        annual_df = merged_df[merged_df["category_name"] == "Annual Subscriptions"]
        if not annual_df.empty:
            st.divider()
            st.markdown("##### Annual Subscriptions (Sinking Funds)")
            _render_sinking_funds_list(annual_df)

    st.divider()
    st.markdown(f"##### {selected_member.title()}'s Income")
    _render_income_streams_list(
        member_incomes,
        is_personal=True,
        annual_totals=member_annual_income_totals,
    )

    st.divider()
    st.markdown(f"##### {selected_member.title()}'s Expenses")
    pers_recurring_df, pers_one_time_df = _split_recurring_expenses(member_personal_df)

    st.markdown("**Recurring**")
    _render_expense_manage_rows(
        pers_recurring_df,
        f"family_recur_{selected_member}",
        "No recurring personal expenses logged for this month yet.",
        categories_df=categories_df,
        can_edit=False,
    )
    st.markdown("**One-Time**")
    _render_expense_manage_rows(
        pers_one_time_df,
        f"family_once_{selected_member}",
        "No one-time personal expenses logged for this month yet.",
        categories_df=categories_df,
        can_edit=False,
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


def _render_two_col_selector(key: str, options: list, format_func=None):
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
                    rerun_app_with_reason("selector_change")

            if col_right.button(
                right_label,
                key=f"{key}_btn_{idx}_right",
                type="primary" if selected_value == right_opt else "secondary",
                width="stretch",
            ):
                if selected_value != right_opt:
                    st.session_state[key] = right_opt
                    rerun_app_with_reason("selector_change")
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
                    rerun_app_with_reason("selector_change")

    return st.session_state.get(key)


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


def _can_access_projects_module():
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    return bool(st.session_state.get("can_view_projects", st.session_state.get("can_view_budget", False)))


def _can_edit_projects_module():
    role = st.session_state.get("user_role", "member")
    if role in ["admin", "developer"]:
        return True
    return bool(st.session_state.get("can_edit_projects", False))


def _can_access_monthly_module():
    return _is_budget_admin()


def render_budget_module(show_back_to_hub=False):
    _render_budget_fragment(show_back_to_hub)


def _render_budget_fragment(show_back_to_hub=False):
    household_id = st.session_state.get("household_id")
    if household_id:
        guard_key = f"project_category_ready_{household_id}"
        if not st.session_state.get(guard_key):
            ensure_project_expense_category(household_id)
            st.session_state[guard_key] = True
        ensure_allowance_categories(household_id)

    if "budget_view" not in st.session_state:
        st.session_state["budget_view"] = "menu"
    if "projects_funds" not in st.session_state:
        st.session_state["projects_funds"] = None
    if "pending_restore_project_id" not in st.session_state:
        st.session_state["pending_restore_project_id"] = None
    if "projects_funds_input" not in st.session_state:
        st.session_state["projects_funds_input"] = ""
    if "projects_active_section" not in st.session_state:
        st.session_state["projects_active_section"] = "workspace"
    if "projects_workspace_active_category" not in st.session_state:
        st.session_state["projects_workspace_active_category"] = "priority"
    if "wishlist_active_owner" not in st.session_state:
        st.session_state["wishlist_active_owner"] = None
    if "wishlist_pending_owner" not in st.session_state:
        st.session_state["wishlist_pending_owner"] = None
    if "wishlist_active_section" not in st.session_state:
        st.session_state["wishlist_active_section"] = "active"

    view = st.session_state["budget_view"]
    can_access_projects = _can_access_projects_module()
    can_edit_projects = _can_edit_projects_module()
    can_access_monthly = _can_access_monthly_module()

    if view == "menu":
        if show_back_to_hub:
            if st.button("⬅️ Back to Hub Menu", width="content"):
                st.session_state["active_hub_view"] = "main_menu"
                rerun_app_with_reason("hub_nav")

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
                    st.caption("Shared household ledger, bills, and budget breakdown.")
                    if st.button("Open Household", key="btn_household", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "household"
                        st.rerun()
                else:
                    st.caption("Household budget is limited to family admins and developers.")
                    st.button("Household Locked", disabled=True, width="stretch")

        with r1c2:
            with st.container(border=True):
                # Safely grab the username, defaulting to 'User' just in case
                username = st.session_state.get("username", "User")
                
                # 🟢 DYNAMIC HEADER INJECTED HERE
                st.markdown(f"### 👤 {username.title()}'s Personal Ledger")
                st.caption("Your private dashboard for personal 'spend money'.")
                
                if st.button("Open Personal", key="btn_personal", type="secondary", width="stretch"):
                    st.session_state["budget_view"] = "personal"
                    st.rerun()

        # --- ROW 2: Projects & Wish List ---
        r2c1, r2c2 = st.columns(2)                  
        with r2c1:
            with st.container(border=True):
                st.markdown("### 🛠️ Projects")
                if can_access_projects:
                    st.caption("View active project estimates and execution notes.")
                    if st.button("Open Projects", key="btn_projects", type="secondary", width="stretch"):
                        st.session_state["budget_view"] = "projects"
                        st.session_state["projects_active_section"] = "workspace"
                        st.rerun()
                else:
                    st.caption("Projects access is restricted.")
                    st.button("Projects Locked", disabled=True, width="stretch")

        with r2c2:
            with st.container(border=True):
                st.markdown("### 💝 Wish List")
                st.caption("Track purchase ideas in a shared household list.")
                if st.button("Open Wish List", key="btn_wish", type="secondary", width="stretch"):
                    st.session_state["budget_view"] = "wishlist"
                    st.rerun()

        return

    if st.button("⬅️ Back to Budget Modules", width="content"):
        st.session_state["budget_view"] = "menu"
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

        selected_owner = _render_two_col_selector(
            key="wishlist_active_owner",
            options=available_owner_names,
            format_func=lambda owner: f"👤 {owner}",
        )

        active_rows = [r for r in visible_rows if not bool(r.get("is_completed", False))]
        completed_rows = [r for r in visible_rows if bool(r.get("is_completed", False))]

        filtered_active_rows = [r for r in active_rows if get_row_owner_username(r) == selected_owner]
        filtered_completed_rows = [r for r in completed_rows if get_row_owner_username(r) == selected_owner]

        wishlist_section_keys = ["active", "completed"]
        if st.session_state.get("wishlist_active_section") not in wishlist_section_keys:
            st.session_state["wishlist_active_section"] = "active"

        def wishlist_section_label(section_key):
            if section_key == "active":
                return f"🛍️ Active ({len(filtered_active_rows)})"
            return f"✅ Completed ({len(filtered_completed_rows)})"

        selected_wishlist_section = _render_two_col_selector(
            key="wishlist_active_section",
            options=wishlist_section_keys,
            format_func=wishlist_section_label,
        )

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

        if selected_wishlist_section == "active":
            if not filtered_active_rows:
                st.info(f"No active wish list items for {selected_owner} yet.")
                return

            for row in filtered_active_rows:
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
                        wishlist_popover_key = f"wishlist_{row_id}"
                        with right.popover("⚙️ Manage", key=manage_popover_key(wishlist_popover_key)):
                            st.markdown(f"**Edit: {item_name}**")
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
                                        finish_manage_popover("wishlist_write", wishlist_popover_key)
                                    else:
                                        st.error("Could not update this wish list item.")

                            if complete_clicked:
                                if complete_wish_list_item(str(row_id)):
                                    finish_manage_popover("wishlist_write", wishlist_popover_key)
                                else:
                                    st.error("Could not complete this wish list item.")

                            if delete_clicked:
                                if delete_wish_list_item(str(row_id)):
                                    finish_manage_popover("wishlist_write", wishlist_popover_key)
                                else:
                                    st.error("Could not delete this wish list item.")

                            if cancel_clicked:
                                finish_manage_popover("wishlist_edit_cancel", wishlist_popover_key)

                st.divider()
        else:
            if not filtered_completed_rows:
                st.info(f"No completed wish list items for {selected_owner} yet.")
                return

            for row in filtered_completed_rows:
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
        auth_user_id = st.session_state.get("auth_user_id")
        username = st.session_state.get("username")
        
        current_month = datetime.now().strftime("%Y-%m")
        selected_month = st.selectbox("Select Month", [current_month, "2026-05", "2026-04"], index=0)
        _maybe_auto_rollover(household_id, selected_month)
            
        incomes_df = get_household_incomes(household_id, selected_month)
        incomes_actual_df = _filter_incomes_for_actual_totals(incomes_df, selected_month)
        expenses_df = get_monthly_expenses(household_id, selected_month, include_private_members=True)
        routing_df = get_cash_flow_routing(household_id)
        
        total_take_home = sum_income_for_month(incomes_actual_df, selected_month)
        annual_income_totals = compute_annual_income_totals(incomes_actual_df)
        
        hh_expenses_df = (
            expenses_df[expenses_df["is_personal_spend"] == False]
            if not expenses_df.empty and "is_personal_spend" in expenses_df.columns
            else expenses_df
        )

        categories_df = get_budget_categories(household_id, is_personal=False)
        hh_expenses_no_project = _exclude_system_category_expenses(hh_expenses_df, categories_df)
        hh_actual_shared = _filter_expenses_for_actual_totals(hh_expenses_no_project, selected_month)
        total_expenses = hh_actual_shared["amount"].sum() if not hh_actual_shared.empty else 0.0

        hh_actual_all = _filter_expenses_for_actual_totals(hh_expenses_df, selected_month)
        _, project_expenses_df = _split_project_household_expenses(hh_actual_all, categories_df)
        project_spending = project_expenses_df["amount"].sum() if not project_expenses_df.empty else 0.0
        net_cash_flow = total_take_home - total_expenses

        household_options = _household_submodule_options()
        _sync_selector_option("household_view_mode", household_options)
        household_view_mode = _render_two_col_selector(
            key="household_view_mode",
            options=household_options,
        )
        
        # --- TAB 1: MASTER LEDGER ---
        if household_view_mode == "📊 Master Ledger":
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Est. Monthly Take-Home", f"${total_take_home:,.2f}")
            col2.metric("Total Shared Expenses", f"${total_expenses:,.2f}")
            _render_signed_currency_metric(col3, "Net Cash Flow", net_cash_flow)
            col4.metric("Project Spending", f"${project_spending:,.2f}", help="Informational only — not included in shared expenses or net cash flow.")
            st.divider()
            
            st.markdown("#### Household Budget Breakdown")
            
            if categories_df.empty:
                st.info("No categories setup yet. Add some to build your ledger!")
            else:
                if not hh_actual_shared.empty:
                    exp_summary = hh_actual_shared.groupby("category_id")["amount"].sum().reset_index()
                else:
                    exp_summary = pd.DataFrame(columns=["category_id", "amount"])
                
                merged_df = pd.merge(categories_df, exp_summary, left_on="id", right_on="category_id", how="left")
                merged_df["actual_amount"] = merged_df["amount"].fillna(0.0)
                merged_df = _exclude_system_categories(merged_df)

                year, month = map(int, selected_month.split("-"))
                prev_month = month - 1 if month > 1 else 12
                prev_year = year if month > 1 else year - 1
                prev_month_str = f"{prev_year}-{prev_month:02d}"
                recurring_schedule = get_recurring_schedule(household_id, prev_month_str)

                _render_household_budget_breakdown(
                    merged_df,
                    hh_expenses_no_project,
                    recurring_schedule,
                    selected_month,
                    filter_key="hh_breakdown_category",
                )
            
            st.divider()
            
            # 🟢 SINKING FUNDS TRACKER
            annual_df = (
                categories_df[categories_df["category_name"] == "Annual Subscriptions"]
                if not categories_df.empty
                else pd.DataFrame()
            )

            if not annual_df.empty:
                with st.expander("📅 Annual Subscriptions (Sinking Funds) Tracker", expanded=False):
                    _render_sinking_funds_list(annual_df)
        

            # 🟢 NEW: Annual Reports Footer
            with st.expander("📈 Annual Reports & YTD Summary"):
                st.markdown("##### Year-to-Date Performance")
                st.caption("YTD aggregations and charting logic will go here.")
                # We will write the complex SQL lookbacks for this next!
                
        # --- TAB 2: LOG EXPENSE (HOUSEHOLD) ---
        elif household_view_mode == "💳 Expenses":
            st.markdown("#### Log a Shared Household Bill/Expense")
            
            categories_df = get_budget_categories(household_id, is_personal=False)
            user_categories_df = _exclude_system_categories(categories_df)
            hh_expenses_display_df = _exclude_system_category_expenses(hh_expenses_df, categories_df)
            
            if user_categories_df.empty:
                st.warning("No active categories found. Please add one below.")
            else:
                user_categories_df = user_categories_df.copy()
                user_categories_df["display_name"] = user_categories_df.apply(
                    lambda row: f"{row['category_name']} - {row['sub_category_name']}"
                    if pd.notnull(row.get("sub_category_name"))
                    else row["category_name"],
                    axis=1,
                )
                display_list = user_categories_df["display_name"].tolist()

                selected_display_name = st.selectbox(
                    "Category",
                    display_list,
                    key="hh_expense_category_select",
                )
                cat_row = user_categories_df[
                    user_categories_df["display_name"] == selected_display_name
                ].iloc[0]
                is_allowance_payment = is_allowance_subcategory(
                    cat_row.get("category_name"), cat_row.get("sub_category_name")
                )
                allowance_recipient = cat_row.get("username") if is_allowance_payment else None
                if is_allowance_payment and allowance_recipient:
                    st.caption(
                        f"This payment will appear as income in **{allowance_recipient}**'s Personal Budget. "
                        "Check recurring to pay the same amount each month on this day."
                    )

                with st.form("hh_expense_entry", clear_on_submit=True):
                    a1, a2 = st.columns([1, 1])
                    date_logged = a1.date_input("Date")
                    amount_raw = a2.text_input("Amount ($) *")

                    details = st.text_input("Details")
                    is_recurring = st.checkbox(
                        "🔄 Make this a recurring monthly bill",
                        value=False,
                    )

                    if st.form_submit_button("💾 Save Shared Expense", type="primary", width="stretch"):
                        parsed_amount = _parse_currency_input(amount_raw)
                        if "invalid" == parsed_amount or parsed_amount is None:
                            st.error("Please enter a valid dollar amount.")
                        else:
                            success = log_expense_and_check_project(
                                auth_user_id=auth_user_id, username=username, household_id=household_id,
                                month_year=date_logged.strftime("%Y-%m"), date_logged=date_logged,
                                category_id=cat_row["id"], amount=parsed_amount, details=details.strip(),
                                is_personal_spend=False, is_recurring=is_recurring,
                            )
                            if success:
                                st.success(f"Logged ${_format_money(parsed_amount)} to Household Ledger.")
                                st.rerun()
                            else:
                                st.error("Failed to log expense.")
            
            st.divider()

            hh_recurring_df, hh_one_time_df = _split_recurring_expenses(hh_expenses_display_df)

            with st.expander("🔄 Recurring Household Expenses", expanded=False):
                _render_expense_manage_rows(
                    hh_recurring_df,
                    "exp_hh_recur",
                    "No recurring household expenses logged for this month yet.",
                    categories_df=user_categories_df,
                    can_edit=True,
                )

            with st.expander("📝 One-Time Household Expenses", expanded=False):
                _render_expense_manage_rows(
                    hh_one_time_df,
                    "exp_hh_once",
                    "No one-time household expenses logged for this month yet.",
                    categories_df=user_categories_df,
                    can_edit=True,
                )
            
            st.divider()

            with st.expander("⚙️ Manage Expense Categories"):
                st.caption(
                    "Project purchases use a system-managed category and are not listed here. "
                    "Allowance sub-categories are managed automatically for each household member."
                )
                tab_add, tab_edit = st.tabs(["➕ Add New", "✏️ Edit Existing"])

                with tab_add:
                    st.markdown("**🏷️ Add New Category**")
                    parent_options = ["➕ Create New Parent Category"]
                    if not user_categories_df.empty:
                        existing_parents = sorted(user_categories_df["category_name"].unique().tolist())
                        parent_options.extend(existing_parents)

                    selected_parent = st.selectbox("Parent Category", parent_options, key="hh_parent_sel")

                    if selected_parent == "➕ Create New Parent Category":
                        final_parent_input = st.text_input("New Parent Name *", placeholder="e.g., Annual Subscriptions, Auto", key="hh_new_parent_input")
                    else:
                        final_parent_input = selected_parent

                    is_annual = (final_parent_input == "Annual Subscriptions")
                    target_label = "Full YEARLY Budget ($) - Will automatically divide by 12" if is_annual else "Projected Monthly Budget ($)"

                    new_sub_cat = st.text_input("Sub-Category (Optional)", placeholder="e.g., Amazon Prime, Costco", key="hh_new_sub_input")
                    target_budget_raw = st.text_input(target_label, placeholder="e.g., 120" if is_annual else "e.g., 300", key="hh_new_target_input")

                    if st.button("💾 Save Category", type="primary", width='stretch', key="hh_save_cat_btn"):
                        final_parent = final_parent_input.strip() if isinstance(final_parent_input, str) else final_parent_input
                        parsed_target = _parse_currency_input(target_budget_raw) if target_budget_raw else 0.0

                        if not final_parent or parsed_target == "invalid":
                            st.error("Valid category name and numeric budget required.")
                        elif is_system_project_expense_category(final_parent, new_sub_cat):
                            st.error("That category name is reserved for automatic project purchase logging.")
                        elif is_system_managed_allowance_category(final_parent, new_sub_cat):
                            st.error("Allowance categories are managed automatically for each household member.")
                        else:
                            if is_annual:
                                parsed_target = parsed_target / 12.0

                            if insert_budget_category(household_id, final_parent, new_sub_cat, target_budget=parsed_target):
                                st.success(f"Added {final_parent}!")
                                st.rerun()

                with tab_edit:
                    st.markdown("**✏️ Edit or Delete Categories**")
                    editable_cats_df = user_categories_df[
                        ~user_categories_df.apply(
                            lambda row: is_allowance_subcategory(
                                row.get("category_name"), row.get("sub_category_name")
                            ),
                            axis=1,
                        )
                    ]
                    if not editable_cats_df.empty:
                        edit_cat_options = editable_cats_df.apply(
                            lambda row: f"{row['category_name']} - {row.get('sub_category_name', '')} (${row.get('target_budget', 0):.2f}/mo)", axis=1
                        ).tolist()

                        selected_edit_str = st.selectbox("Select Category to Edit", edit_cat_options, key="edit_cat_hh_select")
                        selected_edit_idx = edit_cat_options.index(selected_edit_str)
                        target_cat_row = editable_cats_df.iloc[selected_edit_idx]
                        target_cat_id = target_cat_row["id"]

                        is_edit_annual = (target_cat_row["category_name"] == "Annual Subscriptions")
                        edit_val = target_cat_row.get("target_budget", 0.0)
                        display_val = edit_val * 12.0 if is_edit_annual else edit_val
                        target_label = "Full YEARLY Budget ($) - Will automatically divide by 12" if is_edit_annual else "Projected Monthly Budget ($)"

                        with st.form("edit_cat_hh_form", clear_on_submit=True):
                            edit_parent = st.text_input("Parent Category", value=target_cat_row["category_name"])
                            safe_sub = target_cat_row.get("sub_category_name")
                            edit_sub = st.text_input("Sub-Category", value=safe_sub if pd.notnull(safe_sub) else "")
                            edit_target = st.text_input(target_label, value=f"{display_val:.2f}")

                            u1, u2 = st.columns(2)
                            update_clicked = u1.form_submit_button("💾 Update Category", type="primary", width="stretch")
                            delete_clicked = u2.form_submit_button("🗑️ Delete Category", type="secondary", width="stretch")

                        if update_clicked:
                            parsed_target = _parse_currency_input(edit_target)
                            if not edit_parent.strip() or parsed_target == "invalid":
                                st.error("Valid category name and numeric budget required.")
                            else:
                                if edit_parent.strip() == "Annual Subscriptions":
                                    parsed_target = parsed_target / 12.0

                                if update_budget_category(target_cat_id, edit_parent, edit_sub, parsed_target):
                                    rerun_app_with_reason("category_write")

                        if delete_clicked:
                            if delete_budget_category(target_cat_id):
                                rerun_app_with_reason("category_write")
                    else:
                        st.caption("No categories found to edit.")

        # --- TAB 3: CASH FLOW & TREASURY (Admin/Developer only) ---
        elif household_view_mode == "🔄 Cash Flow & Treasury":
            st.markdown("#### 💵 Cash Flow & Income Tracking")
            st.markdown("**Current Month Income Streams**")
            _render_income_streams_list(
                incomes_df,
                is_personal=False,
                annual_totals=annual_income_totals,
            )

            st.divider()

            household_users = get_household_users_for_admin()
            user_options = [_clean_text(u.get("username")) for u in household_users if _clean_text(u.get("username"))]
            if not user_options:
                user_options = ["unassigned"]

            _render_income_management(
                expander_title="🛠️ Manage Household Income",
                incomes_df=incomes_df,
                household_id=household_id,
                selected_month=selected_month,
                form_key_prefix="hh",
                is_personal=False,
                earner_options=user_options,
            )

        return # Hard stop for Household

    # ==========================================
    # 👤 TOP-LEVEL: PERSONAL BUDGET (All Users)
    # ==========================================
    if view == "personal":
        household_id = st.session_state.get("household_id")
        username = st.session_state.get("username", "User")
        auth_user_id = st.session_state.get("auth_user_id")
        
        st.subheader(f"👤 {username.title()}'s Personal Ledger")
        
        current_month = datetime.now().strftime("%Y-%m")
        selected_month = st.selectbox("Select Month", [current_month, "2026-05", "2026-04"], index=0, key="personal_month")
        
        _maybe_auto_rollover(household_id, selected_month)
            
        settings = get_user_finance_settings(household_id, username)
        indiv_expenses_df = get_individual_expenses(household_id, auth_user_id, selected_month)
        
        personal_incomes_df = get_household_incomes(household_id, selected_month, is_personal_income=True, username=username)
        personal_incomes_actual_df = _filter_incomes_for_actual_totals(personal_incomes_df, selected_month)
        total_personal_income = sum_income_for_month(personal_incomes_actual_df, selected_month)
        personal_annual_income_totals = compute_annual_income_totals(personal_incomes_actual_df)
        
        if not indiv_expenses_df.empty and "is_personal_spend" in indiv_expenses_df.columns:
            my_personal_df = indiv_expenses_df[indiv_expenses_df["is_personal_spend"] == True]
        else:
            my_personal_df = pd.DataFrame()
            
        my_actual_df = _filter_expenses_for_actual_totals(my_personal_df, selected_month)
        total_personal_spend = my_actual_df["amount"].sum() if not my_actual_df.empty else 0.0

        if _is_budget_admin():
            toggle_text = "Allow other Family Admins to view my Personal Budget"
        else:
            toggle_text = "Allow Family Admins to view my Personal Budget"
        current_share_status = bool(settings.get("share_budget_with_admin", False))
        allow_family_view = st.toggle(toggle_text, value=current_share_status)
        
        if allow_family_view != current_share_status:
            if update_user_privacy_toggle(household_id, username, allow_family_view):
                st.rerun()
                
        personal_options = _personal_submodule_options(username)
        _sync_selector_option("personal_view_mode", personal_options)
        personal_view_mode = _render_two_col_selector(
            key="personal_view_mode",
            options=personal_options,
        )
        st.divider()

        # --- TAB 1: MASTER LEDGER (PERSONAL) ---
        if personal_view_mode == f"📊 {username.title()}'s Ledger":
            
            p_col1, p_col2, p_col3 = st.columns(3)
            p_col1.metric("Est. Monthly Income", f"${total_personal_income:,.2f}")
            p_col2.metric("Total Personal Spend", f"${total_personal_spend:,.2f}")
            
            net_personal_cash = total_personal_income - total_personal_spend
            _render_signed_currency_metric(p_col3, "Net Personal Cash Flow", net_personal_cash)
            st.divider()
            
            st.markdown(f"#### {username.title()}'s Budget Breakdown")
            
            categories_df = get_budget_categories(household_id, is_personal=True, username=username)
            
            if categories_df.empty:
                st.info("No personal categories setup yet. Build your blank slate below!")
            else:
                if not my_actual_df.empty:
                    exp_summary = my_actual_df.groupby("category_id")["amount"].sum().reset_index()
                else:
                    exp_summary = pd.DataFrame(columns=["category_id", "amount"])
                
                merged_df = pd.merge(categories_df, exp_summary, left_on="id", right_on="category_id", how="left")
                merged_df["actual_amount"] = merged_df["amount"].fillna(0.0)
                
                # Grab personal recurring schedule
                year, month = map(int, selected_month.split('-'))
                prev_month = month - 1 if month > 1 else 12
                prev_year = year if month > 1 else year - 1
                prev_month_str = f"{prev_year}-{prev_month:02d}"
                recurring_schedule = get_recurring_schedule(household_id, prev_month_str, is_personal=True)
                
                _render_household_budget_breakdown(
                    merged_df,
                    my_personal_df,
                    recurring_schedule,
                    selected_month,
                    filter_key="pers_breakdown_category",
                )

                annual_df = merged_df[merged_df["category_name"] == "Annual Subscriptions"]
                if not annual_df.empty:
                    st.divider()
                    with st.expander("📅 Annual Subscriptions (Sinking Funds) Tracker", expanded=False):
                        _render_sinking_funds_list(annual_df)

            with st.expander("📈 Annual Reports & YTD Summary"):
                st.markdown("##### Year-to-Date Performance")
                st.caption("YTD aggregations and charting logic will go here.")

            st.divider()

        # --- TAB 2: LOG EXPENSE (PERSONAL) ---
        elif personal_view_mode == "💳 Expenses":
            st.markdown("#### Log a Personal Expense")
            categories_df = get_budget_categories(household_id, is_personal=True, username=username)
            
            if categories_df.empty:
                st.warning("No personal categories found. Please add one below.")
            else:
                categories_df["display_name"] = categories_df.apply(lambda row: f"{row['category_name']} - {row['sub_category_name']}" if pd.notnull(row.get('sub_category_name')) else row['category_name'], axis=1)
                display_list = categories_df["display_name"].tolist()
                
                with st.form("pers_expense_entry", clear_on_submit=True):
                    a1, a2 = st.columns([1, 1])
                    date_logged = a1.date_input("Date")
                    amount_raw = a2.text_input("Amount ($) *")
                    
                    selected_display_name = st.selectbox("Category", display_list, key="pers_cat_form")
                    
                    details = st.text_input("Details")
                    is_recurring = st.checkbox("🔄 Make this a recurring monthly expense", value=False)
                    
                    if st.form_submit_button("💾 Save Personal Expense", type="primary", width="stretch"):
                        parsed_amount = _parse_currency_input(amount_raw)
                        if "invalid" == parsed_amount or parsed_amount is None:
                            st.error("Please enter a valid dollar amount.")
                        else:
                            cat_row = categories_df[categories_df["display_name"] == selected_display_name].iloc[0]
                            
                            success = log_expense_and_check_project(
                                auth_user_id=auth_user_id, username=username, household_id=household_id,
                                month_year=date_logged.strftime("%Y-%m"), date_logged=date_logged,
                                category_id=cat_row["id"], amount=parsed_amount, details=details.strip(), 
                                is_personal_spend=True, is_recurring=is_recurring
                            )
                            if success:
                                st.success(f"Logged ${_format_money(parsed_amount)} to Personal Ledger.")
                                st.rerun()
                            else:
                                st.error("Failed to log expense.")
                                
            st.divider()

            pers_recurring_df, pers_one_time_df = _split_recurring_expenses(my_personal_df)

            with st.expander("🔄 Recurring Personal Expenses", expanded=False):
                _render_expense_manage_rows(
                    pers_recurring_df,
                    "exp_pers_recur",
                    "No recurring personal expenses logged for this month yet.",
                    categories_df=categories_df,
                    can_edit=True,
                )

            with st.expander("📝 One-Time Personal Expenses", expanded=False):
                _render_expense_manage_rows(
                    pers_one_time_df,
                    "exp_pers_once",
                    "No one-time personal expenses logged for this month yet.",
                    categories_df=categories_df,
                    can_edit=True,
                )
            
            st.divider()

            with st.expander("⚙️ Manage Personal Categories"):
                tab_add, tab_edit = st.tabs(["➕ Add New", "✏️ Edit Existing"])
                
                with tab_add:
                    st.markdown("**🏷️ Add New Personal Category**")
                    parent_options = ["➕ Create New Parent Category"]
                    if not categories_df.empty:
                        existing_parents = sorted(categories_df["category_name"].unique().tolist())
                        parent_options.extend(existing_parents)
                        
                    selected_parent = st.selectbox("Parent Category", parent_options, key="pers_parent_sel")
                    
                    if selected_parent == "➕ Create New Parent Category":
                        final_parent_input = st.text_input("New Parent Name *", placeholder="e.g., Annual Subscriptions, Gaming", key="pers_new_parent_input")
                    else:
                        final_parent_input = selected_parent
                        
                    is_annual = (final_parent_input == "Annual Subscriptions")
                    target_label = "Full YEARLY Budget ($) - Will automatically divide by 12" if is_annual else "Projected Monthly Budget ($)"
                        
                    new_sub_cat = st.text_input("Sub-Category (Optional)", placeholder="e.g., PlayStation Plus, Coffee", key="pers_new_sub_input")
                    target_budget_raw = st.text_input(target_label, placeholder="e.g., 60" if is_annual else "e.g., 50", key="pers_new_target_input")
                    
                    if st.button("💾 Save Personal Category", type="primary", width='stretch', key="pers_save_cat_btn"):
                        final_parent = final_parent_input.strip() if isinstance(final_parent_input, str) else final_parent_input
                        parsed_target = _parse_currency_input(target_budget_raw) if target_budget_raw else 0.0
                        
                        if not final_parent or parsed_target == "invalid":
                            st.error("Valid category name and numeric budget required.")
                        else:
                            if is_annual:
                                parsed_target = parsed_target / 12.0
                                
                            if insert_budget_category(household_id, final_parent, new_sub_cat, is_personal=True, username=username, target_budget=parsed_target):
                                st.success(f"Added {final_parent} to your private list!")
                                st.rerun()
                                
                with tab_edit:
                    st.markdown("**✏️ Edit or Delete Personal Categories**")
                    if not categories_df.empty:
                        edit_cat_options = categories_df.apply(
                            lambda row: f"{row['category_name']} - {row.get('sub_category_name', '')} (${row.get('target_budget', 0):.2f}/mo)", axis=1
                        ).tolist()
                        
                        selected_edit_str = st.selectbox("Select Category to Edit", edit_cat_options, key="edit_cat_pers_select")
                        selected_edit_idx = edit_cat_options.index(selected_edit_str)
                        target_cat_row = categories_df.iloc[selected_edit_idx]
                        target_cat_id = target_cat_row["id"]
                        
                        is_edit_annual = (target_cat_row["category_name"] == "Annual Subscriptions")
                        edit_val = target_cat_row.get("target_budget", 0.0)
                        display_val = edit_val * 12.0 if is_edit_annual else edit_val
                        target_label = "Full YEARLY Budget ($) - Will automatically divide by 12" if is_edit_annual else "Projected Monthly Budget ($)"
                        
                        with st.form("edit_cat_pers_form", clear_on_submit=True):
                            edit_parent = st.text_input("Parent Category", value=target_cat_row["category_name"])
                            safe_sub = target_cat_row.get("sub_category_name")
                            edit_sub = st.text_input("Sub-Category", value=safe_sub if pd.notnull(safe_sub) else "")
                            edit_target = st.text_input(target_label, value=f"{display_val:.2f}")

                            u1, u2 = st.columns(2)
                            update_clicked = u1.form_submit_button("💾 Update Category", type="primary", width="stretch")
                            delete_clicked = u2.form_submit_button("🗑️ Delete Category", type="secondary", width="stretch")

                        if update_clicked:
                            parsed_target = _parse_currency_input(edit_target)
                            if not edit_parent.strip() or parsed_target == "invalid":
                                st.error("Valid category name and numeric budget required.")
                            else:
                                if edit_parent.strip() == "Annual Subscriptions":
                                    parsed_target = parsed_target / 12.0

                                if update_budget_category(target_cat_id, edit_parent, edit_sub, parsed_target):
                                    rerun_app_with_reason("category_write")

                        if delete_clicked:
                            if delete_budget_category(target_cat_id):
                                rerun_app_with_reason("category_write")
                    else:
                        st.caption("No categories found to edit.")                    
                                
        # --- TAB 3: PERSONAL CASH FLOW & TREASURY ---
        elif personal_view_mode == "🔄 Cash Flow & Treasury":
            st.markdown(f"#### 💵 {username.title()}'s Income Tracking")
            st.markdown("**Current Month Personal Income**")
            _render_income_streams_list(
                personal_incomes_df,
                is_personal=True,
                annual_totals=personal_annual_income_totals,
            )

            st.divider()

            _render_income_management(
                expander_title=f"🛠️ Manage {username.title()}'s Personal Income",
                incomes_df=personal_incomes_df,
                household_id=household_id,
                selected_month=selected_month,
                form_key_prefix="pers",
                is_personal=True,
                fixed_owner=username,
            )

        elif personal_view_mode == "👨‍👩‍👧 Family Member Budgets":
            household_users = get_household_users_for_admin()
            _render_family_member_budgets(household_id, selected_month, household_users)
                    
        return # Hard stop for Personal

    # ==========================================
    # 💳 TOP-LEVEL: EXPENSE TRACKER
    # ==========================================
    if view == "expense_tracker":
        st.subheader("💳 Log an Expense")
        
        household_id = st.session_state.get("household_id")
        auth_user_id = st.session_state.get("auth_user_id")
        
        categories_df = get_budget_categories(household_id)
        user_categories_df = _exclude_system_categories(categories_df)
        if user_categories_df.empty:
            st.warning("No active categories found. Please ask an Admin to add categories in the Household Setup.")
            return
            
        user_categories_df = user_categories_df.copy()
        user_categories_df["display_name"] = user_categories_df.apply(
            lambda row: f"{row['category_name']} - {row['sub_category_name']}" if pd.notnull(row.get('sub_category_name')) else row['category_name'], 
            axis=1
        )
        
        cat_options = user_categories_df["display_name"].tolist()
        selected_display_name = st.selectbox("Category", cat_options)
        
        cat_row = user_categories_df[user_categories_df["display_name"] == selected_display_name].iloc[0]
        category_id = cat_row["id"]
        category_type = cat_row.get("category_type")
        
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

    projects_section_keys = ["workspace", "overview", "completed"]

    if st.session_state.get("projects_active_section") not in projects_section_keys:
        st.session_state["projects_active_section"] = "workspace"

    def projects_section_label(section_key):
        if section_key == "overview":
            return "📊 Projects Overview"
        if section_key == "workspace":
            return "🧭 Projects Workspace"
        return "✅ Completed Projects"

    selected_projects_section = _render_two_col_selector(
        key="projects_active_section",
        options=projects_section_keys,
        format_func=projects_section_label,
    )

    if selected_projects_section == "overview":
        with st.expander("🎛️ Overview Filters", expanded=False):
            st.caption("Overview Year")
            selected_year = _render_two_col_selector(
                key="projects_overview_year",
                options=available_years,
                format_func=lambda year_value: f"🗓️ {year_value}",
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
                        project_popover_key = f"project_{project_id}"
                        with right_col.popover("⚙️ Manage", key=manage_popover_key(project_popover_key)):
                            tab_expense, tab_edit = st.tabs(["➕ Add Expense", "✏️ Edit Project"])

                            with tab_expense:
                                st.markdown(f"**Log purchase for {title}**")
                                st.caption("Adds to this project's Actual Spent and records a Projects line in the household budget.")
                                with st.form(f"add_project_expense_form_{project_id}"):
                                    exp_date = st.date_input(
                                        "Purchase date",
                                        value=date.today(),
                                        key=f"proj_exp_date_{project_id}",
                                    )
                                    exp_amount_raw = st.text_input(
                                        "Amount ($) *",
                                        placeholder="e.g., 125.00",
                                        key=f"proj_exp_amt_{project_id}",
                                    )
                                    expense_submit = st.form_submit_button(
                                        "💾 Add Expense",
                                        type="primary",
                                        width="stretch",
                                    )

                                if expense_submit:
                                    parsed_exp_amount = _parse_currency_input(exp_amount_raw)
                                    if parsed_exp_amount == "invalid" or parsed_exp_amount is None:
                                        st.error("Please enter a valid dollar amount.")
                                    elif add_project_purchase_expense(project_id, exp_date, parsed_exp_amount):
                                        finish_manage_popover("project_expense_write", project_popover_key)
                                    else:
                                        st.error("Could not log project expense.")

                            with tab_edit:
                                st.markdown(f"**Edit: {title}**")
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

                                    save_col, complete_col = st.columns(2)
                                    save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                                    complete_clicked = complete_col.form_submit_button("✅ Complete Project", width="stretch")

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
                                            finish_manage_popover("project_write", project_popover_key)
                                        else:
                                            st.error("Could not update project.")

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

            selected_workspace_section = _render_two_col_selector(
                key="projects_workspace_active_category",
                options=workspace_section_keys,
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