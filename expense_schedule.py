"""Pay-schedule helpers for expense streams (reuses income cadence math)."""

from income_schedule import (  # noqa: F401
    INCOME_SUB_MONTHLY_FREQUENCIES,
    paycheck_occurrences_in_month,
    pay_dates_for_version_in_range,
    pay_dates_in_month,
    resolve_version_at_date,
)

EXPENSE_SUB_MONTHLY_FREQUENCIES = INCOME_SUB_MONTHLY_FREQUENCIES


def expense_is_sub_monthly_frequency(pay_frequency: str) -> bool:
    return pay_frequency in EXPENSE_SUB_MONTHLY_FREQUENCIES


def bill_occurrences_in_month(versions: list[dict], month_year: str) -> list[dict]:
    """Each bill date in month_year with the active version."""
    occurrences = []
    for item in paycheck_occurrences_in_month(versions, month_year):
        occurrences.append({"date_logged": item["payment_date"], "version": item["version"]})
    return occurrences
