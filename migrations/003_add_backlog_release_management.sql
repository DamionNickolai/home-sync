-- Migration: Add work_notes, release_date, and version columns to backlog table
-- This migration adds support for the enhanced backlog management system

-- Add work_notes column (internal implementation notes)
ALTER TABLE backlog
ADD COLUMN IF NOT EXISTS work_notes TEXT;

-- Add release_date column (when item was released to production)
ALTER TABLE backlog
ADD COLUMN IF NOT EXISTS release_date DATE;

-- Add version column (semantic version when released)
ALTER TABLE backlog
ADD COLUMN IF NOT EXISTS version TEXT;

-- Add index on version for quick lookups of released items
CREATE INDEX IF NOT EXISTS idx_backlog_version ON backlog(version);

-- Add index on release_date for chronological queries
CREATE INDEX IF NOT EXISTS idx_backlog_release_date ON backlog(release_date DESC);

-- Add index on status for staged/done item queries
CREATE INDEX IF NOT EXISTS idx_backlog_status ON backlog(status);
