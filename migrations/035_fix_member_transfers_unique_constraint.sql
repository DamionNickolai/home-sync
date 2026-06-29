-- ==========================================
-- 035: Fix household_member_transfers unique constraint
-- ==========================================
-- The original constraint only covered (household_id, month_year, payment_date,
-- recipient_username). With per-member multi-stream support, the same member can
-- receive multiple transfers on the same date from different income streams.
-- This migration expands the constraint to include funding_income_stream_id.

-- Prod table ---------------------------------------------------------------
DROP INDEX IF EXISTS uq_hh_member_transfers_paycheck;

CREATE UNIQUE INDEX IF NOT EXISTS uq_hh_member_transfers_paycheck
    ON household_member_transfers (
        household_id, month_year, payment_date, recipient_username,
        COALESCE(funding_income_stream_id::text, '')
    );

-- Dev table ----------------------------------------------------------------
-- The dev table was created via LIKE ... INCLUDING ALL which copied the old
-- constraint with an auto-generated name.
DROP INDEX IF EXISTS household_member_transfers_de_household_id_month_year_payme_idx;
DROP INDEX IF EXISTS uq_hh_member_transfers_paycheck_dev;

CREATE UNIQUE INDEX IF NOT EXISTS uq_hh_member_transfers_paycheck_dev
    ON household_member_transfers_dev (
        household_id, month_year, payment_date, recipient_username,
        COALESCE(funding_income_stream_id::text, '')
    );

NOTIFY pgrst, 'reload schema';
