-- Home Management module permissions (members default to no access).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS can_view_home_solar BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_home_solar BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_view_home_security BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_home_security BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_view_home_garage BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_home_garage BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_view_home_logs BOOLEAN,
    ADD COLUMN IF NOT EXISTS can_edit_home_logs BOOLEAN;

UPDATE users
SET
    can_view_home_solar = COALESCE(can_view_home_solar, FALSE),
    can_edit_home_solar = COALESCE(can_edit_home_solar, FALSE),
    can_view_home_security = COALESCE(can_view_home_security, FALSE),
    can_edit_home_security = COALESCE(can_edit_home_security, FALSE),
    can_view_home_garage = COALESCE(can_view_home_garage, FALSE),
    can_edit_home_garage = COALESCE(can_edit_home_garage, FALSE),
    can_view_home_logs = COALESCE(can_view_home_logs, FALSE),
    can_edit_home_logs = COALESCE(can_edit_home_logs, FALSE);

UPDATE users
SET
    can_view_home_solar = TRUE,
    can_edit_home_solar = TRUE,
    can_view_home_security = TRUE,
    can_edit_home_security = TRUE,
    can_view_home_garage = TRUE,
    can_edit_home_garage = TRUE,
    can_view_home_logs = TRUE,
    can_edit_home_logs = TRUE
WHERE role IN ('admin', 'developer');

ALTER TABLE users
    ALTER COLUMN can_view_home_solar SET DEFAULT FALSE,
    ALTER COLUMN can_edit_home_solar SET DEFAULT FALSE,
    ALTER COLUMN can_view_home_security SET DEFAULT FALSE,
    ALTER COLUMN can_edit_home_security SET DEFAULT FALSE,
    ALTER COLUMN can_view_home_garage SET DEFAULT FALSE,
    ALTER COLUMN can_edit_home_garage SET DEFAULT FALSE,
    ALTER COLUMN can_view_home_logs SET DEFAULT FALSE,
    ALTER COLUMN can_edit_home_logs SET DEFAULT FALSE;

ALTER TABLE users
    ALTER COLUMN can_view_home_solar SET NOT NULL,
    ALTER COLUMN can_edit_home_solar SET NOT NULL,
    ALTER COLUMN can_view_home_security SET NOT NULL,
    ALTER COLUMN can_edit_home_security SET NOT NULL,
    ALTER COLUMN can_view_home_garage SET NOT NULL,
    ALTER COLUMN can_edit_home_garage SET NOT NULL,
    ALTER COLUMN can_view_home_logs SET NOT NULL,
    ALTER COLUMN can_edit_home_logs SET NOT NULL;

NOTIFY pgrst, 'reload schema';
