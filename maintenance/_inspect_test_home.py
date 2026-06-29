import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "maintenance"))

from db_connection import connect
from database import decrypt_text, decrypt_float

HID = "test_home"
MONTH = "2026-06"

conn, _ = connect()
with conn:
    cur = conn.cursor()
    print("=== TRANSFERS ===")
    cur.execute(
        """
        SELECT id, payment_date, recipient_username, status,
               household_allowance_expense_id, personal_allowance_income_id
        FROM household_member_transfers_dev
        WHERE household_id = %s AND month_year = %s
        ORDER BY payment_date, recipient_username
        """,
        (HID, MONTH),
    )
    transfers = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    for t in transfers:
        print(t)
    print(f"total: {len(transfers)}")

    print("\n=== HH EXPENSES (shared) ===")
    cur.execute(
        """
        SELECT id, date_logged, amount, details, category_id
        FROM expenses_dev
        WHERE household_id = %s AND month_year = %s AND is_personal_spend = false
        ORDER BY date_logged
        """,
        (HID, MONTH),
    )
    expenses = []
    for r in cur.fetchall():
        row = dict(zip([d[0] for d in cur.description], r))
        row["amount"] = decrypt_float(row.get("amount"))
        row["details"] = decrypt_text(row.get("details"))
        expenses.append(row)
        print(row)
    print(f"total: {len(expenses)} sum={sum(e['amount'] or 0 for e in expenses):.2f}")

    linked_expense_ids = {
        str(t["household_allowance_expense_id"])
        for t in transfers
        if t.get("household_allowance_expense_id")
    }
    orphans = [e for e in expenses if str(e["id"]) not in linked_expense_ids]
    auto_orphans = [e for e in orphans if e.get("details") == "Disbursement transfer (auto)"]
    print(f"\norphan auto allowance expenses: {len(auto_orphans)}")
    for e in auto_orphans:
        print("  ORPHAN", e)

    print("\n=== PERSONAL INCOMES (allowance/obligation) ===")
    cur.execute(
        """
        SELECT id, owner_username, payment_date, source_name, take_home_amount
        FROM household_incomes_dev
        WHERE household_id = %s AND month_year = %s AND is_personal_income = true
        ORDER BY payment_date, owner_username
        """,
        (HID, MONTH),
    )
    for r in cur.fetchall():
        row = dict(zip([d[0] for d in cur.description], r))
        row["source_name"] = decrypt_text(row.get("source_name"))
        row["take_home_amount"] = decrypt_float(row.get("take_home_amount"))
        print(row)
conn.close()
