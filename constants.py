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
    {"name": "Projects", "sub": "Home Improvement", "type": "Project"},
    {"name": "Income", "sub": "Paycheck", "type": "Income"},
    {"name": "Income", "sub": "Bonus/Windfall", "type": "Income"}
]

# Auto-created for project purchase logging when a household has no matching category yet.
PROJECT_EXPENSE_CATEGORY = {
    "name": "Projects",
    "sub": "General Purchases",
}


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