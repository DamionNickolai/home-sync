import unittest

from household_obligations import (
    aggregate_member_obligations,
    build_assignment_maps,
    build_parent_summaries,
    compute_allowance_coverage,
    compute_supplement_gap,
    is_assignable_household_category,
    reconcile_displacement,
    resolve_obligation_lines,
)


class HouseholdObligationMathTests(unittest.TestCase):
    def setUp(self):
        self.categories = [
            {
                "id": "cat-groc",
                "category_name": "Food",
                "sub_category_name": "Groceries",
                "target_budget": 1500.0,
                "is_personal": False,
            },
            {
                "id": "cat-dine",
                "category_name": "Food",
                "sub_category_name": "Dining Out",
                "target_budget": 200.0,
                "is_personal": False,
            },
            {
                "id": "cat-util",
                "category_name": "Utilities",
                "sub_category_name": "Electricity",
                "target_budget": 300.0,
                "is_personal": False,
            },
        ]
        self.projections = {}  # unused; obligations use target_budget only

    def test_parent_assignment_rolls_up_subcategories(self):
        parent_map, override_map = build_assignment_maps(
            [
                {
                    "assignment_level": "parent",
                    "parent_category_name": "Food",
                    "member_username": "wife",
                    "is_active": True,
                }
            ]
        )
        lines = resolve_obligation_lines(
            self.categories, parent_map, override_map
        )
        totals = aggregate_member_obligations(lines)
        self.assertEqual(totals["wife"], 1700.0)

    def test_subcategory_override_splits_parent(self):
        parent_map, override_map = build_assignment_maps(
            [
                {
                    "assignment_level": "parent",
                    "parent_category_name": "Food",
                    "member_username": "wife",
                    "is_active": True,
                },
                {
                    "assignment_level": "subcategory",
                    "category_id": "cat-dine",
                    "member_username": "husband",
                    "is_active": True,
                },
            ]
        )
        lines = resolve_obligation_lines(
            self.categories, parent_map, override_map
        )
        totals = aggregate_member_obligations(lines)
        self.assertEqual(totals["wife"], 1500.0)
        self.assertEqual(totals["husband"], 200.0)

    def test_displacement_reconcile_identity(self):
        parent_map, override_map = build_assignment_maps(
            [
                {
                    "assignment_level": "parent",
                    "parent_category_name": "Food",
                    "member_username": "wife",
                    "is_active": True,
                }
            ]
        )
        lines = resolve_obligation_lines(
            self.categories, parent_map, override_map
        )
        displacement = reconcile_displacement(lines)
        self.assertEqual(displacement["total_hh_projected"], 2000.0)
        self.assertEqual(displacement["total_assigned"], 1700.0)
        self.assertEqual(displacement["total_unassigned"], 300.0)
        self.assertEqual(displacement["unassigned_parents"], ["Utilities"])

    def test_supplement_gap_wife_groceries_scenario(self):
        gap = compute_supplement_gap(1500.0, 1365.82)
        self.assertAlmostEqual(gap, 134.18, places=2)

    def test_allowance_coverage_target_and_shortfall(self):
        coverage = compute_allowance_coverage(1500.0, 1365.82, 0.0)
        self.assertAlmostEqual(coverage["target_recurring_allowance"], 134.18, places=2)
        self.assertAlmostEqual(coverage["shortfall"], 134.18, places=2)
        self.assertFalse(coverage["is_covered"])

    def test_allowance_coverage_when_recurring_matches_target(self):
        coverage = compute_allowance_coverage(1500.0, 1365.82, 134.18)
        self.assertTrue(coverage["is_covered"])
        self.assertFalse(coverage["needs_allowance_update"])

    def test_allowance_coverage_partial_recurring_still_short(self):
        coverage = compute_allowance_coverage(1500.0, 1365.82, 50.0)
        self.assertAlmostEqual(coverage["shortfall"], 84.18, places=2)
        self.assertAlmostEqual(coverage["allowance_adjustment"], 84.18, places=2)

    def test_supplement_gap_never_negative(self):
        self.assertEqual(compute_supplement_gap(1000.0, 1200.0), 0.0)

    def test_parent_summaries_flags_unassigned_subs(self):
        parent_map, override_map = build_assignment_maps([])
        lines = resolve_obligation_lines(
            self.categories, parent_map, override_map
        )
        summaries = build_parent_summaries(lines, parent_map)
        food = next(s for s in summaries if s["parent_category_name"] == "Food")
        self.assertEqual(len(food["unassigned_subs"]), 2)

    def test_taxes_category_not_assignable_for_obligations(self):
        taxes_row = {
            "id": "cat-tax",
            "category_name": "Taxes",
            "sub_category_name": "General",
            "target_budget": 500.0,
            "is_personal": False,
        }
        self.assertFalse(is_assignable_household_category(taxes_row))

        parent_map, override_map = build_assignment_maps(
            [
                {
                    "assignment_level": "parent",
                    "parent_category_name": "Taxes",
                    "member_username": "wife",
                    "is_active": True,
                }
            ]
        )
        lines = resolve_obligation_lines(
            self.categories + [taxes_row], parent_map, override_map
        )
        tax_lines = [line for line in lines if line.get("parent_category_name") == "Taxes"]
        self.assertEqual(tax_lines, [])
        summaries = build_parent_summaries(lines, parent_map)
        self.assertNotIn("Taxes", [s["parent_category_name"] for s in summaries])


if __name__ == "__main__":
    unittest.main()
