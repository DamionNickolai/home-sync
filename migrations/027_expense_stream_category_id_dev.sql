-- ==========================================
-- 027: Expense stream category_id type (dev)
-- Live dev uses UUID budget_categories_dev.id and expenses_dev.category_id.
-- Migration 025 created household_expense_streams_dev.category_id as BIGINT,
-- which rejects UUID category keys when logging recurring expenses.
-- ==========================================

ALTER TABLE household_expense_streams_dev
    ALTER COLUMN category_id TYPE UUID
    USING NULL;

NOTIFY pgrst, 'reload schema';
