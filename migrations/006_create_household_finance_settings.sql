-- ==========================================
-- 006: Household finance settings
-- Purpose: Persist per-household project funds for the budget workspace.
-- ==========================================

CREATE TABLE IF NOT EXISTS household_finance_settings (
    household_id TEXT PRIMARY KEY,
    projects_funds NUMERIC,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_finance_settings_updated_at
    ON household_finance_settings (updated_at DESC);
