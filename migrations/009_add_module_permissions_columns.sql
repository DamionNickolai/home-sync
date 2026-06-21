-- Add explicit module-level permission columns for granular view/edit controls.
-- This keeps existing behavior by backfilling from legacy can_view_budget.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS can_view_projects BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_projects BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_view_monthly_budget BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_monthly_budget BOOLEAN;

UPDATE users
SET
    can_view_projects = COALESCE(can_view_projects, can_view_budget, FALSE),
    can_edit_projects = COALESCE(can_edit_projects, can_view_budget, FALSE),
    can_view_monthly_budget = COALESCE(can_view_monthly_budget, can_view_budget, FALSE),
    can_edit_monthly_budget = COALESCE(can_edit_monthly_budget, FALSE);

-- Privileged roles always retain full module access/edit rights.
UPDATE users
SET
    can_view_projects = TRUE,
    can_edit_projects = TRUE,
    can_view_monthly_budget = TRUE,
    can_edit_monthly_budget = TRUE,
    can_view_budget = TRUE
WHERE role IN ('admin', 'developer');

ALTER TABLE users
    ALTER COLUMN can_view_projects SET DEFAULT FALSE,
    ALTER COLUMN can_edit_projects SET DEFAULT FALSE,
    ALTER COLUMN can_view_monthly_budget SET DEFAULT FALSE,
    ALTER COLUMN can_edit_monthly_budget SET DEFAULT FALSE;

ALTER TABLE users
    ALTER COLUMN can_view_projects SET NOT NULL,
    ALTER COLUMN can_edit_projects SET NOT NULL,
    ALTER COLUMN can_view_monthly_budget SET NOT NULL,
    ALTER COLUMN can_edit_monthly_budget SET NOT NULL;

-- Maintain legacy rollup field for compatibility with existing code paths.
UPDATE users
SET can_view_budget = (can_view_projects OR can_view_monthly_budget)
WHERE can_view_budget IS DISTINCT FROM (can_view_projects OR can_view_monthly_budget);
