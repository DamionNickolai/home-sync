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
                "take_home_amount": database.encrypt_data(300.0),
                "source_member_transfer_id": database.member_transfer_income_link_key("t1", database.ALLOWANCE_INCOME_SOURCE_NAME),
            },
            {
                "id": "inc-legacy",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "stream_id": "stream-1",
                "source_expense_id": "exp-old",
                "take_home_amount": database.encrypt_data(300.0),
                "source_member_transfer_id": None,
            },
            {
                "id": "inc-unlinked-transfer",
                "source_name": database.encrypt_data("Allowance"),
                "owner_username": "Jason",
                "stream_id": None,
                "source_expense_id": None,
                "take_home_amount": database.encrypt_data(300.0),
                "source_member_transfer_id": None,
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

    def test_reconcile_materializes_planned_allowance_transfers(self):
        """Planned transfers get projected Allowance income rows before completion."""
        transfers = [
            {
                "id": "t1",
                "status": "planned",
                "recipient_username": "Angelle",
                "payment_date": "2026-07-08",
                "allowance_amount": 120.0,
                "funding_income_stream_id": "s1",
                "personal_allowance_income_id": None,
            },
            {
                "id": "t2",
                "status": "planned",
                "recipient_username": "Angelle",
                "payment_date": "2026-07-22",
                "allowance_amount": 130.0,
                "funding_income_stream_id": "s1",
                "personal_allowance_income_id": None,
            },
        ]
        transfer_table = MagicMock()
        with patch.object(database, "get_member_transfers", return_value=transfers), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_find_income_id_by_member_transfer_link", return_value=None), \
             patch.object(database, "_upsert_personal_transfer_income", side_effect=["inc-1", "inc-2"]) as upsert_mock, \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = transfer_table
            fixed = database._reconcile_transfer_allowance_incomes("home-1", "2026-07")
        self.assertEqual(upsert_mock.call_count, 2)
        self.assertEqual(fixed, 2)
        self.assertEqual(upsert_mock.call_args_list[0].kwargs["amount"], 120.0)
        self.assertEqual(upsert_mock.call_args_list[1].kwargs["amount"], 130.0)

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


class SyncDisbursementPlanTests(unittest.TestCase):
    """Tests for sync_disbursement_plan: freeze, rollover, stale detection."""

    def _schedule_rows(self, obl=100.0, allow=50.0):
        return [
            {
                "payment_date": "2026-07-15",
                "stream_id": "stream-1",
                "recipient_username": "Angelle",
                "obligation": obl,
                "allowance": allow,
            }
        ]

    def test_historical_month_is_noop(self):
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=[]), \
             patch.object(database, "get_member_transfers", return_value=[]):
            result = database.sync_disbursement_plan("home-1", "2025-01")
        self.assertEqual(result["action"], "historical-noop")
        self.assertEqual(result["inserted"], 0)

    def test_current_month_with_existing_rows_is_frozen(self):
        existing = [{
            "payment_date": "2026-06-25",
            "recipient_username": "Angelle",
            "funding_income_stream_id": "stream-1",
            "obligation_amount": 100.0,
            "allowance_amount": 50.0,
            "status": "planned",
            "id": "t1",
        }]
        schedule = [{
            "payment_date": "2026-06-25",
            "stream_id": "stream-1",
            "recipient_username": "Angelle",
            "obligation": 100.0,
            "allowance": 50.0,
        }]
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        cur = __import__("datetime").datetime.now(tz).strftime("%Y-%m")
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=existing):
            result = database.sync_disbursement_plan("home-1", cur)
        self.assertEqual(result["action"], "frozen")
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertFalse(result["stale"])

    def test_current_month_frozen_detects_stale_amounts(self):
        existing = [{
            "payment_date": "2026-06-25",
            "recipient_username": "Angelle",
            "funding_income_stream_id": "stream-1",
            "obligation_amount": 100.0,
            "allowance_amount": 50.0,
            "status": "planned",
            "id": "t1",
        }]
        schedule = [{
            "payment_date": "2026-06-25",
            "stream_id": "stream-1",
            "recipient_username": "Angelle",
            "obligation": 120.0,
            "allowance": 60.0,
        }]
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        cur = __import__("datetime").datetime.now(tz).strftime("%Y-%m")
        table = MagicMock()
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=existing), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True) as upsert_rec, \
             patch.object(database, "get_disbursement_reconciliation", return_value=None):
            result = database.sync_disbursement_plan("home-1", cur)
        self.assertEqual(result["action"], "frozen")
        self.assertTrue(result["stale"])
        upsert_rec.assert_called_once_with("home-1", cur, plan_stale=True)

    def test_next_month_rollover_inserts_new_slots(self):
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        now = __import__("datetime").datetime.now(tz)
        m = now.month
        y = now.year
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
        next_month = f"{next_y}-{next_m:02d}"
        schedule = self._schedule_rows()
        table = MagicMock()
        table.insert.return_value.execute.return_value.data = [{"id": "new-t"}]
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=[]), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True) as upsert_rec, \
             patch.object(database, "get_disbursement_reconciliation", return_value=None), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            result = database.sync_disbursement_plan("home-1", next_month)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["action"], "first-insert")
        self.assertTrue(result["new_month"])
        upsert_rec.assert_called_once()

    def test_next_month_rollover_preserves_existing_planned_row_amounts(self):
        """Planned amounts on existing rows must NOT be overwritten by the computed schedule.

        As of the July month-flip fix, amounts are frozen once written (either
        copied from prior month or first-inserted from the schedule). The computed
        schedule is used only to insert missing slots and remove orphan planned rows.
        """
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        now = __import__("datetime").datetime.now(tz)
        m = now.month
        y = now.year
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
        next_month = f"{next_y}-{next_m:02d}"
        existing = [{
            "payment_date": f"{next_month}-15",
            "recipient_username": "Angelle",
            "funding_income_stream_id": "stream-1",
            "obligation_amount": 80.0,
            "allowance_amount": 40.0,
            "status": "planned",
            "id": "t-old",
        }]
        schedule = [{
            "payment_date": f"{next_month}-15",
            "stream_id": "stream-1",
            "recipient_username": "Angelle",
            "obligation": 100.0,  # different from saved
            "allowance": 50.0,    # different from saved
        }]
        table = MagicMock()
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=existing), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True), \
             patch.object(database, "get_disbursement_reconciliation", return_value=None), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            result = database.sync_disbursement_plan("home-1", next_month)
        # Existing planned row must NOT be updated — amounts are preserved
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["inserted"], 0)
        table.update.assert_not_called()

    def test_next_month_rollover_deletes_orphan_planned_slots(self):
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        now = __import__("datetime").datetime.now(tz)
        m = now.month
        y = now.year
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
        next_month = f"{next_y}-{next_m:02d}"
        # Existing row no longer in computed schedule
        existing = [{
            "payment_date": f"{next_month}-01",
            "recipient_username": "Jason",
            "funding_income_stream_id": "stream-99",
            "obligation_amount": 50.0,
            "allowance_amount": 25.0,
            "status": "planned",
            "id": "t-orphan",
        }]
        schedule = []  # no slots computed
        table = MagicMock()
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=existing), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True), \
             patch.object(database, "get_disbursement_reconciliation", return_value=None), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            result = database.sync_disbursement_plan("home-1", next_month)
        self.assertEqual(result["deleted"], 1)

    def test_completed_rows_are_never_deleted_during_rollover(self):
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        now = __import__("datetime").datetime.now(tz)
        m = now.month
        y = now.year
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
        next_month = f"{next_y}-{next_m:02d}"
        existing = [{
            "payment_date": f"{next_month}-05",
            "recipient_username": "Jason",
            "funding_income_stream_id": "stream-1",
            "obligation_amount": 50.0,
            "allowance_amount": 25.0,
            "status": "completed",  # must not be touched
            "id": "t-done",
        }]
        schedule = []  # not in schedule
        table = MagicMock()
        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=existing), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True), \
             patch.object(database, "get_disbursement_reconciliation", return_value=None), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table
            result = database.sync_disbursement_plan("home-1", next_month)
        self.assertEqual(result["deleted"], 0)
        table.delete.assert_not_called()


class IncomeMaterilaizationTests(unittest.TestCase):
    """Tests for the full-month income materialization gate change."""

    def _make_stream(self):
        return {"id": "stream-1", "display_name": "Jason Salary", "owner_username": "jason",
                "is_personal_income": False}

    def _make_version(self, freq="biweekly"):
        return {"id": "v1", "take_home_amount": 3000.0, "gross_amount": 4000.0,
                "pay_frequency": freq, "is_windfall": False, "is_taxable": True}

    def test_future_paycheck_in_current_month_is_materialized(self):
        """A paycheck date in the future but within the month_year should write a row."""
        from datetime import date
        import database
        # Use a payment_date that is two weeks from today but still in the same month
        today = date.today()
        import calendar
        _, last = calendar.monthrange(today.year, today.month)
        future_day = min(today.day + 14, last)
        future_date = date(today.year, today.month, future_day)
        month_year = today.strftime("%Y-%m")

        table_mock = MagicMock()
        table_mock.update.return_value = table_mock
        table_mock.insert.return_value = table_mock
        table_mock.eq.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])

        with patch.object(database, "get_budget_table", return_value="household_incomes_dev"), \
             patch.object(database, "_fetch_income_occurrence_row", return_value=None), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table_mock
            result = database._materialize_income_occurrence(
                stream=self._make_stream(),
                version=self._make_version(),
                month_year=month_year,
                payment_date=future_date,
                household_id="home-1",
                existing=None,
            )
        # Should have attempted an insert, not returned False early
        self.assertTrue(result)
        table_mock.insert.assert_called_once()

    def test_payment_date_before_month_start_is_skipped(self):
        """A payment_date before the month_year start is skipped (historical guard)."""
        from datetime import date
        import database
        month_year = "2026-07"
        june_date = date(2026, 6, 25)  # Before July

        with patch.object(database, "get_budget_table", return_value="household_incomes_dev"):
            result = database._materialize_income_occurrence(
                stream=self._make_stream(),
                version=self._make_version(),
                month_year=month_year,
                payment_date=june_date,
                household_id="home-1",
                existing=None,
            )
        self.assertFalse(result)


class CopyTransferPlanTests(unittest.TestCase):
    """Tests for copy_transfer_plan_from_month."""

    def _planned_row(self, day, member, obl, allow_, stream_id="stream-1"):
        return {
            "id": f"t-{day}-{member}",
            "payment_date": f"2026-06-{day:02d}",
            "recipient_username": member,
            "funding_income_stream_id": stream_id,
            "obligation_amount": obl,
            "allowance_amount": allow_,
            "total_amount": obl + allow_,
            "status": "planned",
        }

    def test_copy_preserves_amounts_and_remaps_dates(self):
        source_rows = [
            self._planned_row(10, "Jason", 500.0, 150.0),
            self._planned_row(24, "Angelle", 400.0, 200.0),
        ]
        table_mock = MagicMock()
        table_mock.insert.return_value = table_mock
        table_mock.update.return_value = table_mock
        table_mock.eq.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])

        def pay_dates(stream_id, month_year):
            if month_year == "2026-06":
                return [date(2026, 6, 10), date(2026, 6, 24)], "bi_weekly"
            if month_year == "2026-07":
                return [date(2026, 7, 8), date(2026, 7, 22)], "bi_weekly"
            return [], "monthly"

        with patch.object(database, "get_member_transfers", side_effect=lambda hid, m: source_rows if m == "2026-06" else []), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_can_edit_monthly_budget_server_side", return_value=True), \
             patch.object(database, "_stream_pay_dates_for_month", side_effect=pay_dates), \
             patch.object(database, "encrypt_data", side_effect=lambda x: x), \
             patch.object(database, "supabase") as supabase_mock:
            supabase_mock.table.return_value = table_mock
            result = database.copy_transfer_plan_from_month("home-1", "2026-06", "2026-07")

        self.assertEqual(result["copied"], 2)
        self.assertEqual(result["skipped"], 0)
        insert_calls = [call[0][0] for call in table_mock.insert.call_args_list]
        dates = sorted(c["payment_date"] for c in insert_calls)
        self.assertEqual(dates, ["2026-07-08", "2026-07-22"])

    def test_remap_biweekly_occurrence_not_day_of_month(self):
        def pay_dates(stream_id, month_year):
            if month_year == "2026-06":
                return [date(2026, 6, 10), date(2026, 6, 24)], "bi_weekly"
            if month_year == "2026-07":
                return [date(2026, 7, 8), date(2026, 7, 22)], "bi_weekly"
            return [], "monthly"

        with patch.object(database, "_stream_pay_dates_for_month", side_effect=pay_dates):
            first = database.remap_transfer_payment_date_between_months(
                "stream-1", date(2026, 6, 10), "2026-06", "2026-07"
            )
            second = database.remap_transfer_payment_date_between_months(
                "stream-1", date(2026, 6, 24), "2026-06", "2026-07"
            )
        self.assertEqual(first, "2026-07-08")
        self.assertEqual(second, "2026-07-22")

    def test_copy_skips_completed_target_rows(self):
        source_rows = [self._planned_row(10, "Jason", 500.0, 150.0)]
        completed_target = [{
            "id": "t-done", "payment_date": "2026-07-10",
            "recipient_username": "Jason",
            "funding_income_stream_id": "stream-1",
            "obligation_amount": 500.0, "allowance_amount": 150.0,
            "status": "completed",
        }]

        with patch.object(database, "get_member_transfers", side_effect=lambda hid, m: source_rows if m == "2026-06" else completed_target), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_can_edit_monthly_budget_server_side", return_value=True), \
             patch.object(database, "encrypt_data", side_effect=lambda x: x), \
             patch.object(database, "supabase") as supabase_mock:
            result = database.copy_transfer_plan_from_month("home-1", "2026-06", "2026-07")

        self.assertEqual(result["copied"], 0)
        self.assertEqual(result["skipped"], 1)
        supabase_mock.table.return_value.insert.assert_not_called()

    def test_sync_prefers_prior_month_copy_on_first_insert(self):
        """sync_disbursement_plan should copy from prior month when no rows exist yet."""
        tz = __import__("zoneinfo").ZoneInfo("America/Chicago")
        now = __import__("datetime").datetime.now(tz)
        month_year = now.strftime("%Y-%m")

        schedule = [{
            "payment_date": f"{month_year}-10",
            "recipient_username": "Jason",
            "stream_id": "stream-1",
            "obligation": 500.0,
            "allowance": 150.0,
        }]

        with patch.object(database, "_transfer_rows_from_disbursement_schedule", return_value=schedule), \
             patch.object(database, "get_member_transfers", return_value=[]), \
             patch.object(database, "get_member_transfers_table", return_value="transfers_dev"), \
             patch.object(database, "_upsert_disbursement_reconciliation", return_value=True), \
             patch.object(database, "get_disbursement_reconciliation", return_value=None), \
             patch.object(database, "_can_edit_monthly_budget_server_side", return_value=True), \
             patch.object(database, "copy_transfer_plan_from_month", return_value={"copied": 2, "skipped": 0}) as copy_mock:
            result = database.sync_disbursement_plan("home-1", month_year)

        copy_mock.assert_called_once()
        self.assertEqual(result["action"], "first-insert-from-prior-month")
        self.assertEqual(result["inserted"], 2)


class DisbursementReadinessTests(unittest.TestCase):
    def test_readiness_missing_income_streams(self):
        with patch.object(database, "get_household_income_stream_options", return_value=[]), \
             patch.object(database, "compute_household_obligations", return_value={"lines": []}), \
             patch.object(database, "compute_household_disbursement_plan", return_value={"member_bundled_amounts": {}}):
            result = database.get_disbursement_readiness("home-1")
        self.assertFalse(result["ready"])
        self.assertFalse(result["has_income_streams"])

    def test_readiness_complete(self):
        with patch.object(database, "get_household_income_stream_options", return_value=[{"stream_id": "s1"}]), \
             patch.object(database, "compute_household_obligations", return_value={
                 "lines": [{"member_username": "Angelle", "category_id": "c1", "projected_amount": 100.0}]
             }), \
             patch.object(database, "compute_household_disbursement_plan", return_value={
                 "member_bundled_amounts": {"Angelle": {"total_amount": 100.0}}
             }), \
             patch.object(database, "get_member_funding_streams", return_value=["s1"]):
            result = database.get_disbursement_readiness("home-1")
        self.assertTrue(result["ready"])
        self.assertEqual(result["members_missing_streams"], [])


if __name__ == "__main__":
    unittest.main()
