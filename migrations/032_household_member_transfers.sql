-- ==========================================
-- 032: Household member transfers + user setting
-- ==========================================

-- Dedicated transfer ledger (separate from expenses).
-- One row per paycheck disbursement per recipient.
CREATE TABLE IF NOT EXISTS household_member_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    month_year TEXT NOT NULL,
    payment_date DATE NOT NULL,
    recipient_username TEXT NOT NULL,
    funding_income_stream_id UUID,
    allowance_amount TEXT,          -- encrypted; surplus-share portion
    obligation_amount TEXT,         -- encrypted; obligation-gap portion
    total_amount TEXT,              -- encrypted; bundled wire amount
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'completed')),
    transferred_at TIMESTAMPTZ,
    transferred_by TEXT,
    personal_allowance_income_id TEXT,   -- nullable link to household_incomes row
    personal_obligation_income_id TEXT,  -- nullable link to household_incomes row
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hh_member_transfers_household_month
    ON household_member_transfers (household_id, month_year);

CREATE INDEX IF NOT EXISTS idx_hh_member_transfers_recipient
    ON household_member_transfers (household_id, recipient_username, month_year);

-- NULL stream_id treated as '' so two same-date rows from different streams stay distinct.
CREATE UNIQUE INDEX IF NOT EXISTS uq_hh_member_transfers_paycheck
    ON household_member_transfers (household_id, month_year, payment_date, recipient_username,
        COALESCE(funding_income_stream_id::text, ''));

-- Per-member toggle: show household obligation support on personal income ledger.
ALTER TABLE user_finance_settings
    ADD COLUMN IF NOT EXISTS show_obligation_transfers_on_personal BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE user_finance_settings_dev
    ADD COLUMN IF NOT EXISTS show_obligation_transfers_on_personal BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS household_member_transfers_dev (
    LIKE household_member_transfers INCLUDING ALL
);

NOTIFY pgrst, 'reload schema';
