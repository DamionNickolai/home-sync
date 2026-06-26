"""Apply the prod/dev schema parity migration to production.

Runs migrations/022_prod_dev_schema_parity.sql (idempotent) and, if prod
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
    if need_017:
        print(f"  Step 1: {MIGRATION_017.name}")
        print("          Creates the five core prod budget tables.")
        print()
        print(f"  Step 2: {MIGRATION_022.name}")
        print("          Adds all missing columns (idempotent catch-all).")
    else:
        print(f"  Step 1: {MIGRATION_022.name}")
        print("          Adds all missing columns (idempotent catch-all).")
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
