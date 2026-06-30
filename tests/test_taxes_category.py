import unittest
from unittest.mock import patch

import pandas as pd


class TestTaxesCategoryEnsure(unittest.TestCase):
    def test_ensure_household_taxes_returns_existing(self):
        import database as db

        existing_df = pd.DataFrame([{
            "id": "tax-hh-1",
            "category_name": "Taxes",
            "sub_category_name": "General",
        }])
        with patch.object(db, "get_budget_categories", return_value=existing_df):
            result = db.ensure_household_taxes_category("hh-1")
        self.assertEqual(result, "tax-hh-1")

    def test_ensure_personal_taxes_creates_when_missing(self):
        import database as db

        empty_df = pd.DataFrame(columns=["id", "category_name", "sub_category_name"])
        created_df = pd.DataFrame([{
            "id": "tax-p-1",
            "category_name": "Taxes",
            "sub_category_name": "General",
        }])
        with (
            patch.object(db, "get_budget_categories", side_effect=[empty_df, created_df]),
            patch.object(db, "insert_budget_category", return_value=True) as mock_insert,
        ):
            result = db.ensure_personal_taxes_category("hh-1", "alice")
        mock_insert.assert_called_once()
        self.assertEqual(result, "tax-p-1")


class TestTaxesConstant(unittest.TestCase):
    def test_in_default_budget_categories(self):
        from constants import DEFAULT_BUDGET_CATEGORIES, TAXES_EXPENSE_CATEGORY

        match = [
            c for c in DEFAULT_BUDGET_CATEGORIES
            if c["name"] == TAXES_EXPENSE_CATEGORY["name"]
            and c["sub"] == TAXES_EXPENSE_CATEGORY["sub"]
        ]
        self.assertEqual(len(match), 1)


if __name__ == "__main__":
    unittest.main()
