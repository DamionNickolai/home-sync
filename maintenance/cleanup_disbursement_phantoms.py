"""Remove orphan disbursement transfer expenses/incomes for a household/month.

Usage:
  python maintenance/cleanup_disbursement_phantoms.py --household-id test_home
  python maintenance/cleanup_disbursement_phantoms.py --household-id test_home --month-year 2026-06 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean orphan transfer expenses/incomes.")
    parser.add_argument("--household-id", default="test_home")
    parser.add_argument("--month-year", default=None, help="Optional YYYY-MM filter for incomes/expenses cleanup.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    household_id = args.household_id.strip()
    if not household_id:
        print("household-id required", file=sys.stderr)
        return 1

    month_year = args.month_year
    if month_year:
        print(f"Preview orphan cleanup for {household_id!r} / {month_year}")
    else:
        print(f"Preview orphan cleanup for {household_id!r} (all months with transfers)")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete orphan rows.")
        return 0

    if month_year:
        stats = database.cleanup_orphan_disbursement_artifacts(household_id, month_year)
        print(f"Removed {stats['expenses']} orphan expense(s), {stats['incomes']} orphan income(s).")
        return 0

    # Without month filter, scan distinct months from transfers table.
    from maintenance.db_connection import connect

    conn, _ = connect()
    table = database.get_member_transfers_table()
    total_exp = total_inc = 0
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT month_year FROM {table} WHERE household_id = %s ORDER BY month_year",
                    (household_id,),
                )
                months = [row[0] for row in cur.fetchall()]
        for month in months:
            stats = database.cleanup_orphan_disbursement_artifacts(household_id, month)
            total_exp += stats["expenses"]
            total_inc += stats["incomes"]
            if stats["expenses"] or stats["incomes"]:
                print(f"  {month}: {stats['expenses']} expenses, {stats['incomes']} incomes")
    finally:
        conn.close()
    print(f"Done — removed {total_exp} orphan expense(s) and {total_inc} orphan income(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
