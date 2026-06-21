-- Drop tables if you already created them during testing
DROP TABLE IF EXISTS public.wish_list;
DROP TABLE IF EXISTS public.wish_list_dev;

-- ==========================================
-- 1. PRODUCTION TABLE
-- ==========================================
CREATE TABLE public.wish_list (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    household_id TEXT NOT NULL,
    owner_auth_user_id TEXT NOT NULL,
    owner_username TEXT,
    item TEXT,               -- ENCRYPTED
    description TEXT,        -- ENCRYPTED
    estimated_price TEXT,    -- ENCRYPTED (Stored as text, converted to float in Python)
    actual_cost TEXT,        -- ENCRYPTED (Stored as text, converted to float in Python)
    veteran_discount BOOLEAN DEFAULT FALSE, -- FIXED: Standard boolean
    vendor TEXT,             -- ENCRYPTED
    notes TEXT,              -- ENCRYPTED
    is_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ==========================================
-- 2. DEVELOPMENT TABLE (_dev)
-- ==========================================
CREATE TABLE public.wish_list_dev (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    household_id TEXT NOT NULL,
    owner_auth_user_id TEXT NOT NULL,
    owner_username TEXT,
    item TEXT,               -- ENCRYPTED
    description TEXT,        -- ENCRYPTED
    estimated_price TEXT,    -- ENCRYPTED 
    actual_cost TEXT,        -- ENCRYPTED 
    veteran_discount BOOLEAN DEFAULT FALSE, -- FIXED: Standard boolean
    vendor TEXT,             -- ENCRYPTED
    notes TEXT,              -- ENCRYPTED
    is_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);