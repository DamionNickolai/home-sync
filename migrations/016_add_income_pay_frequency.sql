-- Income pay frequency (replaces recurring auto-roll semantics for income).

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS pay_frequency TEXT;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS pay_frequency TEXT;

UPDATE household_incomes
SET pay_frequency = CASE
    WHEN COALESCE(is_recurring, FALSE) THEN 'monthly'
    ELSE 'one_time'
END
WHERE pay_frequency IS NULL OR pay_frequency = '';

UPDATE household_incomes_dev
SET pay_frequency = CASE
    WHEN COALESCE(is_recurring, FALSE) THEN 'monthly'
    ELSE 'one_time'
END
WHERE pay_frequency IS NULL OR pay_frequency = '';

ALTER TABLE household_incomes
    ALTER COLUMN pay_frequency SET DEFAULT 'monthly';

ALTER TABLE household_incomes_dev
    ALTER COLUMN pay_frequency SET DEFAULT 'monthly';

NOTIFY pgrst, 'reload schema';
