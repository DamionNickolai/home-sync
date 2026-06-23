"""Read-only validation for encrypted user_sessions refresh tokens.

This script NEVER writes to the database. It validates whether refresh_token
values in user_sessions can be decrypted with the current ENCRYPTION_KEY.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from supabase import create_client


@dataclass
class ValidationResult:
    total_rows: int = 0
    active_rows: int = 0
    inactive_rows: int = 0
    empty_tokens: int = 0
    encrypted_shape_tokens: int = 0
    plaintext_shape_tokens: int = 0
    decrypt_ok: int = 0
    decrypt_failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only validation for user_sessions.refresh_token encryption health."
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only validate rows where is_active = true.",
    )
    parser.add_argument(
        "--max-failure-samples",
        type=int,
        default=20,
        help="Maximum number of failing session_ids to print.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 2 when any decrypt failures or plaintext-shape tokens are found.",
    )
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
        raise RuntimeError(
            "SUPABASE_URL must be an absolute URL like https://<project-ref>.supabase.co"
        )


def get_env() -> tuple[str, str, str]:
    load_dotenv(dotenv_path=".env", override=True)
    url = _clean_env_value(os.getenv("SUPABASE_URL"))
    service_key = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY"))
    encryption_key = _clean_env_value(os.getenv("ENCRYPTION_KEY"))

    if not url or not service_key or not encryption_key:
        raise RuntimeError(
            "SUPABASE_URL, SUPABASE_SERVICE_KEY, and ENCRYPTION_KEY must be set (environment or .env file)."
        )

    _validate_supabase_url(url)
    return url, service_key, encryption_key


def looks_encrypted(token: object) -> bool:
    # Fernet tokens typically begin with gAAAAA for current timestamp ranges.
    return isinstance(token, str) and token.startswith("gAAAA")


def validate_rows(rows: list[dict], cipher: Fernet, active_only: bool, max_failure_samples: int) -> tuple[ValidationResult, list[str]]:
    stats = ValidationResult(total_rows=len(rows))
    failing_session_ids: list[str] = []

    for row in rows:
        is_active = bool(row.get("is_active"))
        if is_active:
            stats.active_rows += 1
        else:
            stats.inactive_rows += 1

        if active_only and not is_active:
            continue

        token = row.get("refresh_token")
        session_id = row.get("session_id")

        if not token:
            stats.empty_tokens += 1
            continue

        if not isinstance(token, str):
            stats.decrypt_failed += 1
            if session_id and len(failing_session_ids) < max_failure_samples:
                failing_session_ids.append(str(session_id))
            continue

        if looks_encrypted(token):
            stats.encrypted_shape_tokens += 1
            try:
                cipher.decrypt(token.encode()).decode()
                stats.decrypt_ok += 1
            except (InvalidToken, ValueError, UnicodeDecodeError):
                stats.decrypt_failed += 1
                if session_id and len(failing_session_ids) < max_failure_samples:
                    failing_session_ids.append(str(session_id))
        else:
            stats.plaintext_shape_tokens += 1
            if session_id and len(failing_session_ids) < max_failure_samples:
                failing_session_ids.append(str(session_id))

    return stats, failing_session_ids


def main() -> int:
    args = parse_args()
    url, service_key, encryption_key = get_env()

    try:
        cipher = Fernet(encryption_key.encode())
    except Exception as exc:
        raise RuntimeError("ENCRYPTION_KEY is not a valid Fernet key.") from exc

    supabase = create_client(url, service_key)
    response = supabase.table("user_sessions").select(
        "session_id, auth_user_id, refresh_token, is_active, created_at, last_accessed_at, expires_at"
    ).execute()
    rows = response.data or []

    stats, failing_session_ids = validate_rows(
        rows=rows,
        cipher=cipher,
        active_only=args.active_only,
        max_failure_samples=max(0, args.max_failure_samples),
    )

    print("=== user_sessions encryption validation (read-only) ===")
    print(f"Total rows loaded: {stats.total_rows}")
    print(f"Active rows: {stats.active_rows}")
    print(f"Inactive rows: {stats.inactive_rows}")
    print(f"Empty refresh_token rows: {stats.empty_tokens}")
    print(f"Encrypted-shape tokens: {stats.encrypted_shape_tokens}")
    print(f"Plaintext-shape tokens: {stats.plaintext_shape_tokens}")
    print(f"Decrypt OK: {stats.decrypt_ok}")
    print(f"Decrypt failed: {stats.decrypt_failed}")

    if failing_session_ids:
        print("Sample session_ids requiring investigation:")
        for session_id in failing_session_ids:
            print(f"  - {session_id}")

    has_issues = stats.decrypt_failed > 0 or stats.plaintext_shape_tokens > 0
    if has_issues:
        print("Validation result: ISSUES DETECTED")
        print("Do not rotate ENCRYPTION_KEY. Investigate key mismatch or legacy plaintext rows first.")
    else:
        print("Validation result: PASS")

    if args.strict and has_issues:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
