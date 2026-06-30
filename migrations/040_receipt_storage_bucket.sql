-- ==========================================
-- 040: Supabase Storage bucket for receipt uploads
-- ==========================================
-- Creates the private household-receipts bucket and RLS policies scoped to
-- each user's household_id prefix: {household_id}/{receipt_id}/{filename}
--
-- Note: bucket rows live in storage.buckets (not public schema).
-- You can also run: python maintenance/provision_receipt_storage.py --apply

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'household-receipts',
    'household-receipts',
    false,
    20971520,  -- 20 MB
    ARRAY['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
)
ON CONFLICT (id) DO NOTHING;

-- Helper: first path segment must match the signed-in user's household_id.
CREATE OR REPLACE FUNCTION public.receipt_storage_household_prefix()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT household_id
    FROM public.users
    WHERE auth_user_id = auth.uid()::text
    LIMIT 1;
$$;

-- Read own household prefix
DROP POLICY IF EXISTS "receipt_storage_select_household" ON storage.objects;
CREATE POLICY "receipt_storage_select_household"
    ON storage.objects
    FOR SELECT
    TO authenticated
    USING (
        bucket_id = 'household-receipts'
        AND (storage.foldername(name))[1] = public.receipt_storage_household_prefix()
    );

-- Upload to own household prefix
DROP POLICY IF EXISTS "receipt_storage_insert_household" ON storage.objects;
CREATE POLICY "receipt_storage_insert_household"
    ON storage.objects
    FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'household-receipts'
        AND (storage.foldername(name))[1] = public.receipt_storage_household_prefix()
    );

-- Update own household prefix
DROP POLICY IF EXISTS "receipt_storage_update_household" ON storage.objects;
CREATE POLICY "receipt_storage_update_household"
    ON storage.objects
    FOR UPDATE
    TO authenticated
    USING (
        bucket_id = 'household-receipts'
        AND (storage.foldername(name))[1] = public.receipt_storage_household_prefix()
    )
    WITH CHECK (
        bucket_id = 'household-receipts'
        AND (storage.foldername(name))[1] = public.receipt_storage_household_prefix()
    );

-- Delete own household prefix
DROP POLICY IF EXISTS "receipt_storage_delete_household" ON storage.objects;
CREATE POLICY "receipt_storage_delete_household"
    ON storage.objects
    FOR DELETE
    TO authenticated
    USING (
        bucket_id = 'household-receipts'
        AND (storage.foldername(name))[1] = public.receipt_storage_household_prefix()
    );

NOTIFY pgrst, 'reload schema';
