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

fake_supabase = types.ModuleType("supabase")
fake_supabase.Client = object
fake_supabase.create_client = lambda *_args, **_kwargs: MagicMock()

sys.modules.setdefault("streamlit", fake_streamlit)
sys.modules.setdefault("supabase", fake_supabase)
sys.modules.setdefault("pandas", types.ModuleType("pandas"))

auth = importlib.import_module("auth")
database = importlib.import_module("database")


class QueryStub:
    def __init__(self, data=None):
        self.data = data or []
        self.eq_calls = []
        self.ilike_calls = []
        self.update_payload = None
        self.deleted = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eq_calls.append((column, value))
        return self

    def ilike(self, column, value):
        self.ilike_calls.append((column, value))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.update_payload = payload
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

    def test_check_password_restores_from_server_side_tokens(self):
        auth_client = MagicMock()
        verified_user = SimpleNamespace(
            user=SimpleNamespace(id="auth-user-1", user_metadata={"username": "Casey"})
        )
        auth_client.auth.get_user.return_value = verified_user

        auth.st.session_state = {
            "auth_access_token": "access-token",
            "auth_refresh_token": "refresh-token",
        }

        with patch.object(auth, "get_auth_client", return_value=auth_client), \
             patch.object(auth, "get_user_data_client", return_value=object()), \
             patch.object(auth, "get_app_user_record", return_value={"username": "Casey"}) as get_record, \
             patch.object(auth, "hydrate_user_session", return_value=True) as hydrate:
            self.assertTrue(auth.check_password())

        auth_client.auth.set_session.assert_called_once_with("access-token", "refresh-token")
        get_record.assert_called_once()
        self.assertEqual(get_record.call_args.args[1], "auth-user-1")
        hydrate.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()