"""Tests for receipt expense permission matrix and post routing.

All database / Streamlit calls are mocked so these run without a live
Supabase connection or active Streamlit session.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import date


# ---------------------------------------------------------------------------
# Helpers used across test classes
# ---------------------------------------------------------------------------

def _mock_session(username="alice", auth_user_id="uid-alice", household_id="hh-1"):
    """Return a dict that mimics st.session_state for the receipt DB helpers."""
    return {
        "username": username,
        "auth_user_id": auth_user_id,
        "household_id": household_id,
    }


# ---------------------------------------------------------------------------
# Ledger target permission matrix
# ---------------------------------------------------------------------------

class TestAllowedLedgerTargets(unittest.TestCase):
    """_allowed_ledger_targets returns the correct subset per user role."""

    def _run(self, *, has_obligations, is_privileged, can_edit_projects):
        with (
            patch(
                "receipt_expense_module.get_member_obligation_expense_categories",
                return_value=MagicMock(empty=not has_obligations),
            ),
            patch(
                "receipt_expense_module._can_edit_monthly_budget_server_side",
                return_value=is_privileged,
            ),
            patch(
                "receipt_expense_module._can_edit_projects_server_side",
                return_value=can_edit_projects,
            ),
            patch(
                "receipt_expense_module._is_budget_privileged",
                return_value=is_privileged,
            ),
        ):
            from receipt_expense_module import _allowed_ledger_targets
            return _allowed_ledger_targets("hh-1", "alice")

    def test_regular_member_no_obligations(self):
        targets = self._run(has_obligations=False, is_privileged=False, can_edit_projects=False)
        self.assertEqual(targets, ["personal"])

    def test_member_with_obligations(self):
        targets = self._run(has_obligations=True, is_privileged=False, can_edit_projects=False)
        self.assertIn("personal", targets)
        self.assertIn("hh_obligation", targets)
        self.assertNotIn("hh_shared", targets)
        self.assertNotIn("project", targets)

    def test_admin_gets_hh_shared(self):
        targets = self._run(has_obligations=False, is_privileged=True, can_edit_projects=False)
        self.assertIn("hh_shared", targets)

    def test_project_editor_gets_project(self):
        targets = self._run(has_obligations=False, is_privileged=False, can_edit_projects=True)
        self.assertIn("project", targets)

    def test_admin_gets_all_except_obligation_without_assignments(self):
        targets = self._run(has_obligations=False, is_privileged=True, can_edit_projects=True)
        self.assertIn("personal", targets)
        self.assertIn("hh_shared", targets)
        self.assertIn("project", targets)
        self.assertNotIn("hh_obligation", targets)


# ---------------------------------------------------------------------------
# post_receipt_line_item routing
# ---------------------------------------------------------------------------

class TestPostReceiptLineItemRouting(unittest.TestCase):
    """post_receipt_line_item calls the correct write function per ledger_target."""

    _BASE_LINE = {
        "id": "line-1",
        "description": "Test item",
        "line_amount": 12.50,
        "ledger_target": "personal",
        "category_id": "cat-1",
        "project_budget_id": None,
        "status": "draft",
    }

    def _post(self, line_override: dict):
        import database as db

        line = {**self._BASE_LINE, **line_override}

        session = _mock_session()
        with (
            patch.object(db.st, "session_state", session),
            patch.object(db, "log_expense_and_check_project", return_value=True) as mock_log,
            patch.object(db, "log_household_expense_from_personal", return_value=True) as mock_obl,
            patch.object(db, "add_project_purchase_expense", return_value=True) as mock_proj,
            patch.object(db, "_mark_line_posted"),
            patch.object(db, "ensure_personal_uncategorized_category", return_value="cat-uncat"),
            patch.object(db, "ensure_household_uncategorized_category", return_value="cat-uncat-hh"),
            patch.object(db, "_can_edit_monthly_budget_server_side", return_value=True),
            patch.object(db, "_can_edit_projects_server_side", return_value=True),
        ):
            from database import post_receipt_line_item
            ok, msg = post_receipt_line_item(
                line,
                receipt_date=date(2026, 6, 1),
                household_id="hh-1",
                allow_uncategorized=True,
            )
        return ok, msg, mock_log, mock_obl, mock_proj

    def test_personal_calls_log_expense(self):
        ok, msg, mock_log, mock_obl, mock_proj = self._post({"ledger_target": "personal"})
        self.assertTrue(ok)
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_personal_spend"))
        mock_obl.assert_not_called()
        mock_proj.assert_not_called()

    def test_hh_shared_calls_log_expense_not_personal(self):
        ok, msg, mock_log, mock_obl, mock_proj = self._post({"ledger_target": "hh_shared"})
        self.assertTrue(ok)
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args.kwargs
        self.assertFalse(call_kwargs.get("is_personal_spend"))
        mock_obl.assert_not_called()

    def test_hh_obligation_calls_log_household_expense(self):
        ok, msg, mock_log, mock_obl, mock_proj = self._post({"ledger_target": "hh_obligation"})
        self.assertTrue(ok)
        mock_obl.assert_called_once()
        mock_log.assert_not_called()
        mock_proj.assert_not_called()

    def test_project_calls_add_project_purchase(self):
        ok, msg, mock_log, mock_obl, mock_proj = self._post({
            "ledger_target": "project",
            "project_budget_id": "proj-1",
            "category_id": None,
        })
        self.assertTrue(ok)
        mock_proj.assert_called_once()
        mock_log.assert_not_called()
        mock_obl.assert_not_called()

    def test_project_without_id_fails(self):
        ok, msg, *_ = self._post({"ledger_target": "project", "project_budget_id": None})
        self.assertFalse(ok)

    def test_zero_amount_blocked(self):
        ok, msg, *_ = self._post({"line_amount": 0})
        self.assertFalse(ok)
        self.assertIn("greater than zero", msg)


# ---------------------------------------------------------------------------
# Uncategorized category ensure helpers
# ---------------------------------------------------------------------------

class TestUncategorizedCategoryEnsure(unittest.TestCase):
    """ensure_personal_uncategorized_category creates the bucket when absent."""

    def test_creates_category_when_missing(self):
        import database as db
        import pandas as pd

        empty_df = pd.DataFrame(columns=["id", "category_name", "sub_category_name"])
        session = _mock_session()
        with (
            patch.object(db.st, "session_state", session),
            patch.object(db, "get_budget_categories", return_value=empty_df),
            patch.object(db, "insert_budget_category", return_value=True),
        ):
            # After creation, second call returns a row with the new id
            created_df = pd.DataFrame([{
                "id": "cat-uncat-new",
                "category_name": "Receipt",
                "sub_category_name": "Uncategorized",
            }])
            with patch.object(db, "get_budget_categories", side_effect=[empty_df, created_df]):
                result = db.ensure_personal_uncategorized_category("hh-1", "alice")
        self.assertEqual(result, "cat-uncat-new")

    def test_returns_existing_category(self):
        import database as db
        import pandas as pd

        existing_df = pd.DataFrame([{
            "id": "cat-already",
            "category_name": "Receipt",
            "sub_category_name": "Uncategorized",
        }])
        session = _mock_session()
        with (
            patch.object(db.st, "session_state", session),
            patch.object(db, "get_budget_categories", return_value=existing_df),
        ):
            result = db.ensure_personal_uncategorized_category("hh-1", "alice")
        self.assertEqual(result, "cat-already")


# ---------------------------------------------------------------------------
# OCR parser (no live API)
# ---------------------------------------------------------------------------

class TestOcrParser(unittest.TestCase):
    """_parse_response normalizes well-formed and edge-case model output."""

    def setUp(self):
        from receipt_ocr import _parse_response
        self._parse = _parse_response

    def test_valid_json(self):
        raw = """{"merchant":"Walmart","date":"2026-06-15","total":47.32,
                  "lines":[{"description":"Milk","amount":3.49},
                            {"description":"Bread","amount":2.79}]}"""
        result = self._parse(raw)
        self.assertEqual(result["merchant"], "Walmart")
        self.assertAlmostEqual(result["total"], 47.32)
        self.assertEqual(len(result["lines"]), 2)
        self.assertAlmostEqual(result["lines"][0]["amount"], 3.49)

    def test_strips_markdown_fences(self):
        raw = "```json\n{\"merchant\":null,\"date\":null,\"total\":null,\"lines\":[]}\n```"
        result = self._parse(raw)
        self.assertIsNone(result["merchant"])
        self.assertEqual(result["lines"], [])

    def test_null_amount_stays_none(self):
        raw = '{"merchant":"Store","date":null,"total":null,"lines":[{"description":"Item","amount":null}]}'
        result = self._parse(raw)
        self.assertIsNone(result["lines"][0]["amount"])

    def test_date_normalization_us_format(self):
        from receipt_ocr import _parse_date
        self.assertEqual(_parse_date("06/15/2026"), "2026-06-15")

    def test_invalid_json_returns_none(self):
        result = self._parse("This is not JSON at all.")
        self.assertIsNone(result)

    def test_no_api_key_returns_none(self):
        with patch("receipt_ocr._resolve_ocr_config", return_value=None):
            from receipt_ocr import extract_receipt_data
            result = extract_receipt_data(b"fake", "image/jpeg")
        self.assertIsNone(result)

    def test_auto_prefers_gemini_when_both_keys_present(self):
        from receipt_ocr import _resolve_ocr_config

        secrets = {
            "receipt_ocr": {"provider": "auto"},
            "ai": {"GEMINI_API_KEY": "g-key"},
            "openai": {"api_key": "o-key"},
        }
        with patch("receipt_ocr.st") as mock_st:
            mock_st.secrets = secrets
            config = _resolve_ocr_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.model, "gemini-3.1-flash-lite")

    def test_explicit_openai_provider(self):
        from receipt_ocr import _resolve_ocr_config

        secrets = {
            "receipt_ocr": {"provider": "openai"},
            "openai": {"api_key": "o-key"},
        }
        with patch("receipt_ocr.st") as mock_st:
            mock_st.secrets = secrets
            config = _resolve_ocr_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.provider, "openai")


# ---------------------------------------------------------------------------
# post_all_receipt_lines batch orchestration
# ---------------------------------------------------------------------------

class TestPostAllReceiptLines(unittest.TestCase):
    """post_all_receipt_lines skips posted lines and marks receipt posted on full success."""

    def test_skips_already_posted_lines(self):
        import database as db

        lines = [
            {"id": "l1", "status": "posted", "ledger_target": "personal"},
            {"id": "l2", "status": "draft", "ledger_target": "personal",
             "line_amount": 5.0, "description": "Item", "category_id": "cat-1",
             "project_budget_id": None},
        ]
        session = _mock_session()
        with (
            patch.object(db.st, "session_state", session),
            patch.object(db, "log_expense_and_check_project", return_value=True),
            patch.object(db, "_mark_line_posted"),
            patch.object(db, "update_receipt_upload"),
            patch.object(db, "ensure_personal_uncategorized_category", return_value="cat-u"),
        ):
            result = db.post_all_receipt_lines(
                "receipt-1",
                lines,
                receipt_date=date(2026, 6, 1),
                household_id="hh-1",
            )
        self.assertEqual(result["posted"], 1)
        self.assertEqual(result["failed"], 0)

    def test_marks_receipt_posted_on_full_success(self):
        import database as db

        lines = [
            {"id": "l1", "status": "draft", "ledger_target": "personal",
             "line_amount": 5.0, "description": "A", "category_id": "cat-1",
             "project_budget_id": None},
        ]
        session = _mock_session()
        with (
            patch.object(db.st, "session_state", session),
            patch.object(db, "log_expense_and_check_project", return_value=True),
            patch.object(db, "_mark_line_posted"),
            patch.object(db, "update_receipt_upload") as mock_update,
            patch.object(db, "ensure_personal_uncategorized_category", return_value="cat-u"),
        ):
            db.post_all_receipt_lines(
                "receipt-1",
                lines,
                receipt_date=date(2026, 6, 1),
                household_id="hh-1",
            )
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs if mock_update.call_args.kwargs else {}
        call_args = mock_update.call_args.args
        self.assertIn("posted", str(call_kwargs) + str(call_args))


if __name__ == "__main__":
    unittest.main()
