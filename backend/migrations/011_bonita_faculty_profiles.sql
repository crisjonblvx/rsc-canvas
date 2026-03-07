-- Migration 011: Bonita Faculty Interview System
-- Creates faculty_profiles, bonita_interview_log, course_context tables
-- Adds faculty_profile_id to bonita_course_signals

-- Faculty profiles — teaching identity captured by Bonita interview
CREATE TABLE IF NOT EXISTS faculty_profiles (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,

    -- Layer 1: Identity
    preferred_name      VARCHAR(100),
    pronouns            VARCHAR(50),
    teaching_role       VARCHAR(50),  -- professor, adjunct, lecturer, k12_teacher, ta
    institution         VARCHAR(200),
    department          VARCHAR(200),
    years_teaching      INTEGER,
    is_returning        BOOLEAN DEFAULT FALSE,  -- returning RSC Canvas user

    -- Layer 2: Discipline
    primary_discipline  VARCHAR(200),
    sub_disciplines     TEXT[],
    typical_course_levels TEXT[],    -- intro, mid, upper, grad, mixed
    course_types        TEXT[],      -- lecture, seminar, lab, hybrid, online

    -- Layer 3: Students
    typical_class_size  VARCHAR(50), -- small (<20), medium (20-50), large (50+), massive
    student_population  TEXT[],      -- first_gen, stem, arts, pre_med, grad, community_college
    student_challenges  TEXT,        -- free text summary

    -- Layer 4: Pedagogy
    teaching_philosophy TEXT,
    assignment_style    VARCHAR(50), -- project_based, essay_heavy, quiz_heavy, mixed
    grading_approach    VARCHAR(50), -- specs, rubric, holistic, contract
    scaffolding_pref    VARCHAR(50), -- heavy, moderate, minimal

    -- Layer 5: Voice
    communication_tone  VARCHAR(50), -- formal, professional, balanced, friendly, casual
    use_humor           BOOLEAN DEFAULT FALSE,
    cultural_notes      TEXT,
    language_pref       VARCHAR(10) DEFAULT 'en',

    -- Layer 6: Reference
    has_existing_syllabus BOOLEAN DEFAULT FALSE,
    syllabus_notes      TEXT,
    key_texts           TEXT[],

    -- Interview state
    interview_complete  BOOLEAN DEFAULT FALSE,
    interview_layer     INTEGER DEFAULT 0,  -- which layer they're on (0 = not started)
    last_updated        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faculty_profiles_user ON faculty_profiles(user_id);

-- Interview log — full turn-by-turn transcript
CREATE TABLE IF NOT EXISTS bonita_interview_log (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      UUID DEFAULT gen_random_uuid(),
    layer           INTEGER NOT NULL,  -- 1-6 matching the 6 interview layers
    turn            INTEGER NOT NULL,  -- turn number within layer
    role            VARCHAR(10) NOT NULL CHECK (role IN ('bonita', 'user')),
    message         TEXT NOT NULL,
    extracted_data  JSONB,            -- structured data Bonita extracted from this turn
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interview_log_user ON bonita_interview_log(user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_interview_log_layer ON bonita_interview_log(user_id, layer);

-- Course context — per-course customization on top of faculty profile
CREATE TABLE IF NOT EXISTS course_context (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_name     VARCHAR(500),
    course_code     VARCHAR(50),
    canvas_course_id BIGINT,
    semester        VARCHAR(100),

    -- Course-specific overrides
    course_tone     VARCHAR(50),      -- override faculty profile tone for this course
    special_notes   TEXT,             -- what's unique about this specific course
    content_warning TEXT,             -- topics needing care
    learning_goals  TEXT[],

    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_course_context_user ON course_context(user_id);
CREATE INDEX IF NOT EXISTS idx_course_context_canvas ON course_context(canvas_course_id);

-- Add faculty_profile_id to bonita_course_signals (if column doesn't exist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bonita_course_signals'
          AND column_name = 'faculty_profile_id'
    ) THEN
        ALTER TABLE bonita_course_signals
            ADD COLUMN faculty_profile_id INTEGER REFERENCES faculty_profiles(id);
    END IF;
END $$;
