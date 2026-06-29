-- ==========================================
-- 031: Household disbursement funding settings
-- ==========================================

CREATE TABLE IF NOT EXISTS household_disbursement_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL UNIQUE,
    funding_income_stream_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_hh_disbursement_settings_household
    ON household_disbursement_settings (household_id);

CREATE TABLE IF NOT EXISTS household_disbursement_settings_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL UNIQUE,
    funding_income_stream_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_hh_disbursement_settings_dev_household
    ON household_disbursement_settings_dev (household_id);
