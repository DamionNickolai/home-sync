"""Tests for Quick Expense module — permissions and submit routing."""

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _cat(cat_id, name, sub=None, *, is_obl=False):
    return {
        "id": cat_id,
        "category_name": name,
        "sub_category_name": sub,
        "display_name": f"{name} - {sub}" if sub else name,
        "is_household_obligation": is_obl,
        "target_budget": 100.0,
    }


HH_CAT = _cat("hh-1", "Utilities", "Electricity")
PERSONAL_CAT = _cat("p-1", "Groceries")
OBL_CAT = _cat("obl-1", "Utilities", "Electricity", is_obl=True)


# ---------------------------------------------------------------------------
# Permission helper tests
# ---------------------------------------------------------------------------

class TestCanQuickLogExpense(unittest.TestCase):
    def _call(self, *, household_id="hh-1", username="jason"):
        import streamlit as st
        with patch.dict(
            st.session_state,
            {"household_id": household_id, "username": username},
            clear=False,
        ):
            from quick_expense_module import can_quick_log_expense
            return can_quick_log_expense()

    def test_returns_true_for_signed_in_household_member(self):
        self.assertTrue(self._call())

    def test_returns_false_without_household(self):
        self.assertFalse(self._call(household_id=None, username="jason"))

    def test_returns_false_for_unassigned_household(self):
        self.assertFalse(self._call(household_id="unassigned", username="jason"))

    def test_returns_false_without_username(self):
        self.assertFalse(self._call(household_id="hh-1", username=""))


# ---------------------------------------------------------------------------
# Picker builder tests
# ---------------------------------------------------------------------------

class TestBuildHouseholdExpensePickerDf(unittest.TestCase):
    def test_returns_filtered_sorted_df(self):
        raw = pd.DataFrame([
            {"id": "s1", "category_name": "System-Projects", "sub_category_name": None, "target_budget": 0},
            {"id": "a1", "category_name": "Allowance", "sub_category_name": "Jason", "target_budget": 0},
            {"id": "u1", "category_name": "Utilities", "sub_category_name": "Electricity", "target_budget": 100},
        ])
        with (
            patch("budget_module.get_budget_categories", return_value=raw),
            patch("budget_module.is_system_project_expense_category", side_effect=lambda n, s: n == "System-Projects"),
            patch("budget_module.is_allowance_subcategory", side_effect=lambda n, s: n == "Allowance"),
        ):
            from budget_module import build_household_expense_picker_df
            result = build_household_expense_picker_df("hh-id")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["id"], "u1")
        self.assertIn("display_name", result.columns)

    def test_returns_empty_df_when_no_categories(self):
        with patch("budget_module.get_budget_categories", return_value=pd.DataFrame()):
            from budget_module import build_household_expense_picker_df
            result = build_household_expense_picker_df("hh-id")
        self.assertTrue(result.empty)


class TestBuildPersonalExpensePickerDf(unittest.TestCase):
    def test_includes_personal_and_obligation_when_integrated(self):
        personal = pd.DataFrame([{"id": "p1", "category_name": "Groceries", "sub_category_name": None, "target_budget": 50}])
        obligation = pd.DataFrame([{"id": "o1", "category_name": "Utilities", "sub_category_name": "Electric", "target_budget": 80}])
        with (
            patch("budget_module.get_budget_categories", return_value=personal),
            patch("budget_module.get_member_obligation_expense_categories", return_value=obligation),
        ):
            from budget_module import build_personal_expense_picker_df
            result = build_personal_expense_picker_df("hh-id", "user", integrated=True)
        self.assertEqual(len(result), 2)
        obl_rows = result[result["is_household_obligation"] == True]
        self.assertEqual(len(obl_rows), 1)

    def test_includes_obligations_when_flagged_even_if_not_integrated(self):
        personal = pd.DataFrame([{"id": "p1", "category_name": "Groceries", "sub_category_name": None, "target_budget": 50}])
        obligation = pd.DataFrame([{"id": "o1", "category_name": "Utilities", "sub_category_name": "Electric", "target_budget": 80}])
        with (
            patch("budget_module.get_budget_categories", return_value=personal),
            patch("budget_module.get_member_obligation_expense_categories", return_value=obligation),
        ):
            from budget_module import build_personal_expense_picker_df
            result = build_personal_expense_picker_df(
                "hh-id", "user", integrated=False, include_member_obligations=True
            )
        self.assertEqual(len(result), 2)

    def test_excludes_obligation_when_not_integrated(self):
        personal = pd.DataFrame([{"id": "p1", "category_name": "Groceries", "sub_category_name": None, "target_budget": 50}])
        with patch("budget_module.get_budget_categories", return_value=personal):
            from budget_module import build_personal_expense_picker_df
            result = build_personal_expense_picker_df("hh-id", "user", integrated=False)
        self.assertEqual(len(result), 1)
        self.assertFalse(any(result.get("is_household_obligation", pd.Series([False]))))

    def test_returns_empty_when_no_categories(self):
        with (
            patch("budget_module.get_budget_categories", return_value=pd.DataFrame()),
            patch("budget_module.get_member_obligation_expense_categories", return_value=pd.DataFrame()),
        ):
            from budget_module import build_personal_expense_picker_df
            result = build_personal_expense_picker_df("hh-id", "user", integrated=True)
        self.assertTrue(result.empty)


# ---------------------------------------------------------------------------
# Submit routing tests
# ---------------------------------------------------------------------------

class TestSubmitExpenseFromPicker(unittest.TestCase):
    def _submit(self, cat_row, *, is_household_admin):
        from budget_module import submit_expense_from_picker
        return submit_expense_from_picker(
            cat_row=cat_row,
            date_logged=date(2026, 6, 1),
            amount=50.0,
            details="test",
            pay_frequency="one_time",
            household_id="hh-id",
            auth_user_id="auth-id",
            username="user",
            is_household_admin=is_household_admin,
        )

    def test_hh_admin_non_obligation_calls_log_expense(self):
        cat = {**HH_CAT, "is_household_obligation": False}
        with (
            patch("budget_module.log_expense_and_check_project", return_value=True) as mock_log,
            patch("budget_module.log_household_expense_from_personal") as mock_obl,
        ):
            ok, msg = self._submit(cat, is_household_admin=True)
        self.assertTrue(ok)
        self.assertIn("Household", msg)
        mock_log.assert_called_once()
        mock_obl.assert_not_called()
        self.assertFalse(mock_log.call_args.kwargs.get("is_personal_spend", True))

    def test_obligation_category_calls_log_household_from_personal(self):
        cat = {**OBL_CAT}
        with (
            patch("budget_module.log_household_expense_from_personal", return_value=True) as mock_obl,
            patch("budget_module.log_expense_and_check_project") as mock_log,
        ):
            ok, msg = self._submit(cat, is_household_admin=False)
        self.assertTrue(ok)
        self.assertIn("Household", msg)
        mock_obl.assert_called_once()
        mock_log.assert_not_called()

    def test_personal_category_calls_log_expense_personal(self):
        cat = {**PERSONAL_CAT, "is_household_obligation": False}
        with (
            patch("budget_module.log_expense_and_check_project", return_value=True) as mock_log,
            patch("budget_module.log_household_expense_from_personal") as mock_obl,
        ):
            ok, msg = self._submit(cat, is_household_admin=False)
        self.assertTrue(ok)
        self.assertIn("Personal", msg)
        mock_obl.assert_not_called()
        self.assertTrue(mock_log.call_args.kwargs.get("is_personal_spend"))

    def test_returns_false_on_log_failure(self):
        cat = {**PERSONAL_CAT, "is_household_obligation": False}
        with patch("budget_module.log_expense_and_check_project", return_value=False):
            ok, _ = self._submit(cat, is_household_admin=False)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
