"""Apply the prod/dev schema parity migration to production.

Runs migrations/022_prod_dev_schema_parity.sql,
migrations/023_household_income_streams.sql,
migrations/024_income_paycheck_occurrences.sql,
migrations/025_household_expense_streams.sql, and
migrations/026_expense_paycheck_occurrences.sql,
migrations/027_expense_stream_category_id_dev.sql (all idempotent), and
migrations/028_project_funds_rollover.sql, and
migrations/029_projects_funds_opening_text.sql (all idempotent), and if prod
budget tables are entirely absent, migrations/017_create_budget_prod_tables.sql
first.

Usage:
  python maintenance/apply_schema_parity.py              # dry-run (default)
  python maintenance/apply_schema_parity.py --apply      # execute on prod
  python maintenance/apply_schema_parity.py --check-only # audit only, no SQL

Run the audit first to see what gaps exist:
  python maintenance/audit_schema_parity.py

Requires one of:
  - SUPABASE_DB_URL in project root .env file
  - SUPABASE_DB_PASSWORD + SUPABASE_URL in .env (plain password; recommended)
  - SUPABASE_DB_URL environment variable (postgresql://...)
  - DATABASE_URL environment variable
  - [database] url in .streamlit/secrets.toml

Also requires psycopg2-binary:
  pip install psycopg2-binary
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_connection import connect
MIGRATION_017 = ROOT / "migrations" / "017_create_budget_prod_tables.sql"
MIGRATION_022 = ROOT / "migrations" / "022_prod_dev_schema_parity.sql"
MIGRATION_023 = ROOT / "migrations" / "023_household_income_streams.sql"
MIGRATION_024 = ROOT / "migrations" / "024_income_paycheck_occurrences.sql"
MIGRATION_025 = ROOT / "migrations" / "025_household_expense_streams.sql"
MIGRATION_026 = ROOT / "migrations" / "026_expense_paycheck_occurrences.sql"
MIGRATION_027 = ROOT / "migrations" / "027_expense_stream_category_id_dev.sql"
MIGRATION_028 = ROOT / "migrations" / "028_project_funds_rollover.sql"
MIGRATION_029 = ROOT / "migrations" / "029_projects_funds_opening_text.sql"
MIGRATION_030 = ROOT / "migrations" / "030_household_obligation_assignments.sql"
MIGRATION_031 = ROOT / "migrations" / "031_household_disbursement_settings.sql"
MIGRATION_032 = ROOT / "migrations" / "032_household_member_transfers.sql"
MIGRATION_033 = ROOT / "migrations" / "033_member_disbursement_funding_stream.sql"
MIGRATION_034 = ROOT / "migrations" / "034_member_disbursement_funding_streams.sql"
MIGRATION_035 = ROOT / "migrations" / "035_fix_member_transfers_unique_constraint.sql"
MIGRATION_036 = ROOT / "migrations" / "036_integrate_household_on_personal.sql"
MIGRATION_037 = ROOT / "migrations" / "037_income_occurrence_suppressions.sql"
MIGRATION_038 = ROOT / "migrations" / "038_member_transfer_allowance_expense.sql"
MIGRATION_041 = ROOT / "migrations" / "041_income_member_transfer_link.sql"

BUDGET_PROD_TABLES = [
    "budget_categories",
    "household_incomes",
    "expenses",
    "cash_flow_routing",
    "user_finance_settings",
]


def _tables_exist(cur, table_names: list[str]) -> dict[str, bool]:
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (table_names,),
    )
    found = {row[0] for row in cur.fetchall()}
    return {t: t in found for t in table_names}


def _run_audit(cur) -> dict:
    """Import and run the audit module's _audit function."""
    sys.path.insert(0, str(ROOT / "maintenance"))
    from audit_schema_parity import _audit  # type: ignore

    return _audit(cur)


def _print_migration_plan(need_017: bool) -> None:
    print()
    print("=== Apply Plan (dry-run) ===")
    print()
    step = 1
    if need_017:
        print(f"  Step {step}: {MIGRATION_017.name}")
        print("          Creates the five core prod budget tables.")
        step += 1
        print()
    print(f"  Step {step}: {MIGRATION_022.name}")
    print("          Adds all missing columns (idempotent catch-all).")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_023.name}")
    print("          Income streams, versions, and ledger link columns.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_024.name}")
    print("          Per-paycheck unique index for bi-weekly / weekly rows.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_025.name}")
    print("          Expense streams, versions, and ledger link columns.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_026.name}")
    print("          Per-bill unique index for bi-weekly / weekly expense rows.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_027.name}")
    print("          Dev expense-stream category_id UUID (matches budget_categories_dev).")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_028.name}")
    print("          Project funds opening balance + expense project_budget_id link.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_029.name}")
    print("          projects_funds_opening TEXT (encrypted ciphertext, like projects_funds).")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_030.name}")
    print("          Obligation assignments + supplement snapshot tables.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_031.name}")
    print("          Disbursement funding income stream settings.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_032.name}")
    print("          Member transfer ledger + obligation toggle on user_finance_settings.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_033.name}")
    print("          Per-member disbursement funding stream on user_finance_settings.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_034.name}")
    print("          Per-member multi-stream disbursement funding junction table.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_035.name}")
    print("          Expand member transfers unique constraint to include funding stream ID.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_036.name}")
    print("          Master integrate_household_on_personal toggle on user_finance_settings.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_037.name}")
    print("          Suppressions for deleted stream income occurrences.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_038.name}")
    print("          Link member transfers to auto-created household allowance expenses.")
    step += 1
    print()
    print(f"  Step {step}: {MIGRATION_041.name}")
    print("          Plaintext source_member_transfer_id on household incomes.")
    print()
    print("To apply, re-run with --apply:")
    print("  python maintenance/apply_schema_parity.py --apply")
    print()
    print("Or paste the SQL directly into Supabase → SQL Editor.")
    print()


def main() -> int:
    args = set(sys.argv[1:])
    do_apply = "--apply" in args
    check_only = "--check-only" in args

    try:
        import psycopg2  # type: ignore  # noqa: F401
    except ImportError:
        print(
            "ERROR: psycopg2-binary required.\n"
            "Install with:  pip install psycopg2-binary",
            file=sys.stderr,
        )
        return 2

    try:
        conn, warning = connect()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if warning:
        print(f"NOTE: {warning}")

    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            # 1. Audit current state
            print("Running schema audit...")
            report = _run_audit(cur)

            if report["ok"]:
                print("✓  All prod tables are already column-complete. Nothing to apply.")
                return 0

            err_count = len(report["issues"])
            print(f"  {err_count} issue(s) found:")
            for item in report["issues"]:
                print(f"    ✗  [{item['kind']}]  {item['message']}")

            if check_only:
                print()
                print("Run without --check-only to see the apply plan.")
                return 1

            # 2. Determine whether 017 is needed (budget prod tables missing)
            existence = _tables_exist(cur, BUDGET_PROD_TABLES)
            missing_budget_tables = [t for t, exists in existence.items() if not exists]
            need_017 = bool(missing_budget_tables)

            if need_017:
                print(f"\nMissing prod budget tables: {', '.join(missing_budget_tables)}")
                print("Migration 017 will be applied first.")

            # 3. Dry-run or apply
            if not do_apply:
                _print_migration_plan(need_017)
                return 1

            # --- APPLY ---
            print()
            if need_017:
                sql_017 = MIGRATION_017.read_text(encoding="utf-8")
                print(f"Applying {MIGRATION_017.name} ...")
                with conn.cursor() as cur2:
                    cur2.execute(sql_017)
                print(f"  ✓  {MIGRATION_017.name} applied.")

            sql_022 = MIGRATION_022.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_022.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_022)
            print(f"  ✓  {MIGRATION_022.name} applied.")

            sql_023 = MIGRATION_023.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_023.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_023)
            print(f"  ✓  {MIGRATION_023.name} applied.")

            sql_024 = MIGRATION_024.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_024.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_024)
            print(f"  ✓  {MIGRATION_024.name} applied.")

            sql_025 = MIGRATION_025.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_025.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_025)
            print(f"  ✓  {MIGRATION_025.name} applied.")

            sql_026 = MIGRATION_026.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_026.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_026)
            print(f"  ✓  {MIGRATION_026.name} applied.")

            sql_027 = MIGRATION_027.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_027.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_027)
            print(f"  ✓  {MIGRATION_027.name} applied.")

            sql_028 = MIGRATION_028.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_028.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_028)
            print(f"  ✓  {MIGRATION_028.name} applied.")

            sql_029 = MIGRATION_029.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_029.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_029)
            print(f"  ✓  {MIGRATION_029.name} applied.")

            sql_030 = MIGRATION_030.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_030.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_030)
            print(f"  ✓  {MIGRATION_030.name} applied.")

            sql_031 = MIGRATION_031.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_031.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_031)
            print(f"  ✓  {MIGRATION_031.name} applied.")

            sql_032 = MIGRATION_032.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_032.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_032)
            print(f"  ✓  {MIGRATION_032.name} applied.")

            sql_033 = MIGRATION_033.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_033.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_033)
            print(f"  ✓  {MIGRATION_033.name} applied.")

            sql_034 = MIGRATION_034.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_034.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_034)
            print(f"  ✓  {MIGRATION_034.name} applied.")

            sql_035 = MIGRATION_035.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_035.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_035)
            print(f"  ✓  {MIGRATION_035.name} applied.")

            sql_036 = MIGRATION_036.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_036.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_036)
            print(f"  ✓  {MIGRATION_036.name} applied.")

            sql_037 = MIGRATION_037.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_037.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_037)
            print(f"  ✓  {MIGRATION_037.name} applied.")

            sql_038 = MIGRATION_038.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_038.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_038)
            print(f"  ✓  {MIGRATION_038.name} applied.")

            sql_041 = MIGRATION_041.read_text(encoding="utf-8")
            print(f"Applying {MIGRATION_041.name} ...")
            with conn.cursor() as cur2:
                cur2.execute(sql_041)
            print(f"  ✓  {MIGRATION_041.name} applied.")

            # 4. Re-audit to confirm
            print()
            print("Re-running audit to verify...")
            with conn.cursor() as cur3:
                report2 = _run_audit(cur3)

            if report2["ok"]:
                print("✓  All prod tables are now column-complete.")
                print()
                print("Next steps:")
                print("  1. Reload the Streamlit app (restart or rerun).")
                print("  2. Smoke-test Financial Hub, Projects, Wish List, and To-Do in production mode.")
                print("  3. Backfill legacy recurring incomes:")
                print("     python maintenance/backfill_income_streams.py --apply")
                print("  4. Backfill legacy recurring expenses:")
                print("     python maintenance/backfill_expense_streams.py --apply")
                return 0
            else:
                remaining = len(report2["issues"])
                print(f"WARNING: {remaining} issue(s) remain after apply. Re-run the audit for details.")
                print("  python maintenance/audit_schema_parity.py")
                return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
