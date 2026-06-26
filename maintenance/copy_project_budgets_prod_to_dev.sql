-- ==========================================
-- Copy project_budgets (prod) → project_budgets_dev (local testing)
-- ==========================================
--
-- SAFE FOR PRODUCTION DATA:
--   • SELECT only from project_budgets (prod) — never modified
--   • DELETE + INSERT only on project_budgets_dev
--   • Encrypted fields copied as raw ciphertext (no decrypt in SQL)
--
-- ENCRYPTION: local ENCRYPTION_KEY must match production.
--
-- ==========================================
-- ★ SET YOUR HOUSEHOLD ID HERE (inside the quotes) ★
-- ==========================================
-- Example: 'INSERT_HOUSEHOLD_ID'   ← quotes required (text, not a column name)
--
-- Find yours:
--   SELECT household_id, username FROM users WHERE username = 'YourUsername';


-- Step 1: PREVIEW (read-only) — run this block alone first
SELECT
    'prod' AS source,
    COUNT(*) AS row_count,
    COUNT(*) FILTER (WHERE item IS NOT NULL AND item LIKE 'gAAAA%') AS encrypted_item_count
FROM project_budgets
WHERE household_id = 'INSERT_HOUSEHOLD_ID'   -- ← change this line only

UNION ALL

SELECT
    'dev' AS source,
    COUNT(*) AS row_count,
    COUNT(*) FILTER (WHERE item IS NOT NULL AND item LIKE 'gAAAA%') AS encrypted_item_count
FROM project_budgets_dev
WHERE household_id = 'INSERT_HOUSEHOLD_ID';  -- ← same value, with quotes


-- Step 2: COPY prod → dev (replaces dev rows for this household)
-- Run this entire DO block as one query in Supabase SQL Editor.

DO $$
DECLARE
    -- ★ ONLY edit the value between the single quotes below ★
    v_household_id TEXT := 'INSERT_HOUSEHOLD_ID';
    v_prod_count   INT;
    v_dev_count    INT;
BEGIN
    SELECT COUNT(*) INTO v_prod_count
    FROM project_budgets
    WHERE household_id = v_household_id;

    IF v_prod_count = 0 THEN
        RAISE EXCEPTION 'No rows in project_budgets for household_id=%. Check spelling and quotes.', v_household_id;
    END IF;

    DELETE FROM project_budgets_dev
    WHERE household_id = v_household_id;

    INSERT INTO project_budgets_dev (
        household_id,
        item,
        description,
        category,
        priority,
        est_low_cost,
        est_high_cost,
        actual_cost,
        vendors,
        notes,
        veteran_discount,
        status
    )
    SELECT
        p.household_id,
        p.item,
        p.description,
        p.category,
        p.priority,
        p.est_low_cost,
        p.est_high_cost,
        p.actual_cost,
        p.vendors,
        p.notes,
        p.veteran_discount,
        p.status
    FROM project_budgets p
    WHERE p.household_id = v_household_id;

    SELECT COUNT(*) INTO v_dev_count
    FROM project_budgets_dev
    WHERE household_id = v_household_id;

    RAISE NOTICE 'Done: % prod rows -> % dev rows for household_id=%',
        v_prod_count, v_dev_count, v_household_id;

    IF v_prod_count <> v_dev_count THEN
        RAISE WARNING 'Row counts differ — review before testing in the app.';
    END IF;
END $$;


-- Step 3: If INSERT fails on unknown column, compare schemas:
--
-- SELECT column_name, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND table_name IN ('project_budgets', 'project_budgets_dev')
-- ORDER BY table_name, ordinal_position;
--
-- If project_budgets_dev lacks `status`, remove status from the INSERT list in Step 2.
