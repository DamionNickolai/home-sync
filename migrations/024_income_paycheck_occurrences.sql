-- ==========================================
-- 024: Income paycheck occurrences (Phase 2)
-- Multiple ledger rows per stream/month for bi-weekly, weekly, semi-monthly.
-- ==========================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_household_incomes_stream_pay_date
    ON household_incomes (household_id, stream_id, month_year, payment_date)
    WHERE stream_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_household_incomes_dev_stream_pay_date
    ON household_incomes_dev (household_id, stream_id, month_year, payment_date)
    WHERE stream_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
