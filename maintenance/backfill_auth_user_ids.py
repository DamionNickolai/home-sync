"""Backfill users.auth_user_id from Supabase Auth metadata.

This script is intentionally dry-run by default. Pass --apply to write changes.
"""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv
from supabase import create_client


@dataclass
class AppUser:
    record_id: Any
    username: str
    auth_user_id: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill users.auth_user_id from Supabase Auth metadata.")
    parser.add_argument("--apply", action="store_true", help="Write the backfill updates to Supabase.")
    parser.add_argument("--page-size", type=int, default=100, help="Supabase admin list_users page size.")
    return parser.parse_args()


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def _validate_supabase_url(url: str) -> None:
    if "your-project-ref.supabase.co" in url:
        raise RuntimeError(
            "SUPABASE_URL is still using the template value. Replace it with your real project URL "
            "from Supabase Settings -> API."
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "SUPABASE_URL must be an absolute URL like https://<project-ref>.supabase.co"
        )


def get_client():
    # Prefer repository-local .env values for one-off maintenance scripts.
    load_dotenv(dotenv_path=".env", override=True)
    url = _clean_env_value(os.getenv("SUPABASE_URL"))
    service_key = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY"))

    if not url or not service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set (environment or .env file)."
        )

    _validate_supabase_url(url)

    return create_client(url, service_key)


def extract_rows(response: Any) -> list[dict[str, Any]]:
    if response is None:
        return []

    if isinstance(response, list):
        return response

    for attribute in ("data", "users"):
        value = getattr(response, attribute, None)
        if isinstance(value, list):
            return value

    if isinstance(response, dict):
        for key in ("data", "users"):
            value = response.get(key)
            if isinstance(value, list):
                return value

    return []


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def iter_auth_users(supabase, page_size: int):
    page = 1
    while True:
        response = supabase.auth.admin.list_users(page=page, per_page=page_size)
        users = extract_rows(response)
        if not users:
            break
        for user in users:
            yield user
        if len(users) < page_size:
            break
        page += 1


def get_auth_username(user: dict[str, Any]) -> str | None:
    metadata = _get_value(user, "user_metadata") or {}
    if isinstance(metadata, dict):
        username = metadata.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
    return None


def _column_exists(supabase, table_name: str, column_name: str) -> bool:
    try:
        supabase.table(table_name).select(column_name).limit(1).execute()
        return True
    except Exception as exc:
        return f"column {table_name}.{column_name} does not exist" not in str(exc)


def _resolve_users_key_column(supabase) -> str:
    for candidate in ("id", "user_id", "username"):
        if _column_exists(supabase, "users", candidate):
            return candidate
    raise RuntimeError("Unable to find a usable key column on users table (checked: id, user_id, username).")


def load_app_users(supabase) -> tuple[list[AppUser], str]:
    if not _column_exists(supabase, "users", "auth_user_id"):
        raise RuntimeError(
            "users.auth_user_id column does not exist. Add it first, then rerun backfill. "
            "Example SQL: alter table public.users add column auth_user_id uuid;"
        )

    id_column = _resolve_users_key_column(supabase)
    if id_column == "username":
        response = supabase.table("users").select("username, auth_user_id").execute()
    else:
        response = supabase.table("users").select(f"{id_column}, username, auth_user_id").execute()
    rows = extract_rows(response)

    app_users: list[AppUser] = []

    for row in rows:
        username = row.get("username")
        if not isinstance(username, str) or not username.strip():
            continue
        app_users.append(
            AppUser(
                record_id=row.get(id_column),
                username=username.strip(),
                auth_user_id=row.get("auth_user_id"),
            )
        )

    return app_users, id_column


def build_auth_username_index(auth_users: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for user in auth_users:
        username = get_auth_username(user)
        auth_user_id = _get_value(user, "id")
        if username and auth_user_id:
            index[username.casefold()].append(str(auth_user_id))
    return index


def main() -> int:
    args = parse_args()
    supabase = get_client()

    try:
        app_users, id_column = load_app_users(supabase)
        auth_users = list(iter_auth_users(supabase, args.page_size))
    except httpx.ConnectError as exc:
        parsed = urlparse(_clean_env_value(os.getenv("SUPABASE_URL")) or "")
        host = parsed.netloc or "<invalid-host>"
        raise RuntimeError(
            "Failed to reach Supabase host via DNS. "
            f"Host parsed from SUPABASE_URL: {host}. "
            "Check SUPABASE_URL format (https://<project-ref>.supabase.co), "
            "network DNS, and VPN/proxy settings."
        ) from exc

    auth_username_index = build_auth_username_index(auth_users)

    pending_updates: list[tuple[AppUser, str]] = []
    skipped_missing = []
    skipped_ambiguous = []

    for app_user in app_users:
        if app_user.auth_user_id:
            continue

        matches = auth_username_index.get(app_user.username.casefold(), [])
        if len(matches) == 1:
            pending_updates.append((app_user, matches[0]))
        elif len(matches) == 0:
            skipped_missing.append(app_user.username)
        else:
            skipped_ambiguous.append((app_user.username, matches))

    print(f"Loaded {len(app_users)} app users and {len(auth_users)} auth users.")
    print(f"Found {len(pending_updates)} users that can be backfilled.")

    if skipped_missing:
        print("No auth match for:")
        for username in skipped_missing:
            print(f"  - {username}")

    if skipped_ambiguous:
        print("Ambiguous auth username matches:")
        for username, matches in skipped_ambiguous:
            print(f"  - {username}: {', '.join(matches)}")

    if not args.apply:
        print("Dry run only. Re-run with --apply to write updates.")
        return 0

    if not pending_updates:
        print("Nothing to update.")
        return 0

    applied = 0
    for app_user, auth_user_id in pending_updates:
        supabase.table("users").update({"auth_user_id": auth_user_id}).eq(id_column, app_user.record_id).execute()
        applied += 1
        print(f"Updated {app_user.username} -> {auth_user_id}")

    print(f"Applied {applied} updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())