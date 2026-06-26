import unittest

from constants import allowance_recipient_username


class AllowanceRecipientTests(unittest.TestCase):
    def test_uses_username_field_when_present(self):
        recipient = allowance_recipient_username(
            "Allowance",
            "jason",
            username_field="jason",
        )
        self.assertEqual(recipient, "jason")

    def test_falls_back_to_sub_category_name(self):
        recipient = allowance_recipient_username(
            "Allowance",
            "jason",
            username_field=None,
        )
        self.assertEqual(recipient, "jason")

    def test_non_allowance_returns_none(self):
        recipient = allowance_recipient_username(
            "Food",
            "Groceries",
            username_field="jason",
        )
        self.assertIsNone(recipient)


class AllowanceCategorySyncTests(unittest.TestCase):
    def test_in_sync_when_all_members_have_subcategories(self):
        import importlib
        import sys
        import types

        fake_streamlit = types.ModuleType("streamlit")
        fake_streamlit.session_state = {}
        fake_streamlit.secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-key",
            "app_config": {},
        }
        fake_streamlit.cache_resource = lambda f=None, **_k: f if f else (lambda inner: inner)
        fake_streamlit.cache_data = lambda f=None, **_k: f if f else (lambda inner: inner)
        sys.modules.setdefault("streamlit", fake_streamlit)

        database = importlib.import_module("database")
        importlib.reload(database)
        import pandas as pd

        database._fetch_household_users_cached = lambda _hid: [
            {"username": "testadmin"},
            {"username": "testmember"},
        ]
        database.get_budget_categories = lambda _hid, is_personal=False: pd.DataFrame(
            [
                {
                    "category_name": "Allowance",
                    "sub_category_name": "testadmin",
                    "username": "testadmin",
                },
                {
                    "category_name": "Allowance",
                    "sub_category_name": "testmember",
                    "username": "testmember",
                },
            ]
        )
        self.assertTrue(database.allowance_categories_in_sync("test_home"))

    def test_not_in_sync_when_member_missing_subcategory(self):
        import importlib
        import sys
        import types

        fake_streamlit = types.ModuleType("streamlit")
        fake_streamlit.session_state = {}
        fake_streamlit.secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-key",
            "app_config": {},
        }
        fake_streamlit.cache_resource = lambda f=None, **_k: f if f else (lambda inner: inner)
        fake_streamlit.cache_data = lambda f=None, **_k: f if f else (lambda inner: inner)
        sys.modules.setdefault("streamlit", fake_streamlit)

        database = importlib.import_module("database")
        importlib.reload(database)
        import pandas as pd

        database._fetch_household_users_cached = lambda _hid: [
            {"username": "testadmin"},
            {"username": "testdeveloper"},
        ]
        database.get_budget_categories = lambda _hid, is_personal=False: pd.DataFrame(
            [
                {
                    "category_name": "Allowance",
                    "sub_category_name": "testadmin",
                    "username": "testadmin",
                },
            ]
        )
        self.assertFalse(database.allowance_categories_in_sync("test_home"))


class AllowanceAnnualIncomeTests(unittest.TestCase):
    def test_recurring_monthly_allowance_annualizes_to_twelve_payments(self):
        import importlib
        import sys
        import types

        fake_streamlit = types.ModuleType("streamlit")
        fake_streamlit.session_state = {}
        fake_streamlit.secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-key",
            "app_config": {},
        }
        fake_streamlit.cache_resource = lambda f=None, **_k: f if f else (lambda inner: inner)
        fake_streamlit.cache_data = lambda f=None, **_k: f if f else (lambda inner: inner)
        sys.modules.setdefault("streamlit", fake_streamlit)

        database = importlib.import_module("database")
        importlib.reload(database)
        import pandas as pd

        rows = [
            {
                "source_name": "Allowance",
                "stream_id": "allowance-stream-1",
                "pay_frequency": "monthly",
                "is_recurring": True,
                "take_home_amount": 50.0,
                "gross_amount": 50.0,
                "month_year": "2026-06",
                "source_expense_id": "exp-1",
            }
        ]
        totals = database.compute_annual_income_totals(pd.DataFrame(rows))
        self.assertEqual(totals["annual_takehome"], 600.0)

    def test_recurring_biweekly_allowance_annualizes_to_twenty_six_payments(self):
        import importlib
        import sys
        import types

        fake_streamlit = types.ModuleType("streamlit")
        fake_streamlit.session_state = {}
        fake_streamlit.secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-key",
            "app_config": {},
        }
        fake_streamlit.cache_resource = lambda f=None, **_k: f if f else (lambda inner: inner)
        fake_streamlit.cache_data = lambda f=None, **_k: f if f else (lambda inner: inner)
        sys.modules.setdefault("streamlit", fake_streamlit)

        database = importlib.import_module("database")
        importlib.reload(database)
        import pandas as pd

        rows = [
            {
                "source_name": "Allowance",
                "stream_id": "allowance-stream-1",
                "pay_frequency": "bi_weekly",
                "is_recurring": True,
                "take_home_amount": 50.0,
                "gross_amount": 50.0,
                "month_year": "2026-06",
                "source_expense_id": "exp-1",
            },
            {
                "source_name": "Allowance",
                "stream_id": "allowance-stream-1",
                "pay_frequency": "bi_weekly",
                "is_recurring": True,
                "take_home_amount": 50.0,
                "gross_amount": 50.0,
                "month_year": "2026-06",
                "source_expense_id": "exp-2",
            },
        ]
        totals = database.compute_annual_income_totals(pd.DataFrame(rows))
        self.assertEqual(totals["annual_takehome"], 1300.0)

    def test_resolve_allowance_pay_frequency_from_expense_row(self):
        import importlib
        import sys
        import types

        fake_streamlit = types.ModuleType("streamlit")
        fake_streamlit.session_state = {}
        fake_streamlit.secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-key",
            "app_config": {},
        }
        fake_streamlit.cache_resource = lambda f=None, **_k: f if f else (lambda inner: inner)
        fake_streamlit.cache_data = lambda f=None, **_k: f if f else (lambda inner: inner)
        sys.modules.setdefault("streamlit", fake_streamlit)

        database = importlib.import_module("database")
        importlib.reload(database)

        freq = database._resolve_allowance_pay_frequency(
            expense_flags={"pay_frequency": "bi_weekly", "is_recurring": True}
        )
        self.assertEqual(freq, "bi_weekly")


if __name__ == "__main__":
    unittest.main()
