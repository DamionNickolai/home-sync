-- ==========================================
-- 026: Expense bill occurrences (bi-weekly / weekly / semi-monthly)
-- Multiple ledger rows per stream/month when sub-monthly.
-- ==========================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_stream_pay_date
    ON expenses (household_id, stream_id, month_year, date_logged)
    WHERE stream_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_dev_stream_pay_date
    ON expenses_dev (household_id, stream_id, month_year, date_logged)
    WHERE stream_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
