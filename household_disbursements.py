"""Household disbursement planning: transfer needs, surplus splits, paycheck schedule."""

from __future__ import annotations

from datetime import date

ADMIN_DEVELOPER_ROLES = frozenset({"admin", "developer"})

# Typical paycheck slots in a normal calendar month (not the occasional extra-pay period).
TYPICAL_PAYCHECKS_BY_FREQUENCY = {
    "weekly": 4,
    "bi_weekly": 2,
    "semi_monthly": 2,
    "monthly": 1,
    "school_year": 1,
    "one_time": 0,
    "annual": 0,
    "quarterly": 0,
}


def typical_paycheck_count_for_frequency(freq: str) -> int:
    key = str(freq or "monthly").strip().lower().replace("-", "_")
    return TYPICAL_PAYCHECKS_BY_FREQUENCY.get(key, 1)


def typical_paycheck_count_for_streams(stream_details: list[dict]) -> int:
    """Expected paycheck count for a member's selected funding streams in a normal month."""
    return sum(
        typical_paycheck_count_for_frequency(s.get("frequency") or "monthly")
        for s in (stream_details or [])
    )


def disbursement_review_flags(per_member_stream_info: dict) -> list[dict]:
    """Flag members whose month has more paychecks than their streams usually produce."""
    flags: list[dict] = []
    for member, info in sorted((per_member_stream_info or {}).items()):
        streams = info.get("streams") or []
        if not streams:
            continue
        actual = int(info.get("paycheck_count") or 0)
        typical = typical_paycheck_count_for_streams(streams)
        if actual > typical:
            flags.append({
                "member": member,
                "actual_paycheck_count": actual,
                "typical_paycheck_count": typical,
                "message": (
                    f"{member}: {actual} paycheck(s) this month (usually {typical}) — "
                    "review per-transfer amounts."
                ),
            })
    return flags


def sum_transfer_allowance_total(transfers: list[dict]) -> float:
    """Monthly allowance total from saved member transfer rows."""
    total = 0.0
    for row in transfers or []:
        total += float(row.get("allowance_amount") or 0)
    return round(total, 2)


def disbursement_allowance_surplus_flags(
    *,
    current_surplus_pool: float,
    planned_allowance_total: float,
    recommended_allowance_total: float | None = None,
    tolerance: float = 0.02,
) -> list[dict]:
    """Warn when planned allowance disbursements exceed available household surplus."""
    flags: list[dict] = []
    pool = round(float(current_surplus_pool or 0), 2)
    planned = round(float(planned_allowance_total or 0), 2)
    recommended = (
        round(float(recommended_allowance_total or 0), 2)
        if recommended_allowance_total is not None
        else None
    )

    if planned <= tolerance:
        return flags

    if planned > pool + tolerance:
        overage = round(planned - pool, 2)
        flags.append({
            "kind": "allowance_exceeds_surplus",
            "current_surplus_pool": pool,
            "planned_allowance_total": planned,
            "overage": overage,
            "message": (
                "Household Budget: Review Allowance Disbursement. "
                "Current amounts exceed Surplus Income."
            ),
        })
        return flags

    if recommended is not None and abs(planned - recommended) > tolerance:
        flags.append({
            "kind": "allowance_stale_vs_recommended",
            "current_surplus_pool": pool,
            "planned_allowance_total": planned,
            "recommended_allowance_total": recommended,
            "message": (
                "Household Budget: Review Allowance Disbursement. "
                "Amounts no longer match Surplus Income."
            ),
        })

    return flags


def filter_disbursement_eligible_usernames(users: list[dict]) -> list[str]:
    """Usernames with admin or developer role (even split recipients)."""
    names = []
    for row in users or []:
        role = str(row.get("role") or "").strip().lower()
        username = str(row.get("username") or "").strip()
        if username and role in ADMIN_DEVELOPER_ROLES:
            names.append(username)
    return sorted(set(names))


def compute_member_transfer_needs(by_member: dict) -> dict[str, float]:
    """Monthly obligation gap for each member who is short (obligation > take-home)."""
    needs: dict[str, float] = {}
    for member, totals in (by_member or {}).items():
        gap = float(totals.get("supplement_gap") or 0)
        if gap > 0.005:
            needs[str(member)] = round(gap, 2)
    return needs


def compute_surplus_pool(total_regular_income: float, total_assigned_obligations: float) -> float:
    """Household income left after all assigned obligation targets."""
    return max(0.0, float(total_regular_income or 0) - float(total_assigned_obligations or 0))


def compute_surplus_shares(surplus_pool: float, eligible_usernames: list[str]) -> dict[str, float]:
    """Even split of surplus pool among eligible (admin/developer) members."""
    if not eligible_usernames or surplus_pool <= 0.005:
        return {}
    share = surplus_pool / len(eligible_usernames)
    return {name: round(share, 2) for name in eligible_usernames}


def compute_member_bundled_amounts(
    member_transfer_needs: dict[str, float],
    surplus_shares: dict[str, float],
) -> dict[str, dict]:
    """Per-member monthly totals broken into obligation + allowance components.

    Returns a dict keyed by username:
        {
            "obligation_amount": float,  # gap to cover assigned obligations
            "allowance_amount":  float,  # discretionary surplus share
            "total_amount":      float,  # bundled wire (obligation + allowance)
        }
    """
    all_members = set(member_transfer_needs) | set(surplus_shares)
    result: dict[str, dict] = {}
    for member in all_members:
        obligation = round(float(member_transfer_needs.get(member) or 0), 2)
        allowance = round(float(surplus_shares.get(member) or 0), 2)
        result[member] = {
            "obligation_amount": obligation,
            "allowance_amount": allowance,
            "total_amount": round(obligation + allowance, 2),
        }
    return result


def build_paycheck_disbursement_schedule(
    pay_dates: list[date],
    member_transfer_needs: dict[str, float],
    surplus_shares: dict[str, float],
) -> list[dict]:
    """Split monthly per-member totals evenly across funding paychecks.

    Each schedule entry:
        {
            "payment_date": "YYYY-MM-DD",
            "payouts": {
                "Angelle": {
                    "obligation": float,
                    "allowance": float,
                    "total": float,
                },
                ...
            },
            "total": float,   # sum across all members for this paycheck
        }
    """
    if not pay_dates:
        return []

    paycheck_count = len(pay_dates)
    monthly_bundles = compute_member_bundled_amounts(member_transfer_needs, surplus_shares)
    schedule: list[dict] = []

    for pay_date in sorted(pay_dates):
        payouts: dict[str, dict] = {}
        for member, bundle in monthly_bundles.items():
            obl = round(bundle["obligation_amount"] / paycheck_count, 2)
            allow = round(bundle["allowance_amount"] / paycheck_count, 2)
            payouts[member] = {
                "obligation": obl,
                "allowance": allow,
                "total": round(obl + allow, 2),
            }
        schedule.append(
            {
                "payment_date": pay_date.isoformat(),
                "payouts": payouts,
                "total": round(sum(p["total"] for p in payouts.values()), 2),
            }
        )
    return schedule


def summarize_monthly_disbursement(
    member_transfer_needs: dict[str, float],
    surplus_shares: dict[str, float],
) -> dict:
    transfer_total = round(sum(member_transfer_needs.values()), 2)
    surplus_total = round(sum(surplus_shares.values()), 2)
    return {
        "member_transfer_total": transfer_total,
        "surplus_split_total": surplus_total,
        "monthly_disbursement_total": round(transfer_total + surplus_total, 2),
    }
