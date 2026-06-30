# constants.py

# --- SYSTEM DEFAULT CATEGORIES ---
DEFAULT_BUDGET_CATEGORIES = [
    {"name": "Housing", "sub": "Mortgage/Rent", "type": "Fixed Expense"},
    {"name": "Housing", "sub": "Maintenance", "type": "Variable Expense"},
    {"name": "Utilities", "sub": "Electricity", "type": "Fixed Expense"},
    {"name": "Utilities", "sub": "Water/Sewer", "type": "Fixed Expense"},
    {"name": "Utilities", "sub": "Internet", "type": "Fixed Expense"},
    {"name": "Transportation", "sub": "Fuel", "type": "Variable Expense"},
    {"name": "Transportation", "sub": "Auto Insurance", "type": "Fixed Expense"},
    {"name": "Transportation", "sub": "Maintenance", "type": "Variable Expense"},
    {"name": "Food", "sub": "Groceries", "type": "Variable Expense"},
    {"name": "Food", "sub": "Dining Out", "type": "Variable Expense"},
    {"name": "Personal", "sub": "Clothing", "type": "Variable Expense"},
    {"name": "Personal", "sub": "Subscriptions", "type": "Fixed Expense"},
    {"name": "Health", "sub": "Medical/Dental", "type": "Variable Expense"},
    {"name": "Taxes", "sub": "General", "type": "Variable Expense"},
    {"name": "Projects", "sub": "Home Improvement", "type": "Project"},
    {"name": "Income", "sub": "Paycheck", "type": "Income"},
    {"name": "Income", "sub": "Bonus/Windfall", "type": "Income"}
]

# Auto-created for project purchase logging when a household has no matching category yet.
PROJECT_EXPENSE_CATEGORY = {
    "name": "Projects",
    "sub": "General Purchases",
}

# System bucket for receipt line items that cannot be categorized at post time.
RECEIPT_UNCATEGORIZED = {"name": "Receipt", "sub": "Uncategorized"}

# Auto-created for HH shared and personal ledgers (Quick Expense + receipt logger).
TAXES_EXPENSE_CATEGORY = {
    "name": "Taxes",
    "sub": "General",
}


def is_taxes_expense_category(category_name, sub_category_name=None) -> bool:
    if str(category_name or "").strip() != TAXES_EXPENSE_CATEGORY["name"]:
        return False
    if sub_category_name is None:
        return True
    return normalize_sub_category_name(sub_category_name) == TAXES_EXPENSE_CATEGORY["sub"]


def normalize_sub_category_name(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def is_system_project_expense_category(category_name, sub_category_name=None) -> bool:
    return (
        str(category_name or "").strip() == PROJECT_EXPENSE_CATEGORY["name"]
        and normalize_sub_category_name(sub_category_name) == PROJECT_EXPENSE_CATEGORY["sub"]
    )


ALLOWANCE_CATEGORY_NAME = "Allowance"
ALLOWANCE_INCOME_SOURCE_NAME = "Allowance"
TRANSFER_ALLOWANCE_EXPENSE_DETAILS = "Disbursement transfer (auto)"
OBLIGATION_SUPPORT_INCOME_SOURCE_NAME = "Obligation Support"

# Plaintext income→transfer link (not encrypted). Format: "{transfer_uuid}#allowance|obligation"
MEMBER_TRANSFER_INCOME_LINK_SEP = "#"


def member_transfer_income_link_key(transfer_id, source_name: str) -> str:
    """Stable plaintext key tying one personal income row to one transfer side."""
    tid = str(transfer_id or "").strip()
    if source_name == ALLOWANCE_INCOME_SOURCE_NAME:
        kind = "allowance"
    elif source_name == OBLIGATION_SUPPORT_INCOME_SOURCE_NAME:
        kind = "obligation"
    else:
        kind = "other"
    return f"{tid}{MEMBER_TRANSFER_INCOME_LINK_SEP}{kind}"


def parse_member_transfer_income_link_key(link_key: str) -> tuple[str, str] | None:
    """Return (transfer_id, kind) from a link key, or None if invalid."""
    text = str(link_key or "").strip()
    if MEMBER_TRANSFER_INCOME_LINK_SEP not in text:
        return None
    transfer_id, kind = text.split(MEMBER_TRANSFER_INCOME_LINK_SEP, 1)
    transfer_id = transfer_id.strip()
    kind = kind.strip()
    if not transfer_id or not kind:
        return None
    return transfer_id, kind


def is_allowance_category(category_name, sub_category_name=None) -> bool:
    return str(category_name or "").strip() == ALLOWANCE_CATEGORY_NAME


def is_allowance_subcategory(category_name, sub_category_name) -> bool:
    return (
        is_allowance_category(category_name)
        and bool(normalize_sub_category_name(sub_category_name))
    )


def is_system_managed_allowance_category(category_name, sub_category_name=None) -> bool:
    """Block manual create/edit/delete of Allowance parent or member sub-categories."""
    if not is_allowance_category(category_name):
        return False
    if sub_category_name is None:
        return True
    return is_allowance_subcategory(category_name, sub_category_name)


def allowance_recipient_username(
    category_name,
    sub_category_name,
    *,
    username_field=None,
) -> str | None:
    """Resolve who receives allowance income for an Allowance sub-category."""
    if not is_allowance_subcategory(category_name, sub_category_name):
        return None
    recipient = (username_field or sub_category_name or "").strip()
    return recipient or None