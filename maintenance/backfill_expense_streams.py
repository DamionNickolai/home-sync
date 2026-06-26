"""Backfill expense streams + versions from existing expenses rows.

Usage:
  python maintenance/backfill_expense_streams.py           # dry-run
  python maintenance/backfill_expense_streams.py --apply
  python maintenance/backfill_expense_streams.py --local --apply

Requires SUPABASE_URL + SUPABASE_SERVICE_KEY in project root .env.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT / ".env", override=True)

from supabase import create_client
from postgrest.exceptions import APIError

from database import (
    normalize_expense_pay_frequency,
    expense_is_recurring_frequency,
)
from security import decrypt_text


def _budget_tables(*, local: bool) -> tuple[str, str, str]:
    suffix = "_dev" if local else ""
    return (
        f"expenses{suffix}",
        f"household_expense_streams{suffix}",
        f"household_expense_stream_versions{suffix}",
    )


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def _client():
    url = _clean_env_value(os.getenv("SUPABASE_URL"))
    key = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY"))
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env or environment."
        )
    return create_client(url, key)


def _signature(row) -> str:
    details = decrypt_text(row.get("details")) if row.get("details") else ""
    return (
        f"{row['household_id']}|{row.get('category_id')}|"
        f"{row.get('username')}|{row.get('is_personal_spend')}|{details}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Target *_dev tables (default: production tables).",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target production tables (default).",
    )
    args = parser.parse_args()
    if args.local and args.prod:
        print("Use only one of --local or --prod.", file=sys.stderr)
        return 2

    supabase = _client()
    expenses_table, streams_table, versions_table = _budget_tables(local=args.local)

    try:
        response = (
            supabase.table(expenses_table)
            .select("*")
            .is_("stream_id", "null")
            .execute()
        )
    except APIError as exc:
        message = str(exc)
        if "stream_id" in message and "does not exist" in message:
            print(
                f"ERROR: {expenses_table} is missing stream_id. "
                "Run migrations/025_household_expense_streams.sql first "
                "(or: python maintenance/apply_schema_parity.py --apply).",
                file=sys.stderr,
            )
            return 2
        raise

    rows = response.data or []
    recurring = [
        r
        for r in rows
        if expense_is_recurring_frequency(
            normalize_expense_pay_frequency(
                r.get("pay_frequency") or ("monthly" if r.get("is_recurring") else "one_time")
            )
        )
    ]

    groups: dict[str, list] = defaultdict(list)
    for row in recurring:
        groups[_signature(row)].append(row)

    print(f"Table set: {expenses_table} (use --local for *_dev).")
    print(f"Found {len(recurring)} recurring rows in {len(groups)} stream groups (no stream_id).")

    planned = 0
    for sig, group_rows in groups.items():
        group_rows.sort(key=lambda r: r.get("month_year") or "")
        first = group_rows[0]
        stream_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        date_logged = str(first.get("date_logged") or f"{first.get('month_year')}-01")[:10]
        planned += 1
        print(f"  Stream {stream_id[:8]}…  {sig}  ({len(group_rows)} months)")

        if not args.apply:
            continue

        supabase.table(streams_table).insert(
            {
                "id": stream_id,
                "household_id": first["household_id"],
                "category_id": first.get("category_id"),
                "auth_user_id": first.get("auth_user_id"),
                "username": first.get("username"),
                "is_personal_spend": bool(first.get("is_personal_spend", False)),
                "display_name": first.get("details"),
                "is_active": True,
            }
        ).execute()

        supabase.table(versions_table).insert(
            {
                "id": version_id,
                "stream_id": stream_id,
                "effective_from": date_logged,
                "amount": first.get("amount"),
                "pay_frequency": normalize_expense_pay_frequency(
                    first.get("pay_frequency") or "monthly"
                ),
                "payment_anchor_day": int(date_logged[8:10]),
            }
        ).execute()

        for row in group_rows:
            supabase.table(expenses_table).update(
                {"stream_id": stream_id, "version_id": version_id}
            ).eq("id", row["id"]).execute()

    if args.apply:
        print(f"Applied {planned} streams.")
    else:
        print(f"Dry-run only. Re-run with --apply to write {planned} streams.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
