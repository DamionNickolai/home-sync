"""Revert completed household member transfers back to planned (testing reset).

For each completed transfer:
  1. Delete linked personal allowance / obligation support income rows (if any)
  2. Set status back to planned and clear completion metadata

Dry-run by default. Pass --apply to execute.

Usage:
  python maintenance/reset_completed_member_transfers.py
  python maintenance/reset_completed_member_transfers.py --household-id test_home
  python maintenance/reset_completed_member_transfers.py --household-id test_home --month-year 2026-06
  python maintenance/reset_completed_member_transfers.py --household-id test_home --apply

Requires SUPABASE_DB_PASSWORD + SUPABASE_URL (or SUPABASE_DB_URL) in .env.
Defaults to *_dev tables (local). Pass --prod to target production tables.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_connection import connect

DEFAULT_HOUSEHOLD_ID = "test_home"


def _table_exists(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def _fetch_completed(cur, transfers_table: str, household_id: str, month_year: str | None) -> list[dict]:
    sql = f"""
        SELECT id, month_year, payment_date, recipient_username,
               personal_allowance_income_id, personal_obligation_income_id,
               household_allowance_expense_id,
               transferred_at, transferred_by
        FROM {transfers_table}
        WHERE household_id = %s AND status = 'completed'
    """
    params: list = [household_id]
    if month_year:
        sql += " AND month_year = %s"
        params.append(month_year)
    sql += " ORDER BY month_year, payment_date, recipient_username"
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revert completed member transfers to planned (dry-run by default)."
    )
    parser.add_argument(
        "--household-id",
        default=DEFAULT_HOUSEHOLD_ID,
        help=f"Household id (default: {DEFAULT_HOUSEHOLD_ID}).",
    )
    parser.add_argument(
        "--month-year",
        default=None,
        help="Optional month filter (YYYY-MM). Default: all months.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually reset transfers (default is preview only).",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target production tables instead of *_dev.",
    )
    args = parser.parse_args()

    suffix = "" if args.prod else "_dev"
    transfers_table = f"household_member_transfers{suffix}"
    incomes_table = f"household_incomes{suffix}"
    expenses_table = f"expenses{suffix}"

    conn, warning = connect()
    if warning:
        print(f"Note: {warning}")

    try:
        with conn:
            with conn.cursor() as cur:
                if not _table_exists(cur, transfers_table):
                    print(f"Error: table {transfers_table} not found.", file=sys.stderr)
                    return 1

                household_id = args.household_id.strip()
                rows = _fetch_completed(cur, transfers_table, household_id, args.month_year)
                if not rows:
                    scope = f" for {args.month_year}" if args.month_year else ""
                    print(f"No completed transfers found for {household_id!r}{scope}.")
                    return 0

                print(f"Household: {household_id}")
                print(f"Tables: {transfers_table}, {incomes_table}")
                print(f"Mode: {'RESET' if args.apply else 'preview (dry-run)'}\n")
                print(f"Completed transfers to revert: {len(rows)}\n")

                income_ids: set[str] = set()
                expense_ids: set[str] = set()
                for row in rows:
                    pay_date = str(row["payment_date"])[:10]
                    print(
                        f"  {row['month_year']} | {pay_date} | {row['recipient_username']} | "
                        f"id={row['id']}"
                    )
                    for field in ("personal_allowance_income_id", "personal_obligation_income_id"):
                        income_id = row.get(field)
                        if income_id:
                            income_ids.add(str(income_id))
                            print(f"      -> delete linked income {field}: {income_id}")
                    expense_id = row.get("household_allowance_expense_id")
                    if expense_id:
                        expense_ids.add(str(expense_id))
                        print(f"      -> delete linked allowance expense: {expense_id}")

                if income_ids and not _table_exists(cur, incomes_table):
                    print(f"\nWarning: {incomes_table} missing; cannot delete linked incomes.")
                if expense_ids and not _table_exists(cur, expenses_table):
                    print(f"\nWarning: {expenses_table} missing; cannot delete linked allowance expenses.")

                if not args.apply:
                    print(f"\nWould delete {len(income_ids)} linked income row(s) and {len(expense_ids)} allowance expense row(s).")
                    print("Dry-run only. Re-run with --apply to revert these transfers.")
                    return 0

                deleted_incomes = 0
                deleted_expenses = 0
                if income_ids and _table_exists(cur, incomes_table):
                    for income_id in sorted(income_ids):
                        cur.execute(
                            f"DELETE FROM {incomes_table} WHERE id = %s",
                            (income_id,),
                        )
                        deleted_incomes += cur.rowcount
                if expense_ids and _table_exists(cur, expenses_table):
                    for expense_id in sorted(expense_ids):
                        cur.execute(
                            f"DELETE FROM {expenses_table} WHERE id = %s",
                            (expense_id,),
                        )
                        deleted_expenses += cur.rowcount

                transfer_ids = [str(r["id"]) for r in rows]
                cur.execute(
                    f"""
                    UPDATE {transfers_table}
                    SET status = 'planned',
                        transferred_at = NULL,
                        transferred_by = NULL,
                        personal_allowance_income_id = NULL,
                        personal_obligation_income_id = NULL,
                        household_allowance_expense_id = NULL,
                        updated_at = NOW()
                    WHERE id = ANY(%s::uuid[])
                    """,
                    (transfer_ids,),
                )
                updated = cur.rowcount

                print(
                    f"\nDone — reverted {updated} transfer(s), deleted {deleted_incomes} income row(s), "
                    f"{deleted_expenses} allowance expense row(s)."
                )
                return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
