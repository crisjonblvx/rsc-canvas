-- Migration 008: Add preferred_language to users table
-- Enables true multilingual support (A1)

ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(5) DEFAULT 'en';

-- Update any existing nulls
UPDATE users SET preferred_language = 'en' WHERE preferred_language IS NULL;

-- Add check constraint for supported languages
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_preferred_language_check;
ALTER TABLE users ADD CONSTRAINT users_preferred_language_check
    CHECK (preferred_language IN ('en', 'es', 'fr', 'pt', 'ar', 'zh'));

COMMENT ON COLUMN users.preferred_language IS 'User preferred UI language code. Supported: en, es, fr, pt, ar, zh';
