import streamlit as st
from supabase import create_client
import hashlib

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
    """Returns `True` if the user has a valid password or secure URL token."""
    
    # --- 1. MAGIC LINK / URL TOKEN CHECKER ---
    # Look for saved bookmarks containing ?user=...&auth=...
    query_params = st.query_params
    url_user = query_params.get("user")
    url_token = query_params.get("auth")

    def perform_login(username, raw_password=None, url_hash=None):
        """Core login logic handling both manual passwords and URL hashes."""
        try:
            # Connect using the secure Service Key
            supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])
            response = supabase.table("users").select("*").ilike("username", username).execute()
            
            if response.data:
                db_pass = response.data[0]["password"]
                # Generate a secure hash of the real password
                db_hash = hashlib.sha256(db_pass.encode()).hexdigest()
                
                is_valid = False
                # Check if they typed the right password OR if their URL hash matches
                if raw_password and raw_password == db_pass:
                    is_valid = True
                elif url_hash and url_hash == db_hash:
                    is_valid = True

                if is_valid:
                    st.session_state["password_correct"] = True
                    st.session_state["logged_in_user"] = response.data[0]["username"]
                    
                    # Load all the custom styling from Supabase into memory
                    user_data = response.data[0]
                    st.session_state["user_role"] = user_data.get("role", "user")
                    st.session_state["primary_color"] = user_data.get("primary_color", "#1E3A8A")
                    st.session_state["sidebar_color"] = user_data.get("sidebar_color", "#162A61")
                    st.session_state["line_color"] = user_data.get("line_color", "#60A5FA")
                    st.session_state["garmin_prefix"] = user_data.get("garmin_prefix", username.lower())
                    
                    # 🟢 INJECT MAGIC LINK: Update the browser URL so they can bookmark it
                    st.query_params["user"] = response.data[0]["username"]
                    st.query_params["auth"] = db_hash
                    return True
        except Exception as e:
            st.error(f"Database Connection Error: {e}")
        return False

    # 🟢 SILENT LOGIN: If they aren't logged in yet but have URL params, try the magic link
    if not st.session_state.get("password_correct", False) and url_user and url_token:
        if perform_login(username=url_user, url_hash=url_token):
            return True # Magic link worked, bypass the screen!

    # Return True if the user is already actively logged in
    if st.session_state.get("password_correct", False):
        return True

    # --- 2. THE UI: Restored and Cleaned Up ---
    
    # 🟢 ACTIVATE THE BACKGROUND
    
    bg_url = st.secrets["app_config"]["bg_image_url"]
    set_login_background(image_url=bg_url)
    
    st.markdown("<h2 style='text-align: center;'>🔒 Home Sync Login</h2>", unsafe_allow_html=True)
    
    def password_entered():
        # Triggered when the user clicks 'Log In'
        entered_username = st.session_state.get("username", "").strip()
        entered_password = st.session_state.get("password", "")
        
        if perform_login(username=entered_username, raw_password=entered_password):
            if "password" in st.session_state:
                del st.session_state["password"] # Clear the password from memory for security
        else:
            # 🟢 This is the ONLY time we set it to False!
            st.session_state["password_correct"] = False
            
    # Wrap in a form to keep it grouped and allow 'Enter' to submit
    col1, col2, col3 = st.columns([1, 2, 1]) 
    with col2:
        with st.form("login_form"):
            # The autocomplete attributes tell Google Passwords exactly what these are
            st.text_input("Username", key="username", autocomplete="username")
            st.text_input("Password", type="password", key="password", autocomplete="current-password")
            
            st.form_submit_button("Log In", on_click=password_entered)

        # It will only show this error if a login attempt actually failed
        if "password_correct" in st.session_state and st.session_state["password_correct"] is False:
            st.error("😕 User not known or password incorrect")
        
    return False