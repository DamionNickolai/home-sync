"""Shared Supabase PostgreSQL connection URL helpers for maintenance scripts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=ROOT / ".env", override=False)
    except ImportError:
        pass


def _project_ref_from_supabase_url(url: str) -> str | None:
    match = re.search(r"https?://([a-z0-9-]+)\.supabase\.co", url.strip(), re.I)
    return match.group(1) if match else None


def _build_direct_url(password: str, project_ref: str) -> str:
    encoded = quote(password, safe="")
    return f"postgresql://postgres:{encoded}@db.{project_ref}.supabase.co:5432/postgres"


def _normalize_password_in_url(url: str) -> tuple[str, str | None]:
    """Fix common copy-paste mistakes. Returns (url, warning_or_none)."""
    warning = None

    # Supabase UI placeholder: postgresql://postgres:[YOUR-PASSWORD]@host...
    if ":[YOUR-PASSWORD]@" in url or ":[your-password]@" in url.lower():
        return url, (
            "Connection string still contains [YOUR-PASSWORD]. "
            "Replace it with your actual database password from Supabase → Connect → Direct connection."
        )

    # Bracket-wrapped password from copying the placeholder literally, e.g. [abc123]
    bracket_match = re.search(
        r"^postgresql://postgres:\[([^@\]]+)\]@",
        url,
        re.I,
    )
    if bracket_match:
        inner = bracket_match.group(1)
        encoded = quote(inner, safe="")
        url = re.sub(
            r"^postgresql://postgres:\[[^@\]]+\]@",
            f"postgresql://postgres:{encoded}@",
            url,
            count=1,
            flags=re.I,
        )
        warning = (
            "Removed square brackets around the database password in SUPABASE_DB_URL. "
            "Supabase shows [password] as a placeholder — do not include the brackets."
        )

    return url, warning


def load_db_url() -> tuple[str | None, str | None]:
    """Return (connection_url, warning_message)."""
    _load_dotenv()

    for env_key in ("SUPABASE_DB_URL", "DATABASE_URL"):
        val = os.environ.get(env_key)
        if val:
            url, warning = _normalize_password_in_url(val.strip())
            return url, warning

    # Plain password + REST URL avoids URL-encoding issues in .env
    db_password = os.environ.get("SUPABASE_DB_PASSWORD")
    if db_password:
        project_ref = os.environ.get("SUPABASE_PROJECT_REF")
        if not project_ref:
            project_ref = _project_ref_from_supabase_url(os.environ.get("SUPABASE_URL", ""))
        if project_ref:
            pwd = db_password.strip()
            if pwd.startswith("[") and pwd.endswith("]"):
                pwd = pwd[1:-1]
            return _build_direct_url(pwd, project_ref), None

    try:
        import streamlit as st  # type: ignore

        db_section = st.secrets.get("database") or {}
        if isinstance(db_section, dict):
            url = db_section.get("url") or db_section.get("SUPABASE_DB_URL")
            if url:
                normalized, warning = _normalize_password_in_url(str(url).strip())
                return normalized, warning
        direct = st.secrets.get("SUPABASE_DB_URL")
        if direct:
            normalized, warning = _normalize_password_in_url(str(direct).strip())
            return normalized, warning
    except Exception:
        pass

    return None, None


def connect():
    """Return psycopg2 connection or raise with a helpful message."""
    import psycopg2  # type: ignore

    db_url, warning = load_db_url()
    if not db_url:
        raise RuntimeError(
            "No database URL found.\n"
            "Set SUPABASE_DB_URL in .env, or set SUPABASE_DB_PASSWORD with SUPABASE_URL.\n"
            "Get the password from Supabase → Connect → Direct connection (not the API keys)."
        )

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        msg = str(exc).lower()
        hint = ""
        if "password authentication failed" in msg:
            hint = (
                "\n\nPassword auth failed. Check:\n"
                "  1. Use the DATABASE password from Supabase → Connect → Direct connection.\n"
                "     This is NOT SUPABASE_SERVICE_KEY or SUPABASE_KEY.\n"
                "  2. Do not include [brackets] around the password.\n"
                "  3. If the password has special characters (@ # : / etc.), use instead:\n"
                "       SUPABASE_DB_PASSWORD=your-plain-password\n"
                "       SUPABASE_URL=https://your-ref.supabase.co\n"
                "  4. Reset the database password: Project Settings → Database → Reset database password."
            )
        raise RuntimeError(f"Could not connect to database: {exc}{hint}") from exc

    return conn, warning
