"""Pure income pay-schedule helpers (Phase 2: per-paycheck materialization)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

INCOME_SUB_MONTHLY_FREQUENCIES = frozenset({"weekly", "bi_weekly", "semi_monthly"})

SCHOOL_YEAR_ACTIVE_MONTHS = frozenset({9, 10, 11, 12, 1, 2, 3, 4, 5, 6})


def parse_iso_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def month_bounds(month_year: str) -> tuple[date, date]:
    year, month = map(int, month_year.split("-"))
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def income_is_sub_monthly_frequency(pay_frequency: str) -> bool:
    return pay_frequency in INCOME_SUB_MONTHLY_FREQUENCIES


def resolve_version_at_date(versions: list[dict], as_of: date) -> dict | None:
    """Latest version with effective_from <= as_of (versions sorted ascending)."""
    chosen = None
    for version in versions:
        effective_from = parse_iso_date(version.get("effective_from"))
        if effective_from is None or effective_from > as_of:
            continue
        chosen = version
    return chosen


def _cadence_step_days(pay_frequency: str) -> int | None:
    if pay_frequency == "weekly":
        return 7
    if pay_frequency == "bi_weekly":
        return 14
    return None


def _advance_cadence(anchor: date, step_days: int, range_start: date) -> date:
    current = anchor
    while current < range_start:
        current += timedelta(days=step_days)
    return current


def _cadence_dates_in_range(
    anchor: date,
    pay_frequency: str,
    range_start: date,
    range_end: date,
) -> list[date]:
    step = _cadence_step_days(pay_frequency)
    if step is None:
        return []
    current = _advance_cadence(anchor, step, range_start)
    dates: list[date] = []
    while current <= range_end:
        dates.append(current)
        current += timedelta(days=step)
    return dates


def _monthly_date_in_range(
    anchor_day: int,
    range_start: date,
    range_end: date,
) -> date | None:
    year, month = range_start.year, range_start.month
    _, last_day = calendar.monthrange(year, month)
    day = min(max(int(anchor_day or 1), 1), last_day)
    candidate = date(year, month, day)
    if range_start <= candidate <= range_end:
        return candidate
    return None


def _semi_monthly_dates_in_range(
    anchor_day: int,
    range_start: date,
    range_end: date,
) -> list[date]:
    year, month = range_start.year, range_start.month
    _, last_day = calendar.monthrange(year, month)
    first_day = min(max(int(anchor_day or 1), 1), last_day)
    second_day = min(first_day + 15, last_day)
    if second_day == first_day:
        second_day = min(15, last_day)
    dates = []
    for day in sorted({first_day, second_day}):
        candidate = date(year, month, day)
        if range_start <= candidate <= range_end:
            dates.append(candidate)
    return dates


def _quarterly_dates_in_range(
    anchor: date,
    range_start: date,
    range_end: date,
) -> list[date]:
    dates: list[date] = []
    month_index = anchor.year * 12 + (anchor.month - 1)
    probe_index = range_start.year * 12 + (range_start.month - 1)
    while probe_index < month_index:
        probe_index += 3
    while True:
        year = probe_index // 12
        month = (probe_index % 12) + 1
        _, last_day = calendar.monthrange(year, month)
        day = min(anchor.day, last_day)
        candidate = date(year, month, day)
        if candidate > range_end:
            break
        if candidate >= range_start:
            dates.append(candidate)
        probe_index += 3
    return dates


def _annual_date_in_range(
    anchor: date,
    range_start: date,
    range_end: date,
) -> list[date]:
    year = range_start.year
    _, last_day = calendar.monthrange(year, anchor.month)
    day = min(anchor.day, last_day)
    candidate = date(year, anchor.month, day)
    if range_start <= candidate <= range_end:
        return [candidate]
    return []


def pay_dates_for_version_in_range(
    *,
    schedule_anchor: date,
    pay_frequency: str,
    anchor_day: int,
    range_start: date,
    range_end: date,
) -> list[date]:
    if range_start > range_end:
        return []

    if pay_frequency in {"weekly", "bi_weekly"}:
        return _cadence_dates_in_range(schedule_anchor, pay_frequency, range_start, range_end)
    if pay_frequency == "semi_monthly":
        return _semi_monthly_dates_in_range(anchor_day, range_start, range_end)
    if pay_frequency == "monthly":
        monthly = _monthly_date_in_range(anchor_day, range_start, range_end)
        return [monthly] if monthly else []
    if pay_frequency == "school_year_monthly":
        if range_start.month not in SCHOOL_YEAR_ACTIVE_MONTHS:
            return []
        monthly = _monthly_date_in_range(anchor_day, range_start, range_end)
        return [monthly] if monthly else []
    if pay_frequency == "quarterly":
        return _quarterly_dates_in_range(schedule_anchor, range_start, range_end)
    if pay_frequency == "annually":
        return _annual_date_in_range(schedule_anchor, range_start, range_end)
    return []


def pay_dates_in_month(versions: list[dict], month_year: str) -> list[date]:
    """All paycheck dates in month_year across version windows."""
    if not versions:
        return []

    ordered = sorted(
        versions,
        key=lambda row: parse_iso_date(row.get("effective_from")) or date.min,
    )
    schedule_anchor = parse_iso_date(ordered[0].get("effective_from"))
    if schedule_anchor is None:
        return []

    month_start, month_end = month_bounds(month_year)
    all_dates: list[date] = []

    for index, version in enumerate(ordered):
        effective_from = parse_iso_date(version.get("effective_from"))
        if effective_from is None:
            continue
        if index + 1 < len(ordered):
            next_effective = parse_iso_date(ordered[index + 1].get("effective_from"))
            version_end = (
                next_effective - timedelta(days=1)
                if next_effective and next_effective > effective_from
                else month_end
            )
        else:
            version_end = month_end

        window_start = max(effective_from, month_start)
        window_end = min(version_end, month_end)
        freq = version.get("pay_frequency") or "monthly"
        anchor_day = int(version.get("payment_anchor_day") or effective_from.day)
        all_dates.extend(
            pay_dates_for_version_in_range(
                schedule_anchor=schedule_anchor,
                pay_frequency=freq,
                anchor_day=anchor_day,
                range_start=window_start,
                range_end=window_end,
            )
        )

    return sorted(set(all_dates))


def paycheck_occurrences_in_month(versions: list[dict], month_year: str) -> list[dict]:
    """Each paycheck in month_year with the version active on that date."""
    occurrences = []
    for pay_date in pay_dates_in_month(versions, month_year):
        version = resolve_version_at_date(versions, pay_date)
        if version:
            occurrences.append({"payment_date": pay_date, "version": version})
    return occurrences
