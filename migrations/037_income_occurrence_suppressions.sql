-- Migration 037: Suppress re-materialization of intentionally deleted income occurrences.
-- When a user deletes "This occurrence only" on a stream-linked paycheck, the
-- stream stays active but that (stream, month, payment_date) must not come back.

CREATE TABLE IF NOT EXISTS income_occurrence_suppressions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    stream_id UUID NOT NULL,
    month_year TEXT NOT NULL,
    payment_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_income_occ_suppression UNIQUE (household_id, stream_id, month_year, payment_date)
);

CREATE INDEX IF NOT EXISTS idx_income_occ_suppression_lookup
    ON income_occurrence_suppressions (household_id, month_year);

CREATE TABLE IF NOT EXISTS income_occurrence_suppressions_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    stream_id UUID NOT NULL,
    month_year TEXT NOT NULL,
    payment_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_income_occ_suppression_dev UNIQUE (household_id, stream_id, month_year, payment_date)
);

CREATE INDEX IF NOT EXISTS idx_income_occ_suppression_dev_lookup
    ON income_occurrence_suppressions_dev (household_id, month_year);

NOTIFY pgrst, 'reload schema';
