-- ==========================================
-- 023: Income streams + versions (effective-from)
-- Purpose:
-- Logical income streams with versioned terms; monthly household_incomes
-- rows remain materialized ledger facts linked to stream/version.
-- ==========================================

-- ------------------------------------------
-- Prod: household_income_streams
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_income_streams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    owner_username TEXT,
    is_personal_income BOOLEAN NOT NULL DEFAULT FALSE,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ended_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_income_streams_household_active
    ON household_income_streams (household_id, is_active);

CREATE INDEX IF NOT EXISTS idx_household_income_streams_household_owner
    ON household_income_streams (household_id, is_personal_income, owner_username);

-- ------------------------------------------
-- Prod: household_income_stream_versions
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_income_stream_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id UUID NOT NULL REFERENCES household_income_streams (id) ON DELETE CASCADE,
    effective_from DATE NOT NULL,
    take_home_amount TEXT,
    gross_amount TEXT,
    is_taxable BOOLEAN NOT NULL DEFAULT TRUE,
    is_windfall BOOLEAN NOT NULL DEFAULT FALSE,
    pay_frequency TEXT NOT NULL DEFAULT 'monthly',
    payment_anchor_day INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_income_stream_version_effective UNIQUE (stream_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_income_stream_versions_stream_effective
    ON household_income_stream_versions (stream_id, effective_from DESC);

-- ------------------------------------------
-- Prod: link monthly ledger rows
-- ------------------------------------------
ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS stream_id UUID;

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS version_id UUID;

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_household_incomes_stream_month
    ON household_incomes (household_id, stream_id, month_year);

-- ------------------------------------------
-- Dev tables
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_income_streams_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    owner_username TEXT,
    is_personal_income BOOLEAN NOT NULL DEFAULT FALSE,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ended_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_income_streams_dev_household_active
    ON household_income_streams_dev (household_id, is_active);

CREATE TABLE IF NOT EXISTS household_income_stream_versions_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id UUID NOT NULL REFERENCES household_income_streams_dev (id) ON DELETE CASCADE,
    effective_from DATE NOT NULL,
    take_home_amount TEXT,
    gross_amount TEXT,
    is_taxable BOOLEAN NOT NULL DEFAULT TRUE,
    is_windfall BOOLEAN NOT NULL DEFAULT FALSE,
    pay_frequency TEXT NOT NULL DEFAULT 'monthly',
    payment_anchor_day INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_income_stream_version_dev_effective UNIQUE (stream_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_income_stream_versions_dev_stream_effective
    ON household_income_stream_versions_dev (stream_id, effective_from DESC);

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS stream_id UUID;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS version_id UUID;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_household_incomes_dev_stream_month
    ON household_incomes_dev (household_id, stream_id, month_year);

NOTIFY pgrst, 'reload schema';
