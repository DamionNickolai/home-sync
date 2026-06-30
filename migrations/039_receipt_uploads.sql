-- ==========================================
-- 039: Receipt uploads and line items
-- ==========================================

-- Stores uploaded receipt/invoice metadata and OCR state.
CREATE TABLE IF NOT EXISTS receipt_uploads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id TEXT NOT NULL,
    uploaded_by_username TEXT NOT NULL,
    uploaded_by_auth_user_id TEXT NOT NULL,
    storage_path TEXT,
    file_name TEXT,
    mime_type TEXT,
    merchant TEXT,
    receipt_date DATE,
    total_amount TEXT,         -- encrypted float
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'posted', 'archived')),
    ocr_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (ocr_status IN ('pending', 'done', 'failed')),
    ocr_raw_json TEXT,         -- encrypted JSON blob
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    posted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_receipt_uploads_household
    ON receipt_uploads (household_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_receipt_uploads_user
    ON receipt_uploads (household_id, uploaded_by_username, created_at DESC);


-- One row per extracted or manually-added line on a receipt.
CREATE TABLE IF NOT EXISTS receipt_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_upload_id UUID NOT NULL REFERENCES receipt_uploads (id) ON DELETE CASCADE,
    line_index INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    line_amount TEXT,          -- encrypted float
    ledger_target TEXT NOT NULL DEFAULT 'personal'
        CHECK (ledger_target IN ('personal', 'hh_obligation', 'hh_shared', 'project')),
    category_id BIGINT REFERENCES budget_categories (id) ON DELETE SET NULL,
    project_budget_id TEXT,
    posted_expense_id TEXT,    -- id of resulting expense row (if posted)
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'posted', 'uncategorized'))
);

CREATE INDEX IF NOT EXISTS idx_receipt_line_items_receipt
    ON receipt_line_items (receipt_upload_id, line_index);


-- Dev equivalents (same pattern as all other budget tables).
CREATE TABLE IF NOT EXISTS receipt_uploads_dev (
    LIKE receipt_uploads INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS receipt_line_items_dev (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_upload_id UUID NOT NULL REFERENCES receipt_uploads_dev (id) ON DELETE CASCADE,
    line_index INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    line_amount TEXT,
    ledger_target TEXT NOT NULL DEFAULT 'personal'
        CHECK (ledger_target IN ('personal', 'hh_obligation', 'hh_shared', 'project')),
    -- budget_categories_dev uses UUID pk; omit FK to avoid type mismatch with BIGINT.
    -- The application layer enforces category validity at write time.
    category_id BIGINT,
    project_budget_id TEXT,
    posted_expense_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'posted', 'uncategorized'))
);

-- Optional audit link from an expense row back to its receipt line.
-- Runs separately against both prod and dev expenses tables if desired.
ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS receipt_line_item_id UUID;

ALTER TABLE expenses_dev
    ADD COLUMN IF NOT EXISTS receipt_line_item_id UUID;

NOTIFY pgrst, 'reload schema';
