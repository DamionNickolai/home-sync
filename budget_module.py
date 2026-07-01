import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import calendar
import html
from zoneinfo import ZoneInfo
from datetime import datetime, date

from database import (
    get_project_budgets,
    update_project_budget_item,
    insert_project_budget_item,
    delete_project_budget_item,
    add_project_purchase_expense,
    update_project_purchase_expense,
    delete_project_purchase_expense,
    get_project_purchase_expenses,
    get_project_purchase_expense_entries,
    sum_project_purchase_expenses_for_year,
    format_project_purchase_expense_line,
    _project_expense_product_from_details,
    ensure_project_expense_category,
    ensure_household_taxes_category,
    ensure_personal_taxes_category,
    strip_expense_audit_lines_from_notes,
    ensure_allowance_categories,
    allowance_categories_in_sync,
    get_household_finance_settings,
    adjust_household_projects_funds,
    apply_projects_funds_year_rollover,
    reconstruct_projects_funds_opening,
    get_project_expense_totals_for_year,
    get_household_users_for_admin,
    get_wish_list_items,
    insert_wish_list_item,
    update_wish_list_item,
    delete_wish_list_item,
    complete_wish_list_item,
    restore_wish_list_item,
    get_household_incomes, 
    get_monthly_expenses,
    get_expenses_for_period,
    get_household_incomes_for_period,
    get_distinct_budget_years,
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
    normalize_income_amount_for_month,
    income_amount_for_month_total,
    school_year_active_month,
    get_individual_expenses,
    update_user_privacy_toggle,
    delete_budget_category,
    delete_household_income,
    delete_household_income_month_only,
    schedule_income_change,
    end_income_stream,
    get_income_stream_versions,
    ensure_income_stream_for_row,
    delete_expense,
    update_expense,
    update_budget_category,
    auto_rollover_recurring_expenses,
    auto_rollover_recurring_incomes,
    get_expense_stream_projections,
    expense_pay_frequency_label,
    normalize_expense_pay_frequency,
    schedule_expense_change,
    end_expense_stream,
    get_expense_stream_versions,
    delete_expense_month_only,
    compute_household_obligations,
    upsert_parent_assignment,
    clear_parent_assignment,
    upsert_subcategory_override,
    clear_subcategory_override,
    compute_household_disbursement_plan,
    get_disbursement_allowance_surplus_flags,
    get_disbursement_automation_audit_flags,
    get_household_income_stream_options,
    get_member_transfers,
    upsert_planned_transfers_from_schedule,
    reset_disbursement_plan_transfers,
    cleanup_orphan_disbursement_artifacts,
    auto_complete_due_member_transfers,
    complete_due_member_transfers,
    get_due_planned_member_transfers,
    ensure_completed_transfer_allowance_expenses,
    repair_all_disbursement_allowance_incomes,
    repair_disbursement_allowance_incomes,
    auto_materialize_disbursement_plan,
    sync_disbursement_plan,
    get_disbursement_reconciliation,
    get_disbursement_plan_drift,
    acknowledge_disbursement_plan,
    get_disbursement_readiness,
    ensure_member_funding_streams_defaults,
    complete_member_transfer,
    update_personal_household_integration,
    get_personal_household_integration,
    get_personal_ledger_incomes,
    get_personal_ledger_expenses,
    get_member_obligation_parent_names,
    get_member_obligation_expense_categories,
    get_member_obligation_inactive_subcategories,
    insert_obligation_subcategory,
    deactivate_obligation_subcategory,
    reactivate_obligation_subcategory,
    log_household_expense_from_personal,
    get_member_funding_streams,
    set_member_funding_streams,
)
from ui_helpers import (
    rerun_app_with_reason,
    rerun_fragment_with_reason,
    rerun_with_reason,
    manage_popover_key,
    finish_manage_popover,
    arm_delete_confirm,
    is_delete_confirm_armed,
    render_delete_confirmation,
    render_metrics_grid,
    render_signed_currency_metric,
    render_checkbox_grid,
    render_two_col_selector,
)
from constants import (
    allowance_recipient_username,
    is_system_project_expense_category,
    is_system_managed_allowance_category,
    is_allowance_subcategory,
    TRANSFER_ALLOWANCE_EXPENSE_DETAILS,
)


def _clear_disbursement_session_guards(household_id, month_year) -> None:
    """Clear per-session automation guards so the next render re-runs automation."""
    prefixes = (
        f"disburse_materialized_{household_id}_",
        f"transfer_allowance_expenses_{household_id}_",
        f"disbursement_orphan_cleanup_{household_id}_",
        f"disburse_income_repair_{household_id}_",
        f"rollover_checked_{household_id}_",
        f"transfers_auto_completed_{household_id}_",
    )
    for key in list(st.session_state.keys()):
        if any(key.startswith(prefix) for prefix in prefixes):
            st.session_state.pop(key, None)
        if key == f"disbursement_editor_rev_{household_id}_{month_year}":
            st.session_state.pop(key, None)


def _arm_disbursement_reset_autocomplete_hold(household_id) -> None:
    """Block auto-complete for the rest of this session after a manual plan reset."""
    tz = ZoneInfo("America/Chicago")
    today = datetime.now(tz).date().isoformat()
    st.session_state[f"transfers_auto_completed_{household_id}_{today}"] = True


# Bump when allowance income repair logic changes so existing sessions re-run once.
DISBURSEMENT_INCOME_REPAIR_VERSION = 5


def maybe_run_disbursement_income_repair(household_id, selected_month=None) -> None:
    """Repair transfer-linked Allowance personal incomes (all household members)."""
    if not household_id:
        return
    tz = ZoneInfo("America/Chicago")
    month_year = selected_month or datetime.now(tz).strftime("%Y-%m")
    guard = (
        f"disburse_income_repair_{household_id}_{month_year}"
        f"_v{DISBURSEMENT_INCOME_REPAIR_VERSION}"
    )
    if st.session_state.get(guard):
        return
    repair_disbursement_allowance_incomes(household_id, month_year)
    st.session_state[guard] = True


def _run_disbursement_automation_server_side(household_id, month_year) -> int:
    """Run plan-sync + completion for one month without requiring an admin session.

    Returns the number of meaningful changes made (rows inserted, updated, deleted,
    or transfers completed). Safe to call from any user's session.
    """
    if not household_id or not month_year:
        return 0

    tz = ZoneInfo("America/Chicago")
    cur_month = datetime.now(tz).strftime("%Y-%m")
    cur_year, cur_month_int = map(int, cur_month.split("-"))
    next_month = (
        f"{cur_year + 1}-01" if cur_month_int == 12
        else f"{cur_year}-{cur_month_int + 1:02d}"
    )

    changes = 0

    ensure_member_funding_streams_defaults(household_id)

    # 1. Stale-check current month (freeze: no row changes if rows exist)
    result_current = sync_disbursement_plan(household_id, cur_month)
    changes += result_current.get("inserted", 0) + result_current.get("updated", 0)

    # 2. Full sync for next month (insert/update/delete planned rows)
    result_next = sync_disbursement_plan(household_id, next_month)
    changes += (
        result_next.get("inserted", 0)
        + result_next.get("updated", 0)
        + result_next.get("deleted", 0)
    )

    # 3. Auto-complete due transfers
    changes += auto_complete_due_member_transfers(household_id)

    # 4. Ensure completed transfer allowance expenses
    changes += ensure_completed_transfer_allowance_expenses(household_id, month_year)

    # 5. Cleanup orphan disbursement artifacts
    orphan_stats = cleanup_orphan_disbursement_artifacts(household_id, month_year)
    changes += (orphan_stats.get("expenses", 0) or 0) + (orphan_stats.get("incomes", 0) or 0)

    # 6. Repair transfer-linked Allowance incomes
    repair_disbursement_allowance_incomes(household_id, month_year)

    return changes


def maybe_run_household_automation(household_id, selected_month=None, *, rerun_scope: str = "fragment") -> None:
    """Run rollover, disbursement plan sync, and due transfer completion once per session."""
    if not household_id:
        return
    tz = ZoneInfo("America/Chicago")
    month_year = selected_month or datetime.now(tz).strftime("%Y-%m")
    today = datetime.now(tz).date().isoformat()
    changed = False

    # Expense + income rollover
    guard_key = f"rollover_checked_{household_id}_{month_year}"
    if not st.session_state.get(guard_key):
        expense_rolled = auto_rollover_recurring_expenses(household_id, month_year)
        income_rolled = auto_rollover_recurring_incomes(household_id, month_year)
        st.session_state[guard_key] = True
        changed = changed or bool(expense_rolled or income_rolled)

    # Disbursement sync (current month stale-check + next month full sync)
    disburse_guard = f"disburse_materialized_{household_id}_{month_year}"
    if not st.session_state.get(disburse_guard):
        n = _run_disbursement_automation_server_side(household_id, month_year)
        st.session_state[disburse_guard] = True
        # Also mark the per-day transfer completion guard so we don't double-complete
        st.session_state[f"transfers_auto_completed_{household_id}_{today}"] = True
        changed = changed or n > 0

    # Backfill transfer allowance expenses (idempotent catch-up)
    backfill_guard = f"transfer_allowance_expenses_{household_id}_{month_year}"
    if not st.session_state.get(backfill_guard):
        backfilled = ensure_completed_transfer_allowance_expenses(household_id, month_year)
        st.session_state[backfill_guard] = True
        changed = changed or backfilled > 0

    # Orphan cleanup
    orphan_guard = f"disbursement_orphan_cleanup_{household_id}_{month_year}"
    if not st.session_state.get(orphan_guard):
        orphan_stats = cleanup_orphan_disbursement_artifacts(household_id, month_year)
        st.session_state[orphan_guard] = True
        changed = changed or bool(orphan_stats.get("expenses") or orphan_stats.get("incomes"))

    # Repair transfer-linked Allowance incomes for the active month (idempotent)
    maybe_run_disbursement_income_repair(household_id, month_year)

    if changed:
        rerun_with_reason("household_automation", scope=rerun_scope)


def _maybe_auto_rollover(household_id, selected_month):
    """Backward-compatible alias for household session automation."""
    maybe_run_household_automation(household_id, selected_month)


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


def _exclude_allowance_categories(categories_df):
    """Hide system-managed Allowance sub-categories from manual HH expense pickers."""
    if categories_df is None or categories_df.empty:
        return categories_df
    mask = ~categories_df.apply(
        lambda row: is_allowance_subcategory(
            row.get("category_name"),
            row.get("sub_category_name"),
        ),
        axis=1,
    )
    return categories_df[mask].copy()


def _is_transfer_allowance_expense_row(row) -> bool:
    return (str(row.get("details") or "").strip() == TRANSFER_ALLOWANCE_EXPENSE_DETAILS)


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


def _render_project_expense_entries(entries: list[dict], container=None) -> None:
    target = container or st
    if not entries:
        target.markdown("**Expenses:** —")
        return
    target.markdown("**Expenses:**")
    for entry in entries:
        label = entry.get("display") or ""
        if entry.get("is_legacy"):
            target.caption(f"{label} (legacy)")
        else:
            target.caption(label)


def _project_purchase_expense_label(row, project_name: str) -> str:
    date_str = str(row.get("date_logged") or "")[:10]
    product = _project_expense_product_from_details(row.get("details"), project_name)
    return format_project_purchase_expense_line(date_str, row.get("amount"), product)


def _clear_project_expense_add_form(project_id) -> None:
    """Reset Add Expense fields so another purchase can be logged in a row."""
    for key in (
        f"proj_exp_date_{project_id}",
        f"proj_exp_product_{project_id}",
        f"proj_exp_amt_{project_id}",
    ):
        st.session_state.pop(key, None)


def _render_project_expense_manage_popover(
    *,
    project_id,
    project_name: str,
    projects_household_id,
    project_popover_key: str,
    mode: str,
):
    project_expenses_df = get_project_purchase_expenses(projects_household_id, project_id)

    if mode == "add":
        st.caption("Adds to this project's Actual Spent and records a Projects line in the household budget.")
        with st.form(f"add_project_expense_form_{project_id}"):
            exp_date = st.date_input(
                "Purchase date",
                value=date.today(),
                key=f"proj_exp_date_{project_id}",
            )
            exp_product = st.text_input(
                "Product or Service",
                placeholder="e.g., Lumber, paint, contractor labor",
                key=f"proj_exp_product_{project_id}",
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
            elif add_project_purchase_expense(
                project_id,
                exp_date,
                parsed_exp_amount,
                product_or_service=exp_product,
            ):
                _clear_project_expense_add_form(project_id)
                rerun_fragment_with_reason("project_expense_write")
            else:
                st.error("Could not log project expense.")
        return

    if project_expenses_df is None or project_expenses_df.empty:
        st.caption("No logged expenses for this project yet.")
        st.caption("Legacy purchases shown on the card are read-only and cannot be edited here.")
        return

    picker_df = project_expenses_df.sort_values("date_logged", ascending=False).copy()
    expense_labels = picker_df.apply(
        lambda row: _project_purchase_expense_label(row, project_name),
        axis=1,
    ).tolist()
    selected_label = st.selectbox(
        "Select expense",
        expense_labels,
        key=f"proj_exp_edit_select_{project_id}",
    )
    target_row = picker_df.iloc[expense_labels.index(selected_label)]
    exp_id = target_row["id"]
    try:
        row_date = datetime.strptime(
            str(target_row["date_logged"])[:10], "%Y-%m-%d"
        ).date()
    except (ValueError, TypeError):
        row_date = date.today()
    product_value = _project_expense_product_from_details(
        target_row.get("details"), project_name
    )

    with st.form(f"edit_project_expense_form_{project_id}_{exp_id}"):
        edit_date = st.date_input(
            "Purchase date",
            value=row_date,
            key=f"proj_exp_edit_date_{project_id}_{exp_id}",
        )
        edit_product = st.text_input(
            "Product or Service",
            value=product_value,
            key=f"proj_exp_edit_product_{project_id}_{exp_id}",
        )
        edit_amount_raw = st.text_input(
            "Amount ($) *",
            value=_format_currency_for_input(target_row.get("amount")),
            key=f"proj_exp_edit_amt_{project_id}_{exp_id}",
        )
        save_exp = st.form_submit_button("💾 Save Expense", type="primary", width="stretch")
        delete_exp = st.form_submit_button("🗑️ Delete Expense", width="stretch")

    if save_exp:
        parsed_edit_amount = _parse_currency_input(edit_amount_raw)
        if parsed_edit_amount == "invalid" or parsed_edit_amount is None:
            st.error("Please enter a valid dollar amount.")
        elif update_project_purchase_expense(
            exp_id,
            edit_date,
            parsed_edit_amount,
            product_or_service=edit_product,
        ):
            finish_manage_popover("project_expense_edit", project_popover_key, scope="fragment")
        else:
            st.error("Could not update expense.")

    if delete_exp:
        if delete_project_purchase_expense(exp_id):
            finish_manage_popover("project_expense_delete", project_popover_key, scope="fragment")
        else:
            st.error("Could not delete expense.")


def _format_expense_category_label(row) -> str:
    cat = row.get("category_name") or "—"
    sub = row.get("sub_category_name")
    if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() in ("", "—"):
        base = str(cat)
    else:
        base = f"{cat} > {sub}"
    if row.get("ledger_source") == "household_obligation":
        return f"{base} [Household obligation]"
    return base


def _sort_subcategory_rows(sub_rows: list[dict]) -> list[dict]:
    """Alphabetical sub-category order within a parent category on ledgers."""
    return sorted(sub_rows, key=lambda row: str(row.get("name", "")).lower())


def _normalize_sub_category_label(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return "(General)"
    return str(value)


def _sorted_parent_category_names(values) -> list[str]:
    names = {
        str(name)
        for name in values
        if name is not None
        and not (isinstance(name, float) and pd.isna(name))
        and str(name).strip()
    }
    return sorted(names, key=str.lower)


def _sort_categories_dataframe(categories_df: pd.DataFrame) -> pd.DataFrame:
    """Sort budget categories by parent name, then sub-category."""
    if categories_df is None or categories_df.empty:
        return categories_df
    df = categories_df.copy()
    df["_sort_cat"] = df["category_name"].map(lambda value: str(value or "").lower())
    df["_sort_sub"] = df["sub_category_name"].map(
        lambda value: _normalize_sub_category_label(value).lower()
    )
    return (
        df.sort_values(["_sort_cat", "_sort_sub"], kind="stable")
        .drop(columns=["_sort_cat", "_sort_sub"])
        .reset_index(drop=True)
    )


def _format_income_source_with_badge(row) -> str:
    source = row.get("source_name", "—")
    ledger_source = row.get("ledger_source", "personal")
    if ledger_source == "household_mirror":
        return f"{source} [Household]"
    if ledger_source == "transfer":
        return f"{source} [Transfer]"
    return str(source)


def _build_obligation_category_display_name(row) -> str:
    sub = row.get("sub_category_name")
    parent = row.get("category_name") or ""
    if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
        return f"{parent} [Household obligation]"
    return f"{parent} → {sub} [Household obligation]"


# ---------------------------------------------------------------------------
# Shared expense-picker builders (used by Budget module + Quick Expense page)
# ---------------------------------------------------------------------------

def build_household_expense_picker_df(household_id):
    """Return a sorted, display-ready DataFrame of HH categories for admins.

    Returns an empty DataFrame when no eligible categories exist.
    Columns guaranteed: id, category_name, sub_category_name, display_name.
    """
    ensure_household_taxes_category(household_id)
    cats = get_budget_categories(household_id, is_personal=False)
    if cats is None or cats.empty:
        return pd.DataFrame()
    user_cats = _exclude_allowance_categories(_exclude_system_categories(cats))
    if user_cats is None or user_cats.empty:
        return pd.DataFrame()
    return _prepare_sorted_category_picker(user_cats)


def build_personal_expense_picker_df(
    household_id,
    username,
    *,
    integrated: bool,
    include_member_obligations: bool | None = None,
):
    """Return a combined picker DataFrame for personal expense entry.

    Always includes personal categories. When `integrated` is True (or
    `include_member_obligations` is True), appends obligation HH categories
    tagged with `is_household_obligation=True`.
    Returns an empty DataFrame when nothing is available.
    """
    ensure_personal_taxes_category(household_id, username)
    frames = []

    personal_cats = get_budget_categories(household_id, is_personal=True, username=username)
    if personal_cats is not None and not personal_cats.empty:
        picker = _prepare_sorted_category_picker(personal_cats).copy()
        picker["is_household_obligation"] = False
        frames.append(picker)

    show_obligations = integrated if include_member_obligations is None else include_member_obligations
    if show_obligations:
        obl_cats = get_member_obligation_expense_categories(household_id, username)
        if obl_cats is not None and not obl_cats.empty:
            obl = _sort_categories_dataframe(obl_cats).copy()
            obl["display_name"] = obl.apply(_build_obligation_category_display_name, axis=1)
            obl["is_household_obligation"] = True
            frames.append(obl)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def submit_expense_from_picker(
    *,
    cat_row,
    date_logged,
    amount: float,
    details: str,
    pay_frequency: str,
    household_id: str,
    auth_user_id: str,
    username: str,
    is_household_admin: bool,
) -> tuple[bool, str]:
    """Log an expense from a picker row; return (success, human-readable message).

    Routes to the correct log function based on category type and role:
    - HH admin + non-obligation category → shared HH ledger
    - integrated member + obligation category → shared HH ledger via personal path
    - personal category → personal ledger
    """
    month_year = date_logged.strftime("%Y-%m")
    is_obl = bool(cat_row.get("is_household_obligation"))
    is_hh = bool(cat_row.get("is_personal_spend") is False) if "is_personal_spend" in cat_row else False

    if is_household_admin and not is_obl:
        ok = log_expense_and_check_project(
            auth_user_id=auth_user_id,
            username=username,
            household_id=household_id,
            month_year=month_year,
            date_logged=date_logged,
            category_id=int(cat_row["id"]),
            amount=amount,
            details=details,
            is_personal_spend=False,
            is_recurring=pay_frequency != "one_time",
            pay_frequency=pay_frequency,
        )
        return ok, f"Logged ${_format_money(amount)} to Household Ledger."

    if is_obl:
        ok = log_household_expense_from_personal(
            auth_user_id=auth_user_id,
            username=username,
            household_id=household_id,
            month_year=month_year,
            date_logged=date_logged,
            category_id=int(cat_row["id"]),
            amount=amount,
            details=details,
            is_recurring=pay_frequency != "one_time",
            pay_frequency=pay_frequency,
        )
        return ok, f"Logged ${_format_money(amount)} to Household Ledger."

    ok = log_expense_and_check_project(
        auth_user_id=auth_user_id,
        username=username,
        household_id=household_id,
        month_year=month_year,
        date_logged=date_logged,
        category_id=int(cat_row["id"]),
        amount=amount,
        details=details,
        is_personal_spend=True,
        is_recurring=pay_frequency != "one_time",
        pay_frequency=pay_frequency,
    )
    return ok, f"Logged ${_format_money(amount)} to Personal Ledger."


def _build_category_display_name(row) -> str:
    sub = row.get("sub_category_name")
    parent = row.get("category_name") or ""
    if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
        return str(parent)
    return f"{parent} - {sub}"


def _build_category_edit_label(row) -> str:
    budget = float(row.get("target_budget") or 0.0)
    return f"{_build_category_display_name(row)} (${budget:,.2f}/mo)"


def _prepare_sorted_category_picker(categories_df: pd.DataFrame) -> pd.DataFrame:
    sorted_df = _sort_categories_dataframe(categories_df)
    if sorted_df is None or sorted_df.empty:
        return sorted_df
    out = sorted_df.copy()
    out["display_name"] = out.apply(_build_category_display_name, axis=1)
    return out


def _sort_dataframe_column_case_insensitive(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df is None or df.empty or column not in df.columns:
        return df
    return df.sort_values(
        column,
        key=lambda series: series.map(lambda value: str(value or "").lower()),
        kind="stable",
    )


INCOME_FREQUENCY_OPTIONS = list(INCOME_PAY_FREQUENCY_LABELS.keys())
EXPENSE_FREQUENCY_OPTIONS = [f for f in INCOME_FREQUENCY_OPTIONS if f != "school_year_monthly"]


def _render_html_scroll_table(headers, rows, *, right_align_from: int = 1, variant: str = "") -> None:
    """Render a budget grid as one HTML table (works on narrow mobile screens)."""
    wrap_classes = "hs-budget-table-wrap"
    if variant:
        wrap_classes += f" {html.escape(variant)}"
    head_cells = []
    for idx, label in enumerate(headers):
        cls = "num" if idx >= right_align_from else ""
        head_cells.append(f'<th class="{cls}">{html.escape(str(label))}</th>')
    body_parts = []
    for row in rows:
        cells = row.get("cells", [])
        tr_classes = []
        if row.get("emphasize"):
            tr_classes.append("emphasis")
        if row.get("parent"):
            tr_classes.append("parent")
        if row.get("indent"):
            tr_classes.append("indent")
        tr_attr = f' class="{" ".join(tr_classes)}"' if tr_classes else ""
        td_parts = []
        for idx, cell in enumerate(cells):
            cls = "num" if idx >= right_align_from else ""
            td_parts.append(f'<td class="{cls}">{html.escape(str(cell))}</td>')
        body_parts.append(f"<tr{tr_attr}>{''.join(td_parts)}</tr>")
    st.markdown(
        f'<div class="{wrap_classes}"><table class="hs-budget-table">'
        f"<thead><tr>{''.join(head_cells)}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


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


def _category_projected_amount(row, *, month_count=1) -> float:
    """Projected monthly cost from category target_budget (category management only)."""
    return float(row.get("target_budget") or 0) * month_count


def _render_household_budget_breakdown(
    merged_df,
    hh_expenses_df,
    recurring_schedule,
    selected_month,
    *,
    filter_key: str,
):
    if merged_df.empty:
        st.info("No categories setup yet. Add some to build your ledger!")
        return

    year, month = map(int, selected_month.split("-"))
    purchase_counts = _purchase_counts_by_category(hh_expenses_df, selected_month)

    parent_groups = []
    for parent in _sorted_parent_category_names(merged_df["category_name"].unique()):
        parent_mask = merged_df["category_name"] == parent
        parent_target = float(
            sum(_category_projected_amount(row) for _, row in merged_df[parent_mask].iterrows())
        )
        parent_actual = float(merged_df.loc[parent_mask, "actual_amount"].sum())
        if parent_target == 0 and parent_actual == 0:
            continue

        sub_rows = []
        parent_purchase_count = 0
        for _, row in merged_df[parent_mask].iterrows():
            target = _category_projected_amount(row)
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

            if (
                not hh_expenses_df.empty
                and "category_id" in hh_expenses_df.columns
                and "is_recurring" in hh_expenses_df.columns
            ):
                recurring_items = hh_expenses_df[
                    (hh_expenses_df["category_id"] == cat_id) & (hh_expenses_df["is_recurring"] == True)
                ]
            else:
                recurring_items = hh_expenses_df
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

        sub_rows = _sort_subcategory_rows(sub_rows)

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

    table_rows = []
    for index, group in enumerate(visible_groups):
        table_rows.append(
            {
                "cells": [
                    group["name"],
                    _format_purchase_count(group.get("purchase_count", 0)),
                    _format_ledger_amount(group["projected"]),
                    _format_ledger_amount(group["actual"]),
                    _format_ledger_diff(group["projected"], group["actual"]),
                ],
                "parent": True,
            }
        )
        for sub in group["subs"]:
            table_rows.append(
                {
                    "cells": [
                        sub["name"],
                        _format_purchase_count(sub.get("purchase_count", 0)),
                        _format_ledger_amount(sub["projected"]),
                        _format_ledger_amount(sub["actual"]),
                        _format_ledger_diff(sub["projected"], sub["actual"]),
                    ],
                    "indent": True,
                }
            )

    total_projected = sum(float(group["projected"]) for group in visible_groups)
    total_actual = sum(float(group["actual"]) for group in visible_groups)
    total_purchases = sum(int(group.get("purchase_count", 0)) for group in visible_groups)
    table_rows.append(
        {
            "cells": [
                "Total",
                _format_purchase_count(total_purchases),
                _format_ledger_amount(total_projected),
                _format_ledger_amount(total_actual),
                _format_ledger_diff(total_projected, total_actual),
            ],
            "emphasize": True,
        }
    )

    _render_html_scroll_table(
        ["Category", "Qty", "Projected", "Actual", "Difference"],
        table_rows,
        right_align_from=1,
        variant="ledger",
    )


def _format_payment_date(value) -> str:
    if not value:
        return "—"
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%b %d, %Y")


def _render_signed_currency_metric(column, label: str, amount: float) -> None:
    """Metric-style display with green for positive and red for negative amounts."""
    with column:
        render_signed_currency_metric(label, amount)


def _render_annual_income_metrics(annual_totals: dict) -> None:
    render_metrics_grid([
        {"label": "Annual Take-Home", "value": f"${annual_totals['annual_takehome']:,.2f}"},
        {"label": "Annual Gross", "value": f"${annual_totals['annual_gross']:,.2f}"},
        {"label": "Annual Taxable", "value": f"${annual_totals['annual_taxable']:,.2f}"},
        {"label": "Annual Non-Taxable", "value": f"${annual_totals['annual_non_taxable']:,.2f}"},
    ], desktop_columns=4)


def _render_income_streams_list(incomes_df, *, is_personal=False, annual_totals=None):
    if incomes_df is None or incomes_df.empty:
        st.info("No income logged for this month.")
        if annual_totals:
            st.divider()
            _render_annual_income_metrics(annual_totals)
        return

    if is_personal:
        headers = ["Source", "Net (Per Payment)", "Gross (Per Payment)", "Frequency", "Payment Date"]
    else:
        headers = ["Source", "Earner", "Net (Per Payment)", "Gross (Per Payment)", "Frequency", "Payment Date"]

    table_rows = []
    for _, row in incomes_df.iterrows():
        source_label = _format_income_source_with_badge(row) if "ledger_source" in incomes_df.columns else row.get("source_name", "—")
        cells = [source_label]
        if not is_personal:
            cells.append(row.get("owner_username", "—"))
        cells.extend([
            _format_ledger_amount(row.get("take_home_amount", 0)),
            _format_ledger_amount(row.get("gross_amount", 0)),
            income_pay_frequency_label(row.get("pay_frequency")),
            _format_payment_date(row.get("payment_date")),
        ])
        table_rows.append({"cells": cells})

    _render_html_scroll_table(
        headers,
        table_rows,
        right_align_from=1 if is_personal else 2,
        variant="income",
    )

    if annual_totals:
        st.divider()
        _render_annual_income_metrics(annual_totals)


def _render_sinking_funds_list(annual_df) -> None:
    if annual_df.empty:
        return

    st.caption(
        "Monthly set-aside is the amount budgeted each month. Annual cost is that monthly amount × 12."
    )

    table_rows = []
    for _, row in _sort_categories_dataframe(annual_df).iterrows():
        sub = row["sub_category_name"]
        if sub is None or (isinstance(sub, float) and pd.isna(sub)) or str(sub).strip() == "":
            sub = "(General)"
        else:
            sub = str(sub)
        monthly_target = float(row["target_budget"])
        annual_target = monthly_target * 12
        table_rows.append(
            {
                "cells": [
                    sub,
                    _format_ledger_amount(monthly_target),
                    _format_ledger_amount(annual_target),
                ],
            }
        )

    _render_html_scroll_table(
        ["Subscription", "Monthly set-aside", "Annual cost"],
        table_rows,
        right_align_from=1,
        variant="sinking",
    )


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
    edit_picker_df = enriched.copy()
    if can_edit:
        edit_picker_df = edit_picker_df[
            ~edit_picker_df.apply(_is_transfer_allowance_expense_row, axis=1)
        ]
    edit_picker_df["_sort_cat"] = edit_picker_df["category_name"].map(
        lambda value: str(value or "").lower()
    )
    edit_picker_df["_sort_sub"] = edit_picker_df["sub_category_name"].map(
        lambda value: _normalize_sub_category_label(value).lower()
    )
    edit_picker_df = edit_picker_df.sort_values(
        ["_sort_cat", "_sort_sub", "date_logged"],
        ascending=[True, True, False],
        kind="stable",
    ).drop(columns=["_sort_cat", "_sort_sub"])

    table_rows = []
    for _, row in sorted_df.iterrows():
        freq = normalize_expense_pay_frequency(
            row.get("pay_frequency")
            or ("monthly" if row.get("is_recurring") else "one_time")
        )
        recurring_tag = (
            f" 🔄 {expense_pay_frequency_label(freq)}" if freq != "one_time" else ""
        )
        details_text = f"{row.get('details', '') or ''}{recurring_tag}".strip() or "—"
        table_rows.append(
            {
                "cells": [
                    row["date_logged"],
                    row.get("category_name", "—"),
                    row.get("sub_category_name", "—"),
                    details_text,
                    _format_ledger_amount(row["amount"]),
                ],
            }
        )

    _render_html_scroll_table(
        ["Date", "Category", "Sub-category", "Details", "Amount"],
        table_rows,
        right_align_from=4,
        variant="expense",
    )

    if not can_edit:
        return

    if edit_picker_df.empty:
        st.caption("System-managed transfer expenses cannot be edited here.")
        return

    def _expense_label(row):
        cat_label = _format_expense_category_label(row)
        amt = _format_ledger_amount(row.get("amount", 0))
        det = (row.get("details") or "").strip() or "—"
        dt = row.get("date_logged", "—")
        return f"{cat_label} · {amt} · {det} · {dt}"

    edit_options = edit_picker_df.apply(_expense_label, axis=1).tolist()
    with st.expander("🛠️ Edit or Delete Expense", expanded=False):
        selected_label = st.selectbox(
            "Select expense",
            edit_options,
            key=f"{key_prefix}_edit_select",
        )
        selected_idx = edit_options.index(selected_label)
        target_row = edit_picker_df.iloc[selected_idx]
        exp_id = target_row["id"]
        stream_id = target_row.get("stream_id")
        if stream_id and pd.notna(stream_id):
            stream_id = str(stream_id)
        else:
            stream_id = None

        current_freq = normalize_expense_pay_frequency(
            target_row.get("pay_frequency")
            or ("monthly" if target_row.get("is_recurring") else "one_time")
        )

        if stream_id:
            versions = get_expense_stream_versions(stream_id)
            if versions:
                with st.expander("Version history", expanded=False):
                    for ver in versions:
                        eff = ver.get("effective_from", "")[:10]
                        amt = _to_number(ver.get("amount"))
                        freq = expense_pay_frequency_label(ver.get("pay_frequency"))
                        st.caption(f"From {eff}: ${amt:,.2f} · {freq}")

        edit_scope = "This occurrence only"
        if current_freq != "one_time":
            edit_scope = st.radio(
                "Apply changes",
                (
                    "From effective date forward",
                    "This occurrence only",
                    "End stream",
                ),
                key=f"expense_edit_scope_{key_prefix}_{exp_id}",
            )

        form_key = f"edit_form_{key_prefix}_{exp_id}"
        try:
            row_date = datetime.strptime(
                str(target_row["date_logged"])[:10], "%Y-%m-%d"
            ).date()
        except (ValueError, TypeError):
            row_date = date.today()

        with st.form(form_key):
            st.markdown(f"**Category:** {_format_expense_category_label(target_row)}")
            new_amt = st.text_input("Amount ($)", value=str(target_row["amount"]))
            new_det = st.text_input("Details", value=target_row.get("details") or "")
            if edit_scope == "From effective date forward":
                st.caption(
                    "New terms apply to this date and every future bill for this "
                    "recurring expense."
                )
                effective_from = st.date_input(
                    "New terms start on",
                    value=row_date,
                    key=f"exp_edit_effective_{key_prefix}_{exp_id}",
                )
                new_date = effective_from
            else:
                new_date = st.date_input("Date", value=row_date)
                effective_from = new_date
            if edit_scope != "End stream":
                new_freq = st.selectbox(
                    "Frequency",
                    EXPENSE_FREQUENCY_OPTIONS,
                    format_func=expense_pay_frequency_label,
                    index=EXPENSE_FREQUENCY_OPTIONS.index(current_freq)
                    if current_freq in EXPENSE_FREQUENCY_OPTIONS
                    else EXPENSE_FREQUENCY_OPTIONS.index("monthly"),
                    key=f"exp_edit_freq_{key_prefix}_{exp_id}",
                )
            else:
                new_freq = current_freq
            save_clicked = st.form_submit_button("💾 Save Changes", type="primary", width="stretch")

        if save_clicked:
            parsed_amt = _parse_currency_input(new_amt)
            if parsed_amt == "invalid":
                st.error("Invalid amount.")
            elif edit_scope == "End stream":
                if end_expense_stream(exp_id):
                    rerun_fragment_with_reason("budget_nav")
                else:
                    st.error("Could not end expense stream.")
            elif edit_scope == "From effective date forward" and current_freq != "one_time":
                eff_key = f"exp_edit_effective_{key_prefix}_{exp_id}"
                effective_from_val = st.session_state.get(eff_key, new_date)
                if schedule_expense_change(
                    exp_id,
                    effective_from_val,
                    parsed_amt,
                    new_det.strip(),
                    new_freq,
                ):
                    rerun_fragment_with_reason("budget_nav")
                else:
                    st.error("Could not schedule expense change.")
            elif update_expense(
                exp_id,
                parsed_amt,
                new_det.strip(),
                new_freq != "one_time",
                date_logged=new_date,
                pay_frequency=new_freq,
            ):
                rerun_fragment_with_reason("budget_nav")

        delete_scope = "This occurrence only"
        if stream_id or current_freq != "one_time":
            delete_scope = st.radio(
                "Delete scope",
                ("This occurrence only", "End stream"),
                key=f"expense_delete_scope_{key_prefix}_{exp_id}",
                horizontal=True,
            )

        expense_delete_key = f"expense_{key_prefix}_{exp_id}"
        if st.button("❌ Delete Expense", key=f"del_{key_prefix}_{exp_id}", type="secondary", width="stretch"):
            arm_delete_confirm(expense_delete_key)
            rerun_fragment_with_reason("delete_arm")

        if render_delete_confirmation(expense_delete_key, item_label=selected_label, rerun_scope="fragment"):
            if delete_scope == "End stream" and current_freq != "one_time":
                ok = end_expense_stream(exp_id)
            else:
                ok = delete_expense_month_only(exp_id)
            if ok:
                rerun_fragment_with_reason("delete_expense")


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
                owner_username = fixed_owner
                if not is_personal:
                    owner_username = st.selectbox(
                        "Assign to Earner", earner_options or ["unassigned"]
                    )

                a1, a2 = st.columns([1, 1])
                payment_date = a1.date_input("Date", value=date.today())
                take_home_raw = a2.text_input("Take-Home (Net) $ *")

                gross_raw = st.text_input("Gross $")

                source_name = st.text_input(
                    "Details",
                    placeholder="e.g., Paycheck, Side Gig, Bonus"
                    if not is_personal
                    else "e.g., Side Hustle, Dividends",
                )

                is_taxable = st.checkbox("Is Taxable?", value=True)

                pay_frequency = st.selectbox(
                    "Frequency",
                    INCOME_FREQUENCY_OPTIONS,
                    format_func=income_pay_frequency_label,
                    index=INCOME_FREQUENCY_OPTIONS.index("monthly"),
                )
                if pay_frequency == "school_year_monthly":
                    st.caption(
                        "Regular paychecks run Sep–Jun on this day each month. "
                        "Add two **One-time** incomes in July and August for summer checks."
                    )

                if st.form_submit_button("💾 Save Income", type="primary", width="stretch"):
                    th_val = _parse_currency_input(take_home_raw)
                    g_val = _parse_currency_input(gross_raw) if gross_raw.strip() else th_val
                    if not source_name.strip() or th_val == "invalid" or g_val == "invalid":
                        st.error("Please provide details and valid take-home and gross amounts.")
                    elif insert_household_income(
                        household_id,
                        selected_month,
                        source_name.strip(),
                        th_val,
                        g_val,
                        is_taxable,
                        owner_username,
                        False,
                        pay_frequency,
                        is_personal_income=is_personal,
                        payment_date=payment_date,
                    ):
                        rerun_fragment_with_reason("budget_nav")

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
                det = (row.get("source_name") or "").strip() or "—"
                pay_date = _format_payment_date(row.get("payment_date"))
                pay_suffix = f" · {pay_date}" if pay_date != "—" else ""
                if is_personal:
                    return f"{det} · {freq} · ${_format_money(amount)}{pay_suffix}"
                return f"{det} ({owner}) · {freq} · ${_format_money(amount)}{pay_suffix}"

            edit_options = editable_df.apply(_income_label, axis=1).tolist()
            selected_edit_str = st.selectbox(
                "Select income",
                edit_options,
                key=f"edit_{form_key_prefix}_income_select",
            )
            selected_edit_idx = edit_options.index(selected_edit_str)
            target_row = editable_df.iloc[selected_edit_idx]
            target_income_id = target_row["id"]
            stream_id = target_row.get("stream_id")
            if stream_id and pd.notna(stream_id):
                stream_id = str(stream_id)
            else:
                stream_id = None

            current_freq = normalize_income_pay_frequency(target_row.get("pay_frequency"))
            edit_owner = fixed_owner or target_row.get("owner_username")

            if stream_id:
                versions = get_income_stream_versions(stream_id)
                if versions:
                    with st.expander("Version history", expanded=False):
                        for ver in versions:
                            eff = ver.get("effective_from", "")[:10]
                            amt = _to_number(ver.get("take_home_amount"))
                            freq = income_pay_frequency_label(ver.get("pay_frequency"))
                            st.caption(f"From {eff}: ${amt:,.2f} · {freq}")

            edit_scope = "This occurrence only"
            if current_freq != "one_time":
                edit_scope = st.radio(
                    "Apply changes",
                    (
                        "From effective date forward",
                        "This occurrence only",
                    ),
                    key=f"income_edit_scope_{form_key_prefix}_{target_income_id}",
                    help="Forward changes preserve past months.",
                )

            try:
                row_date = datetime.strptime(
                    str(target_row.get("payment_date") or target_row.get("date_logged") or "")[:10],
                    "%Y-%m-%d",
                ).date()
            except (ValueError, TypeError):
                row_date = date.today()

            form_key = f"edit_{form_key_prefix}_income_form_{target_income_id}"
            with st.form(form_key, clear_on_submit=False):
                if not is_personal:
                    st.markdown(f"**Earner:** {target_row.get('owner_username', '—')}")

                e1, e2 = st.columns(2)
                new_take_home = e1.text_input(
                    "Take-Home (Net) $",
                    value=f"{_to_number(target_row.get('take_home_amount')):.2f}",
                )
                new_gross = e2.text_input(
                    "Gross $",
                    value=f"{_to_number(target_row.get('gross_amount')):.2f}",
                )
                new_source = st.text_input(
                    "Details", value=target_row.get("source_name") or ""
                )
                new_taxable = st.checkbox(
                    "Is Taxable?", value=bool(target_row.get("is_taxable", True))
                )

                if edit_scope == "From effective date forward":
                    st.caption(
                        "New terms apply to this date and every future payment for this "
                        "recurring income."
                    )
                    effective_from = st.date_input(
                        "New terms start on",
                        value=row_date,
                        key=f"edit_effective_from_{form_key_prefix}_{target_income_id}",
                    )
                    new_date = effective_from
                else:
                    new_date = st.date_input("Date", value=row_date)
                    effective_from = new_date

                new_freq = st.selectbox(
                    "Frequency",
                    INCOME_FREQUENCY_OPTIONS,
                    format_func=income_pay_frequency_label,
                    index=INCOME_FREQUENCY_OPTIONS.index(current_freq)
                    if current_freq in INCOME_FREQUENCY_OPTIONS
                    else INCOME_FREQUENCY_OPTIONS.index("monthly"),
                    key=f"edit_pay_freq_{form_key_prefix}_{target_income_id}",
                )
                if new_freq == "school_year_monthly":
                    st.caption(
                        "Regular paychecks run Sep–Jun on this day each month. "
                        "Add two **One-time** incomes in July and August for summer checks."
                    )

                save_clicked = st.form_submit_button(
                    "💾 Save Changes", type="primary", width="stretch"
                )

            if save_clicked:
                parsed_take_home = _parse_currency_input(new_take_home)
                parsed_gross = _parse_currency_input(new_gross)
                if (
                    not new_source.strip()
                    or parsed_take_home == "invalid"
                    or parsed_gross == "invalid"
                ):
                    st.error("Please provide details and valid take-home and gross amounts.")
                elif (
                    edit_scope == "From effective date forward"
                    and current_freq != "one_time"
                ):
                    eff_key = f"edit_effective_from_{form_key_prefix}_{target_income_id}"
                    effective_from_val = st.session_state.get(eff_key, new_date)
                    ensure_income_stream_for_row(target_income_id)
                    if schedule_income_change(
                        target_income_id,
                        effective_from_val,
                        new_source.strip(),
                        parsed_take_home,
                        parsed_gross,
                        new_taxable,
                        edit_owner,
                        False,
                        new_freq,
                    ):
                        rerun_fragment_with_reason("budget_nav")
                    else:
                        st.error("Could not schedule income change.")
                elif update_household_income(
                    target_income_id,
                    new_source.strip(),
                    parsed_take_home,
                    parsed_gross,
                    new_taxable,
                    edit_owner,
                    False,
                    new_freq,
                    payment_date=new_date,
                ):
                    rerun_fragment_with_reason("budget_nav")
                else:
                    st.error("Could not update income.")

            delete_scope = "This occurrence only"
            if stream_id or current_freq != "one_time":
                delete_scope = st.radio(
                    "Delete scope",
                    ("This occurrence only", "End stream"),
                    key=f"income_delete_scope_{form_key_prefix}_{target_income_id}",
                    horizontal=True,
                )

            income_delete_key = f"income_{form_key_prefix}_{target_income_id}"
            if st.button(
                "❌ Delete Income",
                key=f"del_{form_key_prefix}_{target_income_id}",
                type="secondary",
                width="stretch",
            ):
                arm_delete_confirm(income_delete_key)
                rerun_fragment_with_reason("delete_arm")

            if render_delete_confirmation(
                income_delete_key, item_label=selected_edit_str, rerun_scope="fragment"
            ):
                if delete_scope == "End stream" and current_freq != "one_time":
                    ok = end_income_stream(target_income_id)
                else:
                    ok = delete_household_income_month_only(target_income_id)
                if ok:
                    rerun_fragment_with_reason("delete_income")


def _render_obligation_assignments_tab(household_id, selected_month, member_options):
    """Assign household budget categories to members."""
    st.caption(
        "Assign each household budget parent (and optional sub-category overrides) to the "
        "member responsible for that spend. The Disbursement plan tab uses these assignments "
        "to calculate transfer amounts."
    )
    data = compute_household_obligations(household_id, selected_month)
    parent_summaries = data.get("parent_summaries") or []
    assign_options = ["— Unassigned —"] + list(member_options)

    if parent_summaries:
        st.markdown("**Parent category assignments**")
        for summary in parent_summaries:
            parent_name = summary.get("parent_category_name") or ""
            cols = st.columns([2, 1, 2])
            cols[0].markdown(f"**{parent_name}**")
            cols[1].markdown(f"${summary.get('projected', 0):,.2f}")
            current = summary.get("assigned_member") or "— Unassigned —"
            idx = assign_options.index(current) if current in assign_options else 0
            cols[2].selectbox(
                "Assigned member",
                assign_options,
                index=idx,
                key=f"parent_assign_{parent_name}",
                label_visibility="collapsed",
            )
            unassigned_subs = summary.get("unassigned_subs") or []
            if unassigned_subs:
                st.caption(f"Unassigned sub-categories: {', '.join(unassigned_subs)}")

        if st.button("Save parent assignments", key="save_parent_assignments", type="primary"):
            for summary in parent_summaries:
                parent_name = summary.get("parent_category_name") or ""
                chosen = st.session_state.get(f"parent_assign_{parent_name}", "— Unassigned —")
                if chosen == "— Unassigned —":
                    clear_parent_assignment(household_id, parent_name)
                else:
                    upsert_parent_assignment(household_id, parent_name, chosen)
            rerun_fragment_with_reason("obligation_assign")
    else:
        st.info("No household budget categories to assign yet.")

    lines = data.get("lines") or []
    override_candidates = [
        line for line in lines if line.get("source") in ("parent", "override", "unassigned")
    ]
    if override_candidates:
        with st.expander("Sub-category overrides", expanded=False):
            st.caption("Override a single sub-category when it should not follow the parent assignee.")
            for line in override_candidates:
                label = f"{line.get('parent_category_name')} → {line.get('sub_category_name')}"
                ocols = st.columns([2, 1, 2])
                ocols[0].markdown(label)
                ocols[1].markdown(f"${line.get('projected_amount', 0):,.2f}")
                current = line.get("member_username") or "— Unassigned —"
                idx = assign_options.index(current) if current in assign_options else 0
                ocols[2].selectbox(
                    "Member",
                    assign_options,
                    index=idx,
                    key=f"sub_override_{line.get('category_id')}",
                    label_visibility="collapsed",
                )
            if st.button("Save sub-category overrides", key="save_sub_overrides"):
                for line in override_candidates:
                    cat_id = line.get("category_id")
                    chosen = st.session_state.get(f"sub_override_{cat_id}", "— Unassigned —")
                    if chosen == "— Unassigned —":
                        clear_subcategory_override(household_id, cat_id)
                    else:
                        upsert_subcategory_override(household_id, cat_id, chosen)
                rerun_fragment_with_reason("obligation_override")


DISBURSEMENT_FLASH_KEY = "disbursement_action_flash"


def _set_disbursement_flash(level: str, message: str) -> None:
    st.session_state[DISBURSEMENT_FLASH_KEY] = (level, message)


def _render_disbursement_flash() -> None:
    flash = st.session_state.pop(DISBURSEMENT_FLASH_KEY, None)
    if not flash:
        return
    level, message = flash
    if level == "success":
        st.success(message)
    elif level == "error":
        st.error(message)
    elif level == "info":
        st.info(message)
    else:
        st.warning(message)


def _build_disbursement_editor_rows(
    paycheck_schedule: list,
    saved_transfers: list,
    stream_options: list,
) -> list[dict]:
    """Rows for the editable transfer plan table (computed + saved orphans)."""
    stream_label_by_id = {
        str(opt.get("stream_id") or ""): opt.get("label") or ""
        for opt in (stream_options or [])
    }
    saved_by_key = {
        (r["payment_date"][:10], r["recipient_username"], str(r.get("funding_income_stream_id") or "")): r
        for r in saved_transfers
        if r.get("payment_date") and r.get("recipient_username")
    }
    editor_rows: list[dict] = []
    seen_keys: set[tuple] = set()

    for entry in paycheck_schedule or []:
        pay_date_str = str(entry.get("payment_date", ""))[:10]
        stream_id = str(entry.get("stream_id") or "")
        stream_label = entry.get("stream_label") or stream_label_by_id.get(stream_id, stream_id)
        for member in sorted(entry.get("payouts") or {}):
            parts = entry["payouts"][member]
            plan_obl = round(float(parts.get("obligation") or 0), 2)
            plan_allow = round(float(parts.get("allowance") or 0), 2)
            key = (pay_date_str, member, stream_id)
            saved = saved_by_key.get(key)
            if saved:
                obl = round(float(saved.get("obligation_amount") or 0), 2)
                allow = round(float(saved.get("allowance_amount") or 0), 2)
                status = (saved.get("status") or "planned").title()
            else:
                obl, allow = plan_obl, plan_allow
                status = "—"
            editor_rows.append({
                "Pay Date": pay_date_str,
                "Stream": stream_label,
                "Member": member,
                "Obligation ($)": obl,
                "Allowance ($)": allow,
                "Status": status,
                "_stream_id": stream_id,
                "_plan_obligation": plan_obl,
                "_plan_allowance": plan_allow,
            })
            seen_keys.add(key)

    for key, saved in saved_by_key.items():
        if key in seen_keys:
            continue
        pay_date_str, member, stream_id = key
        editor_rows.append({
            "Pay Date": pay_date_str,
            "Stream": stream_label_by_id.get(stream_id, stream_id or "—"),
            "Member": member,
            "Obligation ($)": round(float(saved.get("obligation_amount") or 0), 2),
            "Allowance ($)": round(float(saved.get("allowance_amount") or 0), 2),
            "Status": (saved.get("status") or "planned").title(),
            "_stream_id": stream_id,
            "_plan_obligation": round(float(saved.get("obligation_amount") or 0), 2),
            "_plan_allowance": round(float(saved.get("allowance_amount") or 0), 2),
        })

    editor_rows.sort(key=lambda r: (r["Pay Date"], r["Stream"], r["Member"]))
    return editor_rows


def _render_disbursement_plan_tab(household_id, selected_month):
    """Hands-off disbursement schedule — setup, read-only schedule, audit banners, advanced overrides."""
    is_admin = _is_budget_admin()
    _render_disbursement_flash()

    plan = compute_household_disbursement_plan(household_id, selected_month)
    summary = plan.get("monthly_summary") or {}
    review_flags = plan.get("review_flags") or []
    bundled = plan.get("member_bundled_amounts") or {}
    per_member_stream_info = plan.get("per_member_stream_info") or {}
    paycheck_schedule = plan.get("paycheck_schedule") or []
    stream_options = get_household_income_stream_options(household_id)
    saved_transfers = get_member_transfers(household_id, selected_month)
    drift = get_disbursement_plan_drift(household_id, selected_month)
    rec = get_disbursement_reconciliation(household_id, selected_month)
    plan_is_stale = bool(drift.get("stale"))

    # ── Section 1: Audit banners ──────────────────────────────────────
    audit_flags = get_disbursement_automation_audit_flags(household_id, selected_month)
    for flag in audit_flags:
        kind = flag.get("kind")
        msg = flag.get("message", "")
        sev = flag.get("severity", "warning")
        if kind == "plan_pending_review" and is_admin:
            col_msg, col_btn = st.columns([4, 1])
            col_msg.info(msg)
            with col_btn:
                st.write("")
                if st.button("Plan looks good", key="ack_disbursement_plan_btn", type="primary"):
                    acknowledge_disbursement_plan(household_id, selected_month)
                    rerun_fragment_with_reason("plan_acknowledged")
        elif kind == "plan_stale":
            st.warning(msg)
        elif sev == "error":
            st.error(msg)
        elif sev == "info":
            st.info(msg)
        else:
            st.warning(msg)

    if plan_is_stale and drift.get("has_saved_rows"):
        st.warning(
            "**This month's saved transfers no longer match the current income/obligations plan.** "
            "The schedule is frozen so in-flight bank transfers are not changed automatically. "
            "Changes will apply to **next month** on rollover, or use **Advanced → Reset plan** "
            "to rebuild this month from the latest data."
        )
        diff_preview = drift.get("diff_lines") or []
        if diff_preview:
            st.caption("\n".join(f"• {line}" for line in diff_preview[:5]))
            if len(diff_preview) > 5:
                st.caption(f"…and {len(diff_preview) - 5} more row(s).")
    elif drift.get("slot_split_drift"):
        st.info(
            "Monthly transfer totals match the current plan, but per-paycheck amounts differ "
            "(e.g. after a split-calculation update). Use **Advanced → Reset plan** only if you "
            "want to redistribute amounts across paycheck dates."
        )
        split_preview = drift.get("slot_split_lines") or []
        if split_preview:
            st.caption("\n".join(f"• {line}" for line in split_preview[:5]))

    if review_flags:
        st.warning(
            "**Extra paycheck month — review recommended**\n\n"
            + "\n".join(f"• {f['message']}" for f in review_flags)
            + "\n\nPer-transfer amounts are lower when split across more checks. "
            "Adjust in the Advanced section below if needed."
        )

    # Reconciliation last-reviewed line
    if rec and rec.get("reviewed"):
        reviewed_by = rec.get("reviewed_by") or ""
        reviewed_at = str(rec.get("reviewed_at") or "")[:10]
        st.caption(f"Last reviewed {reviewed_at} · by {reviewed_by}")

    st.caption(
        "Transfers generate and complete automatically. "
        "Configure income streams in Cash Flow and set funding streams below."
    )

    # ── Section 2: Summary metrics ───────────────────────────────────
    render_metrics_grid(
        [
            {"label": "Household income", "value": f"${plan.get('total_regular_income', 0):,.2f}"},
            {"label": "Assigned obligations", "value": f"${plan.get('total_assigned_obligations', 0):,.2f}"},
            {
                "label": "Surplus to split",
                "value": f"${plan.get('surplus_pool', 0):,.2f}",
                "help": "Income left after all assigned obligation targets.",
            },
            {"label": "Monthly transfers", "value": f"${summary.get('monthly_disbursement_total', 0):,.2f}"},
        ],
        desktop_columns=4,
    )

    # ── Section 3: Setup — funding streams per member ────────────────
    readiness = get_disbursement_readiness(household_id)
    if not readiness["has_income_streams"]:
        st.warning("No household income streams found. Add income streams in Cash Flow & Treasury to enable disbursement.")
    elif not readiness["has_obligation_assignments"]:
        st.info("No obligation assignments found. Assign household categories to members in the Category assignments tab.")

    if bundled:
        st.markdown("**Monthly amounts per member**")
        if stream_options:
            bundle_rows = []
            for member in sorted(bundled):
                parts = bundled[member]
                bundle_rows.append({
                    "cells": [
                        member,
                        _format_ledger_amount(parts.get("obligation_amount", 0)),
                        _format_ledger_amount(parts.get("allowance_amount", 0)),
                        _format_ledger_amount(parts.get("total_amount", 0)),
                    ]
                })
            _render_html_scroll_table(
                ["Member", "Obligation", "Allowance", "Total"],
                bundle_rows,
                right_align_from=1,
                variant="ledger",
            )

            labels = [opt["label"] for opt in stream_options]
            stream_ids = [opt["stream_id"] for opt in stream_options]
            st.markdown("**Funding paycheck streams** — set once; saved until changed:")
            for member in sorted(bundled):
                current_streams = get_member_funding_streams(household_id, member)
                default_labels = [
                    lbl for lbl, sid in zip(labels, stream_ids) if sid in current_streams
                ]
                col_sel, col_btn = st.columns([4, 1])
                with col_sel:
                    chosen_labels = st.multiselect(
                        f"{member}'s paycheck streams",
                        labels,
                        default=default_labels,
                        key=f"member_streams_{member}",
                        help="Transfers are split across all combined pay dates for selected streams.",
                    )
                with col_btn:
                    st.write("")
                    if st.button("Save", key=f"save_member_streams_{member}", type="primary"):
                        chosen_ids = [stream_ids[labels.index(lbl)] for lbl in chosen_labels]
                        if set_member_funding_streams(household_id, member, chosen_ids):
                            sync_disbursement_plan(household_id, selected_month)
                            tz = ZoneInfo("America/Chicago")
                            now = datetime.now(tz)
                            y, m = now.year, now.month
                            next_month = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
                            sync_disbursement_plan(household_id, next_month)
                            _clear_disbursement_session_guards(household_id, selected_month)
                            _set_disbursement_flash("success", f"Saved funding streams for {member}.")
                            rerun_fragment_with_reason("member_streams_saved")
                        else:
                            st.error(f"Could not save streams for {member}.")

            if readiness["members_missing_streams"]:
                st.warning(
                    "Select funding streams for: "
                    + ", ".join(readiness["members_missing_streams"])
                    + " — disbursement schedule cannot be generated until streams are set."
                )
        else:
            st.info("Add household income streams in Cash Flow & Treasury to link paycheck funding.")

    # ── Section 4: Read-only paycheck schedule ────────────────────────
    members_without_stream = [
        m for m in sorted(bundled or {})
        if not (per_member_stream_info.get(m) or {}).get("stream_ids")
    ]

    if paycheck_schedule:
        stream_label_by_id = {
            str(opt.get("stream_id") or ""): opt.get("label") or ""
            for opt in (stream_options or [])
        }

        summary_parts = []
        for member in sorted(per_member_stream_info):
            info = per_member_stream_info[member]
            if info.get("stream_ids"):
                streams_display = info.get("display") or ", ".join(info["stream_ids"])
                count = info.get("paycheck_count", 0)
                summary_parts.append(
                    f"**{member}**: {streams_display} · {count} paycheck(s) this month"
                )
        if summary_parts:
            st.markdown("  \n".join(summary_parts))

        saved_by_key = {
            (r["payment_date"][:10], r["recipient_username"], str(r.get("funding_income_stream_id") or "")): r
            for r in saved_transfers
            if r.get("payment_date") and r.get("recipient_username")
        }
        transfer_index = saved_by_key

        completed_count = sum(1 for r in saved_transfers if r.get("status") == "completed")
        if saved_transfers:
            st.markdown(
                f"**Transfer status** — {completed_count}/{len(saved_transfers)} completed this month"
            )

        for entry in paycheck_schedule:
            pay_date_str = str(entry.get("payment_date", ""))[:10]
            stream_id_str = str(entry.get("stream_id") or "")
            stream_label = entry.get("stream_label") or stream_label_by_id.get(stream_id_str, stream_id_str)
            payouts = entry.get("payouts") or {}
            st.markdown(f"**{_format_payment_date(pay_date_str)}** — {stream_label}")

            status_rows = []
            for member in sorted(payouts):
                t = transfer_index.get((pay_date_str, member, stream_id_str))
                parts = payouts[member]
                status = t["status"] if t else "—"
                badge = "✅ Transferred" if status == "completed" else ("🕐 Planned" if status == "planned" else "—")
                status_rows.append({
                    "cells": [
                        member,
                        _format_ledger_amount(t["obligation_amount"] if t else parts.get("obligation", 0)),
                        _format_ledger_amount(t["allowance_amount"] if t else parts.get("allowance", 0)),
                        _format_ledger_amount(t["total_amount"] if t else parts.get("total", 0)),
                        badge,
                    ]
                })
            _render_html_scroll_table(
                ["Member", "Obligation", "Allowance", "Total", "Status"],
                status_rows,
                right_align_from=1,
                variant="income",
            )

            # Mark transferred early (always available to admins; not hidden in Advanced)
            if is_admin:
                for member in sorted(payouts):
                    t = transfer_index.get((pay_date_str, member, stream_id_str))
                    if t and t.get("status") == "planned":
                        btn_key = f"mark_transferred_{t['id']}"
                        if st.button(
                            f"Mark transferred — {member} on {_format_payment_date(pay_date_str)}",
                            key=btn_key,
                        ):
                            if complete_member_transfer(str(t["id"])):
                                st.success(f"Marked as transferred for {member}.")
                                rerun_fragment_with_reason("complete_transfer")
                            else:
                                st.error("Could not mark transfer as completed.")
    elif members_without_stream:
        st.caption(
            f"No funding streams set for: {', '.join(members_without_stream)}. "
            "Select streams above to generate a paycheck schedule."
        )
    elif bundled:
        st.caption("No paychecks land in this month for the configured funding streams.")

    # ── Section 5: Advanced (admin-only expander) ─────────────────────
    if not is_admin:
        return

    editor_rev_key = f"disbursement_editor_rev_{household_id}_{selected_month}"
    editor_rev = st.session_state.get(editor_rev_key, 0)
    editor_rows = _build_disbursement_editor_rows(paycheck_schedule, saved_transfers, stream_options)
    has_plan_data = bool(editor_rows or saved_transfers)
    expand_advanced = plan_is_stale or bool(rec and not rec.get("reviewed"))

    with st.expander("Advanced — manual controls", expanded=expand_advanced):
        st.caption(
            "Use these when income, obligations, or funding streams changed mid-month. "
            "**Reset plan** rebuilds this month's transfers from the latest computed schedule. "
            "**Update transfer plan** saves edited amounts for planned rows only (completed rows are locked)."
        )

        if not has_plan_data:
            st.info("No transfer rows for this month yet. Set funding streams above, then reload.")
        else:
            due_transfers = get_due_planned_member_transfers(household_id)
            if due_transfers:
                due_cols = st.columns([2, 1])
                with due_cols[0]:
                    st.caption(
                        f"{len(due_transfers)} planned transfer(s) are on or past their pay date."
                    )
                with due_cols[1]:
                    if st.button(
                        f"Complete due ({len(due_transfers)})",
                        key="complete_due_transfers_btn",
                        type="primary",
                        use_container_width=True,
                    ):
                        completed_now = complete_due_member_transfers(household_id)
                        if completed_now < 0:
                            _set_disbursement_flash("error", "Requires admin access.")
                        elif completed_now:
                            cleanup_orphan_disbursement_artifacts(household_id, selected_month)
                            _set_disbursement_flash(
                                "success",
                                f"Completed {completed_now} transfer(s).",
                            )
                        else:
                            _set_disbursement_flash("info", "No due transfers to complete.")
                        rerun_fragment_with_reason("complete_due_transfers")

            if editor_rows:
                default_df = pd.DataFrame(editor_rows)
                all_completed = all(
                    str(r.get("Status") or "").lower() == "completed" for r in editor_rows
                )
                if all_completed:
                    st.info(
                        "All transfer rows are **Completed** — amounts are locked. "
                        "**Update transfer plan** only edits **Planned** rows. "
                        "Use **Reset plan** to rebuild from the latest income/obligations, "
                        "then edit before clicking **Complete due**."
                    )
                edited_df = st.data_editor(
                    default_df,
                    column_order=["Pay Date", "Stream", "Member", "Obligation ($)", "Allowance ($)", "Status"],
                    column_config={
                        "Pay Date": st.column_config.TextColumn("Pay Date", disabled=True),
                        "Stream": st.column_config.TextColumn("Stream", disabled=True),
                        "Member": st.column_config.TextColumn("Member", disabled=True),
                        "Status": st.column_config.TextColumn("Status", disabled=True),
                        "Obligation ($)": st.column_config.NumberColumn(
                            "Obligation ($)", format="$%.2f", min_value=0.0, step=0.01,
                        ),
                        "Allowance ($)": st.column_config.NumberColumn(
                            "Allowance ($)", format="$%.2f", min_value=0.0, step=0.01,
                        ),
                    },
                    disabled=["Pay Date", "Stream", "Member", "Status"],
                    hide_index=True,
                    width="stretch",
                    key=f"transfer_schedule_editor_{household_id}_{selected_month}_{editor_rev}",
                )
            else:
                edited_df = None
                st.caption(
                    "Saved transfers exist but no computed schedule slots match. "
                    "Use **Reset plan** to rebuild from current income and funding streams."
                )

            btn_update, btn_reset = st.columns(2)
            with btn_update:
                update_clicked = st.button(
                    "Update transfer plan",
                    key="plan_transfers_btn",
                    type="secondary",
                    use_container_width=True,
                    disabled=edited_df is None,
                )
            with btn_reset:
                reset_clicked = st.button(
                    "Reset plan",
                    key="reset_disbursement_plan_btn",
                    type="primary",
                    use_container_width=True,
                    help="Clear this month's transfers and rebuild from the current computed schedule.",
                )

            if reset_clicked:
                result = reset_disbursement_plan_transfers(household_id, selected_month)
                if result.get("permission_denied"):
                    _set_disbursement_flash("error", "Reset plan requires admin or developer access.")
                else:
                    _clear_disbursement_session_guards(household_id, selected_month)
                    st.session_state[editor_rev_key] = editor_rev + 1
                    changed_count = (
                        result.get("cleared", 0)
                        + result.get("inserted", 0)
                        + result.get("orphan_expenses", 0)
                        + result.get("orphan_incomes", 0)
                    )
                    if changed_count:
                        parts = []
                        if result.get("cleared"):
                            parts.append(f"{result['cleared']} transfer(s) cleared")
                        if result.get("inserted"):
                            parts.append(f"{result['inserted']} planned row(s) created")
                        if result.get("orphan_expenses"):
                            parts.append(f"{result['orphan_expenses']} orphan expense(s) removed")
                        if result.get("orphan_incomes"):
                            parts.append(f"{result['orphan_incomes']} orphan income(s) removed")
                        _arm_disbursement_reset_autocomplete_hold(household_id)
                        parts.append("rows stay Planned until you click Complete due or reload tomorrow")
                        _set_disbursement_flash("success", f"Reset plan — {', '.join(parts)}.")
                    else:
                        _set_disbursement_flash(
                            "info",
                            "Nothing to reset for this month — no transfers or schedule rows found.",
                        )
                rerun_fragment_with_reason("reset_disbursement_plan")

            if update_clicked and edited_df is not None:
                override_rows = []
                skipped_completed = 0
                for _, row in edited_df.iterrows():
                    if str(row.get("Status") or "").lower() == "completed":
                        skipped_completed += 1
                        continue
                    override_rows.append({
                        "payment_date": str(row["Pay Date"]),
                        "stream_id": row.get("_stream_id") or None,
                        "recipient_username": row["Member"],
                        "obligation": float(row.get("Obligation ($)") or 0),
                        "allowance": float(row.get("Allowance ($)") or 0),
                    })
                n = upsert_planned_transfers_from_schedule(
                    household_id, selected_month, override_rows=override_rows
                )
                if n:
                    _set_disbursement_flash("success", f"Saved {n} planned transfer row(s).")
                elif skipped_completed and not override_rows:
                    _set_disbursement_flash(
                        "info",
                        "All rows are completed — use Reset plan to rebuild, or edit planned rows only.",
                    )
                else:
                    _set_disbursement_flash("info", "All planned transfers already match — nothing to update.")
                rerun_fragment_with_reason("plan_transfers")


def _render_obligations_and_disbursements_panel(household_id, selected_month, member_options):
    """Master Ledger: category assignments and household disbursement planning."""
    with st.expander("Obligations & Disbursements", expanded=False):
        tab_assignments, tab_plan = st.tabs(["Category assignments", "Disbursement plan"])
        with tab_assignments:
            _render_obligation_assignments_tab(household_id, selected_month, member_options)
        with tab_plan:
            _render_disbursement_plan_tab(household_id, selected_month)


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

    render_metrics_grid([
        {"label": "Est. Monthly Income", "value": f"${total_member_income:,.2f}"},
        {"label": "Total Personal Spend", "value": f"${total_member_spend:,.2f}"},
        {"label": "Net Personal Cash Flow", "signed_amount": net_member_cash},
    ], desktop_columns=3)
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
        _, recurring_schedule = get_expense_stream_projections(
            household_id,
            selected_month,
            is_personal_spend=True,
            username=selected_member,
        )

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


def _apply_budget_chart_typography(fig):
    """Readable chart hierarchy: title > axis labels > ticks."""
    fig.update_layout(
        font=dict(
            family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
            size=13,
            color="#111827",
        ),
        title=dict(font=dict(size=17, color="#111827")),
        legend=dict(font=dict(size=12)),
        margin=dict(t=72, b=56, l=48, r=24),
    )
    fig.update_xaxes(
        title_font=dict(size=14, color="#374151"),
        tickfont=dict(size=12, color="#374151"),
    )
    fig.update_yaxes(
        title_font=dict(size=14, color="#374151"),
        tickfont=dict(size=12, color="#374151"),
    )
    fig.update_traces(
        textfont=dict(size=12, color="#111827"),
        selector=dict(type="bar"),
    )
    fig.update_traces(
        textfont=dict(size=12),
        insidetextfont=dict(size=13),
        selector=dict(type="pie"),
    )
    fig.update_traces(
        textfont=dict(size=13),
        insidetextfont=dict(size=12),
        selector=dict(type="treemap"),
    )
    return fig


def _plotly_chart_locked(fig, **kwargs):
    fig.update_layout(dragmode=False)
    _apply_budget_chart_typography(fig)
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


def _project_over_budget_amount(row) -> float:
    est_high = _to_number(row.get("_est_high"), 0)
    actual = _to_number(row.get("_actual"), 0)
    if est_high <= 0:
        return 0.0
    return max(actual - est_high, 0.0)


def _add_chart_corner_total(fig, total: float, *, label: str = "Total Over Budget") -> None:
    if total <= 0:
        return
    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0.99,
        y=0.99,
        xanchor="right",
        yanchor="top",
        text=f"{label}<br><b>{_format_money(total)}</b>",
        showarrow=False,
        bgcolor="rgba(255,255,255,0.92)",
        bordercolor="#DC2626",
        borderwidth=1,
        borderpad=6,
        font=dict(size=14, color="#991B1B"),
    )


def _apply_chart_currency_format(fig):
    fig.update_yaxes(tickformat="$,.0f")
    return fig


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


def _project_visible_in_overview_year(project, year, spend_by_id):
    """Include projects created in a year or with ledger spend in that year."""
    pid = str(project.get("id") or "")
    if project.get("_year") == year:
        return True
    return spend_by_id.get(pid, 0) > 0


def _format_project_funds_rollover_message(rollover_result, current_year):
    prior_year = rollover_result.get("prior_year")
    opening = rollover_result.get("opening")
    prior_remaining = rollover_result.get("prior_remaining")
    if prior_year is None or opening is None or prior_remaining is None:
        return None
    opening_label = _format_money(opening)
    remaining_label = _format_money(prior_remaining)
    if prior_remaining < 0:
        return (
            f"**{prior_year} → {current_year} rollover:** Prior-year pool was overspent by "
            f"{_format_money(abs(prior_remaining))}; {current_year} opening balance set to "
            f"{opening_label}."
        )
    return (
        f"**{prior_year} → {current_year} rollover:** Unspent balance carried forward as "
        f"{opening_label} (prior remaining: {remaining_label})."
    )


ANNUAL_REPORT_ACTIVE_KEY = "annual_report_active"
ANNUAL_REPORT_SCOPE_KEY = "annual_report_scope"
ANNUAL_REPORT_YEAR_KEY = "annual_report_year"
ANNUAL_REPORT_MODE_KEY = "annual_report_mode"


def is_annual_report_active() -> bool:
    return bool(st.session_state.get(ANNUAL_REPORT_ACTIVE_KEY))


def open_annual_report(scope: str, year: int, mode: str) -> None:
    st.session_state[ANNUAL_REPORT_ACTIVE_KEY] = True
    st.session_state[ANNUAL_REPORT_SCOPE_KEY] = scope
    st.session_state[ANNUAL_REPORT_YEAR_KEY] = int(year)
    st.session_state[ANNUAL_REPORT_MODE_KEY] = mode
    rerun_fragment_with_reason("annual_report_open")


def close_annual_report() -> None:
    st.session_state.pop(ANNUAL_REPORT_ACTIVE_KEY, None)
    st.session_state.pop(ANNUAL_REPORT_SCOPE_KEY, None)
    st.session_state.pop(ANNUAL_REPORT_YEAR_KEY, None)
    st.session_state.pop(ANNUAL_REPORT_MODE_KEY, None)


def _period_month_list(year: int, mode: str) -> list:
    today = date.today()
    if mode == "ytd":
        end_month = today.month if year == today.year else 12
        return [f"{year}-{month:02d}" for month in range(1, end_month + 1)]
    return [f"{year}-{month:02d}" for month in range(1, 13)]


def _as_of_for_month(month_year: str, mode: str) -> date:
    today = date.today()
    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    if year == today.year and month == today.month:
        return today
    return date(year, month, last_day)


def _build_period_summary(household_id, year, mode, scope, username=None, auth_user_id=None) -> dict:
    months = _period_month_list(year, mode)
    if not months:
        months = [f"{year}-01"]

    start_month = months[0]
    end_month = months[-1]
    is_personal = scope == "personal"

    expenses_df = get_expenses_for_period(household_id, start_month, end_month)
    incomes_df = get_household_incomes_for_period(
        household_id,
        start_month,
        end_month,
        is_personal_income=is_personal,
        username=username if is_personal else None,
    )
    categories_df = get_budget_categories(
        household_id,
        is_personal=is_personal,
        username=username if is_personal else None,
    )

    monthly_rows = []
    total_income = 0.0
    total_expenses = 0.0
    total_project_spending = 0.0
    income_by_source = {}
    period_expense_frames = []

    auth_user_id_str = str(auth_user_id) if auth_user_id else None

    for month_year in months:
        as_of = _as_of_for_month(month_year, mode)

        if not expenses_df.empty and "month_year" in expenses_df.columns:
            month_expenses = expenses_df[expenses_df["month_year"] == month_year].copy()
        else:
            month_expenses = pd.DataFrame()

        if is_personal:
            if not month_expenses.empty and "is_personal_spend" in month_expenses.columns:
                month_expenses = month_expenses[month_expenses["is_personal_spend"] == True]
            if not month_expenses.empty and auth_user_id_str and "auth_user_id" in month_expenses.columns:
                month_expenses = month_expenses[
                    month_expenses["auth_user_id"].astype(str) == auth_user_id_str
                ]
            expense_actual = _filter_expenses_for_actual_totals(month_expenses, month_year, as_of)
        else:
            if not month_expenses.empty and "is_personal_spend" in month_expenses.columns:
                month_expenses = month_expenses[month_expenses["is_personal_spend"] == False]
            hh_no_project = _exclude_system_category_expenses(month_expenses, categories_df)
            expense_actual = _filter_expenses_for_actual_totals(hh_no_project, month_year, as_of)
            hh_all = _filter_expenses_for_actual_totals(month_expenses, month_year, as_of)
            _, project_df = _split_project_household_expenses(hh_all, categories_df)
            total_project_spending += (
                float(project_df["amount"].sum()) if not project_df.empty else 0.0
            )

        if not incomes_df.empty and "month_year" in incomes_df.columns:
            month_incomes = incomes_df[incomes_df["month_year"] == month_year].copy()
        else:
            month_incomes = pd.DataFrame()

        incomes_actual = _filter_incomes_for_actual_totals(month_incomes, month_year, as_of)
        month_income = sum_income_for_month(incomes_actual, month_year)
        month_expense_total = (
            float(expense_actual["amount"].sum()) if not expense_actual.empty else 0.0
        )

        total_income += month_income
        total_expenses += month_expense_total

        monthly_rows.append(
            {
                "month_year": month_year,
                "label": datetime.strptime(month_year, "%Y-%m").strftime("%b %Y"),
                "income": month_income,
                "expenses": month_expense_total,
                "net": month_income - month_expense_total,
            }
        )

        if not expense_actual.empty:
            period_expense_frames.append(expense_actual)

        if not incomes_actual.empty:
            for _, row in incomes_actual.iterrows():
                source = row.get("source_name") or "—"
                freq = row.get("pay_frequency") or "monthly"
                amt = income_amount_for_month_total(
                    row.get("take_home_amount"),
                    freq,
                    month_year=month_year,
                    row=row.to_dict(),
                )
                income_by_source[source] = income_by_source.get(source, 0.0) + amt

    all_period_expenses = (
        pd.concat(period_expense_frames, ignore_index=True)
        if period_expense_frames
        else pd.DataFrame()
    )
    month_count = len(months)
    exp_summary = (
        all_period_expenses.groupby("category_id")["amount"].sum().reset_index()
        if not all_period_expenses.empty
        else pd.DataFrame(columns=["category_id", "amount"])
    )
    purchase_counts = (
        all_period_expenses.groupby("category_id").size().to_dict()
        if not all_period_expenses.empty
        else {}
    )

    merged_df = pd.merge(
        categories_df, exp_summary, left_on="id", right_on="category_id", how="left"
    )
    merged_df["actual_amount"] = merged_df["amount"].fillna(0.0)
    merged_df = _exclude_system_categories(merged_df)

    category_groups = []
    for parent in _sorted_parent_category_names(merged_df["category_name"].unique()):
        parent_mask = merged_df["category_name"] == parent
        parent_target = float(
            sum(
                _category_projected_amount(row, month_count=month_count)
                for _, row in merged_df[parent_mask].iterrows()
            )
        )
        parent_actual = float(merged_df.loc[parent_mask, "actual_amount"].sum())
        if parent_target == 0 and parent_actual == 0:
            continue

        sub_rows = []
        parent_purchase_count = 0
        for _, row in merged_df[parent_mask].iterrows():
            target = _category_projected_amount(row, month_count=month_count)
            actual = float(row["actual_amount"])
            if target == 0 and actual == 0:
                continue
            cat_id = row["id"]
            sub_purchase_count = int(purchase_counts.get(cat_id, 0))
            parent_purchase_count += sub_purchase_count
            sub_name = row.get("sub_category_name")
            if (
                sub_name is None
                or (isinstance(sub_name, float) and pd.isna(sub_name))
                or str(sub_name).strip() == ""
            ):
                sub_name = "(General)"
            else:
                sub_name = str(sub_name)
            sub_rows.append(
                {
                    "name": sub_name,
                    "projected": target,
                    "actual": actual,
                    "purchase_count": sub_purchase_count,
                }
            )

        sub_rows = _sort_subcategory_rows(sub_rows)

        category_groups.append(
            {
                "name": parent,
                "projected": parent_target,
                "actual": parent_actual,
                "purchase_count": parent_purchase_count,
                "subs": sub_rows,
            }
        )

    category_groups.sort(key=lambda group: group["name"].lower())

    return {
        "months": months,
        "monthly_rows": monthly_rows,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_cash_flow": total_income - total_expenses,
        "project_spending": total_project_spending if not is_personal else 0.0,
        "category_groups": category_groups,
        "income_by_source": income_by_source,
        "month_count": month_count,
    }


def _render_period_category_breakdown(category_groups, filter_key: str) -> None:
    if not category_groups:
        st.info("No category activity for this period.")
        return

    category_options = ["All categories"] + [group["name"] for group in category_groups]
    selected_category = st.selectbox("Category", category_options, key=filter_key)
    visible_groups = (
        category_groups
        if selected_category == "All categories"
        else [group for group in category_groups if group["name"] == selected_category]
    )
    if not visible_groups:
        st.info("No budget data for the selected category.")
        return

    table_rows = []
    for group in visible_groups:
        table_rows.append(
            {
                "cells": [
                    group["name"],
                    _format_purchase_count(group.get("purchase_count", 0)),
                    _format_ledger_amount(group["projected"]),
                    _format_ledger_amount(group["actual"]),
                    _format_ledger_diff(group["projected"], group["actual"]),
                ],
                "parent": True,
            }
        )
        for sub in group["subs"]:
            table_rows.append(
                {
                    "cells": [
                        sub["name"],
                        _format_purchase_count(sub.get("purchase_count", 0)),
                        _format_ledger_amount(sub["projected"]),
                        _format_ledger_amount(sub["actual"]),
                        _format_ledger_diff(sub["projected"], sub["actual"]),
                    ],
                    "indent": True,
                }
            )

    total_projected = sum(float(group["projected"]) for group in visible_groups)
    total_actual = sum(float(group["actual"]) for group in visible_groups)
    total_purchases = sum(int(group.get("purchase_count", 0)) for group in visible_groups)
    table_rows.append(
        {
            "cells": [
                "Total",
                _format_purchase_count(total_purchases),
                _format_ledger_amount(total_projected),
                _format_ledger_amount(total_actual),
                _format_ledger_diff(total_projected, total_actual),
            ],
            "emphasize": True,
        }
    )

    _render_html_scroll_table(
        ["Category", "Qty", "Projected", "Actual", "Difference"],
        table_rows,
        right_align_from=1,
        variant="ledger",
    )


def _render_annual_report_launcher(scope: str, key_prefix: str) -> None:
    if scope == "household" and not _can_access_monthly_module():
        st.caption("Annual household reports are limited to family admins.")
        return

    household_id = st.session_state.get("household_id")
    app_tz = ZoneInfo(st.secrets.get("app_config", {}).get("timezone", "America/New_York"))
    current_year = pd.Timestamp.now(tz=app_tz).year
    available_years = get_distinct_budget_years(household_id) if household_id else [current_year]
    full_year_options = [year for year in available_years if year != current_year]

    st.markdown("##### Year-to-Date Performance")
    if st.button(
        f"Open YTD {current_year}",
        key=f"{key_prefix}_ytd_{current_year}",
        type="primary",
        width="stretch",
    ):
        open_annual_report(scope, current_year, "ytd")

    st.markdown("##### Full Calendar Years")
    if not full_year_options:
        st.caption("Full-year reports are available for prior calendar years once data is logged.")
        return

    cols_per_row = 4
    for row_start in range(0, len(full_year_options), cols_per_row):
        row_years = full_year_options[row_start : row_start + cols_per_row]
        year_cols = st.columns(len(row_years))
        for col, year in zip(year_cols, row_years):
            with col:
                if st.button(str(year), key=f"{key_prefix}_year_{year}", width="stretch"):
                    open_annual_report(scope, year, "full")


def _render_annual_report_page(show_back_to_hub=False) -> None:
    scope = st.session_state.get(ANNUAL_REPORT_SCOPE_KEY, "household")
    year = int(st.session_state.get(ANNUAL_REPORT_YEAR_KEY, date.today().year))
    mode = st.session_state.get(ANNUAL_REPORT_MODE_KEY, "ytd")
    can_access_monthly = _can_access_monthly_module()

    if scope == "household" and not can_access_monthly:
        st.warning("You do not have permission to view household annual reports.")
        if st.button("⬅️ Back to Budget Modules", key="annual_report_access_denied"):
            close_annual_report()
            rerun_fragment_with_reason("annual_report_close")
        return

    household_id = st.session_state.get("household_id")
    username = st.session_state.get("username", "User")
    auth_user_id = st.session_state.get("auth_user_id")

    back_label = (
        "⬅️ Back to Personal Ledger"
        if scope == "personal"
        else "⬅️ Back to Household Master Ledger"
    )
    if st.button(back_label, key="annual_report_back"):
        close_annual_report()
        rerun_fragment_with_reason("annual_report_close")

    period_label = "Year-to-Date Summary" if mode == "ytd" else "Full Year Summary"
    scope_label = (
        f"👤 {username.title()}'s Personal Budget"
        if scope == "personal"
        else "🏦 Household Budget"
    )
    st.subheader(f"📈 {year} {period_label}")
    st.caption(scope_label)

    if not household_id:
        st.info("No household selected.")
        return

    summary = _build_period_summary(
        household_id,
        year,
        mode,
        scope,
        username=username if scope == "personal" else None,
        auth_user_id=auth_user_id,
    )

    if scope == "household":
        render_metrics_grid([
            {"label": "Total Income", "value": f"${summary['total_income']:,.2f}"},
            {"label": "Total Shared Expenses", "value": f"${summary['total_expenses']:,.2f}"},
            {"label": "Net Cash Flow", "signed_amount": summary["net_cash_flow"]},
            {
                "label": "Project Spending",
                "value": f"${summary['project_spending']:,.2f}",
                "help": "Informational only — not included in shared expenses or net cash flow.",
            },
        ], desktop_columns=4)
    else:
        render_metrics_grid([
            {"label": "Total Income", "value": f"${summary['total_income']:,.2f}"},
            {"label": "Total Personal Spend", "value": f"${summary['total_expenses']:,.2f}"},
            {"label": "Net Personal Cash Flow", "signed_amount": summary["net_cash_flow"]},
        ], desktop_columns=3)

    st.divider()
    st.markdown("#### Monthly Trend")

    monthly_rows = summary["monthly_rows"]
    if monthly_rows:
        labels = [row["label"] for row in monthly_rows]
        fig = go.Figure(
            data=[
                go.Bar(name="Income", x=labels, y=[row["income"] for row in monthly_rows], marker_color="#21c354"),
                go.Bar(
                    name="Expenses",
                    x=labels,
                    y=[row["expenses"] for row in monthly_rows],
                    marker_color="#ff4b4b",
                ),
            ]
        )
        fig.update_layout(
            barmode="group",
            xaxis_title="Month",
            yaxis_title="Amount ($)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=20, r=20, t=40, b=20),
            height=360,
        )
        _plotly_chart_locked(fig, width="stretch")
    else:
        st.info("No monthly data for this period.")

    st.markdown("#### Monthly Summary")
    if monthly_rows:
        month_table_rows = [
            {
                "cells": [
                    row["label"],
                    _format_ledger_amount(row["income"]),
                    _format_ledger_amount(row["expenses"]),
                    _format_ledger_diff(row["income"], row["expenses"]),
                ],
            }
            for row in monthly_rows
        ]
        month_table_rows.append(
            {
                "cells": [
                    "Total",
                    _format_ledger_amount(summary["total_income"]),
                    _format_ledger_amount(summary["total_expenses"]),
                    _format_ledger_diff(summary["total_income"], summary["total_expenses"]),
                ],
                "emphasize": True,
            }
        )
        _render_html_scroll_table(
            ["Month", "Income", "Expenses", "Net"],
            month_table_rows,
            right_align_from=1,
            variant="ledger",
        )
    else:
        st.info("No monthly summary available.")

    st.divider()
    st.markdown("#### Category Breakdown")
    filter_key = "annual_report_hh_category" if scope == "household" else "annual_report_pers_category"
    _render_period_category_breakdown(summary["category_groups"], filter_key)

    income_by_source = summary.get("income_by_source") or {}
    if income_by_source:
        st.divider()
        st.markdown("#### Income by Source")
        source_rows = [
            {
                "cells": [source, _format_ledger_amount(amount)],
            }
            for source, amount in sorted(income_by_source.items(), key=lambda item: item[0].lower())
        ]
        source_total = sum(income_by_source.values())
        source_rows.append(
            {
                "cells": ["Total", _format_ledger_amount(source_total)],
                "emphasize": True,
            }
        )
        _render_html_scroll_table(
            ["Source", "Total"],
            source_rows,
            right_align_from=1,
            variant="income",
        )


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


def render_disbursement_surplus_alert_if_needed() -> None:
    """App-level banners for disbursement automation audit flags."""
    household_id = st.session_state.get("household_id")
    if not household_id or household_id == "unassigned":
        return

    month_year = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m")
    flags = get_disbursement_automation_audit_flags(household_id, month_year)
    if _can_access_monthly_module():
        pass  # admins see all flags below
    else:
        flags = [f for f in flags if f.get("kind") == "duplicate_allowance_income"]

    for flag in flags:
        severity = flag.get("severity", "warning")
        message = flag.get("message", "")
        if not message:
            continue
        if severity == "error":
            st.error(message)
        else:
            st.warning(message)


def render_budget_module(show_back_to_hub=False):
    _render_budget_fragment(show_back_to_hub)


@st.fragment
def _render_budget_fragment(show_back_to_hub=False):
    household_id = st.session_state.get("household_id")
    if household_id:
        guard_key = f"project_category_ready_{household_id}"
        if not st.session_state.get(guard_key):
            ensure_project_expense_category(household_id)
            ensure_household_taxes_category(household_id)
            st.session_state[guard_key] = True
        allowance_guard = f"allowance_categories_ready_{household_id}"
        # Sync allowance categories once per session; the per-render in-sync DB
        # probe was the dominant source of click latency, so trust the guard.
        if not st.session_state.get(allowance_guard):
            if ensure_allowance_categories(household_id):
                st.session_state[allowance_guard] = True
            else:
                st.session_state.pop(allowance_guard, None)

    if "budget_view" not in st.session_state:
        st.session_state["budget_view"] = "menu"
    if "projects_funds" not in st.session_state:
        st.session_state["projects_funds"] = None
    if "pending_restore_project_id" not in st.session_state:
        st.session_state["pending_restore_project_id"] = None
    if "projects_active_section" not in st.session_state:
        st.session_state["projects_active_section"] = "workspace"
    if "projects_workspace_active_category" not in st.session_state:
        st.session_state["projects_workspace_active_category"] = "priority"
    if "wishlist_active_owner" not in st.session_state:
        st.session_state["wishlist_active_owner"] = None
    if "wishlist_pending_owner" not in st.session_state:
        st.session_state["wishlist_pending_owner"] = None

    if is_annual_report_active():
        _render_annual_report_page(show_back_to_hub)
        return

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
                        rerun_fragment_with_reason("budget_nav")
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
                    rerun_fragment_with_reason("budget_nav")

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
                        rerun_fragment_with_reason("budget_nav")
                else:
                    st.caption("Projects access is restricted.")
                    st.button("Projects Locked", disabled=True, width="stretch")

        with r2c2:
            with st.container(border=True):
                st.markdown("### 💝 Wish List")
                st.caption("Track purchase ideas in a shared household list.")
                if st.button("Open Wish List", key="btn_wish", type="secondary", width="stretch"):
                    st.session_state["budget_view"] = "wishlist"
                    rerun_fragment_with_reason("budget_nav")

        return

    if st.button("⬅️ Back to Budget Modules", width="content"):
        st.session_state["budget_view"] = "menu"
        rerun_fragment_with_reason("budget_nav")

    st.divider()

    if view == "projects" and not can_access_projects:
        st.warning("Projects access is currently disabled for your account. Ask your household admin to enable it.")
        if st.button("⬅️ Return to Budget Modules", key="projects_access_denied_return"):
            st.session_state["budget_view"] = "menu"
            rerun_fragment_with_reason("budget_nav")
        return

    if view == "monthly" and not can_access_monthly:
        st.warning("Monthly Budget access is currently disabled for your account. Ask your household admin to enable it.")
        if st.button("⬅️ Return to Budget Modules", key="monthly_access_denied_return"):
            st.session_state["budget_view"] = "menu"
            rerun_fragment_with_reason("budget_nav")
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

        selected_owner = render_two_col_selector(
            key="wishlist_active_owner",
            options=available_owner_names,
            format_func=lambda owner: f"👤 {owner}",
            rerun_scope="fragment",
        )

        active_rows = [r for r in visible_rows if not bool(r.get("is_completed", False))]
        completed_rows = [r for r in visible_rows if bool(r.get("is_completed", False))]

        filtered_active_rows = [r for r in active_rows if get_row_owner_username(r) == selected_owner]
        filtered_completed_rows = [r for r in completed_rows if get_row_owner_username(r) == selected_owner]

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
                            rerun_fragment_with_reason("budget_nav")
                        else:
                            st.error("Could not add wish list item.")

        if not filtered_active_rows:
            st.info(f"No active wish list items for {selected_owner} yet.")
        else:
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
                                        finish_manage_popover("wishlist_write", wishlist_popover_key, scope="fragment")
                                    else:
                                        st.error("Could not update this wish list item.")

                            if complete_clicked:
                                if complete_wish_list_item(str(row_id)):
                                    finish_manage_popover("wishlist_write", wishlist_popover_key, scope="fragment")
                                else:
                                    st.error("Could not complete this wish list item.")

                            if delete_clicked:
                                arm_delete_confirm(f"wishlist_{row_id}")
                                finish_manage_popover("delete_arm", wishlist_popover_key, scope="fragment")

                            if cancel_clicked:
                                finish_manage_popover("wishlist_edit_cancel", wishlist_popover_key, scope="fragment")

                    wishlist_delete_key = f"wishlist_{row_id}"
                    if render_delete_confirmation(wishlist_delete_key, item_label=item_name, rerun_scope="fragment"):
                        if delete_wish_list_item(str(row_id)):
                            rerun_fragment_with_reason("wishlist_delete")
                        else:
                            st.error("Could not delete this wish list item.")

                st.divider()

        with st.expander(f"✅ Completed ({len(filtered_completed_rows)})", expanded=False):
            if not filtered_completed_rows:
                st.info(f"No completed wish list items for {selected_owner} yet.")
            else:
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
                                rerun_fragment_with_reason("budget_nav")
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
    rollover_result = apply_projects_funds_year_rollover(current_year)
    if rollover_result.get("applied") or rollover_result.get("backfilled"):
        finance_settings = get_household_finance_settings()
    saved_projects_funds = finance_settings.get("projects_funds")
    saved_projects_funds_opening = finance_settings.get("projects_funds_opening")
    saved_projects_funds_year = finance_settings.get("projects_funds_year")
    saved_projects_funds_updated_at = finance_settings.get("updated_at")

    projects_household_id = st.session_state.get("household_id")
    current_year_expense_totals = get_project_expense_totals_for_year(
        projects_household_id,
        current_year,
    )
    current_year_total_actual = current_year_expense_totals["pool_total"]
    current_year_spend_by_project = current_year_expense_totals["by_project_id"]

    # ==========================================
    # 🏦 TOP-LEVEL: HOUSEHOLD BUDGET (Admin)
    # ==========================================
    if view == "household":
        if not can_access_monthly:
            st.warning("🔒 You do not have permission to view the Household Ledger.")
            if st.button("⬅️ Return to Menu"):
                st.session_state["budget_view"] = "menu"
                rerun_fragment_with_reason("budget_nav")
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
        household_view_mode = render_two_col_selector(
            key="household_view_mode",
            options=household_options,
            rerun_scope="fragment",
        )
        
        # --- TAB 1: MASTER LEDGER ---
        if household_view_mode == "📊 Master Ledger":
            render_metrics_grid([
                {"label": "Est. Monthly Take-Home", "value": f"${total_take_home:,.2f}"},
                {"label": "Total Shared Expenses", "value": f"${total_expenses:,.2f}"},
                {"label": "Net Cash Flow", "signed_amount": net_cash_flow},
                {
                    "label": "Project Spending",
                    "value": f"${project_spending:,.2f}",
                    "help": "Informational only — not included in shared expenses or net cash flow.",
                },
            ], desktop_columns=4)
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

                _, recurring_schedule = get_expense_stream_projections(
                    household_id,
                    selected_month,
                    is_personal_spend=False,
                )

                _render_household_budget_breakdown(
                    merged_df,
                    hh_expenses_no_project,
                    recurring_schedule,
                    selected_month,
                    filter_key="hh_breakdown_category",
                )
            st.divider()

            if _is_budget_admin():
                hh_users = get_household_users_for_admin()
                member_options = [
                    _clean_text(u.get("username"))
                    for u in hh_users
                    if _clean_text(u.get("username"))
                ]
                if member_options:
                    _render_obligations_and_disbursements_panel(
                        household_id, selected_month, member_options
                    )
            
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
                _render_annual_report_launcher("household", "hh_annual")
                
        # --- TAB 2: EXPENSES (HOUSEHOLD) ---
        elif household_view_mode == "💳 Expenses":
            st.markdown("#### Household Expenses")
            st.caption("Log new expenses from **Quick Expense** in the sidebar.")

            categories_df = get_budget_categories(household_id, is_personal=False)
            hh_expenses_display_df = _exclude_system_category_expenses(hh_expenses_df, categories_df)
            user_categories_df = _exclude_allowance_categories(_exclude_system_categories(categories_df)) if categories_df is not None and not categories_df.empty else pd.DataFrame()

            hh_recurring_df, hh_one_time_df = _split_recurring_expenses(hh_expenses_display_df)

            st.markdown("**One-Time**")
            _render_expense_manage_rows(
                hh_one_time_df,
                "exp_hh_once",
                "No one-time household expenses logged for this month yet.",
                categories_df=user_categories_df,
                can_edit=True,
            )

            st.divider()

            with st.expander("🔄 Recurring Household Expenses", expanded=False):
                _render_expense_manage_rows(
                    hh_recurring_df,
                    "exp_hh_recur",
                    "No recurring household expenses logged for this month yet.",
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
                        existing_parents = _sorted_parent_category_names(
                            user_categories_df["category_name"].unique()
                        )
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
                                rerun_fragment_with_reason("budget_nav")

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
                    editable_cats_df = _sort_categories_dataframe(editable_cats_df)
                    if not editable_cats_df.empty:
                        edit_cat_options = [
                            _build_category_edit_label(row)
                            for _, row in editable_cats_df.iterrows()
                        ]

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
                                    rerun_fragment_with_reason("category_write")

                        hh_cat_delete_key = f"hh_category_{target_cat_id}"
                        if delete_clicked:
                            arm_delete_confirm(hh_cat_delete_key)
                            rerun_fragment_with_reason("delete_arm")

                        if render_delete_confirmation(hh_cat_delete_key, item_label=selected_edit_str, rerun_scope="fragment"):
                            if delete_budget_category(target_cat_id):
                                rerun_fragment_with_reason("category_delete")
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
        household_integrated = get_personal_household_integration(household_id, username)

        my_ledger_expenses_df = get_personal_ledger_expenses(
            household_id, auth_user_id, selected_month, username
        )
        
        personal_incomes_df = get_personal_ledger_incomes(household_id, selected_month, username)
        personal_incomes_actual_df = _filter_incomes_for_actual_totals(personal_incomes_df, selected_month)
        total_personal_income = sum_income_for_month(personal_incomes_actual_df, selected_month)
        personal_annual_income_totals = compute_annual_income_totals(personal_incomes_actual_df)
        
        my_actual_df = _filter_expenses_for_actual_totals(my_ledger_expenses_df, selected_month)
        total_personal_spend = my_actual_df["amount"].sum() if not my_actual_df.empty else 0.0
        net_personal_cash = total_personal_income - total_personal_spend

        if _is_budget_admin():
            toggle_text = "Allow other Family Admins to view my Personal Budget"
        else:
            toggle_text = "Allow Family Admins to view my Personal Budget"
        current_share_status = bool(settings.get("share_budget_with_admin", False))
        allow_family_view = st.toggle(toggle_text, value=current_share_status)
        
        if allow_family_view != current_share_status:
            if update_user_privacy_toggle(household_id, username, allow_family_view):
                rerun_fragment_with_reason("budget_nav")

        current_integration = household_integrated
        integrate_household = st.toggle(
            "Integrate household budget on my personal ledger",
            value=current_integration,
            help=(
                "When enabled: obligation support from completed transfers appears on this ledger, "
                "your household paycheck income is shown read-only, and you can log expenses against "
                "your assigned household obligation categories. Allowance is always on your personal "
                "ledger automatically — it is your individual spend money."
            ),
        )
        if integrate_household != current_integration:
            if update_personal_household_integration(household_id, username, integrate_household):
                rerun_fragment_with_reason("household_integration_toggle")
            else:
                st.error("Could not save household integration setting.")
                
        personal_options = _personal_submodule_options(username)
        _sync_selector_option("personal_view_mode", personal_options)
        personal_view_mode = render_two_col_selector(
            key="personal_view_mode",
            options=personal_options,
            rerun_scope="fragment",
        )
        st.divider()

        # --- TAB 1: MASTER LEDGER (PERSONAL) ---
        if personal_view_mode == f"📊 {username.title()}'s Ledger":
            
            render_metrics_grid([
                {"label": "Est. Monthly Income", "value": f"${total_personal_income:,.2f}"},
                {"label": "Total Personal Spend", "value": f"${total_personal_spend:,.2f}"},
                {"label": "Net Personal Cash Flow", "signed_amount": net_personal_cash},
            ], desktop_columns=3)
            st.divider()
            
            st.markdown(f"#### {username.title()}'s Budget Breakdown")
            
            categories_df = get_budget_categories(household_id, is_personal=True, username=username)
            obligation_cats_df = (
                get_member_obligation_expense_categories(household_id, username)
                if household_integrated
                else pd.DataFrame()
            )
            breakdown_categories_df = categories_df
            if household_integrated and obligation_cats_df is not None and not obligation_cats_df.empty:
                breakdown_categories_df = pd.concat(
                    [categories_df, obligation_cats_df],
                    ignore_index=True,
                )
            
            if breakdown_categories_df.empty:
                st.info("No personal categories setup yet. Build your blank slate below!")
            else:
                if not my_actual_df.empty:
                    exp_summary = my_actual_df.groupby("category_id")["amount"].sum().reset_index()
                else:
                    exp_summary = pd.DataFrame(columns=["category_id", "amount"])
                
                merged_df = pd.merge(breakdown_categories_df, exp_summary, left_on="id", right_on="category_id", how="left")
                merged_df["actual_amount"] = merged_df["amount"].fillna(0.0)
                
                _, recurring_schedule = get_expense_stream_projections(
                    household_id,
                    selected_month,
                    is_personal_spend=True,
                    username=username,
                )

                _render_household_budget_breakdown(
                    merged_df,
                    my_ledger_expenses_df,
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
                _render_annual_report_launcher("personal", "pers_annual")

            st.divider()

        # --- TAB 2: EXPENSES (PERSONAL) ---
        elif personal_view_mode == "💳 Expenses":
            st.markdown("#### Personal Expenses")
            st.caption("Log new expenses from **Quick Expense** in the sidebar.")

            categories_df = get_budget_categories(household_id, is_personal=True, username=username)
            obligation_cats_df = (
                get_member_obligation_expense_categories(household_id, username)
                if household_integrated
                else pd.DataFrame()
            )

            if my_ledger_expenses_df is None or my_ledger_expenses_df.empty:
                personal_only_df = pd.DataFrame()
                household_obl_df = pd.DataFrame()
            elif "ledger_source" in my_ledger_expenses_df.columns:
                personal_only_df = my_ledger_expenses_df[
                    my_ledger_expenses_df["ledger_source"] != "household_obligation"
                ].copy()
                household_obl_df = my_ledger_expenses_df[
                    my_ledger_expenses_df["ledger_source"] == "household_obligation"
                ].copy()
            else:
                personal_only_df = my_ledger_expenses_df.copy()
                household_obl_df = pd.DataFrame()

            combined_categories_df = categories_df
            if household_integrated and obligation_cats_df is not None and not obligation_cats_df.empty:
                combined_categories_df = pd.concat(
                    [categories_df, obligation_cats_df],
                    ignore_index=True,
                )

            pers_recurring_df, pers_one_time_df = _split_recurring_expenses(personal_only_df)

            st.markdown("**One-Time**")
            _render_expense_manage_rows(
                pers_one_time_df,
                "exp_pers_once",
                "No one-time personal expenses logged for this month yet.",
                categories_df=combined_categories_df,
                can_edit=True,
            )

            st.divider()

            with st.expander("🔄 Recurring Personal Expenses", expanded=False):
                _render_expense_manage_rows(
                    pers_recurring_df,
                    "exp_pers_recur",
                    "No recurring personal expenses logged for this month yet.",
                    categories_df=combined_categories_df,
                    can_edit=True,
                )

            if household_integrated:
                hh_recurring_df, hh_one_time_df = _split_recurring_expenses(household_obl_df)
                with st.expander("🏠 Household Obligation Expenses", expanded=False):
                    if household_obl_df is None or household_obl_df.empty:
                        st.caption("No household obligation expenses logged this month.")
                    else:
                        if not hh_recurring_df.empty:
                            st.markdown("**Recurring**")
                            _render_expense_manage_rows(
                                hh_recurring_df,
                                "exp_pers_hh_recur",
                                "No recurring household obligation expenses.",
                                categories_df=combined_categories_df,
                                can_edit=True,
                            )
                        if not hh_one_time_df.empty:
                            st.markdown("**One-time**")
                            _render_expense_manage_rows(
                                hh_one_time_df,
                                "exp_pers_hh_once",
                                "No one-time household obligation expenses.",
                                categories_df=combined_categories_df,
                                can_edit=True,
                            )
            
            st.divider()

            if household_integrated:
                with st.expander("🏠 Manage obligation expense categories", expanded=False):
                    parent_names = get_member_obligation_parent_names(household_id, username)
                    if not parent_names:
                        st.info("No household obligation categories are assigned to you yet.")
                    else:
                        st.caption(
                            "Add or remove sub-categories under your assigned household obligation parents. "
                            "You cannot create new parent categories here."
                        )
                        add_parent = st.selectbox(
                            "Parent category",
                            parent_names,
                            key="obl_sub_parent_sel",
                        )
                        new_sub_name = st.text_input(
                            "New sub-category name",
                            placeholder="e.g., Produce, Daycare",
                            key="obl_sub_name_input",
                        )
                        new_sub_budget_raw = st.text_input(
                            "Projected monthly budget ($)",
                            placeholder="0",
                            key="obl_sub_budget_input",
                        )
                        if st.button("➕ Add sub-category", key="obl_sub_add_btn"):
                            parsed_budget = _parse_currency_input(new_sub_budget_raw) if new_sub_budget_raw else 0.0
                            if not new_sub_name.strip() or parsed_budget == "invalid":
                                st.error("Sub-category name required; budget must be numeric.")
                            elif insert_obligation_subcategory(
                                household_id,
                                username,
                                add_parent,
                                new_sub_name.strip(),
                                target_budget=parsed_budget or 0.0,
                            ):
                                st.success(f"Added {add_parent} → {new_sub_name.strip()}.")
                                rerun_fragment_with_reason("obl_sub_add")
                            else:
                                st.error("Could not add sub-category.")

                        subs_df = obligation_cats_df.copy() if obligation_cats_df is not None else pd.DataFrame()
                        if subs_df is not None and not subs_df.empty:
                            subs_df = subs_df[
                                subs_df["sub_category_name"].notna()
                                & (subs_df["sub_category_name"].astype(str).str.strip() != "")
                            ]
                        if subs_df is None or subs_df.empty:
                            st.caption("No sub-categories under your assigned parents yet.")
                        else:
                            st.markdown("**Remove sub-categories**")
                            for _, sub_row in _sort_categories_dataframe(subs_df).iterrows():
                                label = _build_obligation_category_display_name(sub_row)
                                col_label, col_btn = st.columns([3, 1])
                                col_label.markdown(label)
                                if col_btn.button("Remove", key=f"obl_sub_rm_{sub_row['id']}"):
                                    if deactivate_obligation_subcategory(household_id, username, sub_row["id"]):
                                        rerun_fragment_with_reason("obl_sub_rm")
                                    else:
                                        st.error(f"Could not remove {label}.")

                        inactive_subs_df = get_member_obligation_inactive_subcategories(household_id, username)
                        if inactive_subs_df is not None and not inactive_subs_df.empty:
                            st.divider()
                            st.markdown("**Restore removed sub-categories**")
                            st.caption(
                                "Removed sub-categories are hidden from the picker but can be restored here "
                                "or re-added above with the same name."
                            )
                            for _, sub_row in _sort_categories_dataframe(inactive_subs_df).iterrows():
                                label = _build_obligation_category_display_name(sub_row)
                                col_label, col_btn = st.columns([3, 1])
                                col_label.markdown(label)
                                if col_btn.button("Restore", key=f"obl_sub_restore_{sub_row['id']}"):
                                    if reactivate_obligation_subcategory(household_id, username, sub_row["id"]):
                                        rerun_fragment_with_reason("obl_sub_restore")
                                    else:
                                        st.error(f"Could not restore {label}.")

            with st.expander("⚙️ Manage Personal Categories"):
                tab_add, tab_edit = st.tabs(["➕ Add New", "✏️ Edit Existing"])
                
                with tab_add:
                    st.markdown("**🏷️ Add New Personal Category**")
                    parent_options = ["➕ Create New Parent Category"]
                    if not categories_df.empty:
                        existing_parents = _sorted_parent_category_names(
                            categories_df["category_name"].unique()
                        )
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
                                rerun_fragment_with_reason("budget_nav")
                                
                with tab_edit:
                    st.markdown("**✏️ Edit or Delete Personal Categories**")
                    if not categories_df.empty:
                        sorted_edit_cats_df = _sort_categories_dataframe(categories_df)
                        edit_cat_options = [
                            _build_category_edit_label(row)
                            for _, row in sorted_edit_cats_df.iterrows()
                        ]
                        
                        selected_edit_str = st.selectbox("Select Category to Edit", edit_cat_options, key="edit_cat_pers_select")
                        selected_edit_idx = edit_cat_options.index(selected_edit_str)
                        target_cat_row = sorted_edit_cats_df.iloc[selected_edit_idx]
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
                                    rerun_fragment_with_reason("category_write")

                        pers_cat_delete_key = f"pers_category_{target_cat_id}"
                        if delete_clicked:
                            arm_delete_confirm(pers_cat_delete_key)
                            rerun_fragment_with_reason("delete_arm")

                        if render_delete_confirmation(pers_cat_delete_key, item_label=selected_edit_str, rerun_scope="fragment"):
                            if delete_budget_category(target_cat_id):
                                rerun_fragment_with_reason("category_delete")
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
        username = st.session_state.get("username")
        
        categories_df = get_budget_categories(household_id)
        user_categories_df = _exclude_system_categories(categories_df)
        if user_categories_df.empty:
            st.warning("No active categories found. Please ask an Admin to add categories in the Household Setup.")
            return
            
        sorted_user_categories_df = _prepare_sorted_category_picker(user_categories_df)
        cat_options = sorted_user_categories_df["display_name"].tolist()
        selected_display_name = st.selectbox("Category", cat_options)
        
        cat_row = sorted_user_categories_df[
            sorted_user_categories_df["display_name"] == selected_display_name
        ].iloc[0]
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
                        auth_user_id=auth_user_id,
                        username=username,
                        household_id=household_id,
                        month_year=month_year_tag,
                        date_logged=date_logged,
                        category_id=category_id,
                        amount=parsed_amount,
                        details=details,
                        is_personal_spend=False,
                        is_recurring=is_recurring_expense,
                        project_id=selected_project_id,
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

    selected_year_expense_totals = get_project_expense_totals_for_year(
        projects_household_id,
        selected_year,
    )
    selected_year_spend_by_project = selected_year_expense_totals["by_project_id"]

    yearly_projects = [
        r
        for r in normalized
        if _project_visible_in_overview_year(r, selected_year, selected_year_spend_by_project)
    ]
    yearly_active_projects = [r for r in yearly_projects if not r.get("_completed", False)]
    yearly_completed_projects = [r for r in yearly_projects if r.get("_completed", False)]
    yearly_active_total_low = sum(r.get("_est_low", 0) for r in yearly_active_projects)
    yearly_active_total_high = sum(r.get("_est_high", 0) for r in yearly_active_projects)
    yearly_active_total_actual = sum(
        selected_year_spend_by_project.get(str(r.get("id") or ""), 0)
        for r in yearly_active_projects
    )
    yearly_completed_total_actual = sum(r.get("_actual", 0) for r in yearly_completed_projects)

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

    selected_projects_section = render_two_col_selector(
        key="projects_active_section",
        options=projects_section_keys,
        format_func=projects_section_label,
        rerun_scope="fragment",
    )

    if selected_projects_section == "overview":
        with st.expander("🎛️ Overview Filters", expanded=False):
            st.caption("Overview Year")
            selected_year = render_two_col_selector(
                key="projects_overview_year",
                options=available_years,
                format_func=lambda year_value: f"🗓️ {year_value}",
                rerun_scope="fragment",
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
                rerun_fragment_with_reason("budget_nav")

            if clear_all_col.button("Clear All", key="overview_categories_clear_all", width="stretch"):
                st.session_state["projects_overview_categories"] = []
                for category_name in category_options:
                    st.session_state[f"overview_cat_{_make_key_fragment(category_name)}"] = False
                rerun_fragment_with_reason("budget_nav")

            category_pairs = []
            for category_name in category_options:
                key_name = f"overview_cat_{_make_key_fragment(category_name)}"
                if key_name not in st.session_state:
                    st.session_state[key_name] = category_name in st.session_state["projects_overview_categories"]
                category_pairs.append((category_name, key_name))

            render_checkbox_grid(category_pairs)

            selected_categories = [
                category_name
                for category_name, key_name in category_pairs
                if st.session_state.get(key_name, False)
            ]
            st.session_state["projects_overview_categories"] = selected_categories or category_options

        overview_year_expense_totals = get_project_expense_totals_for_year(
            projects_household_id,
            selected_year,
        )
        overview_year_spend_by_project = overview_year_expense_totals["by_project_id"]

        yearly_projects = [
            r
            for r in normalized
            if _project_visible_in_overview_year(r, selected_year, overview_year_spend_by_project)
        ]
        selected_categories = st.session_state.get("projects_overview_categories", category_options)

        yearly_projects = [r for r in yearly_projects if (r.get("category") or "Uncategorized") in selected_categories]
        yearly_active_projects = [r for r in yearly_projects if not r.get("_completed", False)]
        yearly_completed_projects = [r for r in yearly_projects if r.get("_completed", False)]
        yearly_active_total_low = sum(r.get("_est_low", 0) for r in yearly_active_projects)
        yearly_active_total_high = sum(r.get("_est_high", 0) for r in yearly_active_projects)
        yearly_active_total_actual = sum(
            overview_year_spend_by_project.get(str(r.get("id") or ""), 0)
            for r in yearly_active_projects
        )
        yearly_completed_total_actual = sum(r.get("_actual", 0) for r in yearly_completed_projects)
        archive_years = [y for y in available_years if y != selected_year]

        st.caption(
            f"Dashboard is scoped to calendar year {selected_year} ({app_tz.key}). "
            f"Actual spent metrics use ledger expenses by calendar year; projects stay active across years."
        )

        yearly_total_est_low = sum(r.get("_est_low", 0) for r in yearly_projects)
        yearly_total_est_high = sum(r.get("_est_high", 0) for r in yearly_projects)
        yearly_total_actual = overview_year_expense_totals["pool_total"]
        yearly_budget_utilization = (
            (yearly_total_actual / yearly_total_est_high * 100) if yearly_total_est_high > 0 else None
        )

        active_over_total = sum(_project_over_budget_amount(r) for r in yearly_active_projects)
        completed_over_total = sum(_project_over_budget_amount(r) for r in yearly_completed_projects)
        active_over_count = sum(1 for r in yearly_active_projects if _project_over_budget_amount(r) > 0)
        completed_over_count = sum(1 for r in yearly_completed_projects if _project_over_budget_amount(r) > 0)

        st.markdown(f"#### {selected_year} Year at a Glance")
        render_metrics_grid([
            {"label": f"{selected_year} Est. Low (All)", "value": _format_money(yearly_total_est_low)},
            {"label": f"{selected_year} Est. High (All)", "value": _format_money(yearly_total_est_high)},
            {
                "label": f"{selected_year} Actual Spent (All)",
                "value": _format_money(yearly_total_actual),
                "help": "Sum of project-linked ledger expenses logged in this calendar year.",
            },
            {
                "label": f"{selected_year} Completed Final",
                "value": _format_money(yearly_completed_total_actual),
                "help": "Final actual spend on projects completed during this calendar year.",
            },
            {"label": "Active Projects", "value": str(len(yearly_active_projects))},
            {"label": "Completed Projects", "value": str(len(yearly_completed_projects))},
            {
                "label": "Budget Utilization",
                "value": f"{yearly_budget_utilization:.0f}%" if yearly_budget_utilization is not None else "—",
                "help": "Ledger spend in this year divided by total estimated high for projects in scope.",
            },
            {
                "label": "Over Budget Projects",
                "value": f"{active_over_count + completed_over_count}",
                "help": f"{active_over_count} active · {completed_over_count} completed",
            },
        ], desktop_columns=4)

        st.divider()

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

        if yearly_projects:
            overview_all_df = pd.DataFrame(yearly_projects)
            category_costs = overview_all_df.groupby("category", as_index=False)[["_est_low", "_est_high", "_actual"]].sum()
            category_costs = _sort_dataframe_column_case_insensitive(category_costs, "category")
            if not category_costs.empty:
                fig_bar = go.Figure(
                    data=[
                        go.Bar(
                            name="Est. Low",
                            x=category_costs["category"],
                            y=category_costs["_est_low"],
                            marker_color="#22C55E",
                            text=[_format_money(v) for v in category_costs["_est_low"]],
                            textposition="outside",
                        ),
                        go.Bar(
                            name="Est. High",
                            x=category_costs["category"],
                            y=category_costs["_est_high"],
                            marker_color="#DC2626",
                            text=[_format_money(v) for v in category_costs["_est_high"]],
                            textposition="outside",
                        ),
                        go.Bar(
                            name="Actual",
                            x=category_costs["category"],
                            y=category_costs["_actual"],
                            marker_color="#0EA5E9",
                            text=[_format_money(v) for v in category_costs["_actual"]],
                            textposition="outside",
                        ),
                    ]
                )
                fig_bar.update_layout(
                    title=f"{selected_year} Projects (Active + Completed): Estimates vs Actual by Category",
                    barmode="group",
                    xaxis_title="Category",
                    yaxis_title="Amount ($)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(t=60, b=40),
                )
                _apply_chart_currency_format(fig_bar)
                _plotly_chart_locked(fig_bar, width="stretch")
        else:
            st.info(f"No projects in scope for {selected_year} with the selected categories.")

        active_over_budget_rows = [
            {
                "Project": r.get("item") or "Unnamed",
                "Over Budget": _project_over_budget_amount(r),
            }
            for r in yearly_active_projects
            if _project_over_budget_amount(r) > 0
        ]

        completed_over_budget_rows = [
            {
                "category": r.get("category") or "Uncategorized",
                "Project": r.get("item") or "Unnamed",
                "Over Budget": _project_over_budget_amount(r),
            }
            for r in yearly_completed_projects
            if _project_over_budget_amount(r) > 0
        ]

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
                    text="Over Budget",
                )
                fig_active_over.update_traces(texttemplate="$%{y:,.0f}", textposition="outside")
                fig_active_over.update_layout(xaxis_title="", yaxis_title="Over Budget ($)", margin=dict(t=60, b=80))
                _apply_chart_currency_format(fig_active_over)
                _add_chart_corner_total(fig_active_over, active_over_total)
                _plotly_chart_locked(fig_active_over, width="stretch")
            else:
                st.info("No active projects are over budget for this year.")

        with over_col2:
            st.markdown(f"#### Completed Over Budget ({selected_year})")
            if completed_over_budget_rows:
                completed_over_df = _sort_dataframe_column_case_insensitive(
                    pd.DataFrame(completed_over_budget_rows),
                    "category",
                )
                completed_over_df = _sort_dataframe_column_case_insensitive(
                    completed_over_df,
                    "Project",
                )
                fig_completed_over = px.bar(
                    completed_over_df,
                    x="category",
                    y="Over Budget",
                    color="Project",
                    title="Completed Over Budget by Category",
                    text="Over Budget",
                    barmode="stack",
                )
                fig_completed_over.update_traces(texttemplate="$%{y:,.0f}", textposition="inside")
                fig_completed_over.update_layout(
                    xaxis_title="Category",
                    yaxis_title="Over Budget ($)",
                    legend=dict(title="Project", orientation="v"),
                    margin=dict(t=60, b=40),
                )
                _apply_chart_currency_format(fig_completed_over)
                _add_chart_corner_total(fig_completed_over, completed_over_total)
                _plotly_chart_locked(fig_completed_over, width="stretch")
            else:
                st.info("No completed projects are over budget for this year.")

        if yearly_active_projects:
            overview_df = pd.DataFrame(yearly_active_projects)
            chart_col1, chart_col2 = st.columns(2)

            with chart_col1:
                category_df = _sort_dataframe_column_case_insensitive(
                    overview_df.groupby("category", as_index=False)["_est_high"].sum(),
                    "category",
                )
                if not category_df.empty and category_df["_est_high"].sum() > 0:
                    fig_donut = px.pie(
                        category_df,
                        values="_est_high",
                        names="category",
                        hole=0.45,
                        title=f"Active Est. High by Category ({selected_year})",
                    )
                    fig_donut.update_traces(
                        textposition="inside",
                        textinfo="percent+label",
                        hovertemplate="%{label}<br>Est. High: $%{value:,.0f}<br>%{percent}<extra></extra>",
                    )
                    _plotly_chart_locked(fig_donut)
                else:
                    st.info("Add estimated high values to render category distribution.")

            with chart_col2:
                tree_df = overview_df[overview_df["_est_high"] > 0].copy()
                if not tree_df.empty:
                    tree_df["remaining"] = tree_df["_est_high"] - tree_df["_actual"]
                    tree_df = _sort_dataframe_column_case_insensitive(tree_df, "category")
                    tree_df = _sort_dataframe_column_case_insensitive(tree_df, "item")
                    fig_tree = px.treemap(
                        tree_df,
                        path=["category", "item"],
                        values="_est_high",
                        title=f"Active Project Cost Hierarchy ({selected_year})",
                        color="category",
                        custom_data=["_est_low", "_est_high", "_actual", "remaining"],
                    )
                    fig_tree.update_traces(
                        hovertemplate=(
                            "<b>%{label}</b><br>"
                            "Est. Low: $%{customdata[0]:,.0f}<br>"
                            "Est. High: $%{customdata[1]:,.0f}<br>"
                            "Actual: $%{customdata[2]:,.0f}<br>"
                            "Remaining: $%{customdata[3]:,.0f}<extra></extra>"
                        )
                    )
                    _plotly_chart_locked(fig_tree)
                else:
                    st.info("Treemap appears once active projects have estimated high values.")
        else:
            st.info(f"No active projects found for {selected_year}.")

        st.divider()
        with st.expander("🗂️ Previous Years Archive", expanded=False):
            st.caption("Review historical calendar-year totals.")

            if not archive_years:
                st.info("No archived years found yet.")
            else:
                for year_value in archive_years:
                    archive_totals = get_project_expense_totals_for_year(
                        projects_household_id,
                        year_value,
                    )
                    year_rows = [
                        r
                        for r in normalized
                        if _project_visible_in_overview_year(
                            r,
                            year_value,
                            archive_totals["by_project_id"],
                        )
                    ]
                    year_project_count = len(year_rows)
                    year_spent = archive_totals["pool_total"]

                    y1, y2 = st.columns([2, 2])
                    y1.metric(f"{year_value} Projects", f"{year_project_count}")
                    y2.metric(f"{year_value} Spent", f"${year_spent:,.2f}")

    elif selected_projects_section == "workspace":
        st.caption("Open this section to add and manage project records.")

        if not can_edit_projects:
            st.info("You have view-only access to Projects. Editing is restricted by your household admin.")

        rollover_message = _format_project_funds_rollover_message(rollover_result, current_year)
        if rollover_message:
            st.info(rollover_message)

        working_funds_value = _to_number(saved_projects_funds, 0.0)
        if (
            saved_projects_funds_opening is not None
            and float(saved_projects_funds_opening) != 0.0
        ):
            opening_funds_value = float(saved_projects_funds_opening)
        elif working_funds_value > 0 or current_year_total_actual > 0:
            opening_funds_value = reconstruct_projects_funds_opening(
                working_funds_value,
                current_year_total_actual,
            )
        else:
            opening_funds_value = 0.0

        with st.expander("💼 Project Funds", expanded=False):
            st.metric(f"Current Project Funds ({current_year})", _format_money(opening_funds_value))

            if can_edit_projects:
                with st.form("projects_funds_adjustment", clear_on_submit=True):
                    adj_type_col, adj_amount_col = st.columns([1, 2])
                    with adj_type_col:
                        adjustment_type = st.radio(
                            "Adjustment",
                            ["Add", "Subtract"],
                            horizontal=True,
                            key="projects_funds_adj_type",
                        )
                    with adj_amount_col:
                        adjustment_amount = st.text_input(
                            "Amount ($)",
                            placeholder="0.00",
                        )

                    submitted = st.form_submit_button(
                        "Apply Adjustment",
                        width="stretch",
                    )

                if submitted:
                    parsed_adjustment = _parse_currency_input(adjustment_amount)
                    if parsed_adjustment == "invalid" or parsed_adjustment is None:
                        st.error("Enter a valid dollar amount before applying an adjustment.")
                    elif parsed_adjustment <= 0:
                        st.error("Adjustment amount must be greater than zero.")
                    else:
                        delta = (
                            float(parsed_adjustment)
                            if adjustment_type == "Add"
                            else -float(parsed_adjustment)
                        )
                        if working_funds_value + delta < 0:
                            st.error(
                                "Cannot subtract more than the current project fund balance."
                            )
                        elif adjust_household_projects_funds(delta, current_year):
                            st.success(
                                f"Project funds updated to {_format_money(working_funds_value + delta)}."
                            )
                            rerun_fragment_with_reason("budget_nav")
                        else:
                            st.error("Could not update project funds.")

            st.caption(
                f"Current Project Funds is your {current_year} opening allocation (fixed for the year; "
                f"backfilled from your balance + spend if Jan 1 was not recorded). It does not decrease "
                f"when expenses are logged. Add/Subtract updates your working balance only. "
                f"Remaining Funds subtracts {current_year} ledger spend from the working balance."
            )

        remaining_funds = float(working_funds_value or 0.0) - float(current_year_total_actual)

        if saved_projects_funds is not None:
            history_line = f"Working balance ({current_year}): {_format_money(working_funds_value)}"
            if saved_projects_funds_updated_at:
                try:
                    updated_local = pd.to_datetime(saved_projects_funds_updated_at, utc=True).tz_convert(app_tz)
                    history_line = f"{history_line} on {updated_local.strftime('%b %d, %Y %I:%M %p %Z')}"
                except Exception:
                    pass
            st.caption(history_line)

        remaining_color = "#16A34A" if remaining_funds >= 0 else "#DC2626"
        st.markdown(
            f"**Remaining Project Funds ({current_year}):** "
            f"<span style='font-size:1.25rem; font-weight:700; color:{remaining_color};'>"
            f"{_format_money(remaining_funds)}</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Based on {_format_money(current_year_total_actual)} spent from the project fund pool "
            f"in {current_year} (ledger expenses)."
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

                b1, b2 = st.columns(2)
                new_est_low_raw = b1.text_input("Est. Low", value="", placeholder="Enter amount", disabled=not can_edit_projects)
                new_est_high_raw = b2.text_input("Est. High", value="", placeholder="Enter amount", disabled=not can_edit_projects)
                st.caption("Actual spent starts at $0.00 and is updated when you log purchases from Manage → Expenses.")

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

                    if not new_item.strip():
                        st.warning("Project Name is required.")
                    elif "invalid" in [parsed_low, parsed_high]:
                        st.warning("Est. Low and Est. High must be valid numbers.")
                    else:
                        payload = {
                            "item": new_item.strip(),
                            "category": new_category,
                            "priority": int(new_priority),
                            "description": _clean_text(new_description) or None,
                            "est_low_cost": float(parsed_low) if parsed_low is not None else 0.0,
                            "est_high_cost": float(parsed_high) if parsed_high is not None else 0.0,
                            "actual_cost": 0.0,
                            "veteran_discount": bool(new_vet_discount),
                            "vendors": _clean_text(new_vendors) or None,
                            "notes": _mark_completed_notes(new_notes) if complete_clicked else (_clean_text(new_notes) or None),
                        }
                        if insert_project_budget_item(payload):
                            st.success("Project completed and archived." if complete_clicked else "Project added.")
                            rerun_fragment_with_reason("budget_nav")
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
                expense_entries = get_project_purchase_expense_entries(
                    projects_household_id,
                    project_id,
                    legacy_notes=notes,
                )
                notes_without_expenses = strip_expense_audit_lines_from_notes(
                    notes.replace(COMPLETED_TAG, "").strip() if notes else ""
                )
                est_low = item.get("_est_low", 0)
                est_high = item.get("_est_high", 0)
                ledger_actual = sum(float(e.get("amount") or 0) for e in expense_entries if not e.get("is_legacy"))
                legacy_actual = sum(float(e.get("amount") or 0) for e in expense_entries if e.get("is_legacy"))
                actual = ledger_actual + legacy_actual if expense_entries else item.get("_actual", 0)
                spent_in_year = sum_project_purchase_expenses_for_year(expense_entries, current_year)
                project_start_year = item.get("_year")
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
                    status_caption = f"Priority: {priority} | Category: {category} | Status: {budget_status}"
                    if project_start_year and project_start_year < current_year and not item.get("_completed"):
                        status_caption = f"{status_caption} | Started {project_start_year}"
                    left_col.caption(status_caption)
                    left_col.markdown(
                        "Estimated: "
                        f"<span style='color:#16A34A; font-weight:600;'>&#36;{est_low:,.2f}</span> - "
                        f"<span style='color:#DC2626; font-weight:600;'>&#36;{est_high:,.2f}</span> | "
                        f"Spent in {current_year}: {_format_money(spent_in_year)} | "
                        f"Lifetime: {_format_money(actual)}",
                        unsafe_allow_html=True,
                    )
                    if est_high > 0:
                        pct_used = min(actual / est_high * 100, 999)
                        left_col.caption(
                            f"Budget used: {pct_used:.0f}% of est. high "
                            f"({_format_money(actual)} / {_format_money(est_high)})"
                        )
                    elif est_low > 0:
                        left_col.caption(f"Tracking against est. low: {_format_money(actual)} / {_format_money(est_low)}")

                    if remaining_balance is not None:
                        remaining_color = "#16A34A" if remaining_balance >= 0 else "#DC2626"
                        left_col.markdown(
                            f"**Remaining Budget:** <span style='color:{remaining_color}; font-weight:700;'>{_format_money(remaining_balance)}</span>",
                            unsafe_allow_html=True,
                        )

                    if est_high > 0:
                        left_col.progress(min(actual / est_high, 1.0))

                    _render_project_expense_entries(expense_entries, left_col)

                    if description:
                        left_col.markdown(f"**Description:** {description}")
                    if vendors:
                        left_col.markdown(f"**Vendors:** {vendors}")
                    if notes_without_expenses and notes_without_expenses != COMPLETED_TAG:
                        left_col.markdown(f"**Notes:** {notes_without_expenses}")
                    if has_vet_discount:
                        left_col.caption("Eligible for veteran discount.")

                    if can_edit_projects:
                        project_popover_key = f"project_{project_id}"
                        with right_col.popover("⚙️ Manage", key=manage_popover_key(project_popover_key)):
                            tab_add, tab_edit_exp, tab_edit = st.tabs(
                                ["➕ Add Expense", "✏️ Edit Expense", "✏️ Edit Project"]
                            )

                            with tab_add:
                                _render_project_expense_manage_popover(
                                    project_id=project_id,
                                    project_name=title,
                                    projects_household_id=projects_household_id,
                                    project_popover_key=project_popover_key,
                                    mode="add",
                                )

                            with tab_edit_exp:
                                _render_project_expense_manage_popover(
                                    project_id=project_id,
                                    project_name=title,
                                    projects_household_id=projects_household_id,
                                    project_popover_key=project_popover_key,
                                    mode="edit",
                                )

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

                                    eb1, eb2 = st.columns(2)
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
                                    st.markdown(f"**Actual Spent:** {_format_money(actual)}")
                                    st.caption("Log and edit purchases on the Expenses tab.")

                                    if budget_cap > 0:
                                        remaining_preview = budget_cap - actual
                                        preview_color = "#16A34A" if remaining_preview >= 0 else "#DC2626"
                                        st.markdown(
                                            f"**Remaining Budget:** <span style='color:{preview_color}; font-weight:700;'>{_format_money(remaining_preview)}</span>",
                                            unsafe_allow_html=True,
                                        )

                                    en1, en2 = st.columns(2)
                                    e_vendors = en1.text_input("Vendors", value=vendors)
                                    e_vet_discount = en2.checkbox("Veteran Discount", value=has_vet_discount)
                                    cleaned_edit_notes = strip_expense_audit_lines_from_notes(
                                        notes.replace(COMPLETED_TAG, "").strip()
                                    )
                                    e_notes = st.text_area("Notes", value=cleaned_edit_notes)

                                    save_col, complete_col, delete_col = st.columns([2, 1, 1])
                                    save_clicked = save_col.form_submit_button("💾 Save", type="primary", width="stretch")
                                    complete_clicked = complete_col.form_submit_button("✅ Complete Project", width="stretch")
                                    delete_clicked = delete_col.form_submit_button("🗑️ Delete", width="stretch")

                                if save_clicked or complete_clicked:
                                    parsed_low = _parse_currency_input(e_est_low_raw)
                                    parsed_high = _parse_currency_input(e_est_high_raw)

                                    if not e_item.strip():
                                        st.warning("Project Name is required.")
                                    elif "invalid" in [parsed_low, parsed_high]:
                                        st.warning("Est. Low and Est. High must be valid numbers.")
                                    else:
                                        update_payload = {
                                            "item": e_item.strip(),
                                            "category": e_category,
                                            "priority": int(e_priority),
                                            "description": _clean_text(e_description) or None,
                                            "est_low_cost": float(parsed_low) if parsed_low is not None else float(est_low),
                                            "est_high_cost": float(parsed_high) if parsed_high is not None else float(est_high),
                                            "veteran_discount": bool(e_vet_discount),
                                            "vendors": _clean_text(e_vendors) or None,
                                            "notes": _mark_completed_notes(e_notes) if complete_clicked else (_clean_text(e_notes) or None),
                                        }
                                        if update_project_budget_item(project_id, update_payload):
                                            finish_manage_popover("project_write", project_popover_key, scope="fragment")
                                        else:
                                            st.error("Could not update project.")

                                project_delete_key = f"project_{project_id}"
                                if delete_clicked:
                                    arm_delete_confirm(project_delete_key)
                                    rerun_fragment_with_reason("delete_arm")

                                if render_delete_confirmation(project_delete_key, item_label=title, rerun_scope="fragment"):
                                    if delete_project_budget_item(project_id):
                                        finish_manage_popover("project_delete", project_popover_key, scope="fragment")
                                    else:
                                        st.error("Could not delete this project.")

            def render_tab_totals(project_rows):
                tab_est_low = sum(p.get("_est_low", 0) for p in project_rows)
                tab_est_high = sum(p.get("_est_high", 0) for p in project_rows)
                tab_spent = sum(p.get("_actual", 0) for p in project_rows)
                render_metrics_grid([
                    {"label": "Est Low", "value": _format_money(tab_est_low)},
                    {"label": "Estimated High", "value": _format_money(tab_est_high)},
                    {"label": "Spent", "value": _format_money(tab_spent)},
                ], desktop_columns=3)
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

            selected_workspace_section = render_two_col_selector(
                key="projects_workspace_active_category",
                options=workspace_section_keys,
                format_func=workspace_section_label,
                rerun_scope="fragment",
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
                completed_expense_entries = get_project_purchase_expense_entries(
                    projects_household_id,
                    project_id,
                    legacy_notes=notes,
                )
                restored_notes = strip_expense_audit_lines_from_notes(_restore_active_notes(notes) or "")
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
                    if completed_expense_entries:
                        _render_project_expense_entries(completed_expense_entries, c_left)
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
                                rerun_fragment_with_reason("budget_nav")
                            else:
                                st.error("Could not restore this project.")

                        if cancel_col.button("❌ Cancel", key=f"cancel_restore_project_{project_id}", width="stretch"):
                            st.session_state["pending_restore_project_id"] = None
                            rerun_fragment_with_reason("budget_nav")