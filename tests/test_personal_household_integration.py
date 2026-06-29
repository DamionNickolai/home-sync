import unittest
from unittest.mock import patch

import pandas as pd

import database
from constants import ALLOWANCE_INCOME_SOURCE_NAME, OBLIGATION_SUPPORT_INCOME_SOURCE_NAME


class PersonalHouseholdIntegrationTests(unittest.TestCase):
    def test_get_personal_ledger_incomes_excludes_obligation_transfer_when_off(self):
        personal_df = pd.DataFrame([
            {"id": "1", "source_name": "Side gig", "take_home_amount": 100.0, "pay_frequency": "monthly"},
            {"id": "2", "source_name": ALLOWANCE_INCOME_SOURCE_NAME, "take_home_amount": 50.0, "pay_frequency": "one_time"},
            {"id": "3", "source_name": OBLIGATION_SUPPORT_INCOME_SOURCE_NAME, "take_home_amount": 30.0, "pay_frequency": "one_time"},
        ])
        with patch.object(database, "get_personal_household_integration", return_value=False), \
             patch.object(database, "get_household_incomes", return_value=personal_df):
            result = database.get_personal_ledger_incomes("home-1", "2026-06", "Angelle")

        self.assertEqual(len(result), 2)
        sources = set(result["source_name"].tolist())
        self.assertEqual(sources, {"Side gig", ALLOWANCE_INCOME_SOURCE_NAME})
        self.assertTrue((result["ledger_source"] == "personal").all())

    def test_get_personal_ledger_incomes_includes_mirror_and_transfer_when_on(self):
        personal_df = pd.DataFrame([
            {"id": "1", "source_name": ALLOWANCE_INCOME_SOURCE_NAME, "take_home_amount": 50.0, "pay_frequency": "one_time", "owner_username": "Angelle"},
            {"id": "2", "source_name": OBLIGATION_SUPPORT_INCOME_SOURCE_NAME, "take_home_amount": 30.0, "pay_frequency": "one_time", "owner_username": "Angelle"},
        ])
        hh_df = pd.DataFrame([
            {
                "id": "hh-1",
                "source_name": "Employer",
                "take_home_amount": 3000.0,
                "pay_frequency": "bi_weekly",
                "owner_username": "Angelle",
                "source_expense_id": None,
            },
            {
                "id": "hh-2",
                "source_name": "Other earner",
                "take_home_amount": 4000.0,
                "pay_frequency": "monthly",
                "owner_username": "Jason",
                "source_expense_id": None,
            },
        ])

        def fake_get_incomes(household_id, month_year, is_personal_income=False, username=None):
            if is_personal_income:
                return personal_df
            return hh_df

        with patch.object(database, "get_personal_household_integration", return_value=True), \
             patch.object(database, "get_household_incomes", side_effect=fake_get_incomes):
            result = database.get_personal_ledger_incomes("home-1", "2026-06", "Angelle")

        sources = set(result["ledger_source"].tolist())
        self.assertIn("transfer", sources)
        self.assertIn("household_mirror", sources)
        self.assertEqual(len(result[result["ledger_source"] == "household_mirror"]), 1)
        self.assertEqual(result[result["ledger_source"] == "household_mirror"].iloc[0]["source_name"], "Employer")
        allowance_rows = result[result["source_name"] == ALLOWANCE_INCOME_SOURCE_NAME]
        self.assertEqual(len(allowance_rows), 1)
        self.assertEqual(allowance_rows.iloc[0]["ledger_source"], "personal")

    def test_get_member_obligation_parent_names(self):
        assignments = [
            {
                "assignment_level": "parent",
                "member_username": "Angelle",
                "parent_category_name": "Groceries",
                "is_active": True,
            },
            {
                "assignment_level": "parent",
                "member_username": "Jason",
                "parent_category_name": "Pets",
                "is_active": True,
            },
            {
                "assignment_level": "subcategory",
                "member_username": "Angelle",
                "parent_category_name": "Groceries",
                "is_active": True,
            },
        ]
        with patch.object(database, "get_obligation_assignments", return_value=assignments):
            names = database.get_member_obligation_parent_names("home-1", "Angelle")
        self.assertEqual(names, ["Groceries"])

    def test_get_member_obligation_expense_categories_resolves_subcategory_override(self):
        categories = pd.DataFrame([
            {
                "id": "cat-produce",
                "category_name": "Groceries",
                "sub_category_name": "Produce",
                "target_budget": 100.0,
                "is_personal": False,
            },
            {
                "id": "cat-pets",
                "category_name": "Pets",
                "sub_category_name": "Food",
                "target_budget": 50.0,
                "is_personal": False,
            },
        ])
        assignments = [
            {
                "assignment_level": "parent",
                "member_username": "Jason",
                "parent_category_name": "Pets",
                "is_active": True,
            },
            {
                "assignment_level": "subcategory",
                "member_username": "Angelle",
                "category_id": "cat-produce",
                "parent_category_name": "Groceries",
                "is_active": True,
            },
        ]
        with patch.object(database, "get_budget_categories", return_value=categories), \
             patch.object(database, "get_obligation_assignments", return_value=assignments):
            result = database.get_member_obligation_expense_categories("home-1", "Angelle")

        self.assertEqual(len(result), 1)
        self.assertEqual(str(result.iloc[0]["id"]), "cat-produce")

    def test_log_household_expense_from_personal_rejects_unassigned_category(self):
        allowed = pd.DataFrame([
            {"id": "cat-1", "category_name": "Groceries", "sub_category_name": "Produce"},
        ])
        with patch.object(database, "get_personal_household_integration", return_value=True), \
             patch.object(database, "get_member_obligation_expense_categories", return_value=allowed), \
             patch.object(database, "log_expense_and_check_project") as log_mock:
            ok = database.log_household_expense_from_personal(
                auth_user_id="u1",
                username="Angelle",
                household_id="home-1",
                month_year="2026-06",
                date_logged="2026-06-15",
                category_id="cat-other",
                amount=25.0,
                details="test",
            )
        self.assertFalse(ok)
        log_mock.assert_not_called()

    def test_log_household_expense_from_personal_allows_without_integration(self):
        allowed = pd.DataFrame([
            {"id": "cat-1", "category_name": "Groceries", "sub_category_name": "Produce"},
        ])
        with patch.object(database, "get_personal_household_integration", return_value=False), \
             patch.object(database, "get_member_obligation_expense_categories", return_value=allowed), \
             patch.object(database, "log_expense_and_check_project", return_value=True) as log_mock:
            ok = database.log_household_expense_from_personal(
                auth_user_id="u1",
                username="Angelle",
                household_id="home-1",
                month_year="2026-06",
                date_logged="2026-06-15",
                category_id="cat-1",
                amount=25.0,
                details="test",
            )
        self.assertTrue(ok)
        log_mock.assert_called_once()
        self.assertFalse(log_mock.call_args.kwargs.get("is_personal_spend", True))

    def test_get_personal_ledger_expenses_includes_household_obligation_when_integrated(self):
        all_df = pd.DataFrame([
            {"id": "e1", "category_id": "cat-p", "amount": 10.0, "is_personal_spend": True, "date_logged": "2026-06-01"},
            {"id": "e2", "category_id": "cat-h", "amount": 25.0, "is_personal_spend": False, "date_logged": "2026-06-02"},
            {"id": "e3", "category_id": "cat-other", "amount": 99.0, "is_personal_spend": False, "date_logged": "2026-06-03"},
        ])
        obl_cats = pd.DataFrame([{"id": "cat-h", "category_name": "Groceries", "sub_category_name": "Produce"}])
        with patch.object(database, "get_individual_expenses", return_value=all_df), \
             patch.object(database, "get_personal_household_integration", return_value=True), \
             patch.object(database, "get_member_obligation_expense_categories", return_value=obl_cats):
            result = database.get_personal_ledger_expenses("home-1", "auth-1", "2026-06", "Angelle")

        self.assertEqual(len(result), 2)
        sources = set(result["ledger_source"].tolist())
        self.assertEqual(sources, {"personal", "household_obligation"})

    def test_get_personal_household_integration_falls_back_to_legacy_column(self):
        supabase = unittest.mock.MagicMock()
        table = supabase.table.return_value
        chain = table.select.return_value
        chain.eq.return_value = chain
        chain.limit.return_value.execute.return_value.data = [
            {"integrate_household_on_personal": None, "show_obligation_transfers_on_personal": True}
        ]
        with patch.object(database, "supabase", supabase), \
             patch.object(database, "get_budget_table", return_value="user_finance_settings"):
            self.assertTrue(database.get_personal_household_integration("home-1", "Angelle"))


if __name__ == "__main__":
    unittest.main()
