-- ==========================================
-- 025: Expense streams + versions (effective-from)
-- Logical expense streams with versioned terms; monthly expenses
-- rows remain materialized ledger facts linked to stream/version.
-- ==========================================

-- ------------------------------------------
-- Prod: household_expense_streams
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_expense_streams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    category_id BIGINT,
    auth_user_id TEXT,
    username TEXT,
    is_personal_spend BOOLEAN NOT NULL DEFAULT FALSE,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ended_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_expense_streams_household_active
    ON household_expense_streams (household_id, is_active);

CREATE INDEX IF NOT EXISTS idx_household_expense_streams_household_scope
    ON household_expense_streams (household_id, is_personal_spend, username);

-- ------------------------------------------
-- Prod: household_expense_stream_versions
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_expense_stream_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id UUID NOT NULL REFERENCES household_expense_streams (id) ON DELETE CASCADE,
    effective_from DATE NOT NULL,
    amount TEXT,
    pay_frequency TEXT NOT NULL DEFAULT 'monthly',
    payment_anchor_day INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_expense_stream_version_effective UNIQUE (stream_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_expense_stream_versions_stream_effective
    ON household_expense_stream_versions (stream_id, effective_from DESC);

-- ------------------------------------------
-- Prod: link monthly ledger rows
-- ------------------------------------------
ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS stream_id UUID;

ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS version_id UUID;

ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS pay_frequency TEXT DEFAULT 'monthly';

CREATE INDEX IF NOT EXISTS idx_expenses_stream_month
    ON expenses (household_id, stream_id, month_year);

-- ------------------------------------------
-- Dev tables
-- ------------------------------------------
CREATE TABLE IF NOT EXISTS household_expense_streams_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    category_id UUID,
    auth_user_id TEXT,
    username TEXT,
    is_personal_spend BOOLEAN NOT NULL DEFAULT FALSE,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ended_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_household_expense_streams_dev_household_active
    ON household_expense_streams_dev (household_id, is_active);

CREATE TABLE IF NOT EXISTS household_expense_stream_versions_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id UUID NOT NULL REFERENCES household_expense_streams_dev (id) ON DELETE CASCADE,
    effective_from DATE NOT NULL,
    amount TEXT,
    pay_frequency TEXT NOT NULL DEFAULT 'monthly',
    payment_anchor_day INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_expense_stream_version_dev_effective UNIQUE (stream_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_expense_stream_versions_dev_stream_effective
    ON household_expense_stream_versions_dev (stream_id, effective_from DESC);

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS stream_id UUID;

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS version_id UUID;

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS pay_frequency TEXT DEFAULT 'monthly';

CREATE INDEX IF NOT EXISTS idx_expenses_dev_stream_month
    ON expenses_dev (household_id, stream_id, month_year);

NOTIFY pgrst, 'reload schema';
