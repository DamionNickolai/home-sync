import importlib
import sys
import types
import unittest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def passthrough_decorator(func=None, **_kwargs):
    if func is not None:
        return func

    def decorator(inner):
        return inner

    return decorator


fake_streamlit = types.ModuleType("streamlit")
fake_streamlit.session_state = {}
fake_streamlit.secrets = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "anon-key",
    "SUPABASE_SERVICE_KEY": "service-key",
    "app_config": {},
}
fake_streamlit.cache_resource = passthrough_decorator
fake_streamlit.cache_data = passthrough_decorator
fake_streamlit.rerun = lambda: None


class FakeCookieController:
    def __init__(self, *args, **kwargs):
        self._cookies = {}

    def get(self, key):
        return self._cookies.get(key)

    def set(self, key, value, **_kwargs):
        self._cookies[key] = value

    def remove(self, key, **_kwargs):
        self._cookies.pop(key, None)


fake_cookie_module = types.ModuleType("streamlit_cookies_controller")
fake_cookie_module.CookieController = FakeCookieController

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *_args, **_kwargs: MagicMock()

sys.modules.setdefault("streamlit", fake_streamlit)
sys.modules.setdefault("streamlit_cookies_controller", fake_cookie_module)
sys.modules.setdefault("supabase", fake_supabase)
sys.modules.setdefault("pandas", types.ModuleType("pandas"))

auth = importlib.import_module("auth")
database = importlib.import_module("database")


class QueryStub:
    def __init__(self, data=None):
        self.data = data or []
        self.eq_calls = []
        self.in_calls = []
        self.ilike_calls = []
        self.update_payload = None
        self.insert_payload = None
        self.deleted = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eq_calls.append((column, value))
        return self

    def in_(self, column, values):
        self.in_calls.append((column, values))
        return self

    def ilike(self, column, value):
        self.ilike_calls.append((column, value))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def insert(self, payload):
        self.insert_payload = payload
        return self

    def delete(self):
        self.deleted = True
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class AuthRegressionTests(unittest.TestCase):
    def setUp(self):
        auth.st.session_state = {}
        database.st.session_state = {}

    def test_check_password_returns_true_for_hydrated_session(self):
        controller = MagicMock()
        auth.st.session_state = {
            "password_correct": True,
            "session_id": "session-1",
            "cookie_controller": controller,
        }

        with patch.object(auth, "get_auth_client", return_value=MagicMock()), \
             patch.object(auth, "get_user_data_client", return_value=object()), \
             patch.object(auth, "ensure_session_cookie") as ensure_cookie:
            self.assertTrue(auth.check_password())

        ensure_cookie.assert_called_once_with(controller)

    def test_get_app_user_record_requires_auth_user_id_mapping(self):
        auth_id_query = QueryStub([])
        supabase = MagicMock()
        supabase.table.return_value = auth_id_query

        result = auth.get_app_user_record(supabase, auth_user_id="auth-user-1")

        self.assertIsNone(result)
        self.assertIn(("auth_user_id", "auth-user-1"), auth_id_query.eq_calls)

    def test_get_app_user_record_returns_mapped_user(self):
        auth_id_query = QueryStub([{"id": 7, "username": "Casey", "auth_user_id": "auth-user-1"}])
        supabase = MagicMock()
        supabase.table.return_value = auth_id_query

        result = auth.get_app_user_record(supabase, auth_user_id="auth-user-1")

        self.assertEqual(result["username"], "Casey")
        self.assertEqual(result["auth_user_id"], "auth-user-1")
        self.assertIn(("auth_user_id", "auth-user-1"), auth_id_query.eq_calls)

    def test_clear_auth_session_removes_server_side_auth_state(self):
        auth.st.session_state = {
            "password_correct": True,
            "auth_access_token": "access",
            "auth_refresh_token": "refresh",
            "auth_user_id": "auth-user-1",
            "username": "Casey",
        }

        auth.clear_auth_session()

        self.assertEqual(auth.st.session_state, {})


class DatabaseRegressionTests(unittest.TestCase):
    def setUp(self):
        database.st.session_state = {"household_id": "house-7", "user_role": "developer"}

    def test_batch_update_tasks_scopes_household_id(self):
        query = QueryStub([])
        supabase = MagicMock()
        supabase.table.return_value = query

        with patch.object(database, "supabase", supabase):
            self.assertTrue(database.batch_update_tasks([42], True))

        supabase.table.assert_called_with(database.TASK_TABLE)
        self.assertIn(("id", 42), query.eq_calls)
        self.assertIn(("household_id", "house-7"), query.eq_calls)

    def test_delete_task_uses_task_table_and_household_filter(self):
        query = QueryStub([])
        supabase = MagicMock()
        supabase.table.return_value = query

        with patch.object(database, "supabase", supabase):
            self.assertTrue(database.delete_task(99))

        supabase.table.assert_called_with(database.TASK_TABLE)
        self.assertTrue(query.deleted)
        self.assertIn(("id", 99), query.eq_calls)
        self.assertIn(("household_id", "house-7"), query.eq_calls)

    def test_require_privileged_user_blocks_non_developer(self):
        database.st.session_state = {"household_id": "house-7", "user_role": "member"}

        with self.assertRaises(PermissionError):
            database.require_privileged_user()

    def test_update_task_can_clear_target_date(self):
        query = QueryStub([])
        supabase = MagicMock()
        supabase.table.return_value = query

        with patch.object(database, "supabase", supabase):
            result = database.update_task(task_id=5, clear_target_date=True)

        self.assertTrue(result)
        self.assertEqual(query.update_payload.get("target_date"), None)
        self.assertIn(("id", 5), query.eq_calls)
        self.assertIn(("household_id", "house-7"), query.eq_calls)

    def test_batch_update_tasks_rolls_forward_recurring_task(self):
        fetch_query = QueryStub([
            {
                "id": 42,
                "task_name": "Change filter",
                "description": "Every month",
                "notes": "Use MERV 13",
                "category": "House",
                "priority": "Normal",
                "assigned_to": '["Casey"]',
                "target_date": "2026-06-01",
                "is_recurring": True,
                "recurrence_pattern": "Monthly",
            }
        ])
        update_query = QueryStub([])
        insert_query = QueryStub([])

        supabase = MagicMock()
        supabase.table.side_effect = [fetch_query, update_query, insert_query]

        with patch.object(database, "supabase", supabase):
            with patch.object(database, "_calculate_next_target_date", return_value="2026-07-01"):
                result = database.batch_update_tasks([42], True)

        self.assertTrue(result)
        self.assertEqual(fetch_query.in_calls[0], ("id", [42]))
        self.assertEqual(insert_query.insert_payload.get("task_name"), "Change filter")
        self.assertEqual(insert_query.insert_payload.get("target_date"), "2026-07-01")
        self.assertEqual(insert_query.insert_payload.get("recurrence_pattern"), "Monthly")
        self.assertEqual(insert_query.insert_payload.get("household_id"), "house-7")


if __name__ == "__main__":
    unittest.main()