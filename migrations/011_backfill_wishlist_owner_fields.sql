-- ==========================================
-- 011: Backfill Wish List owner fields
-- Purpose:
-- Ensure explicit owner columns exist and are populated for existing wish list rows.
-- ==========================================

ALTER TABLE IF EXISTS wish_list
    ADD COLUMN IF NOT EXISTS owner_auth_user_id TEXT,
    ADD COLUMN IF NOT EXISTS owner_username TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'wish_list'
          AND column_name = 'created_by_auth_user_id'
    )
    AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'wish_list'
          AND column_name = 'created_by_username'
    ) THEN
        EXECUTE '
            UPDATE wish_list
            SET
                owner_auth_user_id = COALESCE(owner_auth_user_id, created_by_auth_user_id),
                owner_username = COALESCE(owner_username, created_by_username)
            WHERE owner_auth_user_id IS NULL OR owner_username IS NULL
        ';
    ELSE
        RAISE NOTICE 'wish_list: created_by_* columns not found; skipping legacy backfill.';
    END IF;
END
$$;

ALTER TABLE IF EXISTS wish_list_dev
    ADD COLUMN IF NOT EXISTS owner_auth_user_id TEXT,
    ADD COLUMN IF NOT EXISTS owner_username TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'wish_list_dev'
          AND column_name = 'created_by_auth_user_id'
    )
    AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'wish_list_dev'
          AND column_name = 'created_by_username'
    ) THEN
        EXECUTE '
            UPDATE wish_list_dev
            SET
                owner_auth_user_id = COALESCE(owner_auth_user_id, created_by_auth_user_id),
                owner_username = COALESCE(owner_username, created_by_username)
            WHERE owner_auth_user_id IS NULL OR owner_username IS NULL
        ';
    ELSE
        RAISE NOTICE 'wish_list_dev: created_by_* columns not found; skipping legacy backfill.';
    END IF;
END
$$;
