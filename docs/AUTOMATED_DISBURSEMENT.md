# Automated Disbursement — Implementation Notes

## v1 Checklist (June 2026)

[x] Migration 042 run in Supabase
[x] App reloads without error after the update
[x] June plan is frozen — no row changes on load
[x] July transfer rows appear in DB on first load (zero clicks)
[x] July reconciliation record created with reviewed = false
[x] Advanced expander visible in Disbursement plan tab (admin)
[x] Update plan / Reset plan still work from inside the expander
[x] Funding stream Save still works for each member
[x] Stale banner appears after changing an income stream mid-month

---

## July Month-Flip Fixes (July 2026)

### What changed

**Income materialization — full-month plan on day 1**

`_materialize_income_occurrence` no longer skips future payment dates within
the current or next calendar month.  On July 1, every scheduled paycheck for
July (July 10, July 25, etc.) is written to `household_incomes` immediately.
The old `payment_date > today` guard has been replaced with a `payment_date <
month_start` guard that only rejects dates that pre-date the month being
materialized (prevents accidental cross-month backfills).

The legacy rollover path (`_rollover_recurring_incomes_legacy`) has the same
change: the `new_payment_date > today` skip is removed.

**Est. Monthly Take-Home / Est. Monthly Income**

Both the Household Master Ledger and Personal Ledger now compute their
"Estimated" income metric from the full materialized dataset (`incomes_df`)
rather than the actual-filtered view (`incomes_actual_df`).  The
`_filter_incomes_for_actual_totals` filter is still applied to category-level
ledger tables where earned-to-date comparisons matter.

**Cash Flow — Status column**

Income rows in the Cash Flow list now show a "Received" or "Planned" label
based on whether `payment_date <= today`.

**Transfer plan — copy from prior month by default**

`copy_transfer_plan_from_month(household_id, source_month, target_month)` is a
new helper that reads all transfer rows from `source_month`, remaps payment
dates to the matching paycheck occurrence in `target_month` (via each stream's
income schedule — same engine as Cash Flow), and
inserts or updates planned rows in the target month.  Completed rows in the
target are never overwritten.

`sync_disbursement_plan` on first-insert now attempts a prior-month copy before
falling back to the live-computed schedule.  This means July's transfers default
to June amounts on remapped July dates.

On next-month rollover, planned amounts are no longer overwritten by the
computed schedule — only missing slots are inserted and orphan planned slots are
removed.

**Auto-replace for unreviewed current month**

When `sync_disbursement_plan` runs for the current month and the reconciliation
record has `reviewed=false`:
- If no completed transfers exist yet, all planned rows are cleared and replaced
  with a fresh copy from the prior month.
- If some transfers are already completed, only the remaining planned rows are
  updated from the prior month.

This ensures that on the first app load of a new month, transfers automatically
reflect last month's amounts without any admin action.

**Compare to prior month tab**

A third tab "Compare to prior month" is now available in the
Obligations & Disbursements expander.  It shows:
- Side-by-side summary (household income, obligations, surplus, monthly
  transfers) for the selected month vs the previous month.
- Per-member obligation and allowance totals for both months.
- A transfer diff table matched by paycheck occurrence (not day-of-month), with a "Changed",
  "New", or "Removed" tag where amounts differ.

### What you should see on July 1 (and every 1st thereafter)

| View | Expected behavior |
|------|-------------------|
| Cash Flow (July) | All scheduled paycheck rows for the full month, each with "Planned" status |
| Master Ledger Est. Take-Home | Sum of all July paycheck rows (not just past dates) |
| Disbursement Household income | Matches Cash Flow math (ledger rows, not stream fallback) |
| Disbursement transfers | Copied from June amounts on remapped July dates |
| Compare to prior month tab | June vs July summary and per-row transfer diff |

### Monthly workflow — fully automated by default

Every session load triggers `_run_disbursement_automation_server_side`, which:

1. **Syncs the current month** — copies transfers from prior month on first load;
   detects drift if income or obligations changed mid-month.
2. **Syncs the next month** — inserts missing slots, removes orphan planned rows.
3. **Auto-completes due transfers** — any planned transfer whose `payment_date`
   is on or before today is automatically marked completed with actor `"auto"`.
   No admin action required.
4. **Back-fills allowance expenses** — creates the corresponding household
   expense rows for each completed transfer.
5. **Cleans up orphans** — removes stale expense/income artifacts from cancelled
   or replaced transfers.

No one needs to manually mark transfers. The system drives itself day-to-day.

### When admin attention is needed (monthly validation)

1. **Open the app on the 1st.** Transfers are auto-populated from prior month and
   will auto-complete as each paycheck date passes.
2. **Check "Compare to prior month" tab.** Confirm amounts match expectations.
3. **Click "Plan looks good"** in the Disbursement plan tab to acknowledge the
   month's plan. Top-level review banners clear for the rest of the month; the
   Disbursement plan tab shows a green "Plan accepted as-is" confirmation.
4. **If amounts need to change** (income raise, obligation update), use
   Advanced → Reset plan to rebuild from the current computed schedule, edit if
   needed, then click "Plan looks good" again.

That's it — the only required monthly touch is reviewing the numbers and
clicking "Plan looks good".

### Saved transfer plan vs live-computed schedule

From **Monthly amounts per member** down through **Advanced**, the UI reads
**saved transfer rows** (copied from the prior month by default), not the
live-computed schedule. Top summary metrics (Household income, Assigned
obligations, Surplus) still reflect current ledger/obligations data.

When income differs from the prior month, saved transfers intentionally keep
prior-month amounts until you **Reset plan** or edit rows manually. Manual edits
mark the plan as admin-customized so automation does not overwrite them on reload.

Transfer pay dates follow each funding stream's **income schedule** in the new
month (same paycheck occurrence as Cash Flow — e.g. bi-weekly Jun 10/24 → Jul 8/22),
not the same calendar day-of-month.

**Personal Allowance income** is materialized for every planned disbursement
transfer (one row per paycheck date with the transfer's allowance amount), so the
personal ledger and Cash Flow show the full-month projection before transfers
auto-complete. Legacy stream-based Allowance rows are removed when disbursement
transfers own allowance for that member.