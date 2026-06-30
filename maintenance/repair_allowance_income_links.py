"""Repair allowance personal incomes using plaintext transfer link keys.

Requires migration 041_income_member_transfer_link.sql on your Supabase project.

Usage:
  python maintenance/repair_allowance_income_links.py --household-id YOUR_HOME
  python maintenance/repair_allowance_income_links.py --household-id YOUR_HOME --month-year 2026-06
  python maintenance/repair_allowance_income_links.py --household-id YOUR_HOME --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database  # noqa: E402
from constants import (  # noqa: E402
    ALLOWANCE_INCOME_SOURCE_NAME,
    member_transfer_income_link_key,
)


def _print_transfer_status(household_id: str, month_year: str) -> None:
    transfers = database.get_member_transfers(household_id, month_year)
    print(f"\n=== {household_id} / {month_year} ===")
    for row in transfers:
        if row.get("status") != "completed":
            continue
        allowance = round(float(row.get("allowance_amount") or 0), 2)
        if allowance <= 0:
            continue
        tid = str(row.get("id"))
        link_key = member_transfer_income_link_key(tid, ALLOWANCE_INCOME_SOURCE_NAME)
        by_link = database._find_income_id_by_member_transfer_link(link_key)
        linked = row.get("personal_allowance_income_id")
        pay = str(row.get("payment_date") or "")[:10]
        stream = row.get("funding_income_stream_id") or ""
        print(
            f"  {pay}  ${allowance:>7.2f}  transfer={tid[:8]}…  "
            f"stream={str(stream)[:8] or '-'}…  "
            f"linked_income={linked or '-'}  link_key_income={by_link or '-'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair allowance incomes via transfer link keys.")
    parser.add_argument("--household-id", required=True)
    parser.add_argument("--month-year", default=None, help="Optional YYYY-MM; default all transfer months.")
    parser.add_argument("--apply", action="store_true", help="Run repair (default is status preview only).")
    args = parser.parse_args()

    household_id = args.household_id.strip()
    if not household_id:
        print("household-id required", file=sys.stderr)
        return 1

    months = [args.month_year] if args.month_year else database._household_disbursement_months(household_id)
    if not months:
        print("No member transfer months found.")
        return 0

    for month_year in months:
        _print_transfer_status(household_id, month_year)

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to repair.")
        return 0

    if args.month_year:
        stats = database.repair_disbursement_allowance_incomes(household_id, args.month_year)
        print(f"\nRepaired {args.month_year}: {stats}")
    else:
        stats = database.repair_all_disbursement_allowance_incomes(household_id)
        print(f"\nRepaired all months: {stats}")

    for month_year in months:
        _print_transfer_status(household_id, month_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
