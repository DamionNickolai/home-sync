-- Migration: Add richer metadata and recurrence support to household tasks
-- Applies to both production and local/dev task tables.

ALTER TABLE household_tasks
ADD COLUMN IF NOT EXISTS description TEXT;

ALTER TABLE household_tasks
ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE household_tasks
ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE household_tasks
ADD COLUMN IF NOT EXISTS recurrence_pattern TEXT;

ALTER TABLE household_tasks_dev
ADD COLUMN IF NOT EXISTS description TEXT;

ALTER TABLE household_tasks_dev
ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE household_tasks_dev
ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE household_tasks_dev
ADD COLUMN IF NOT EXISTS recurrence_pattern TEXT;

CREATE INDEX IF NOT EXISTS idx_household_tasks_household_due
ON household_tasks(household_id, is_completed, target_date);

CREATE INDEX IF NOT EXISTS idx_household_tasks_dev_household_due
ON household_tasks_dev(household_id, is_completed, target_date);
