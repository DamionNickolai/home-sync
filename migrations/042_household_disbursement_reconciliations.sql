-- ============================================================
-- 042: Monthly disbursement reconciliation records
--
-- One row per (household, month_year). Written by sync_disbursement_plan
-- the first time a new month's planned transfers are materialized.
-- Tracks whether the admin has reviewed the new plan, and whether
-- the current month's saved plan has drifted from the latest computed
-- schedule (plan_stale flag).
-- ============================================================

CREATE TABLE IF NOT EXISTS household_disbursement_reconciliations (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id   TEXT        NOT NULL,
    month_year     TEXT        NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plan_snapshot  JSONB,
    transfer_count INT         NOT NULL DEFAULT 0,
    flags          JSONB,
    reviewed       BOOL        NOT NULL DEFAULT FALSE,
    reviewed_by    TEXT,
    reviewed_at    TIMESTAMPTZ,
    plan_stale     BOOL        NOT NULL DEFAULT FALSE,
    updated_at     TIMESTAMPTZ,
    UNIQUE (household_id, month_year)
);

CREATE INDEX IF NOT EXISTS idx_hh_disb_recon_household_month
    ON household_disbursement_reconciliations (household_id, month_year);

-- Dev variant

CREATE TABLE IF NOT EXISTS household_disbursement_reconciliations_dev (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id   TEXT        NOT NULL,
    month_year     TEXT        NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plan_snapshot  JSONB,
    transfer_count INT         NOT NULL DEFAULT 0,
    flags          JSONB,
    reviewed       BOOL        NOT NULL DEFAULT FALSE,
    reviewed_by    TEXT,
    reviewed_at    TIMESTAMPTZ,
    plan_stale     BOOL        NOT NULL DEFAULT FALSE,
    updated_at     TIMESTAMPTZ,
    UNIQUE (household_id, month_year)
);

CREATE INDEX IF NOT EXISTS idx_hh_disb_recon_dev_household_month
    ON household_disbursement_reconciliations_dev (household_id, month_year);
