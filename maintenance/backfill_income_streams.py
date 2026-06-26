"""Backfill income streams + versions from existing household_incomes rows.

Usage:
  python maintenance/backfill_income_streams.py           # dry-run
  python maintenance/backfill_income_streams.py --apply

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
    normalize_income_pay_frequency,
    income_is_recurring_frequency,
)
from security import decrypt_text


def _budget_tables(*, local: bool) -> tuple[str, str, str]:
    suffix = "_dev" if local else ""
    return (
        f"household_incomes{suffix}",
        f"household_income_streams{suffix}",
        f"household_income_stream_versions{suffix}",
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
    source = decrypt_text(row.get("source_name")) if row.get("source_name") else ""
    return f"{row['household_id']}|{row.get('owner_username')}|{row.get('is_personal_income')}|{source}"


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
    incomes_table, streams_table, versions_table = _budget_tables(local=args.local)

    try:
        response = (
            supabase.table(incomes_table)
            .select("*")
            .is_("stream_id", "null")
            .execute()
        )
    except APIError as exc:
        message = str(exc)
        if "stream_id" in message and "does not exist" in message:
            print(
                f"ERROR: {incomes_table} is missing stream_id. "
                "Run migrations/023_household_income_streams.sql first "
                "(or: python maintenance/apply_schema_parity.py --apply).",
                file=sys.stderr,
            )
            return 2
        raise

    rows = response.data or []
    recurring = [
        r
        for r in rows
        if income_is_recurring_frequency(
            normalize_income_pay_frequency(
                r.get("pay_frequency") or ("monthly" if r.get("is_recurring") else "one_time")
            )
        )
        and not r.get("source_expense_id")
    ]

    groups: dict[str, list] = defaultdict(list)
    for row in recurring:
        groups[_signature(row)].append(row)

    print(f"Table set: {incomes_table} (use --local for *_dev).")
    print(f"Found {len(recurring)} recurring rows in {len(groups)} stream groups (no stream_id).")

    planned = 0
    for sig, group_rows in groups.items():
        group_rows.sort(key=lambda r: r.get("month_year") or "")
        first = group_rows[0]
        stream_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        payment_date = str(first.get("payment_date") or f"{first.get('month_year')}-01")[:10]
        planned += 1
        print(f"  Stream {stream_id[:8]}…  {sig}  ({len(group_rows)} months)")

        if not args.apply:
            continue

        supabase.table(streams_table).insert(
            {
                "id": stream_id,
                "household_id": first["household_id"],
                "owner_username": first.get("owner_username"),
                "is_personal_income": bool(first.get("is_personal_income", False)),
                "display_name": first.get("source_name"),
                "is_active": True,
            }
        ).execute()

        supabase.table(versions_table).insert(
            {
                "id": version_id,
                "stream_id": stream_id,
                "effective_from": payment_date,
                "take_home_amount": first.get("take_home_amount"),
                "gross_amount": first.get("gross_amount"),
                "is_taxable": bool(first.get("is_taxable", True)),
                "is_windfall": bool(first.get("is_windfall", False)),
                "pay_frequency": normalize_income_pay_frequency(
                    first.get("pay_frequency") or "monthly"
                ),
                "payment_anchor_day": int(payment_date[8:10]),
            }
        ).execute()

        for row in group_rows:
            supabase.table(incomes_table).update(
                {"stream_id": stream_id, "version_id": version_id}
            ).eq("id", row["id"]).execute()

    if args.apply:
        print(f"Applied {planned} streams.")
    else:
        print(f"Dry-run only. Re-run with --apply to write {planned} streams.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
