"""Apply migration 015 (Home Management permission columns on users).

Usage:
  python maintenance/apply_home_management_permissions.py

Requires one of:
  - SUPABASE_DB_URL environment variable (postgresql://...)
  - [database] url in .streamlit/secrets.toml

Otherwise prints the migration path for manual execution in Supabase SQL Editor.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "015_add_home_management_permissions.sql"


def _load_db_url() -> str | None:
    env_url = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if env_url:
        return env_url.strip()

    try:
        import streamlit as st

        db_section = st.secrets.get("database") or {}
        if isinstance(db_section, dict):
            url = db_section.get("url") or db_section.get("SUPABASE_DB_URL")
            if url:
                return str(url).strip()
        direct = st.secrets.get("SUPABASE_DB_URL")
        if direct:
            return str(direct).strip()
    except Exception:
        pass

    return None


def main() -> int:
    if not MIGRATION_PATH.exists():
        print(f"Migration file not found: {MIGRATION_PATH}")
        return 1

    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    db_url = _load_db_url()

    if not db_url:
        print("No database URL found.")
        print(f"Run this SQL manually in Supabase → SQL Editor:\n  {MIGRATION_PATH}")
        print("\nOr set SUPABASE_DB_URL and re-run this script.")
        return 1

    try:
        import psycopg2
    except ImportError:
        print("Install psycopg2-binary to apply migrations from the CLI:")
        print("  pip install psycopg2-binary")
        print(f"\nOr run manually in Supabase SQL Editor:\n  {MIGRATION_PATH}")
        return 1

    print(f"Applying {MIGRATION_PATH.name} ...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()

    print("Migration applied successfully. Reload the Streamlit app.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
