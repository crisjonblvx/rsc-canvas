-- Migration 012: Bonita Faculty Profile v2 — Canonical Schema (Sunni spec)
-- Replaces Migration 011 which used an incorrect schema.
-- Detects old schema via presence of 'preferred_name' column (011 only).
-- Safe to re-run: idempotent via IF NOT EXISTS and conditional DO block.

DO $$
BEGIN
    -- Old schema detected when 'preferred_name' exists (011 artifact, not in canonical spec)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'faculty_profiles' AND column_name = 'preferred_name'
    ) THEN
        -- Drop old FK column on signal tables before cascade drop
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'bonita_course_signals' AND column_name = 'faculty_profile_id'
        ) THEN
            ALTER TABLE bonita_course_signals DROP COLUMN faculty_profile_id;
        END IF;

        -- Drop old tables (CASCADE removes FK constraints pointing at faculty_profiles)
        DROP TABLE IF EXISTS course_context CASCADE;
        DROP TABLE IF EXISTS bonita_interview_log CASCADE;
        DROP TABLE IF EXISTS faculty_profiles CASCADE;
    END IF;
END $$;

-- ── Faculty teaching profile (canonical) ──────────────────────────────────────
-- Persists across courses, semesters, and platforms.
-- Shared schema with RSCLMS faculty_profiles. Same email = same Bonita profile.
CREATE TABLE IF NOT EXISTS faculty_profiles (
    id                      SERIAL PRIMARY KEY,
    user_id                 INTEGER NOT NULL UNIQUE,
    canvas_user_id          VARCHAR(100),

    -- IDENTITY LAYER
    years_teaching          INTEGER,
    teaching_level          TEXT[],
      -- ['undergrad','grad','dual_enrollment','community']
    employment_type         VARCHAR(20),
      -- 'full_time','adjunct','visiting','emeritus'
    is_course_owner         BOOLEAN DEFAULT TRUE,
      -- FALSE = adjunct teaching assigned course

    -- DISCIPLINE LAYER
    primary_discipline      VARCHAR(255),
    discipline_focus        TEXT,
    anchor_thinkers         TEXT,
    anchor_frameworks       TEXT,

    -- STUDENT LAYER
    typical_student_profile TEXT,
    student_struggles       TEXT,
    learning_outcome_focus  TEXT,
      -- "What do you want students to be ABLE TO DO?"

    -- PEDAGOGY LAYER
    teaching_style          TEXT,
    grading_philosophy      TEXT,
    what_an_a_looks_like    TEXT,

    -- VOICE LAYER
    communication_style     VARCHAR(20),
      -- 'formal','conversational','warm','direct'
    uses_humor              BOOLEAN,
    voice_sample            TEXT,
    voice_style_profile     JSONB,
      -- {"formality","avg_sentence_length","uses_contractions",
      --  "uses_first_person","uses_humor","tone_markers","vocabulary_level","paragraph_style"}

    -- REFERENCE MATERIAL
    reference_material_keys TEXT[],

    -- PROFILE METADATA
    onboarding_phase        VARCHAR(20) DEFAULT 'not_started',
      -- 'not_started','demo_shown','interview_started','interview_complete'
    interview_step          INTEGER DEFAULT 0,
    interview_completed     BOOLEAN DEFAULT FALSE,
    interview_completed_at  TIMESTAMP,
    profile_version         INTEGER DEFAULT 1,
    generations_count       INTEGER DEFAULT 0,
    last_updated            TIMESTAMP DEFAULT NOW(),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faculty_profiles_user ON faculty_profiles(user_id);

-- ── Interview conversation log ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bonita_interview_log (
    id                      SERIAL PRIMARY KEY,
    faculty_profile_id      INTEGER NOT NULL
                              REFERENCES faculty_profiles(id)
                              ON DELETE CASCADE,
    turn_number             INTEGER NOT NULL,
    speaker                 VARCHAR(10) NOT NULL
                              CHECK (speaker IN ('bonita','faculty')),
    message                 TEXT NOT NULL,
    extracted_data          JSONB,
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interview_log_profile ON bonita_interview_log(faculty_profile_id);

-- ── Course-level context ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS course_context (
    id                      SERIAL PRIMARY KEY,
    canvas_course_id        VARCHAR(100) NOT NULL,
    user_id                 INTEGER NOT NULL,
    faculty_profile_id      INTEGER REFERENCES faculty_profiles(id),

    course_anchor_thinkers  TEXT,
    course_student_profile  TEXT,
    course_outcomes         TEXT[],
    citation_style          VARCHAR(20) DEFAULT 'APA',
      -- APA, MLA, Chicago, Bluebook, AMA, IEEE
    ai_use_policy           VARCHAR(30) DEFAULT 'allowed_with_disclosure',
      -- prohibited, allowed_with_disclosure, fully_permitted

    interview_completed     BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMP DEFAULT NOW(),

    UNIQUE(canvas_course_id, user_id)
);

-- ── Extend signal tables with faculty_profile_id ───────────────────────────────
ALTER TABLE bonita_course_signals
    ADD COLUMN IF NOT EXISTS faculty_profile_id INTEGER REFERENCES faculty_profiles(id);

ALTER TABLE bonita_content_signals
    ADD COLUMN IF NOT EXISTS faculty_profile_id INTEGER REFERENCES faculty_profiles(id);

SELECT 'Faculty profiles v2 migration complete' AS message;
