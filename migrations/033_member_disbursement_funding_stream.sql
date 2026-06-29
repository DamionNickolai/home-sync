-- ==========================================
-- 033: Per-member disbursement funding stream
-- ==========================================
-- Each member chooses which of their own income streams funds their share of
-- the household disbursement (obligation gap + surplus allowance).
-- Replaces the single household-level funding_income_stream_id for scheduling.

ALTER TABLE user_finance_settings
    ADD COLUMN IF NOT EXISTS disbursement_funding_stream_id UUID;

ALTER TABLE user_finance_settings_dev
    ADD COLUMN IF NOT EXISTS disbursement_funding_stream_id UUID;

NOTIFY pgrst, 'reload schema';
