"""Audit prod vs dev table schema parity for Home Sync.

Compares information_schema.columns for each *_dev table against its
production counterpart, reporting missing columns, missing tables, and
type mismatches.

Usage:
  python maintenance/audit_schema_parity.py
  python maintenance/audit_schema_parity.py --json

Exit codes:
  0  All prod tables present and column-complete relative to dev
  1  One or more prod tables missing or missing columns
  2  Could not connect to the database

Requires one of:
  - SUPABASE_DB_URL in project root .env file
  - SUPABASE_DB_PASSWORD + SUPABASE_URL in .env (plain password; no URL encoding needed)
  - SUPABASE_DB_URL environment variable (postgresql://...)
  - DATABASE_URL environment variable
  - [database] url in .streamlit/secrets.toml
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_connection import connect, load_db_url

# --------------------------------------------------------------------- #
# Dev/prod table pairs — (dev_table, prod_table)
# --------------------------------------------------------------------- #
TABLE_PAIRS: list[tuple[str, str]] = [
    ("budget_categories_dev", "budget_categories"),
    ("household_incomes_dev", "household_incomes"),
    ("household_income_streams_dev", "household_income_streams"),
    ("household_income_stream_versions_dev", "household_income_stream_versions"),
    ("household_expense_streams_dev", "household_expense_streams"),
    ("household_expense_stream_versions_dev", "household_expense_stream_versions"),
    ("expenses_dev", "expenses"),
    ("cash_flow_routing_dev", "cash_flow_routing"),
    ("user_finance_settings_dev", "user_finance_settings"),
    ("household_tasks_dev", "household_tasks"),
    ("project_budgets_dev", "project_budgets"),
    ("household_finance_settings_dev", "household_finance_settings"),
    ("wish_list_dev", "wish_list"),
]

# expenses.id is intentionally UUID in dev, BIGINT in prod.
# Flag as expected so we don't alarm on it.
EXPECTED_TYPE_DIFFERENCES: set[tuple[str, str]] = {
    ("expenses", "id"),
    ("household_expense_streams", "category_id"),
}


from db_connection import connect, load_db_url


def _fetch_columns(cur, table_names: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Return {table_name: [{column_name, data_type, is_nullable, column_default}]}."""
    cur.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
        """,
        (table_names,),
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name, column_name, data_type, is_nullable, column_default in cur.fetchall():
        result.setdefault(table_name, []).append(
            {
                "column_name": column_name,
                "data_type": data_type,
                "is_nullable": is_nullable,
                "column_default": column_default,
            }
        )
    return result


def _audit(cur) -> dict[str, Any]:
    all_dev = [p[0] for p in TABLE_PAIRS]
    all_prod = [p[1] for p in TABLE_PAIRS]

    dev_schema = _fetch_columns(cur, all_dev)
    prod_schema = _fetch_columns(cur, all_prod)

    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    tables_ok: list[str] = []

    for dev_table, prod_table in TABLE_PAIRS:
        dev_cols = {c["column_name"]: c for c in dev_schema.get(dev_table, [])}
        prod_cols = {c["column_name"]: c for c in prod_schema.get(prod_table, [])}

        # Missing table entirely
        if dev_cols and not prod_cols:
            issues.append(
                {
                    "severity": "error",
                    "prod_table": prod_table,
                    "dev_table": dev_table,
                    "kind": "missing_table",
                    "message": f"Prod table '{prod_table}' does not exist (dev has {len(dev_cols)} columns).",
                }
            )
            continue

        if not dev_cols:
            warnings.append(
                {
                    "severity": "warn",
                    "prod_table": prod_table,
                    "dev_table": dev_table,
                    "kind": "missing_dev_table",
                    "message": f"Dev table '{dev_table}' not found — cannot compare.",
                }
            )
            continue

        table_clean = True

        # Missing prod columns
        for col_name, dev_col in dev_cols.items():
            if col_name not in prod_cols:
                issues.append(
                    {
                        "severity": "error",
                        "prod_table": prod_table,
                        "dev_table": dev_table,
                        "kind": "missing_column",
                        "column": col_name,
                        "dev_type": dev_col["data_type"],
                        "message": (
                            f"Prod '{prod_table}' missing column '{col_name}' "
                            f"(dev type: {dev_col['data_type']})."
                        ),
                    }
                )
                table_clean = False

        # Type mismatches
        for col_name, dev_col in dev_cols.items():
            if col_name not in prod_cols:
                continue
            prod_col = prod_cols[col_name]
            if dev_col["data_type"] != prod_col["data_type"]:
                key = (prod_table, col_name)
                entry = {
                    "prod_table": prod_table,
                    "dev_table": dev_table,
                    "kind": "type_mismatch",
                    "column": col_name,
                    "dev_type": dev_col["data_type"],
                    "prod_type": prod_col["data_type"],
                    "message": (
                        f"Type mismatch on '{prod_table}'.'{col_name}': "
                        f"dev={dev_col['data_type']}, prod={prod_col['data_type']}."
                    ),
                }
                if key in EXPECTED_TYPE_DIFFERENCES:
                    entry["severity"] = "info"
                    entry["message"] += " (expected; no action needed)"
                    info.append(entry)
                else:
                    entry["severity"] = "warn"
                    warnings.append(entry)
                    table_clean = False

        # Extra prod-only columns (informational)
        for col_name in prod_cols:
            if col_name not in dev_cols:
                info.append(
                    {
                        "severity": "info",
                        "prod_table": prod_table,
                        "dev_table": dev_table,
                        "kind": "extra_prod_column",
                        "column": col_name,
                        "message": f"Prod '{prod_table}' has extra column '{col_name}' not in dev.",
                    }
                )

        if table_clean:
            tables_ok.append(prod_table)

    has_errors = any(i["severity"] == "error" for i in issues)
    return {
        "ok": not has_errors,
        "tables_ok": tables_ok,
        "issues": issues,
        "warnings": warnings,
        "info": info,
    }


def _print_human(report: dict[str, Any]) -> None:
    RESET = "\033[0m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"

    print()
    print(f"{BOLD}=== Home Sync Schema Parity Audit ==={RESET}")
    print()

    if report["tables_ok"]:
        for t in report["tables_ok"]:
            print(f"  {GREEN}OK{RESET}  {t}")

    if report["issues"]:
        print()
        print(f"{BOLD}{RED}ERRORS (action required):{RESET}")
        for item in report["issues"]:
            print(f"  {RED}X{RESET}  [{item['kind']}]  {item['message']}")

    if report["warnings"]:
        print()
        print(f"{BOLD}{YELLOW}WARNINGS:{RESET}")
        for item in report["warnings"]:
            print(f"  {YELLOW}!{RESET}  [{item['kind']}]  {item['message']}")

    if report["info"]:
        print()
        print(f"{BOLD}{CYAN}INFO:{RESET}")
        for item in report["info"]:
            print(f"  {CYAN}i{RESET}  [{item['kind']}]  {item['message']}")

    print()
    if report["ok"]:
        print(f"{GREEN}{BOLD}All prod tables are column-complete relative to dev.{RESET}")
    else:
        err_count = len(report["issues"])
        print(f"{RED}{BOLD}Audit FAILED: {err_count} error(s) found.{RESET}")
        print()
        print("To fix, run migrations in Supabase SQL Editor:")
        print("  1. migrations/022_prod_dev_schema_parity.sql   (idempotent catch-all)")
        print("  2. If prod budget tables are entirely missing, run 017 first.")
        print()
        print("Or use the apply helper (requires SUPABASE_DB_URL + psycopg2):")
        print("  python maintenance/apply_schema_parity.py --apply")
    print()


def main() -> int:
    use_json = "--json" in sys.argv

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        msg = "psycopg2-binary is required. Install with: pip install psycopg2-binary"
        if use_json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    try:
        conn, warning = connect()
    except RuntimeError as exc:
        msg = str(exc)
        if use_json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    if warning and not use_json:
        print(f"NOTE: {warning}", file=sys.stderr)

    try:
        with conn.cursor() as cur:
            report = _audit(cur)
    finally:
        conn.close()

    if use_json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
