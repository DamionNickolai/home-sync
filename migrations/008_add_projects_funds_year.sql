-- ==========================================
-- 008: Add annual year tracking for Projects Funds
-- Purpose: support Jan 1 automatic reset behavior by scoping
-- household project funds to a calendar year.
-- ==========================================

ALTER TABLE IF EXISTS household_finance_settings
    ADD COLUMN IF NOT EXISTS projects_funds_year INTEGER;

ALTER TABLE IF EXISTS household_finance_settings_dev
    ADD COLUMN IF NOT EXISTS projects_funds_year INTEGER;
