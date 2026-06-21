import streamlit as st
from cryptography.fernet import Fernet

@st.cache_resource
def get_cipher():
    """Initializes the encryption engine using your secret key."""
    key = st.secrets["ENCRYPTION_KEY"]
    return Fernet(key.encode())

def encrypt_data(data) -> str:
    """Scrambles any data (text or numbers) into a secure token."""
    if data is None or str(data).strip() == "":
        return ""
    return get_cipher().encrypt(str(data).encode()).decode()

def decrypt_text(encrypted_text: str) -> str:
    """Unscrambles a secure token back into plain text."""
    if not encrypted_text:
        return ""
    try:
        return get_cipher().decrypt(str(encrypted_text).encode()).decode()
    except Exception:
        return str(encrypted_text) # Fallback

def decrypt_float(encrypted_text: str) -> float:
    """Unscrambles a secure token and converts it back to a float for math/UI."""
    if not encrypted_text:
        return 0.0
    try:
        decrypted_str = get_cipher().decrypt(str(encrypted_text).encode()).decode()
        return float(decrypted_str)
    except Exception:
        # Fallback if the data is somehow unencrypted or invalid
        try:
            return float(encrypted_text)
        except ValueError:
            return 0.0