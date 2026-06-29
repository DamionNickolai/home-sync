-- ==========================================
-- 038: Link member transfers to auto-created HH allowance expenses
-- ==========================================

ALTER TABLE household_member_transfers
    ADD COLUMN IF NOT EXISTS household_allowance_expense_id TEXT;

ALTER TABLE household_member_transfers_dev
    ADD COLUMN IF NOT EXISTS household_allowance_expense_id TEXT;

NOTIFY pgrst, 'reload schema';
