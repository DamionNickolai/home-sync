-- Create user_sessions table for database-backed persistent login
-- Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS public.user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    refresh_token TEXT,
    device_fingerprint TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT DEFAULT 'system'
);

-- Create indexes for performance
CREATE INDEX idx_user_sessions_auth_user_id ON public.user_sessions(auth_user_id);
CREATE INDEX idx_user_sessions_is_active ON public.user_sessions(is_active);
CREATE INDEX idx_user_sessions_expires_at ON public.user_sessions(expires_at);

-- Enable RLS (Row Level Security)
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can only see their own sessions
CREATE POLICY "Users can view their own sessions" ON public.user_sessions
    FOR SELECT
    USING (auth_user_id = auth.uid());

-- RLS Policy: Users can delete (invalidate) their own sessions
CREATE POLICY "Users can invalidate their own sessions" ON public.user_sessions
    FOR UPDATE
    USING (auth_user_id = auth.uid())
    WITH CHECK (auth_user_id = auth.uid());

-- Service role policy for app-level operations (like invalidating on logout)
CREATE POLICY "Service role can manage all sessions" ON public.user_sessions
    FOR ALL
    USING (true)
    WITH CHECK (true)
    TO service_role;

-- Add comment
COMMENT ON TABLE public.user_sessions IS 'Stores persistent login sessions with database-backed security. Tokens are stored server-side, only session_id is stored in cookies.';
