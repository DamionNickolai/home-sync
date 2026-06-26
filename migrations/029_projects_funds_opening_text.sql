-- ==========================================
-- 029: projects_funds_opening must be TEXT (encrypted at rest, like projects_funds)
-- Run this if migration 028 created the column as NUMERIC.
-- No-op when the column is already TEXT.
-- ==========================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'household_finance_settings'
          AND column_name = 'projects_funds_opening'
          AND data_type = 'numeric'
    ) THEN
        ALTER TABLE household_finance_settings
            ALTER COLUMN projects_funds_opening TYPE TEXT
            USING projects_funds_opening::text;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'household_finance_settings_dev'
          AND column_name = 'projects_funds_opening'
          AND data_type = 'numeric'
    ) THEN
        ALTER TABLE household_finance_settings_dev
            ALTER COLUMN projects_funds_opening TYPE TEXT
            USING projects_funds_opening::text;
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
