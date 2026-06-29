import unittest
from datetime import date

from household_disbursements import (
    build_paycheck_disbursement_schedule,
    compute_member_bundled_amounts,
    compute_member_transfer_needs,
    compute_surplus_pool,
    compute_surplus_shares,
    disbursement_allowance_surplus_flags,
    disbursement_review_flags,
    filter_disbursement_eligible_usernames,
    sum_transfer_allowance_total,
    summarize_monthly_disbursement,
    typical_paycheck_count_for_streams,
)


class HouseholdDisbursementTests(unittest.TestCase):
    def test_eligible_roles(self):
        users = [
            {"username": "Jason", "role": "developer"},
            {"username": "Angelle", "role": "admin"},
            {"username": "Kid", "role": "member"},
        ]
        self.assertEqual(
            filter_disbursement_eligible_usernames(users),
            ["Angelle", "Jason"],
        )

    def test_surplus_pool_and_shares(self):
        pool = compute_surplus_pool(6000.0, 5400.0)
        self.assertAlmostEqual(pool, 600.0)
        shares = compute_surplus_shares(pool, ["Jason", "Angelle"])
        self.assertAlmostEqual(shares["Jason"], 300.0)
        self.assertAlmostEqual(shares["Angelle"], 300.0)

    def test_surplus_pool_zero_when_obligations_exceed_income(self):
        pool = compute_surplus_pool(1000.0, 1500.0)
        self.assertEqual(pool, 0.0)

    def test_member_transfer_needs(self):
        by_member = {
            "Angelle": {"supplement_gap": 134.18},
            "Jason": {"supplement_gap": 0.0},
        }
        needs = compute_member_transfer_needs(by_member)
        self.assertAlmostEqual(needs["Angelle"], 134.18)
        self.assertNotIn("Jason", needs)

    def test_member_bundled_amounts(self):
        needs = {"Angelle": 134.18}
        shares = {"Jason": 300.0, "Angelle": 300.0}
        bundles = compute_member_bundled_amounts(needs, shares)

        angelle = bundles["Angelle"]
        self.assertAlmostEqual(angelle["obligation_amount"], 134.18)
        self.assertAlmostEqual(angelle["allowance_amount"], 300.0)
        self.assertAlmostEqual(angelle["total_amount"], 434.18, places=2)

        jason = bundles["Jason"]
        self.assertAlmostEqual(jason["obligation_amount"], 0.0)
        self.assertAlmostEqual(jason["allowance_amount"], 300.0)
        self.assertAlmostEqual(jason["total_amount"], 300.0)

    def test_paycheck_schedule_structured_breakdown(self):
        pay_dates = [date(2026, 6, 6), date(2026, 6, 20)]
        schedule = build_paycheck_disbursement_schedule(
            pay_dates,
            {"Angelle": 134.18},
            {"Jason": 300.0, "Angelle": 300.0},
        )
        self.assertEqual(len(schedule), 2)

        first = schedule[0]
        self.assertEqual(first["payment_date"], "2026-06-06")
        payouts = first["payouts"]

        # Angelle: obligation=134.18/2, allowance=300/2
        self.assertAlmostEqual(payouts["Angelle"]["obligation"], 134.18 / 2, places=2)
        self.assertAlmostEqual(payouts["Angelle"]["allowance"], 300.0 / 2, places=2)
        self.assertAlmostEqual(payouts["Angelle"]["total"], (134.18 + 300.0) / 2, places=2)

        # Jason: obligation=0, allowance=300/2
        self.assertAlmostEqual(payouts["Jason"]["obligation"], 0.0)
        self.assertAlmostEqual(payouts["Jason"]["allowance"], 150.0, places=2)
        self.assertAlmostEqual(payouts["Jason"]["total"], 150.0, places=2)

        # Per-paycheck total
        self.assertAlmostEqual(first["total"], (134.18 + 300.0 + 300.0) / 2, places=2)

    def test_paycheck_schedule_no_dates_returns_empty(self):
        schedule = build_paycheck_disbursement_schedule([], {"Angelle": 100.0}, {"Jason": 200.0})
        self.assertEqual(schedule, [])

    def test_summary_totals(self):
        summary = summarize_monthly_disbursement(
            {"Angelle": 134.18},
            {"Jason": 300.0, "Angelle": 300.0},
        )
        self.assertAlmostEqual(summary["member_transfer_total"], 134.18, places=2)
        self.assertAlmostEqual(summary["surplus_split_total"], 600.0, places=2)
        self.assertAlmostEqual(summary["monthly_disbursement_total"], 734.18, places=2)


class MemberTransferRulesTests(unittest.TestCase):
    """Unit tests for transfer completion / income sync rules (pure logic layer)."""

    def test_bundled_amounts_obligation_only_member(self):
        """A member with an obligation gap but no surplus share still gets a bundle row."""
        needs = {"Angelle": 200.0}
        shares = {}
        bundles = compute_member_bundled_amounts(needs, shares)
        self.assertIn("Angelle", bundles)
        self.assertAlmostEqual(bundles["Angelle"]["obligation_amount"], 200.0)
        self.assertAlmostEqual(bundles["Angelle"]["allowance_amount"], 0.0)
        self.assertAlmostEqual(bundles["Angelle"]["total_amount"], 200.0)

    def test_bundled_amounts_allowance_only_member(self):
        """A member with no obligation gap but an even surplus share."""
        needs = {}
        shares = {"Jason": 350.0}
        bundles = compute_member_bundled_amounts(needs, shares)
        self.assertIn("Jason", bundles)
        self.assertAlmostEqual(bundles["Jason"]["obligation_amount"], 0.0)
        self.assertAlmostEqual(bundles["Jason"]["allowance_amount"], 350.0)
        self.assertAlmostEqual(bundles["Jason"]["total_amount"], 350.0)

    def test_allowance_is_always_included_in_total(self):
        """Total must always equal obligation + allowance regardless of toggle state."""
        needs = {"Angelle": 67.09}
        shares = {"Angelle": 150.0}
        bundles = compute_member_bundled_amounts(needs, shares)
        expected_total = 67.09 + 150.0
        self.assertAlmostEqual(bundles["Angelle"]["total_amount"], expected_total, places=2)

    def test_typical_paycheck_count_for_streams(self):
        streams = [
            {"frequency": "bi_weekly"},
            {"frequency": "monthly"},
        ]
        self.assertEqual(typical_paycheck_count_for_streams(streams), 3)

    def test_disbursement_review_flags_extra_paycheck_month(self):
        info = {
            "Jason": {
                "streams": [{"frequency": "bi_weekly"}],
                "paycheck_count": 3,
            },
            "Angelle": {
                "streams": [{"frequency": "bi_weekly"}],
                "paycheck_count": 2,
            },
        }
        flags = disbursement_review_flags(info)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["member"], "Jason")
        self.assertEqual(flags[0]["actual_paycheck_count"], 3)
        self.assertEqual(flags[0]["typical_paycheck_count"], 2)

    def test_sum_transfer_allowance_total(self):
        transfers = [
            {"allowance_amount": 150.0},
            {"allowance_amount": 150.0},
            {"allowance_amount": 0.0, "obligation_amount": 100.0},
        ]
        self.assertAlmostEqual(sum_transfer_allowance_total(transfers), 300.0)

    def test_allowance_surplus_flags_when_planned_exceeds_pool(self):
        flags = disbursement_allowance_surplus_flags(
            current_surplus_pool=400.0,
            planned_allowance_total=600.0,
            recommended_allowance_total=400.0,
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["kind"], "allowance_exceeds_surplus")
        self.assertAlmostEqual(flags[0]["overage"], 200.0)

    def test_allowance_surplus_flags_when_stale_but_within_pool(self):
        flags = disbursement_allowance_surplus_flags(
            current_surplus_pool=600.0,
            planned_allowance_total=500.0,
            recommended_allowance_total=400.0,
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["kind"], "allowance_stale_vs_recommended")

    def test_allowance_surplus_flags_clear_when_in_sync(self):
        flags = disbursement_allowance_surplus_flags(
            current_surplus_pool=600.0,
            planned_allowance_total=600.0,
            recommended_allowance_total=600.0,
        )
        self.assertEqual(flags, [])


if __name__ == "__main__":
    unittest.main()
