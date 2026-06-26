"""Expense bill-schedule tests (no database required)."""

from __future__ import annotations

import unittest
from datetime import date

from expense_schedule import bill_occurrences_in_month


class ExpenseBillOccurrenceTests(unittest.TestCase):
    def test_biweekly_june_has_two_bill_dates(self):
        versions = [
            {
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "amount": 250,
            }
        ]
        occurrences = bill_occurrences_in_month(versions, "2026-06")
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0]["date_logged"], date(2026, 6, 5))
        self.assertEqual(occurrences[1]["date_logged"], date(2026, 6, 19))

    def test_mid_month_raise_splits_june_bills(self):
        versions = [
            {
                "id": "v1",
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "amount": 100,
            },
            {
                "id": "v2",
                "effective_from": "2026-06-19",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 19,
                "amount": 150,
            },
        ]
        occurrences = bill_occurrences_in_month(versions, "2026-06")
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0]["version"]["amount"], 100)
        self.assertEqual(occurrences[1]["version"]["amount"], 150)


if __name__ == "__main__":
    unittest.main()
