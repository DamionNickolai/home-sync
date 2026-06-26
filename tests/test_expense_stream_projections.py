"""Expense stream projection tests (no database required)."""

from __future__ import annotations

import unittest

from expense_schedule import bill_occurrences_in_month


class ExpenseStreamProjectionMathTests(unittest.TestCase):
    def test_biweekly_stream_projects_two_payments_in_june(self):
        versions = [
            {
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "amount": 100,
            }
        ]
        occurrences = bill_occurrences_in_month(versions, "2026-06")
        projected = sum(float(o["version"]["amount"]) for o in occurrences)
        self.assertEqual(projected, 200.0)

    def test_monthly_stream_normalizes_to_single_payment(self):
        from database import normalize_expense_amount_for_month

        projected = normalize_expense_amount_for_month(250, "monthly", month_year="2026-06")
        self.assertEqual(projected, 250.0)

    def test_biweekly_monthly_target_splits_across_june_occurrences(self):
        from database import monthly_amount_to_per_payment

        versions = [
            {
                "effective_from": "2026-06-05",
                "pay_frequency": "bi_weekly",
                "payment_anchor_day": 5,
                "amount": 100,
            }
        ]
        per_payment = monthly_amount_to_per_payment(
            850, "bi_weekly", month_year="2026-06", versions=versions
        )
        self.assertEqual(per_payment, 425.0)


if __name__ == "__main__":
    unittest.main()
