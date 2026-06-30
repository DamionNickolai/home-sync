-- ==========================================
-- 041: Plaintext link from personal income → member transfer
-- ==========================================
-- Avoids matching allowance incomes by decrypting amount/source_name.
-- Value format: "{transfer_uuid}#allowance" or "{transfer_uuid}#obligation"

ALTER TABLE household_incomes
    ADD COLUMN IF NOT EXISTS source_member_transfer_id TEXT;

ALTER TABLE household_incomes_dev
    ADD COLUMN IF NOT EXISTS source_member_transfer_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_household_incomes_source_member_transfer
    ON household_incomes (source_member_transfer_id)
    WHERE source_member_transfer_id IS NOT NULL AND btrim(source_member_transfer_id) <> '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_household_incomes_source_member_transfer_dev
    ON household_incomes_dev (source_member_transfer_id)
    WHERE source_member_transfer_id IS NOT NULL AND btrim(source_member_transfer_id) <> '';

NOTIFY pgrst, 'reload schema';
