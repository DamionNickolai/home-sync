-- Migration: Create dedicated cloud release ledger
-- Purpose: Track authoritative per-app current versions independent of local code.

CREATE TABLE IF NOT EXISTS app_release_ledger (
    id BIGSERIAL PRIMARY KEY,
    app_name TEXT NOT NULL CHECK (app_name IN ('home_sync', 'get_fit')),
    version TEXT NOT NULL,
    release_target TEXT NOT NULL DEFAULT 'manual' CHECK (release_target IN ('home_sync', 'get_fit', 'all', 'manual', 'bootstrap')),
    release_date DATE,
    backlog_items_released INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    released_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_release_ledger_app_released_at
ON app_release_ledger(app_name, released_at DESC);

CREATE INDEX IF NOT EXISTS idx_app_release_ledger_app_release_date
ON app_release_ledger(app_name, release_date DESC);

-- Bootstrap current versions from latest done backlog entries if ledger is empty per app.
INSERT INTO app_release_ledger (app_name, version, release_target, release_date, backlog_items_released, created_by)
SELECT b.app_name, b.version, 'bootstrap', b.release_date, 0, 'migration'
FROM (
    SELECT DISTINCT ON (app_name)
        app_name,
        version,
        CASE
            WHEN release_date IS NULL OR release_date = '' THEN NULL
            WHEN release_date ~ '^\d{4}-\d{2}-\d{2}$' THEN release_date::date
            ELSE NULL
        END AS release_date,
        created_at
    FROM backlog
    WHERE status = 'Done'
      AND app_name IN ('home_sync', 'get_fit')
      AND version IS NOT NULL
      AND version <> ''
    ORDER BY app_name, release_date DESC NULLS LAST, created_at DESC
) b
WHERE NOT EXISTS (
    SELECT 1
    FROM app_release_ledger l
    WHERE l.app_name = b.app_name
);
