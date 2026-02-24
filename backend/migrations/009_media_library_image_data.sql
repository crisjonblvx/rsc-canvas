-- Migration 009: Extend media_library for base64 image storage (C1)
-- image_url VARCHAR(500) is too short for data URLs — change to TEXT
-- Add image_data TEXT for raw base64, thumbnail_url TEXT for compressed preview

ALTER TABLE media_library ALTER COLUMN image_url TYPE TEXT;
ALTER TABLE media_library ADD COLUMN IF NOT EXISTS image_data TEXT;
ALTER TABLE media_library ADD COLUMN IF NOT EXISTS thumbnail_data TEXT;
ALTER TABLE media_library ADD COLUMN IF NOT EXISTS mime_type VARCHAR(30) DEFAULT 'image/png';

COMMENT ON COLUMN media_library.image_url IS 'Full data URL (data:image/png;base64,...) for direct src embedding';
COMMENT ON COLUMN media_library.image_data IS 'Raw base64 image bytes (no prefix)';
COMMENT ON COLUMN media_library.thumbnail_data IS 'Compressed thumbnail data URL for grid display';
