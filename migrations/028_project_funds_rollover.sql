-- ==========================================
-- 028: Project funds rollover + expense ↔ project linkage
-- - projects_funds_opening: Jan 1 snapshot (does not decrease when expenses logged)
-- - project_budget_id: links ledger expenses to project_budgets rows
-- ==========================================

ALTER TABLE household_finance_settings
    ADD COLUMN IF NOT EXISTS projects_funds_opening TEXT;

ALTER TABLE household_finance_settings_dev
    ADD COLUMN IF NOT EXISTS projects_funds_opening TEXT;

ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS project_budget_id TEXT;

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS project_budget_id TEXT;

CREATE INDEX IF NOT EXISTS idx_expenses_household_project_month
    ON expenses (household_id, project_budget_id, month_year);

CREATE INDEX IF NOT EXISTS idx_expenses_dev_household_project_month
    ON expenses_dev (household_id, project_budget_id, month_year);

NOTIFY pgrst, 'reload schema';
