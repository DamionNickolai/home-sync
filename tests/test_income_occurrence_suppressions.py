import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import database


class IncomeOccurrenceSuppressionTests(unittest.TestCase):
    def test_delete_month_only_records_suppression_for_stream_row(self):
        income_id = "inc-1"
        row = {
            "id": income_id,
            "household_id": "home-1",
            "month_year": "2026-06",
            "stream_id": "stream-1",
            "payment_date": "2026-06-15",
        }
        with patch.object(database, "_household_income_is_allowance_linked", return_value=False), \
             patch.object(database, "_can_edit_household_income_server_side", return_value=True), \
             patch.object(database, "_fetch_household_income_row", return_value=row), \
             patch.object(database, "_payment_date_for_income_row", return_value="2026-06-15"), \
             patch.object(database, "delete_household_income", return_value=True) as delete_mock, \
             patch.object(database, "_record_income_occurrence_suppression") as record_mock:
            self.assertTrue(database.delete_household_income_month_only(income_id))

        delete_mock.assert_called_once_with(income_id)
        record_mock.assert_called_once_with(
            household_id="home-1",
            stream_id="stream-1",
            month_year="2026-06",
            payment_date="2026-06-15",
        )

    def test_delete_month_only_links_legacy_row_before_suppressing(self):
        income_id = "inc-2"
        row = {
            "id": income_id,
            "household_id": "home-1",
            "month_year": "2026-06",
            "stream_id": None,
            "payment_date": "2026-06-01",
            "is_recurring": True,
        }
        with patch.object(database, "_household_income_is_allowance_linked", return_value=False), \
             patch.object(database, "_can_edit_household_income_server_side", return_value=True), \
             patch.object(database, "_fetch_household_income_row", return_value=row), \
             patch.object(database, "ensure_income_stream_for_row", return_value="stream-new") as ensure_mock, \
             patch.object(database, "_payment_date_for_income_row", return_value="2026-06-01"), \
             patch.object(database, "delete_household_income", return_value=True), \
             patch.object(database, "_record_income_occurrence_suppression") as record_mock:
            self.assertTrue(database.delete_household_income_month_only(income_id))

        ensure_mock.assert_called_once_with(income_id)
        record_mock.assert_called_once_with(
            household_id="home-1",
            stream_id="stream-new",
            month_year="2026-06",
            payment_date="2026-06-01",
        )

    def test_materialize_skips_suppressed_payment_date(self):
        stream_id = "stream-1"
        household_id = "home-1"
        month_year = "2026-06"
        stream = {
            "id": stream_id,
            "household_id": household_id,
            "owner_username": "Jason",
            "is_personal_income": False,
            "display_name": "encrypted-name",
        }
        version = {
            "id": "ver-1",
            "pay_frequency": "monthly",
            "take_home_amount": "encrypted",
            "gross_amount": "encrypted",
            "is_taxable": True,
            "is_windfall": False,
        }
        supabase = MagicMock()

        def table_router(name):
            mock = MagicMock()
            if name == database.get_income_streams_table():
                mock.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [stream]
            elif name == database.get_income_stream_versions_table():
                mock.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [version]
            elif name == database.get_budget_table("household_incomes"):
                mock.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            return mock

        supabase.table.side_effect = table_router

        with patch.object(database, "supabase", supabase), \
             patch.object(database, "_fetch_stream_versions_raw", return_value=[version]), \
             patch.object(database, "_expected_income_occurrences", return_value=[(date(2026, 6, 15), version)]), \
             patch.object(database, "_fetch_income_suppressions_for_month", return_value={stream_id: {"2026-06-15"}}), \
             patch.object(database, "_materialize_income_occurrence") as materialize_mock:
            database.materialize_income_month(stream_id, month_year, household_id)

        materialize_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
