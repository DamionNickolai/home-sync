-- ==========================================
-- 007: Budget defaults to NULL + dev finance settings table
-- Purpose:
-- 1) Remove numeric defaults from project budget money fields
--    so unknown values remain NULL instead of implicit 0.
-- 2) Add local/dev finance settings table for environment isolation.
-- ==========================================

-- Production table defaults
ALTER TABLE IF EXISTS project_budgets
    ALTER COLUMN est_low_cost DROP DEFAULT,
    ALTER COLUMN est_high_cost DROP DEFAULT,
    ALTER COLUMN actual_cost DROP DEFAULT;

-- Local/dev table defaults
ALTER TABLE IF EXISTS project_budgets_dev
    ALTER COLUMN est_low_cost DROP DEFAULT,
    ALTER COLUMN est_high_cost DROP DEFAULT,
    ALTER COLUMN actual_cost DROP DEFAULT;

-- Local/dev household finance settings table
CREATE TABLE IF NOT EXISTS household_finance_settings_dev (
    household_id TEXT PRIMARY KEY,
    projects_funds NUMERIC,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_finance_settings_dev_updated_at
    ON household_finance_settings_dev (updated_at DESC);
