"""Provision the household-receipts Supabase Storage bucket.

Dry-run by default. Pass --apply to create the bucket.

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (see maintenance/.env.example).

After creating the bucket, also run migrations/040_receipt_storage_bucket.sql in the
Supabase SQL Editor to add RLS policies (or use apply_schema_parity once 040 is wired in).
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

BUCKET_ID = "household-receipts"
ALLOWED_MIME_TYPES = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
]
FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20 MB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the household-receipts storage bucket.")
    parser.add_argument("--apply", action="store_true", help="Create the bucket if missing.")
    return parser.parse_args()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def _get_config() -> tuple[str, str]:
    load_dotenv(dotenv_path=".env", override=True)
    url = _clean(os.getenv("SUPABASE_URL"))
    service_key = _clean(os.getenv("SUPABASE_SERVICE_KEY"))
    if not url or not service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env "
            "(see maintenance/.env.example)."
        )
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("SUPABASE_URL must be an absolute URL.")
    return url.rstrip("/"), service_key


def list_buckets(url: str, service_key: str) -> list[dict]:
    resp = httpx.get(
        f"{url}/storage/v1/bucket",
        headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def create_bucket(url: str, service_key: str) -> dict:
    payload = {
        "id": BUCKET_ID,
        "name": BUCKET_ID,
        "public": False,
        "file_size_limit": FILE_SIZE_LIMIT,
        "allowed_mime_types": ALLOWED_MIME_TYPES,
    }
    resp = httpx.post(
        f"{url}/storage/v1/bucket",
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Bucket create failed ({resp.status_code}): {resp.text}")
    return resp.json()


def main() -> int:
    args = parse_args()
    try:
        url, service_key = _get_config()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    try:
        buckets = list_buckets(url, service_key)
    except httpx.HTTPError as exc:
        print(f"ERROR: Could not list buckets: {exc}")
        return 1

    exists = any(b.get("id") == BUCKET_ID or b.get("name") == BUCKET_ID for b in buckets)
    if exists:
        print(f"Bucket '{BUCKET_ID}' already exists — nothing to do.")
        return 0

    print(f"Bucket '{BUCKET_ID}' is missing.")
    if not args.apply:
        print("Dry run. Re-run with --apply to create it:")
        print("  python maintenance/provision_receipt_storage.py --apply")
        return 0

    try:
        result = create_bucket(url, service_key)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Created bucket '{BUCKET_ID}': {result}")
    print()
    print("Next step: run migrations/040_receipt_storage_bucket.sql in Supabase SQL Editor")
    print("to add storage RLS policies (or wait until apply_schema_parity includes 040).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
