-- Migration 036: Master toggle for household-on-personal integration.
-- Replaces the obligation-only visibility toggle with a single setting that
-- controls transfer income sync, household income mirror, and obligation expenses.

ALTER TABLE user_finance_settings
    ADD COLUMN IF NOT EXISTS integrate_household_on_personal BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE user_finance_settings_dev
    ADD COLUMN IF NOT EXISTS integrate_household_on_personal BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill from legacy obligation-only toggle (idempotent).
UPDATE user_finance_settings
SET integrate_household_on_personal = TRUE
WHERE show_obligation_transfers_on_personal IS TRUE
  AND integrate_household_on_personal IS NOT TRUE;

UPDATE user_finance_settings_dev
SET integrate_household_on_personal = TRUE
WHERE show_obligation_transfers_on_personal IS TRUE
  AND integrate_household_on_personal IS NOT TRUE;
