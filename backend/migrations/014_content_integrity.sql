-- Migration 014: Content Integrity + Accountability System (G1)
-- Zone 1: Interview input quality tracking
-- Zone 2 + 3: Content approval log

-- ── Zone 1: Extend bonita_interview_log ────────────────────────────────────────
ALTER TABLE bonita_interview_log
    ADD COLUMN IF NOT EXISTS input_quality    VARCHAR(10),
      -- 'good', 'low', 'garbage'
    ADD COLUMN IF NOT EXISTS quality_issue    VARCHAR(30),
      -- 'too_short', 'nonsense', 'hostile', 'joke_input', 'off_topic'
    ADD COLUMN IF NOT EXISTS stored_to_profile BOOLEAN DEFAULT TRUE;
      -- FALSE when input was rejected / not stored in profile fields

-- ── Zone 1: Strike tracking for bad-faith onboarding inputs ────────────────────
CREATE TABLE IF NOT EXISTS onboarding_quality_flags (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    institution_id      INTEGER,
    session_date        DATE DEFAULT CURRENT_DATE,
    garbage_count       INTEGER DEFAULT 0,
    hostile_count       INTEGER DEFAULT 0,
    skipped_hostile     BOOLEAN DEFAULT FALSE,
      -- true if user triggered hostile skip path
    flagged_for_review  BOOLEAN DEFAULT FALSE,
      -- auto-true when garbage_count >= 3
    admin_notes         TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oqf_user_date
    ON onboarding_quality_flags(user_id, session_date);
CREATE INDEX IF NOT EXISTS idx_oqf_flagged
    ON onboarding_quality_flags(flagged_for_review) WHERE flagged_for_review = TRUE;

-- ── Zones 2+3: Content approval log ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_approvals (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    institution_id      INTEGER,
    course_id           TEXT,
    content_type        VARCHAR(50),
      -- 'assignment','quiz','syllabus','discussion','announcement','page','manual_upload'
    generation_method   VARCHAR(20),
      -- 'ai_generated' | 'manual_input'
    content_hash        TEXT,
      -- SHA256 of content at time of approval
    bonita_generated    BOOLEAN DEFAULT FALSE,
    faculty_reviewed    BOOLEAN DEFAULT FALSE,
    faculty_approved    BOOLEAN DEFAULT FALSE,
    approved_at         TIMESTAMPTZ,
    content_snapshot    TEXT,
      -- First 500 chars at approval time
    safety_checked      BOOLEAN DEFAULT FALSE,
    safety_passed       BOOLEAN DEFAULT TRUE,
    safety_flags        JSONB,
      -- {level: 'hard'|'soft', reason: str, auto_blocked: bool}
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_approvals_user  ON content_approvals(user_id);
CREATE INDEX IF NOT EXISTS idx_content_approvals_flags ON content_approvals(safety_passed) WHERE safety_passed = FALSE;
CREATE INDEX IF NOT EXISTS idx_content_approvals_date  ON content_approvals(created_at DESC);

SELECT 'Content integrity migration complete' AS message;
