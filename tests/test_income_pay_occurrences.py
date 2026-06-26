"""Phase 2 income schedule tests (no database required)."""

from __future__ import annotations

import unittest
from datetime import date

from income_schedule import (
    paycheck_occurrences_in_month,
    pay_dates_in_month,
    resolve_version_at_date,
)


class IncomePayOccurrenceTests(unittest.TestCase):
    def test_biweekly_june_has_two_pay_dates(self):
        versions = [
            {
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "take_home_amount": 1000,
            }
        ]
        dates = pay_dates_in_month(versions, "2026-06")
        self.assertEqual(dates, [date(2026, 6, 5), date(2026, 6, 19)])

    def test_mid_month_raise_splits_june_paychecks(self):
        versions = [
            {
                "id": "v1",
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "take_home_amount": 1000,
            },
            {
                "id": "v2",
                "effective_from": "2026-06-19",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 19,
                "take_home_amount": 1500,
            },
        ]
        occurrences = paycheck_occurrences_in_month(versions, "2026-06")
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0]["payment_date"], date(2026, 6, 5))
        self.assertEqual(occurrences[0]["version"]["id"], "v1")
        self.assertEqual(occurrences[0]["version"]["take_home_amount"], 1000)
        self.assertEqual(occurrences[1]["payment_date"], date(2026, 6, 19))
        self.assertEqual(occurrences[1]["version"]["id"], "v2")
        self.assertEqual(occurrences[1]["version"]["take_home_amount"], 1500)

    def test_raise_effective_june_9_uses_new_rate_on_june_19_only(self):
        versions = [
            {
                "id": "v1",
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "take_home_amount": 1000,
            },
            {
                "id": "v2",
                "effective_from": "2026-06-09",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 9,
                "take_home_amount": 1500,
            },
        ]
        self.assertEqual(
            resolve_version_at_date(versions, date(2026, 6, 5))["id"],
            "v1",
        )
        self.assertEqual(
            resolve_version_at_date(versions, date(2026, 6, 19))["id"],
            "v2",
        )
        occurrences = paycheck_occurrences_in_month(versions, "2026-06")
        self.assertEqual(occurrences[0]["version"]["take_home_amount"], 1000)
        self.assertEqual(occurrences[1]["version"]["take_home_amount"], 1500)

    def test_prior_month_unchanged_after_july_raise(self):
        versions = [
            {
                "id": "v1",
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "take_home_amount": 1000,
            },
            {
                "id": "v2",
                "effective_from": "2026-07-01",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 1,
                "take_home_amount": 1500,
            },
        ]
        may = paycheck_occurrences_in_month(versions, "2026-05")
        june = paycheck_occurrences_in_month(versions, "2026-06")
        july = paycheck_occurrences_in_month(versions, "2026-07")
        self.assertEqual(may, [])
        self.assertEqual(len(june), 2)
        self.assertTrue(all(o["version"]["id"] == "v1" for o in june))
        self.assertTrue(any(o["version"]["id"] == "v2" for o in july))


class IncomeMonthTotalTests(unittest.TestCase):
    def test_materialized_occurrence_rows_sum_as_single_payments(self):
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

        rows = [
            {
                "stream_id": "stream-1",
                "pay_frequency": "bi_weekly",
                "take_home_amount": 1000,
                "month_year": "2026-06",
            },
            {
                "stream_id": "stream-1",
                "pay_frequency": "bi_weekly",
                "take_home_amount": 1500,
                "month_year": "2026-06",
            },
        ]
        import pandas as pd

        total = database.sum_income_for_month(pd.DataFrame(rows), "2026-06")
        self.assertEqual(total, 2500.0)


if __name__ == "__main__":
    unittest.main()
