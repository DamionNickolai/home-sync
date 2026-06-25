-- Links personal income rows created from household Allowance expenses.
-- TEXT to match expenses.id (UUID in dev, BIGINT or UUID in prod).

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS source_expense_id TEXT;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS source_expense_id TEXT;
