"""Daily disbursement automation batch — intended for cron / GitHub Actions.

Usage:
    python maintenance/run_disbursement_automation.py [--dry-run]

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in environment (or .env file).

For each active household it runs:
1. sync_disbursement_plan(current month)   — stale-check / first-time insert
2. sync_disbursement_plan(next month)      — full rollover sync
3. auto_complete_due_member_transfers      — pay_date <= today
4. ensure_completed_transfer_allowance_expenses
5. cleanup_orphan_disbursement_artifacts
6. repair_disbursement_allowance_incomes
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running from the repo root or from maintenance/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Minimal Streamlit session-state stub so database.py imports cleanly
# ---------------------------------------------------------------------------
import types

_fake_st = types.ModuleType("streamlit")
_state: dict = {}


class _FakeSessionState:
    def get(self, key, default=None):
        return _state.get(key, default)
    def __setitem__(self, key, value):
        _state[key] = value
    def __getitem__(self, key):
        return _state[key]
    def __contains__(self, key):
        return key in _state
    def pop(self, key, *args):
        return _state.pop(key, *args)
    def keys(self):
        return _state.keys()


_fake_st.session_state = _FakeSessionState()

class _FakeSecrets:
    def get(self, key, default=None):
        return default
    def __getitem__(self, key):
        raise KeyError(key)

_fake_st.secrets = _FakeSecrets()
sys.modules.setdefault("streamlit", _fake_st)

# Patch session state so the database module runs with admin context.
_state["user_role"] = "admin"
_state["username"] = "automation"

# ---------------------------------------------------------------------------
import database  # noqa: E402  — must come after the stub


TZ = ZoneInfo("America/Chicago")


def _get_households() -> list[str]:
    """Return all household_ids that have disbursement transfer history."""
    try:
        rows = (
            database.supabase.table(database.get_member_transfers_table())
            .select("household_id")
            .execute()
        ).data or []
        seen: set[str] = set()
        ids = []
        for r in rows:
            hid = r.get("household_id")
            if hid and hid not in seen:
                seen.add(hid)
                ids.append(hid)
        return ids
    except Exception as e:
        print(f"Error listing households: {e}")
        return []


def run_for_household(household_id: str, *, dry_run: bool = False) -> dict:
    now = datetime.now(TZ)
    cur_month = now.strftime("%Y-%m")
    cur_year, cur_month_int = map(int, cur_month.split("-"))
    next_month = (
        f"{cur_year + 1}-01" if cur_month_int == 12
        else f"{cur_year}-{cur_month_int + 1:02d}"
    )
    results: dict = {
        "household_id": household_id,
        "dry_run": dry_run,
        "current_month": cur_month,
        "next_month": next_month,
    }
    if dry_run:
        results["note"] = "dry-run: no changes made"
        return results

    # 1 + 2: sync current (stale-check) + next (full rollover)
    r_cur = database.sync_disbursement_plan(household_id, cur_month)
    r_next = database.sync_disbursement_plan(household_id, next_month)
    results["sync_current"] = r_cur
    results["sync_next"] = r_next

    # 3: auto-complete due transfers
    completed = database.auto_complete_due_member_transfers(household_id)
    results["completed_transfers"] = completed

    # 4: ensure allowance expenses for completed transfers
    backfilled = database.ensure_completed_transfer_allowance_expenses(household_id, cur_month)
    results["backfilled_expenses"] = backfilled

    # 5: orphan cleanup
    orphan_stats = database.cleanup_orphan_disbursement_artifacts(household_id, cur_month)
    results["orphan_cleanup"] = orphan_stats

    # 6: repair allowance income links
    database.repair_disbursement_allowance_incomes(household_id, cur_month)
    results["income_repair"] = "done"

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily disbursement automation batch")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen without changing data")
    parser.add_argument("--household", help="Run for a single household_id only")
    args = parser.parse_args()

    households = [args.household] if args.household else _get_households()
    if not households:
        print("No households found.")
        return

    print(f"Running disbursement automation for {len(households)} household(s) (dry_run={args.dry_run})")
    for hid in households:
        try:
            result = run_for_household(hid, dry_run=args.dry_run)
            print(f"  {hid}: {result}")
        except Exception as e:
            print(f"  {hid}: ERROR — {e}")


if __name__ == "__main__":
    main()
