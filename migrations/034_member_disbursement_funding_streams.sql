-- ==========================================
-- 034: Per-member multi-stream disbursement funding
-- ==========================================
-- Each member can select any number of their income streams to fund their
-- disbursement. Transfers are split evenly across the combined set of
-- distinct pay dates that fall in the disbursement month.

CREATE TABLE IF NOT EXISTS user_disbursement_funding_streams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    username TEXT NOT NULL,
    stream_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_disbursement_stream UNIQUE (household_id, username, stream_id)
);

CREATE INDEX IF NOT EXISTS idx_user_disbursement_streams_member
    ON user_disbursement_funding_streams (household_id, username);

CREATE TABLE IF NOT EXISTS user_disbursement_funding_streams_dev (
    LIKE user_disbursement_funding_streams INCLUDING ALL
);

NOTIFY pgrst, 'reload schema';
