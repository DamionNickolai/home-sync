-- ==========================================
-- 030: Household obligation assignments + supplement snapshots
-- ==========================================

CREATE TABLE IF NOT EXISTS household_obligation_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    member_username TEXT NOT NULL,
    parent_category_name TEXT,
    category_id UUID,
    assignment_level TEXT NOT NULL CHECK (assignment_level IN ('parent', 'subcategory')),
    label TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hh_obligation_assignments_household
    ON household_obligation_assignments (household_id, is_active);

CREATE INDEX IF NOT EXISTS idx_hh_obligation_assignments_parent
    ON household_obligation_assignments (household_id, parent_category_name)
    WHERE assignment_level = 'parent' AND is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_hh_obligation_assignments_category
    ON household_obligation_assignments (household_id, category_id)
    WHERE assignment_level = 'subcategory' AND is_active = TRUE;

CREATE TABLE IF NOT EXISTS household_supplement_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    month_year TEXT NOT NULL,
    member_username TEXT NOT NULL,
    total_obligation TEXT,
    member_take_home TEXT,
    supplement_gap TEXT,
    allowance_logged TEXT,
    recommended_allowance TEXT,
    obligation_breakdown TEXT,
    displacement_summary TEXT,
    applied_to_allowance BOOLEAN NOT NULL DEFAULT FALSE,
    allowance_expense_id TEXT,
    applied_at TIMESTAMPTZ,
    applied_by TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hh_supplement_snapshots_household_month
    ON household_supplement_snapshots (household_id, month_year DESC);

CREATE INDEX IF NOT EXISTS idx_hh_supplement_snapshots_member
    ON household_supplement_snapshots (household_id, member_username, computed_at DESC);

CREATE TABLE IF NOT EXISTS household_obligation_assignments_dev (
    LIKE household_obligation_assignments INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS household_supplement_snapshots_dev (
    LIKE household_supplement_snapshots INCLUDING ALL
);

NOTIFY pgrst, 'reload schema';
