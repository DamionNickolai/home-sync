-- Add completion support for Wish List items.

ALTER TABLE wish_list
    ADD COLUMN IF NOT EXISTS is_completed BOOLEAN;

UPDATE wish_list
SET is_completed = COALESCE(is_completed, FALSE);

ALTER TABLE wish_list
    ALTER COLUMN is_completed SET DEFAULT FALSE,
    ALTER COLUMN is_completed SET NOT NULL;

ALTER TABLE wish_list_dev
    ADD COLUMN IF NOT EXISTS is_completed BOOLEAN;

UPDATE wish_list_dev
SET is_completed = COALESCE(is_completed, FALSE);

ALTER TABLE wish_list_dev
    ALTER COLUMN is_completed SET DEFAULT FALSE,
    ALTER COLUMN is_completed SET NOT NULL;
