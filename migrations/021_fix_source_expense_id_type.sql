-- expense.id is UUID in dev (and may be UUID in prod). source_expense_id must match.

ALTER TABLE household_incomes
    ALTER COLUMN source_expense_id TYPE TEXT
    USING source_expense_id::TEXT;

ALTER TABLE household_incomes_dev
    ALTER COLUMN source_expense_id TYPE TEXT
    USING source_expense_id::TEXT;
