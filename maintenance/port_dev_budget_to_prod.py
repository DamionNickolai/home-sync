"""Port budget-related household data from *_dev tables to production tables.

Dry-run by default. Use --apply to execute. Scrub *_dev only after validating prod.

Excludes project_budgets and wish_list.

Usage:
  python maintenance/port_dev_budget_to_prod.py --household-id gibson_home
  python maintenance/port_dev_budget_to_prod.py --household-id gibson_home --apply
  python maintenance/port_dev_budget_to_prod.py --household-id gibson_home --validate
  python maintenance/port_dev_budget_to_prod.py --household-id gibson_home --scrub-dev --apply

Requires SUPABASE_DB_PASSWORD + SUPABASE_URL (or SUPABASE_DB_URL) in .env.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_connection import connect

DEFAULT_HOUSEHOLD_ID = "gibson_home"

# Delete order: children first. Insert = reversed.
TABLE_SPECS: list[dict[str, Any]] = [
    {"dev": "household_member_transfers_dev", "prod": "household_member_transfers", "scope": "household"},
    {"dev": "household_supplement_snapshots_dev", "prod": "household_supplement_snapshots", "scope": "household"},
    {"dev": "expenses_dev", "prod": "expenses", "scope": "household"},
    {"dev": "household_incomes_dev", "prod": "household_incomes", "scope": "household"},
    {"dev": "user_disbursement_funding_streams_dev", "prod": "user_disbursement_funding_streams", "scope": "household"},
    {"dev": "household_obligation_assignments_dev", "prod": "household_obligation_assignments", "scope": "household"},
    {"dev": "household_disbursement_settings_dev", "prod": "household_disbursement_settings", "scope": "household"},
    {
        "dev": "household_income_stream_versions_dev",
        "prod": "household_income_stream_versions",
        "scope": "stream",
        "stream_dev": "household_income_streams_dev",
        "stream_prod": "household_income_streams",
    },
    {
        "dev": "household_expense_stream_versions_dev",
        "prod": "household_expense_stream_versions",
        "scope": "stream",
        "stream_dev": "household_expense_streams_dev",
        "stream_prod": "household_expense_streams",
    },
    {"dev": "household_income_streams_dev", "prod": "household_income_streams", "scope": "household"},
    {"dev": "household_expense_streams_dev", "prod": "household_expense_streams", "scope": "household"},
    {"dev": "cash_flow_routing_dev", "prod": "cash_flow_routing", "scope": "household"},
    {"dev": "user_finance_settings_dev", "prod": "user_finance_settings", "scope": "household"},
    {"dev": "budget_categories_dev", "prod": "budget_categories", "scope": "household"},
    {
        "dev": "household_finance_settings_dev",
        "prod": "household_finance_settings",
        "scope": "household",
        "skip_if_dev_empty": True,
    },
]

DEV_SCRUB_TABLES = [spec["dev"] for spec in TABLE_SPECS]

UUID_TO_BIGINT_TABLES = frozenset({
    "budget_categories",
    "household_incomes",
    "expenses",
    "cash_flow_routing",
    "user_finance_settings",
})

FK_REMAP_COLUMNS: dict[str, dict[str, str]] = {
    "expenses": {"category_id": "budget_categories"},
    "household_expense_streams": {"category_id": "budget_categories"},
    "household_incomes": {"source_expense_id": "expenses"},
    "household_obligation_assignments": {"category_id": "budget_categories"},
    "household_member_transfers": {
        "personal_allowance_income_id": "household_incomes",
        "personal_obligation_income_id": "household_incomes",
    },
    "household_supplement_snapshots": {"allowance_expense_id": "expenses"},
}

# Remap to TEXT string ids (prod columns are TEXT, dev may be UUID)
TEXT_FK_COLUMNS: dict[str, frozenset[str]] = {
    "household_incomes": frozenset({"source_expense_id"}),
    "household_member_transfers": frozenset({"personal_allowance_income_id", "personal_obligation_income_id"}),
    "household_supplement_snapshots": frozenset({"allowance_expense_id"}),
    "household_obligation_assignments": frozenset({"category_id"}),
}


def _table_exists(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s LIMIT 1
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def _columns(cur, table_name: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return [row[0] for row in cur.fetchall()]


def _id_type(cur, table_name: str) -> str:
    cur.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = 'id'
        """,
        (table_name,),
    )
    row = cur.fetchone()
    return row[0] if row else "unknown"


def _count_household(cur, table: str, household_id: str) -> int | None:
    if not _table_exists(cur, table):
        return None
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE household_id = %s", (household_id,))
    return int(cur.fetchone()[0])


def _count_stream_scoped(cur, table: str, stream_table: str, household_id: str) -> int | None:
    if not _table_exists(cur, table) or not _table_exists(cur, stream_table):
        return None
    cur.execute(
        f"""
        SELECT COUNT(*) FROM {table} v
        JOIN {stream_table} s ON s.id = v.stream_id
        WHERE s.household_id = %s
        """,
        (household_id,),
    )
    return int(cur.fetchone()[0])


def _count_spec(cur, spec: dict[str, Any], household_id: str) -> int | None:
    if spec["scope"] == "household":
        return _count_household(cur, spec["dev"], household_id)
    return _count_stream_scoped(cur, spec["dev"], spec["stream_dev"], household_id)


def _delete_household(cur, table: str, household_id: str) -> int:
    cur.execute(f"DELETE FROM {table} WHERE household_id = %s", (household_id,))
    return cur.rowcount


def _delete_stream_scoped(cur, table: str, stream_table: str, household_id: str) -> int:
    cur.execute(
        f"""
        DELETE FROM {table} v
        USING {stream_table} s
        WHERE v.stream_id = s.id AND s.household_id = %s
        """,
        (household_id,),
    )
    return cur.rowcount


def _fetch_household(cur, table: str, household_id: str) -> list[dict[str, Any]]:
    cols = _columns(cur, table)
    col_sql = ", ".join(cols)
    cur.execute(f"SELECT {col_sql} FROM {table} WHERE household_id = %s", (household_id,))
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_stream_scoped(cur, table: str, stream_table: str, household_id: str) -> list[dict[str, Any]]:
    cols = _columns(cur, table)
    col_sql = ", ".join(f"v.{c}" for c in cols)
    cur.execute(
        f"""
        SELECT {col_sql} FROM {table} v
        JOIN {stream_table} s ON s.id = v.stream_id
        WHERE s.household_id = %s
        """,
        (household_id,),
    )
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_spec(cur, spec: dict[str, Any], household_id: str, *, dev: bool = True) -> list[dict[str, Any]]:
    table = spec["dev"] if dev else spec["prod"]
    if spec["scope"] == "household":
        return _fetch_household(cur, table, household_id)
    stream_table = spec["stream_dev"] if dev else spec["stream_prod"]
    return _fetch_stream_scoped(cur, table, stream_table, household_id)


def _ensure_obligation_category_id_text(cur) -> None:
    """Prod obligation category_id must be TEXT to reference bigint budget category ids."""
    cur.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'household_obligation_assignments'
          AND column_name = 'category_id'
        """
    )
    row = cur.fetchone()
    if row and row[0] == "uuid":
        cur.execute(
            """
            ALTER TABLE household_obligation_assignments
            ALTER COLUMN category_id TYPE TEXT USING category_id::text
            """
        )


def _remap_value(
    id_maps: dict[str, dict[Any, Any]],
    ref_table: str,
    value: Any,
    *,
    as_text: bool = False,
) -> Any:
    if value is None:
        return None
    mapped = id_maps.get(ref_table, {}).get(value, value)
    if as_text and mapped is not None:
        return str(mapped)
    return mapped


def _transform_row(
    row: dict[str, Any],
    prod_table: str,
    prod_cols: list[str],
    id_maps: dict[str, dict[Any, Any]],
) -> dict[str, Any]:
    text_fks = TEXT_FK_COLUMNS.get(prod_table, frozenset())
    fk_map = FK_REMAP_COLUMNS.get(prod_table, {})
    out: dict[str, Any] = {}
    for col in prod_cols:
        if col not in row:
            continue
        val = row[col]
        if col == "id" and prod_table in UUID_TO_BIGINT_TABLES:
            continue
        if col == "auth_user_id" and val is not None and not isinstance(val, str):
            val = str(val)
        if col in fk_map:
            val = _remap_value(
                id_maps,
                fk_map[col],
                val,
                as_text=col in text_fks,
            )
        out[col] = val
    return out


def _insert_row(cur, table: str, row: dict[str, Any], *, returning_id: bool = False) -> Any:
    cols = list(row.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    if returning_id:
        sql += " RETURNING id"
        cur.execute(sql, [row[c] for c in cols])
        return cur.fetchone()[0]
    cur.execute(sql, [row[c] for c in cols])
    return None


def port_household(cur, household_id: str, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"household_id": household_id, "applied": apply, "tables": {}}

    if not apply:
        for spec in TABLE_SPECS:
            dev_n = _count_spec(cur, spec, household_id) or 0
            if spec["scope"] == "stream":
                prod_n = _count_stream_scoped(cur, spec["prod"], spec["stream_prod"], household_id) or 0
            else:
                prod_n = _count_household(cur, spec["prod"], household_id) or 0
            report["tables"][spec["prod"]] = {
                "dev_rows": dev_n,
                "prod_rows_before": prod_n or 0,
                "dev_id_type": _id_type(cur, spec["dev"]),
                "prod_id_type": _id_type(cur, spec["prod"]),
            }
        return report

    _ensure_obligation_category_id_text(cur)
    id_maps: dict[str, dict[Any, Any]] = {}

    for spec in TABLE_SPECS:
        prod_table = spec["prod"]
        if not _table_exists(cur, prod_table):
            continue
        dev_rows = _fetch_spec(cur, spec, household_id, dev=True)
        if spec.get("skip_if_dev_empty") and not dev_rows:
            report["tables"].setdefault(prod_table, {})["skipped"] = "dev empty — prod preserved"
            continue
        if spec["scope"] == "household":
            deleted = _delete_household(cur, prod_table, household_id)
        else:
            deleted = _delete_stream_scoped(cur, prod_table, spec["stream_prod"], household_id)
        report["tables"].setdefault(prod_table, {})["prod_deleted"] = deleted

    for spec in reversed(TABLE_SPECS):
        dev_table = spec["dev"]
        prod_table = spec["prod"]
        if not _table_exists(cur, dev_table) or not _table_exists(cur, prod_table):
            continue

        dev_rows = _fetch_spec(cur, spec, household_id, dev=True)
        prod_cols = _columns(cur, prod_table)
        inserted = 0
        table_map = id_maps.setdefault(prod_table, {})

        needs_new_ids = (
            prod_table in UUID_TO_BIGINT_TABLES
            and _id_type(cur, dev_table) != _id_type(cur, prod_table)
        )

        for dev_row in dev_rows:
            old_id = dev_row.get("id")
            payload = _transform_row(dev_row, prod_table, prod_cols, id_maps)

            if needs_new_ids:
                new_id = _insert_row(cur, prod_table, payload, returning_id=True)
                if old_id is not None and new_id is not None:
                    table_map[old_id] = new_id
            else:
                if "id" in prod_cols and "id" in dev_row:
                    payload["id"] = dev_row["id"]
                elif "id" in prod_cols and "id" not in payload:
                    payload["id"] = str(uuid.uuid4())
                _insert_row(cur, prod_table, payload, returning_id=False)
            inserted += 1

        entry = report["tables"].setdefault(prod_table, {})
        entry["inserted"] = inserted
        if table_map:
            entry["id_remapped"] = len(table_map)

    report["id_maps"] = {k: len(v) for k, v in id_maps.items()}
    return report


def validate_port(cur, household_id: str) -> dict[str, Any]:
    results: dict[str, Any] = {"household_id": household_id, "ok": True, "tables": {}}
    for spec in TABLE_SPECS:
        dev_n = _count_spec(cur, spec, household_id) or 0
        if spec["scope"] == "stream":
            prod_n = _count_stream_scoped(cur, spec["prod"], spec["stream_prod"], household_id) or 0
        else:
            prod_n = _count_household(cur, spec["prod"], household_id) or 0
        match = dev_n == prod_n
        if spec.get("skip_if_dev_empty") and dev_n == 0 and prod_n >= 0:
            match = True
        if not match:
            results["ok"] = False
        results["tables"][spec["prod"]] = {"dev": dev_n, "prod": prod_n, "match": match}
    return results


def scrub_dev(cur, household_id: str, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"household_id": household_id, "applied": apply, "tables": {}}
    for spec in TABLE_SPECS:
        table = spec["dev"]
        if spec["scope"] == "household":
            count = _count_household(cur, table, household_id) or 0
            deleted = _delete_household(cur, table, household_id) if apply and count else 0
        else:
            count = _count_stream_scoped(cur, table, spec["stream_dev"], household_id) or 0
            deleted = (
                _delete_stream_scoped(cur, table, spec["stream_dev"], household_id)
                if apply and count
                else 0
            )
        report["tables"][table] = {"rows_before": count, "deleted": deleted}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Port budget household data dev → prod.")
    parser.add_argument("--household-id", default=DEFAULT_HOUSEHOLD_ID)
    parser.add_argument("--apply", action="store_true", help="Execute changes (default: dry-run).")
    parser.add_argument("--validate", action="store_true", help="Compare dev vs prod row counts.")
    parser.add_argument("--scrub-dev", action="store_true", help="Delete household rows from *_dev only.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    household_id = args.household_id.strip()
    if not household_id:
        print("Error: --household-id required", file=sys.stderr)
        return 1

    conn, warning = connect()
    if warning:
        print(f"Note: {warning}", file=sys.stderr)

    try:
        with conn:
            with conn.cursor() as cur:
                if args.validate:
                    report = validate_port(cur, household_id)
                elif args.scrub_dev:
                    report = scrub_dev(cur, household_id, apply=args.apply)
                else:
                    report = port_household(cur, household_id, apply=args.apply)
                if args.apply:
                    conn.commit()
                else:
                    conn.rollback()

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            if args.validate:
                print(f"\nValidation for {household_id}:")
                for table, info in report.get("tables", {}).items():
                    mark = "OK" if info["match"] else "MISMATCH"
                    print(f"  [{mark}] {table}: dev={info['dev']} prod={info['prod']}")
                print("\nPASS" if report["ok"] else "\nFAIL — counts differ")
            elif args.scrub_dev:
                mode = "APPLIED" if args.apply else "DRY-RUN"
                print(f"\nDev scrub ({mode}) for {household_id}:")
                for table, info in report.get("tables", {}).items():
                    if info["rows_before"]:
                        suffix = f" -> deleted {info['deleted']}" if args.apply else ""
                        print(f"  {table}: {info['rows_before']} rows{suffix}")
            else:
                mode = "APPLIED" if args.apply else "DRY-RUN"
                print(f"\nPort dev -> prod ({mode}) for {household_id}:")
                for table, info in report.get("tables", {}).items():
                    if args.apply:
                        print(
                            f"  {table}: deleted {info.get('prod_deleted', 0)}, "
                            f"inserted {info.get('inserted', 0)}"
                        )
                    else:
                        print(
                            f"  {table}: dev={info.get('dev_rows', 0)} "
                            f"prod_before={info.get('prod_rows_before', 0)} "
                            f"(id: {info.get('dev_id_type')} -> {info.get('prod_id_type')})"
                        )
                if not args.apply:
                    print("\nDry-run only. Re-run with --apply to port.")
                    print("After validating prod, scrub dev with:")
                    print(
                        f"  python maintenance/port_dev_budget_to_prod.py "
                        f"--household-id {household_id} --scrub-dev --apply"
                    )

        if args.validate:
            return 0 if report["ok"] else 1
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
