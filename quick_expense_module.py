"""Quick Expense sidebar tool.

Provides a full-width overlay page (same pattern as Admin Settings) for fast
expense entry. Personal categories are always available; household obligation
categories are optional via an in-page toggle (independent of the Personal
ledger integration setting).
"""

import streamlit as st
from datetime import date

from database import get_member_obligation_expense_categories
from budget_module import (
    EXPENSE_FREQUENCY_OPTIONS,
    expense_pay_frequency_label,
    build_personal_expense_picker_df,
    submit_expense_from_picker,
    _parse_currency_input,
)
from ui_helpers import rerun_with_reason

QUICK_EXPENSE_PAGE_KEY = "show_quick_expense_page"
_INCLUDE_OBLIGATIONS_KEY = "quick_expense_include_obligations"


# ---------------------------------------------------------------------------
# Page lifecycle
# ---------------------------------------------------------------------------

def is_quick_expense_page_active() -> bool:
    return bool(st.session_state.get(QUICK_EXPENSE_PAGE_KEY))


def open_quick_expense_page() -> None:
    st.session_state[QUICK_EXPENSE_PAGE_KEY] = True


def close_quick_expense_page() -> None:
    st.session_state.pop(QUICK_EXPENSE_PAGE_KEY, None)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def can_quick_log_expense() -> bool:
    """Any signed-in household member can use Quick Expense (matches Personal ledger)."""
    household_id = st.session_state.get("household_id")
    username = st.session_state.get("username", "")
    return bool(household_id and household_id != "unassigned" and username)


def _member_has_obligation_categories(household_id: str, username: str) -> bool:
    obl = get_member_obligation_expense_categories(household_id, username)
    return obl is not None and not obl.empty


# ---------------------------------------------------------------------------
# Sidebar entry
# ---------------------------------------------------------------------------

def render_quick_expense_sidebar_entry() -> None:
    """Sidebar button — renders for any signed-in household member."""
    if not can_quick_log_expense():
        return

    if st.button("➕ Quick Expense", key="sidebar_quick_expense", width="stretch"):
        open_quick_expense_page()
        rerun_with_reason("quick_expense_open")


# ---------------------------------------------------------------------------
# Full-width page
# ---------------------------------------------------------------------------

def render_quick_expense_page() -> None:
    """Full-width Quick Expense entry page."""
    household_id = st.session_state.get("household_id")
    auth_user_id = st.session_state.get("auth_user_id")
    username = st.session_state.get("username", "")

    if st.button("⬅️ Back to Dashboard", key="quick_expense_back_btn", width="content"):
        close_quick_expense_page()
        rerun_with_reason("quick_expense_back")

    st.subheader("➕ Quick Expense")

    if not household_id or not auth_user_id or not username:
        st.error("Session data missing — please sign in again.")
        return

    if not can_quick_log_expense():
        st.warning("You do not have permission to log expenses.")
        return

    has_obligation_assignments = _member_has_obligation_categories(household_id, username)

    if has_obligation_assignments:
        if _INCLUDE_OBLIGATIONS_KEY not in st.session_state:
            st.session_state[_INCLUDE_OBLIGATIONS_KEY] = True
        include_obligations = st.toggle(
            "Include household obligations",
            help=(
                "Adds your assigned household obligation categories to the list below. "
                "Independent of the Personal ledger integration setting."
            ),
            key=_INCLUDE_OBLIGATIONS_KEY,
        )
    else:
        include_obligations = False

    picker_df = build_personal_expense_picker_df(
        household_id,
        username,
        integrated=False,
        include_member_obligations=include_obligations,
    )

    if picker_df is None or picker_df.empty:
        st.warning(
            "No categories found. Add personal categories in the Budget module"
            + (
                ", or ask an admin to assign household obligation categories."
                if not has_obligation_assignments
                else "."
            )
        )
        return

    showing_obligations = bool(
        include_obligations
        and "is_household_obligation" in picker_df.columns
        and picker_df["is_household_obligation"].any()
    )
    if showing_obligations:
        st.caption(
            "Household obligation categories are tagged **[Household obligation]** "
            "and post to the shared household ledger."
        )

    display_list = picker_df["display_name"].tolist()

    with st.form("quick_expense_form", clear_on_submit=True):
        col_date, col_amt = st.columns([1, 1])
        entry_date = col_date.date_input("Date", value=date.today())
        amount_raw = col_amt.text_input("Amount ($) *")

        selected_display = st.selectbox("Category", display_list, key="quick_exp_cat")
        details = st.text_input("Details (optional)")

        with st.expander("Advanced"):
            pay_frequency = st.selectbox(
                "Frequency",
                EXPENSE_FREQUENCY_OPTIONS,
                format_func=expense_pay_frequency_label,
                index=EXPENSE_FREQUENCY_OPTIONS.index("one_time"),
                key="quick_exp_freq",
            )

        if st.form_submit_button("💾 Save Expense", type="primary", width="stretch"):
            parsed = _parse_currency_input(amount_raw)
            if parsed == "invalid" or parsed is None:
                st.error("Please enter a valid dollar amount.")
            else:
                cat_row = picker_df[picker_df["display_name"] == selected_display].iloc[0]
                ok, msg = submit_expense_from_picker(
                    cat_row=cat_row,
                    date_logged=entry_date,
                    amount=parsed,
                    details=details.strip(),
                    pay_frequency=pay_frequency,
                    household_id=household_id,
                    auth_user_id=auth_user_id,
                    username=username,
                    is_household_admin=False,
                )
                if ok:
                    st.success(msg)
                    rerun_with_reason("quick_expense_saved")
                else:
                    st.error("Failed to save expense. Check your permissions and try again.")
