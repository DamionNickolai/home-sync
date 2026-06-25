-- Income payment / recurrence-start date for ledger timing.

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS payment_date DATE;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS payment_date DATE;

UPDATE household_incomes
SET payment_date = (month_year || '-01')::DATE
WHERE payment_date IS NULL;

UPDATE household_incomes_dev
SET payment_date = (month_year || '-01')::DATE
WHERE payment_date IS NULL;

NOTIFY pgrst, 'reload schema';
