"""One-shot scrub for test_home June 2026 disbursement phantoms."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "maintenance"))

from db_connection import connect
from database import decrypt_text, decrypt_float
from constants import TRANSFER_ALLOWANCE_EXPENSE_DETAILS, ALLOWANCE_INCOME_SOURCE_NAME, OBLIGATION_SUPPORT_INCOME_SOURCE_NAME

HID = "test_home"
MONTH = "2026-06"
TRANSFERS = "household_member_transfers_dev"
EXPENSES = "expenses_dev"
INCOMES = "household_incomes_dev"
TRANSFER_INCOMES = {ALLOWANCE_INCOME_SOURCE_NAME, OBLIGATION_SUPPORT_INCOME_SOURCE_NAME}

conn, _ = connect()
with conn:
    cur = conn.cursor()

    cur.execute(
        f"SELECT id FROM {TRANSFERS} WHERE household_id = %s AND month_year = %s",
        (HID, MONTH),
    )
    transfer_ids = [row[0] for row in cur.fetchall()]
    if transfer_ids:
        cur.execute(f"DELETE FROM {TRANSFERS} WHERE id = ANY(%s::uuid[])", (transfer_ids,))
        print(f"deleted transfers: {cur.rowcount}")

    cur.execute(
        f"SELECT id, details FROM {EXPENSES} WHERE household_id = %s AND month_year = %s AND is_personal_spend = false",
        (HID, MONTH),
    )
    expense_delete = []
    for eid, details in cur.fetchall():
        text = decrypt_text(details) if details else ""
        if text == TRANSFER_ALLOWANCE_EXPENSE_DETAILS:
            expense_delete.append(eid)
    for eid in expense_delete:
        cur.execute(f"DELETE FROM {EXPENSES} WHERE id = %s", (eid,))
    print(f"deleted auto allowance expenses: {len(expense_delete)}")

    cur.execute(
        f"""
        SELECT id, source_name, source_expense_id
        FROM {INCOMES}
        WHERE household_id = %s AND month_year = %s AND is_personal_income = true
        """,
        (HID, MONTH),
    )
    income_delete = []
    for iid, source_name, source_expense_id in cur.fetchall():
        if source_expense_id:
            continue
        source = decrypt_text(source_name) if source_name else ""
        if source in TRANSFER_INCOMES:
            income_delete.append(iid)
    for iid in income_delete:
        cur.execute(f"DELETE FROM {INCOMES} WHERE id = %s", (iid,))
    print(f"deleted transfer personal incomes: {len(income_delete)}")

conn.close()
print("Done — refresh app; transfers will re-materialize on load. Use Reset plan to force fresh schedule.")
