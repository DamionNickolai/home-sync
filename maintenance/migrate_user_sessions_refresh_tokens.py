"""Backfill existing user_sessions refresh tokens into encrypted form.

This script is intentionally dry-run by default. Pass --apply to write changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from supabase import create_client

# Allow direct execution from the maintenance folder.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security import encrypt_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encrypt existing user_sessions.refresh_token values.")
    parser.add_argument("--apply", action="store_true", help="Write the backfill updates to Supabase.")
    parser.add_argument("--page-size", type=int, default=100, help="Supabase query page size.")
    return parser.parse_args()


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def _validate_supabase_url(url: str) -> None:
    if "your-project-ref.supabase.co" in url:
        raise RuntimeError(
            "SUPABASE_URL is still using the template value. Replace it with your real project URL."
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("SUPABASE_URL must be an absolute URL like https://<project-ref>.supabase.co")


def get_client():
    load_dotenv(dotenv_path=".env", override=True)
    url = _clean_env_value(os.getenv("SUPABASE_URL"))
    service_key = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY"))

    if not url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set (environment or .env file).")

    _validate_supabase_url(url)
    return create_client(url, service_key)


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith("gAAAA")


def main() -> int:
    args = parse_args()
    supabase = get_client()

    response = supabase.table("user_sessions").select("session_id, refresh_token, is_active, last_accessed_at").execute()
    rows = response.data or []

    pending_updates: list[tuple[str, str]] = []
    skipped_already_encrypted = 0
    skipped_empty = 0

    for row in rows:
        token = row.get("refresh_token")
        session_id = row.get("session_id")

        if not token:
            skipped_empty += 1
            continue

        if is_encrypted(token):
            skipped_already_encrypted += 1
            continue

        if not session_id:
            continue

        pending_updates.append((str(session_id), encrypt_data(token)))

    print(f"Loaded {len(rows)} user_sessions rows.")
    print(f"Found {len(pending_updates)} plaintext refresh tokens to encrypt.")
    print(f"Skipped {skipped_already_encrypted} already-encrypted tokens and {skipped_empty} empty tokens.")

    if not args.apply:
        print("Dry run only. Re-run with --apply to write updates.")
        return 0

    if not pending_updates:
        print("Nothing to update.")
        return 0

    applied = 0
    for session_id, encrypted_token in pending_updates:
        supabase.table("user_sessions").update({"refresh_token": encrypted_token}).eq("session_id", session_id).execute()
        applied += 1
        print(f"Encrypted session {session_id}")

    print(f"Applied {applied} updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())