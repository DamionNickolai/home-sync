import streamlit as st
from supabase import create_client
from streamlit_cookies_controller import CookieController

def get_cookie_controller():
    """Ensures every user gets their own isolated cookie reader."""
    if "cookie_controller" not in st.session_state:
        st.session_state["cookie_controller"] = CookieController()
    return st.session_state["cookie_controller"]

# ==========================================
# 🎨 LOGIN SCREEN STYLING
# ==========================================
def set_login_background(image_url):
    """
    Pulls an image securely from Supabase Storage and injects it as a 
    full-screen, mobile-friendly background with a dark readability overlay.
    """
    st.markdown(
        f"""
        <style>
        /* 🟢 1. THE BACKGROUND */
        .stApp {{
            background-image: linear-gradient(rgba(17, 24, 39, 0.5), rgba(17, 24, 39, 0.6)), 
                              url('{image_url}');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            background-repeat: no-repeat;
        }}
        
        /* 🟢 2. REMOVE INPUT BORDERS */
        div[data-baseweb="input"] {{
            border: none !important;
            background-color: rgba(30, 41, 59, 0.7) !important; 
        }}
        
        div[data-baseweb="input"]:focus-within {{
            box-shadow: none !important;
            border: none !important;
        }}

        /* 🟢 3. REMOVE OUTER FORM BORDER */
        [data-testid="stForm"] {{
            border: none !important;
            background-color: transparent !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

def check_password():
    """Returns `True` if the user has a valid Supabase Auth session or cookie."""
    
    # 🟢 NEW: Grab the private controller for this specific user
    controller = get_cookie_controller()
    
    # 1. THE INTERCEPTOR: Only check for auto-logins if we aren't currently logging out!
    if not st.session_state.get("logout_in_progress", False):

    # 1. Check if Streamlit's temporary memory still knows we are logged in.
        if st.session_state.get("password_correct", False):
            return True

        # 2. THE BRIDGE: If memory was wiped, check the 30-day browser cookie!
        cookie_session = controller.get("home_sync_session")
        if cookie_session:
            # Rebuild the entire session state instantly from the cookie backup
            for key, value in cookie_session.items():
                st.session_state[key] = value
            st.session_state["password_correct"] = True
            return True

    def perform_login(email, password):
        try:
            # Connect using the standard Supabase Anon Key
            supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
            auth_response = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            
            if auth_response.user:
                metadata = auth_response.user.user_metadata
                username = metadata.get("username")
                
                # Look up the user's settings and roles in the public table
                user_record = supabase.table("users").select("*").ilike("username", username).execute()
                
                if user_record.data:
                    db_user = user_record.data[0]
                    st.session_state["password_correct"] = True
                    st.session_state["logged_in_user"] = db_user["username"]
                    st.session_state["username"] = db_user["username"]
                    
                    # 🟢 NEW: Grab the household_id (Default to a safe string if missing)
                    st.session_state["household_id"] = db_user.get("household_id", "unassigned")
                    
                    st.session_state["user_role"] = db_user.get("role", "member") # Defaulting to member for safety
                    st.session_state["primary_color"] = db_user.get("primary_color", "#1E3A8A")
                    st.session_state["sidebar_color"] = db_user.get("sidebar_color", "#162A61")
                    st.session_state["line_color"] = db_user.get("line_color", "#60A5FA")
                    st.session_state["garmin_prefix"] = db_user.get("garmin_prefix", username.lower())
                    
                    st.query_params.clear()
                    return True
                else:
                    st.warning("⚠️ DEBUG: User authenticated, but missing from public 'users' table!")
                    return False
        except Exception as e:
            st.error(f"⚠️ DEBUG: Supabase Auth Error: {e}")
            return False

    # --- THE UI ---
    try:
        bg_url = st.secrets["app_config"]["bg_image_url"]
        set_login_background(image_url=bg_url)
    except Exception as e:
        pass # Silently fail if no background is set
    
    st.markdown("<h2 style='text-align: center;'>🔒 Home Sync Login</h2>", unsafe_allow_html=True)
        
    col1, col2, col3 = st.columns([1, 2, 1]) 
    with col2:
        with st.form("login_form"):
            entered_email = st.text_input("Email", autocomplete="email")
            entered_password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Log In")

        if submitted:
            if perform_login(email=entered_email, password=entered_password):
                st.rerun() 
            else:
                st.error("😕 Email not recognized or password incorrect")
        
    return False