"""Household obligation assignment resolution and displacement math."""

from __future__ import annotations

from constants import (
    ALLOWANCE_CATEGORY_NAME,
    allowance_recipient_username,
    is_allowance_subcategory,
    is_system_project_expense_category,
)


def is_assignable_household_category(row) -> bool:
    if row.get("is_personal"):
        return False
    parent = row.get("category_name")
    sub = row.get("sub_category_name")
    if is_system_project_expense_category(parent, sub):
        return False
    if str(parent or "").strip() == ALLOWANCE_CATEGORY_NAME:
        return False
    if is_allowance_subcategory(parent, sub):
        return False
    return True


def obligation_projected_amount(row, *, month_count: int = 1) -> float:
    """Monthly obligation from category target_budget (category management only)."""
    return float(row.get("target_budget") or 0) * month_count


def build_assignment_maps(assignments: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    parent_map: dict[str, str] = {}
    override_map: dict[str, str] = {}
    for row in assignments:
        if not row.get("is_active", True):
            continue
        level = row.get("assignment_level")
        member = (row.get("member_username") or "").strip()
        if not member:
            continue
        if level == "parent":
            parent = (row.get("parent_category_name") or "").strip()
            if parent:
                parent_map[parent] = member
        elif level == "subcategory" and row.get("category_id"):
            override_map[str(row["category_id"])] = member
    return parent_map, override_map


def resolve_obligation_lines(category_rows, parent_map, override_map):
    lines = []
    for row in category_rows:
        if not is_assignable_household_category(row):
            continue
        projected = obligation_projected_amount(row)
        cat_id = str(row.get("id"))
        parent = str(row.get("category_name") or "")
        sub = row.get("sub_category_name")
        if sub is None or str(sub).strip() == "" or str(sub).lower() == "nan":
            sub_label = "(General)"
        else:
            sub_label = str(sub)
        if cat_id in override_map:
            member = override_map[cat_id]
            source = "override"
        elif parent in parent_map:
            member = parent_map[parent]
            source = "parent"
        else:
            member = None
            source = "unassigned"
        lines.append({
            "category_id": cat_id,
            "parent_category_name": parent,
            "sub_category_name": sub_label,
            "member_username": member,
            "projected_amount": projected,
            "source": source,
        })
    return lines


def aggregate_member_obligations(lines):
    totals = {}
    for line in lines:
        member = line.get("member_username")
        if not member:
            continue
        totals[member] = totals.get(member, 0.0) + float(line.get("projected_amount") or 0)
    return totals


def compute_supplement_gap(total_obligation, member_take_home):
    return max(0.0, float(total_obligation or 0) - float(member_take_home or 0))


def compute_allowance_coverage(
    total_obligation,
    member_take_home,
    current_recurring_allowance,
):
    """Derive target recurring allowance and whether income+allowance covers obligations.

    Take-home excludes allowance-linked income by design. The household recurring
  Allowance stream should be set to (obligation - take_home) so the member has
    enough combined funds for assigned budget responsibilities.
    """
    obligation = float(total_obligation or 0)
    take_home = float(member_take_home or 0)
    current = float(current_recurring_allowance or 0)
    target_recurring_allowance = max(0.0, obligation - take_home)
    total_available = take_home + current
    shortfall = max(0.0, obligation - total_available)
    allowance_adjustment = round(target_recurring_allowance - current, 2)
    return {
        "target_recurring_allowance": round(target_recurring_allowance, 2),
        "current_recurring_allowance": round(current, 2),
        "total_available": round(total_available, 2),
        "shortfall": round(shortfall, 2),
        "allowance_adjustment": allowance_adjustment,
        "is_covered": shortfall <= 0.005,
        "needs_allowance_update": abs(allowance_adjustment) > 0.005,
    }


def reconcile_displacement(lines):
    total_hh = sum(float(line.get("projected_amount") or 0) for line in lines)
    assigned = sum(float(line.get("projected_amount") or 0) for line in lines if line.get("member_username"))
    unassigned_parents = set()
    parent_has_unassigned = {}
    for line in lines:
        parent = line.get("parent_category_name") or ""
        if not line.get("member_username"):
            parent_has_unassigned[parent] = True
    for parent in parent_has_unassigned:
        unassigned_parents.add(parent)
    return {
        "total_hh_projected": round(total_hh, 2),
        "total_assigned": round(assigned, 2),
        "total_unassigned": round(total_hh - assigned, 2),
        "unassigned_parents": sorted(unassigned_parents),
    }


def build_parent_summaries(lines, parent_map):
    summaries = {}
    for line in lines:
        parent = line.get("parent_category_name") or ""
        if parent not in summaries:
            summaries[parent] = {
                "parent_category_name": parent,
                "projected": 0.0,
                "assigned_member": parent_map.get(parent),
                "unassigned_subs": [],
            }
        summaries[parent]["projected"] += float(line.get("projected_amount") or 0)
        if line.get("source") == "unassigned":
            summaries[parent]["unassigned_subs"].append(line.get("sub_category_name"))
    for summary in summaries.values():
        summary["projected"] = round(summary["projected"], 2)
    return sorted(summaries.values(), key=lambda s: s["parent_category_name"].lower())


def find_allowance_category_id(category_rows, member_username):
    for row in category_rows:
        recipient = allowance_recipient_username(
            row.get("category_name"),
            row.get("sub_category_name"),
            username_field=row.get("username"),
        )
        if recipient == member_username:
            return str(row.get("id"))
    return None
