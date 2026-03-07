-- Migration 013: Faculty Reference Materials
-- Stores extracted text from uploaded reference materials (syllabi, assignments, notes)
-- Synthetic key pattern: faculty/{user_id}/reference/{uuid}.{ext}
-- No S3/R2 — text stored in DB, key tracked in faculty_profiles.reference_material_keys

CREATE TABLE IF NOT EXISTS faculty_reference_materials (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    key             VARCHAR(255) NOT NULL UNIQUE,
      -- synthetic key: faculty/{user_id}/reference/{uuid}.{ext}
    file_name       VARCHAR(255) NOT NULL,
    file_type       VARCHAR(10) NOT NULL,
      -- pdf, docx, txt, md
    extracted_text  TEXT NOT NULL,
    char_count      INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faculty_refmats_user ON faculty_reference_materials(user_id);
CREATE INDEX IF NOT EXISTS idx_faculty_refmats_key ON faculty_reference_materials(key);

SELECT 'Faculty reference materials migration complete' AS message;
