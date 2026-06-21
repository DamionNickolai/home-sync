-- Add Wish List visibility permissions for member-role users.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS can_view_wishlist_members BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_view_wishlist_admin BOOLEAN;

-- Backfill defaults for existing rows.
UPDATE users
SET
    can_view_wishlist_members = COALESCE(can_view_wishlist_members, TRUE),
    can_view_wishlist_admin = COALESCE(can_view_wishlist_admin, FALSE);

-- Privileged roles always see both member and admin/developer Wish List entries.
UPDATE users
SET
    can_view_wishlist_members = TRUE,
    can_view_wishlist_admin = TRUE
WHERE role IN ('admin', 'developer');

ALTER TABLE users
    ALTER COLUMN can_view_wishlist_members SET DEFAULT TRUE,
    ALTER COLUMN can_view_wishlist_admin SET DEFAULT FALSE;

ALTER TABLE users
    ALTER COLUMN can_view_wishlist_members SET NOT NULL,
    ALTER COLUMN can_view_wishlist_admin SET NOT NULL;
