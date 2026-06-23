import streamlit as st
from supabase import create_client
from security import encrypt_data, decrypt_text
try:
    from streamlit_cookies_controller import CookieController
except ImportError:
    class CookieController:
        """Fallback no-op cookie controller when optional dependency is unavailable."""

        def __init__(self, *args, **kwargs):
            self._cookies = {}

        def get(self, key):
            return self._cookies.get(key)

        def set(self, key, value, **_kwargs):
            self._cookies[key] = value

        def remove(self, key, **_kwargs):
            self._cookies.pop(key, None)

        def refresh(self):
            return None
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


SESSION_COOKIE_NAME = "home_sync_session_v2"
LEGACY_SESSION_COOKIE_NAME = "home_sync_session"
APP_TIMEZONE = ZoneInfo("America/Chicago")


def get_auth_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def get_user_data_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])


def get_cookie_controller():
    """Return a single controller instance per browser session."""
    if "cookie_controller" not in st.session_state:
        st.session_state["cookie_controller"] = CookieController(key="home_sync_cookie_cache")
    return st.session_state["cookie_controller"]


def get_device_fingerprint():
    """Generate a basic device fingerprint for session tracking."""
    if "device_fingerprint" not in st.session_state:
        st.session_state["device_fingerprint"] = str(uuid.uuid4())
    return st.session_state["device_fingerprint"]


def app_now():
    return datetime.now(APP_TIMEZONE)


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def should_use_secure_cookie():
    env = st.secrets.get("app_config", {}).get("environment", "production")
    return env != "local"


def normalize_session_id(raw_value):
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value


def write_session_cookie(controller, session_id):
    secure_cookie = should_use_secure_cookie()
    controller.set(
        SESSION_COOKIE_NAME,
        str(session_id),
        max_age=2592000,
        path="/",
        same_site="lax",
        secure=secure_cookie,
    )
    return True


def ensure_session_cookie(controller):
    session_id = st.session_state.get("session_id")
    if not session_id:
        return
    existing_cookie = normalize_session_id(controller.get(SESSION_COOKIE_NAME))
    if existing_cookie != str(session_id):
        write_session_cookie(controller, session_id)


def remove_session_cookie(controller):
    if controller is None:
        return
    try:
        controller.remove(SESSION_COOKIE_NAME, path="/", same_site="lax", secure=True)
    except Exception:
        pass
    try:
        controller.remove(SESSION_COOKIE_NAME, path="/", same_site="lax", secure=False)
    except Exception:
        pass
    try:
        controller.remove(LEGACY_SESSION_COOKIE_NAME, path="/", same_site="lax", secure=True)
    except Exception:
        pass
    try:
        controller.remove(LEGACY_SESSION_COOKIE_NAME, path="/", same_site="lax", secure=False)
    except Exception:
        pass


def create_user_session(supabase, auth_user_id, refresh_token):
    """Create a new session record in the database and return session_id."""
    try:
        session_id = str(uuid.uuid4())
        session_record = {
            "session_id": session_id,
            "auth_user_id": auth_user_id,
            "refresh_token": encrypt_data(refresh_token),
            "device_fingerprint": get_device_fingerprint(),
            "created_at": app_now().isoformat(),
            "last_accessed_at": app_now().isoformat(),
            "expires_at": (app_now() + timedelta(days=30)).isoformat(),
            "is_active": True,
        }
        supabase.table("user_sessions").insert(session_record).execute()
        return session_id
    except Exception:
        return None


def get_session_from_database(supabase, session_id):
    """Retrieve a session record from the database."""
    try:
        result = supabase.table("user_sessions").select("*").eq("session_id", session_id).limit(1).execute()
        if result.data and len(result.data) > 0:
            session = result.data[0]
            session["refresh_token"] = decrypt_text(session.get("refresh_token"))
            if session.get("is_active"):
                expires_at = parse_iso_datetime(session.get("expires_at"))
                if expires_at and app_now() < expires_at:
                    return session
            return None
        return None
    except Exception:
        return None


def refresh_session_access_time(supabase, session_id):
    """Update the last_accessed_at timestamp."""
    try:
        supabase.table("user_sessions").update(
            {"last_accessed_at": app_now().isoformat()}
        ).eq("session_id", session_id).execute()
    except Exception:
        pass


def update_session_refresh_token(supabase, session_id, refresh_token):
    if not refresh_token:
        return
    try:
        supabase.table("user_sessions").update(
            {
                "refresh_token": encrypt_data(refresh_token),
                "last_accessed_at": app_now().isoformat(),
            }
        ).eq("session_id", session_id).execute()
    except Exception:
        pass


def invalidate_user_session(supabase, session_id):
    """Mark a session as inactive."""
    try:
        supabase.table("user_sessions").update(
            {"is_active": False}
        ).eq("session_id", session_id).execute()
    except Exception:
        pass


def clear_auth_session():
    """Clear session state and invalidate the database session."""
    user_data_client = get_user_data_client()
    session_id = st.session_state.get("session_id")

    if session_id:
        invalidate_user_session(user_data_client, session_id)

    for key in [
        "password_correct",
        "auth_access_token",
        "auth_refresh_token",
        "auth_user_id",
        "logged_in_user",
        "username",
        "household_id",
        "user_role",
        "can_view_budget",
        "can_view_projects",
        "can_edit_projects",
        "can_view_monthly_budget",
        "can_edit_monthly_budget",
        "primary_color",
        "sidebar_color",
        "line_color",
        "garmin_prefix",
        "session_id",
        "auth_bootstrap_attempts",
    ]:
        st.session_state.pop(key, None)

    controller = st.session_state.get("cookie_controller")
    remove_session_cookie(controller)


def get_app_user_record(supabase, auth_user_id):
    if not auth_user_id:
        return None

    try:
        user_record = supabase.table("users").select("*").eq("auth_user_id", auth_user_id).limit(1).execute()
        if user_record.data:
            return user_record.data[0]
    except Exception:
        pass

    return None


def hydrate_user_session(db_user, auth_user_id=None):
    username = db_user["username"]

    st.session_state.pop("logout_in_progress", None)
    st.session_state["password_correct"] = True
    st.session_state["auth_user_id"] = auth_user_id or db_user.get("auth_user_id")
    st.session_state["logged_in_user"] = db_user["username"]
    st.session_state["username"] = db_user["username"]
    st.session_state["household_id"] = db_user.get("household_id", "unassigned")
    st.session_state["user_role"] = db_user.get("role", "member")
    st.session_state["can_view_budget"] = db_user.get("can_view_budget", False)
    st.session_state["can_view_projects"] = db_user.get("can_view_projects", db_user.get("can_view_budget", False))
    st.session_state["can_edit_projects"] = db_user.get("can_edit_projects", db_user.get("can_view_budget", False))
    st.session_state["can_view_monthly_budget"] = db_user.get("can_view_monthly_budget", db_user.get("can_view_budget", False))
    st.session_state["can_edit_monthly_budget"] = db_user.get("can_edit_monthly_budget", False)
    st.session_state["primary_color"] = db_user.get("primary_color", "#1E3A8A")
    st.session_state["sidebar_color"] = db_user.get("sidebar_color", "#162A61")
    st.session_state["line_color"] = db_user.get("line_color", "#60A5FA")
    st.session_state["garmin_prefix"] = db_user.get("garmin_prefix", username.lower())
    return True


def set_login_background(image_url):
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: linear-gradient(rgba(17, 24, 39, 0.5), rgba(17, 24, 39, 0.6)),
                              url('{image_url}');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            background-repeat: no-repeat;
        }}
        div[data-baseweb="input"] {{ border: none !important; background-color: rgba(30, 41, 59, 0.7) !important; }}
        div[data-baseweb="input"]:focus-within {{ box-shadow: none !important; border: none !important; }}
        [data-testid="stForm"] {{ border: none !important; background-color: transparent !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def check_password():
    """Returns True if the user has a valid Home Sync session."""
    auth_client = get_auth_client()
    user_data_client = get_user_data_client()
    had_cookie_cache = "home_sync_cookie_cache" in st.session_state
    controller = get_cookie_controller()

    if had_cookie_cache:
        try:
            controller.refresh()
        except Exception:
            pass

    if not st.session_state.get("logout_in_progress", False):
        if st.session_state.get("password_correct", False):
            ensure_session_cookie(controller)
            return True

        stored_session_id = controller.get(SESSION_COOKIE_NAME)
        stored_session_id = normalize_session_id(stored_session_id)
        if stored_session_id:
            try:
                session = get_session_from_database(user_data_client, stored_session_id)
                if session:
                    refresh_session_access_time(user_data_client, stored_session_id)
                    refresh_token = session.get("refresh_token")
                    auth_user_id = session.get("auth_user_id")

                    if refresh_token and auth_user_id:
                        try:
                            refreshed = auth_client.auth.refresh_session(refresh_token)
                            refreshed_session = getattr(refreshed, "session", None)
                            new_refresh_token = getattr(refreshed_session, "refresh_token", None)
                            if new_refresh_token:
                                update_session_refresh_token(user_data_client, stored_session_id, new_refresh_token)

                            auth_user = getattr(refreshed, "user", None)
                            if auth_user is None:
                                auth_user = getattr(getattr(refreshed, "session", None), "user", None)
                            verified_auth_user_id = getattr(auth_user, "id", None)

                            db_user = get_app_user_record(user_data_client, verified_auth_user_id)
                            if db_user and hydrate_user_session(db_user, auth_user_id=verified_auth_user_id):
                                st.session_state["session_id"] = stored_session_id
                                st.session_state.pop("auth_bootstrap_attempts", None)
                                return True
                        except Exception:
                            pass

                remove_session_cookie(controller)
            except Exception:
                pass

    if (
        not st.session_state.get("logout_in_progress", False)
        and not st.session_state.get("password_correct", False)
    ):
        bootstrap_attempts = st.session_state.get("auth_bootstrap_attempts", 0)
        if bootstrap_attempts < 5:
            st.session_state["auth_bootstrap_attempts"] = bootstrap_attempts + 1
            st.session_state["pending_rerun_reason"] = "auth_bootstrap"
            st.rerun()

    def perform_login(email, password):
        st.session_state.pop("logout_in_progress", None)
        try:
            auth_response = auth_client.auth.sign_in_with_password(
                {
                    "email": email,
                    "password": password,
                }
            )

            if auth_response.user:
                auth_user_id = getattr(auth_response.user, "id", None)
                db_user = get_app_user_record(user_data_client, auth_user_id)

                if db_user and hydrate_user_session(db_user, auth_user_id=auth_user_id):
                    session = getattr(auth_response, "session", None)
                    refresh_token = getattr(session, "refresh_token", None)

                    if refresh_token:
                        session_id = create_user_session(user_data_client, auth_user_id, refresh_token)
                        if session_id:
                            st.session_state["session_id"] = session_id
                            remove_session_cookie(controller)
                            write_ok = write_session_cookie(controller, session_id)
                            if not write_ok:
                                st.warning("Unable to persist browser session cookie. Persistent login will not work in this browser.")
                        else:
                            st.warning("Persistent login setup failed: could not create a session record.")
                    else:
                        st.warning("Persistent login setup failed: no refresh token returned by Supabase.")

                    st.query_params.clear()
                    st.session_state.pop("auth_bootstrap_attempts", None)
                    st.session_state["post_login_clean_rerun"] = True
                    return True

                st.warning("Authenticated user is not provisioned for this app. Missing auth_user_id mapping.")
                return False
            return False
        except Exception:
            st.error("Unable to sign in with the provided credentials.")
            return False

    try:
        bg_url = st.secrets["app_config"]["bg_image_url"]
        set_login_background(image_url=bg_url)
    except Exception:
        pass

    st.markdown("<h2 style='text-align: center;'>Home Sync Login</h2>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            entered_email = st.text_input("Email", autocomplete="email")
            entered_password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Log In")

        if submitted:
            if perform_login(email=entered_email, password=entered_password):
                return True
            else:
                st.error("Email not recognized or password incorrect")

    return False
