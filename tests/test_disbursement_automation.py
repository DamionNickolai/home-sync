import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import database
from constants import TRANSFER_ALLOWANCE_EXPENSE_DETAILS


class DisbursementAutomationTests(unittest.TestCase):
    def test_is_transfer_allowance_expense_record(self):
        self.assertTrue(
            database.is_transfer_allowance_expense_record(
                {"details": TRANSFER_ALLOWANCE_EXPENSE_DETAILS}
            )
        )

    def test_auto_complete_due_member_transfers(self):
        due_rows = [
            {
                "id": "t1",
                "household_id": "home-1",
                "month_year": "2026-06",
                "payment_date": "2026-06-10",
                "recipient_username": "Angelle",
                "allowance_amount": 10.0,
                "obligation_amount": 0.0,
            },
        ]
        with patch.object(database, "get_due_planned_member_transfers", return_value=due_rows), \
             patch.object(database, "_apply_member_transfer_completion", return_value=True) as complete_mock:
            count = database.auto_complete_due_member_transfers("home-1", as_of=date(2026, 6, 29))

        self.assertEqual(count, 1)
        complete_mock.assert_called_once()
        self.assertEqual(complete_mock.call_args.kwargs["actor_username"], "auto")

    def test_complete_due_member_transfers_requires_privilege(self):
        with patch.object(database, "_can_edit_monthly_budget_server_side", return_value=False):
            self.assertEqual(database.complete_due_member_transfers("home-1"), -1)

    def test_complete_due_member_transfers_delegates(self):
        with patch.object(database, "_can_edit_monthly_budget_server_side", return_value=True), \
             patch.object(database, "auto_complete_due_member_transfers", return_value=3) as auto_mock:
            self.assertEqual(database.complete_due_member_transfers("home-1"), 3)
        auto_mock.assert_called_once_with("home-1", as_of=None)

    def test_sync_allowance_personal_income_skips_transfer_auto_expense(self):
        with patch.object(database, "_is_transfer_allowance_expense_id", return_value=True):
            self.assertFalse(
                database._sync_allowance_personal_income(
                    household_id="home-1",
                    expense_id="exp-transfer",
                    recipient_username="Angelle",
                    amount=100.0,
                    payment_date="2026-06-10",
                    month_year="2026-06",
                )
            )

    def test_sync_transfer_allowance_household_expense_inserts(self):
        row = {
            "id": "t1",
            "household_id": "home-1",
            "month_year": "2026-06",
            "payment_date": "2026-06-10",
            "recipient_username": "Angelle",
            "allowance_amount": 25.0,
            "household_allowance_expense_id": None,
        }
        table = MagicMock()
        table.insert.return_value.execute.return_value.data = [{"id": "exp-1"}]
        table.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        with patch.object(database, "_allowance_category_id_for_member", return_value="cat-allow"), \
             patch.object(database, "_household_budget_actor", return_value=("auth-1", "admin")), \
             patch.object(database, "get_budget_table", return_value="expenses_dev"), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            expense_id = database._sync_transfer_allowance_household_expense(row)

        self.assertEqual(expense_id, "exp-1")
        insert_payload = table.insert.call_args[0][0]
        self.assertEqual(insert_payload["category_id"], "cat-allow")
        self.assertFalse(insert_payload["is_personal_spend"])


if __name__ == "__main__":
    unittest.main()
