"""Delete all rows for one household from *_dev tables (local testing reset).

Dry-run by default — prints row counts per table. Pass --apply to delete.

Usage:
  python maintenance/reset_dev_household_data.py
  python maintenance/reset_dev_household_data.py --household-id test_home
  python maintenance/reset_dev_household_data.py --household-id test_home --apply
  python maintenance/reset_dev_household_data.py --list-households

Requires SUPABASE_DB_PASSWORD + SUPABASE_URL (or SUPABASE_DB_URL) in .env.
Only touches *_dev tables — production tables are never modified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_connection import connect

# Delete order: ledger rows first, then streams (versions cascade), then categories.
DEV_TABLES: list[str] = [
    "expenses_dev",
    "household_incomes_dev",
    "household_income_streams_dev",
    "household_expense_streams_dev",
    "budget_categories_dev",
    "cash_flow_routing_dev",
    "user_finance_settings_dev",
    "household_tasks_dev",
    "project_budgets_dev",
    "household_finance_settings_dev",
    "wish_list_dev",
]

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


def _count_rows(cur, table_name: str, household_id: str) -> int | None:
    if not _table_exists(cur, table_name):
        return None
    cur.execute(
        f"SELECT COUNT(*) FROM {table_name} WHERE household_id = %s",
        (household_id,),
    )
    return int(cur.fetchone()[0])


def _list_households(cur) -> None:
    print("Household IDs with rows in *_dev tables:\n")
    seen: set[str] = set()
    for table in DEV_TABLES:
        if not _table_exists(cur, table):
            continue
        cur.execute(
            f"SELECT DISTINCT household_id FROM {table} ORDER BY household_id"
        )
        for (household_id,) in cur.fetchall():
            if household_id not in seen:
                seen.add(household_id)
    if not seen:
        print("  (none)")
        return
    for household_id in sorted(seen):
        total = 0
        parts: list[str] = []
        for table in DEV_TABLES:
            count = _count_rows(cur, table, household_id)
            if count is None or count == 0:
                continue
            total += count
            parts.append(f"{table}={count}")
        print(f"  {household_id}  ({total} rows: {', '.join(parts)})")


def _preview(cur, household_id: str) -> tuple[int, list[tuple[str, int]]]:
    rows: list[tuple[str, int]] = []
    total = 0
    for table in DEV_TABLES:
        count = _count_rows(cur, table, household_id)
        if count is None:
            print(f"  {table}: (table missing — skipped)")
            continue
        rows.append((table, count))
        total += count
        print(f"  {table}: {count}")
    return total, rows


def _apply(cur, household_id: str, tables_with_rows: list[tuple[str, int]]) -> int:
    deleted = 0
    for table, _ in tables_with_rows:
        if not _table_exists(cur, table):
            continue
        cur.execute(
            f"DELETE FROM {table} WHERE household_id = %s",
            (household_id,),
        )
        deleted += cur.rowcount
        print(f"  deleted {cur.rowcount} from {table}")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrub all *_dev table rows for one household (dry-run by default)."
    )
    parser.add_argument(
        "--household-id",
        default=DEFAULT_HOUSEHOLD_ID,
        help=f"Household id to reset (default: {DEFAULT_HOUSEHOLD_ID}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rows (default is preview only).",
    )
    parser.add_argument(
        "--list-households",
        action="store_true",
        help="List distinct household_id values found across *_dev tables.",
    )
    args = parser.parse_args()

    conn, warning = connect()
    if warning:
        print(f"Note: {warning}")

    try:
        with conn:
            with conn.cursor() as cur:
                if args.list_households:
                    _list_households(cur)
                    return 0

                household_id = args.household_id.strip()
                if not household_id:
                    print("Error: --household-id must not be empty.", file=sys.stderr)
                    return 1

                print(f"Household: {household_id}")
                print(f"Mode: {'DELETE' if args.apply else 'preview (dry-run)'}\n")

                total, rows = _preview(cur, household_id)
                tables_with_rows = [(t, c) for t, c in rows if c > 0]

                print(f"\nTotal rows: {total}")
                if total == 0:
                    print("Nothing to delete.")
                    return 0

                if not args.apply:
                    print("\nDry-run only. Re-run with --apply to delete these rows.")
                    return 0

                print("\nDeleting...")
                deleted = _apply(cur, household_id, tables_with_rows)
                print(f"\nDone — deleted {deleted} row(s) for household_id={household_id!r}.")
                return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
