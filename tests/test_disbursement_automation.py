import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import database
from constants import (
    ALLOWANCE_INCOME_SOURCE_NAME,
    TRANSFER_ALLOWANCE_EXPENSE_DETAILS,
    member_transfer_income_link_key,
)


class DisbursementAutomationTests(unittest.TestCase):
    def test_member_transfer_income_link_key(self):
        key = member_transfer_income_link_key("abc-123", ALLOWANCE_INCOME_SOURCE_NAME)
        self.assertEqual(key, "abc-123#allowance")

    def test_upsert_prefers_plaintext_transfer_link(self):
        link_key = member_transfer_income_link_key("t1", ALLOWANCE_INCOME_SOURCE_NAME)
        table = MagicMock()
        with patch.object(database, "get_budget_table", return_value="household_incomes_dev"), \
             patch.object(database, "_find_income_id_by_member_transfer_link", return_value="inc-by-link"), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            result = database._upsert_personal_transfer_income(
                household_id="home-1",
                month_year="2026-06",
                recipient="Jason",
                pay_date_str="2026-06-25",
                source_name=ALLOWANCE_INCOME_SOURCE_NAME,
                amount=38.43,
                transfer_id="t1",
            )
        self.assertEqual(result, "inc-by-link")
        update_payload = table.update.call_args[0][0]
        self.assertEqual(update_payload["source_member_transfer_id"], link_key)
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

    def test_sync_allowance_personal_income_skips_when_disbursement_transfers_exist(self):
        with patch.object(database, "_is_transfer_allowance_expense_id", return_value=False), \
             patch.object(
                 database,
                 "_disbursement_transfers_cover_allowance",
                 return_value=True,
             ):
            self.assertFalse(
                database._sync_allowance_personal_income(
                    household_id="home-1",
                    expense_id="exp-legacy",
                    recipient_username="Jason",
                    amount=100.0,
                    payment_date="2026-06-10",
                    month_year="2026-06",
                )
            )

    def test_prune_legacy_allowance_superseded_by_transfers(self):
        transfers = [
            {
                "id": "t1",
                "status": "completed",
                "recipient_username": "Jason",
                "allowance_amount": 300.0,
                "personal_allowance_income_id": "inc-transfer",
            },
        ]
        income_rows = [
            {
                "id": "inc-transfer",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "stream_id": None,
                "source_expense_id": None,
            },
            {
                "id": "inc-legacy",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "stream_id": "stream-1",
                "source_expense_id": "exp-old",
            },
            {
                "id": "inc-unlinked-transfer",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "stream_id": None,
                "source_expense_id": None,
            },
        ]
        expense_table = MagicMock()
        expense_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        income_table = MagicMock()
        income_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = income_rows

        def table_factory(name):
            if name == "expenses_dev":
                return expense_table
            return income_table

        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_budget_table", side_effect=lambda key: {
                 "expenses": "expenses_dev",
                 "household_incomes": "household_incomes_dev",
             }[key]), \
             patch.object(database, "supabase") as supabase_mock, \
             patch.object(database, "_delete_personal_transfer_income", return_value=True) as delete_mock:
            supabase_mock.table.side_effect = table_factory
            removed = database._prune_legacy_allowance_superseded_by_transfers("home-1", "2026-06")
        self.assertEqual(removed, 1)
        delete_mock.assert_called_once_with("inc-legacy")

    def test_reconcile_shared_transfer_allowance_incomes(self):
        """Two transfers must not share one personal_allowance_income_id."""
        transfers = [
            {
                "id": "t1",
                "status": "completed",
                "recipient_username": "Jason",
                "payment_date": "2026-06-25",
                "allowance_amount": 38.43,
                "funding_income_stream_id": "s1",
                "personal_allowance_income_id": "inc-shared",
            },
            {
                "id": "t2",
                "status": "completed",
                "recipient_username": "Jason",
                "payment_date": "2026-06-25",
                "allowance_amount": 38.43,
                "funding_income_stream_id": "s2",
                "personal_allowance_income_id": "inc-shared",
            },
        ]
        transfer_table = MagicMock()
        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_find_income_id_by_member_transfer_link", return_value=None), \
             patch.object(database, "_upsert_personal_transfer_income", side_effect=["inc-shared", "inc-new"]) as upsert_mock, \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = transfer_table
            fixed = database._reconcile_transfer_allowance_incomes("home-1", "2026-06")
        self.assertEqual(fixed, 1)
        self.assertEqual(upsert_mock.call_count, 2)
        self.assertEqual(upsert_mock.call_args_list[1].kwargs["transfer_id"], "t2")
        transfer_table.update.assert_called_once()

    def test_dedupe_keeps_spare_unlinked_for_same_day_paychecks(self):
        transfers = [
            {
                "id": "t1",
                "status": "completed",
                "personal_allowance_income_id": "inc-1",
                "recipient_username": "Jason",
                "payment_date": "2026-06-25",
                "allowance_amount": 38.43,
            },
            {
                "id": "t2",
                "status": "completed",
                "personal_allowance_income_id": None,
                "recipient_username": "Jason",
                "payment_date": "2026-06-25",
                "allowance_amount": 38.43,
            },
        ]
        income_rows = [
            {
                "id": "inc-1",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "payment_date": "2026-06-25",
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(38.43),
            },
            {
                "id": "inc-2",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "payment_date": "2026-06-25",
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(38.43),
            },
        ]
        table = MagicMock()
        table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = income_rows
        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_budget_table", return_value="household_incomes_dev"), \
             patch.object(database, "supabase") as supabase_mock, \
             patch.object(database, "_delete_personal_transfer_income", return_value=True) as delete_mock:
            supabase_mock.table.return_value = table
            removed = database.dedupe_transfer_allowance_personal_incomes("home-1", "2026-06")
        self.assertEqual(removed, 0)
        delete_mock.assert_not_called()

    def test_dedupe_transfer_allowance_personal_incomes(self):
        transfers = [
            {
                "id": "t1",
                "personal_allowance_income_id": "inc-1",
                "recipient_username": "Angelle",
                "payment_date": "2026-06-10",
                "allowance_amount": 50.0,
            },
        ]
        income_rows = [
            {
                "id": "inc-1",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Angelle",
                "payment_date": "2026-06-10",
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(50.0),
            },
            {
                "id": "inc-2",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Angelle",
                "payment_date": "2026-06-10",
                "source_expense_id": "exp-9",
                "take_home_amount": database.encrypt_data(50.0),
            },
        ]
        table = MagicMock()
        table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = income_rows
        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_budget_table", return_value="household_incomes_dev"), \
             patch.object(database, "supabase") as supabase_mock, \
             patch.object(database, "_delete_personal_transfer_income", return_value=True) as delete_mock:
            supabase_mock.table.return_value = table
            removed = database.dedupe_transfer_allowance_personal_incomes("home-1", "2026-06")
        self.assertEqual(removed, 1)
        delete_mock.assert_called_once_with("inc-2")

    def test_dedupe_keeps_two_same_day_transfer_linked_incomes(self):
        """Two paychecks same date/amount — separate transfers, both incomes kept."""
        transfers = [
            {
                "id": "t1",
                "personal_allowance_income_id": "inc-1",
                "recipient_username": "Jason",
                "payment_date": "2026-06-01",
                "allowance_amount": 300.0,
            },
            {
                "id": "t2",
                "personal_allowance_income_id": "inc-2",
                "recipient_username": "Jason",
                "payment_date": "2026-06-01",
                "allowance_amount": 300.0,
            },
        ]
        income_rows = [
            {
                "id": "inc-1",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "payment_date": "2026-06-01",
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(300.0),
            },
            {
                "id": "inc-2",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "payment_date": "2026-06-01",
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(300.0),
            },
        ]
        table = MagicMock()
        table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = income_rows
        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_budget_table", return_value="household_incomes_dev"), \
             patch.object(database, "supabase") as supabase_mock, \
             patch.object(database, "_delete_personal_transfer_income", return_value=True) as delete_mock:
            supabase_mock.table.return_value = table
            removed = database.dedupe_transfer_allowance_personal_incomes("home-1", "2026-06")
        self.assertEqual(removed, 0)
        delete_mock.assert_not_called()

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
