-- ============================================================
-- Migration 010: Bonita Learning Signal Layer
-- Purpose: Intelligence foundation for the RSC LMS data moat
-- Tables: bonita_course_signals, bonita_content_signals,
--         bonita_learning_patterns
-- ============================================================

-- 1. Course-level structural signals
-- Captures how a course is architected overall
CREATE TABLE IF NOT EXISTS bonita_course_signals (
    id                        SERIAL PRIMARY KEY,
    asset_id                  INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    user_id                   INTEGER NOT NULL,
    course_code               VARCHAR(50),
    subject_area              VARCHAR(100),     -- e.g. "communications", "stem"
    credit_hours              INTEGER,
    course_level              VARCHAR(20),      -- 'intro', 'mid', 'upper', 'grad'
    week_count                INTEGER,
    content_mix               JSONB,            -- {"assignments": 4, "quizzes": 8, "discussions": 3}
    has_rubrics               BOOLEAN DEFAULT FALSE,
    has_weighted_grading      BOOLEAN DEFAULT FALSE,
    learning_objectives_count INTEGER,
    generated_at              TIMESTAMP DEFAULT NOW(),
    bonita_opt_in             BOOLEAN DEFAULT FALSE
);

-- 2. Content-level quality signals
-- Captures what makes individual pieces of content good
CREATE TABLE IF NOT EXISTS bonita_content_signals (
    id                SERIAL PRIMARY KEY,
    asset_id          INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    user_id           INTEGER NOT NULL,
    content_type      VARCHAR(50),     -- 'assignment', 'quiz', 'syllabus', 'discussion', etc.
    subject_area      VARCHAR(100),
    course_level      VARCHAR(20),
    week_number       INTEGER,         -- which week of the semester (1-16)
    difficulty_level  VARCHAR(20),     -- 'foundational', 'developing', 'proficient', 'advanced'
    word_count        INTEGER,
    has_rubric        BOOLEAN DEFAULT FALSE,
    question_types    JSONB,           -- for quizzes: {"mcq": 5, "essay": 2, "tf": 3}
    model_used        VARCHAR(50),     -- 'groq', 'gemini_flash', 'claude_sonnet', 'claude_haiku'
    generation_cost   NUMERIC(10, 6),
    enhance_applied   BOOLEAN DEFAULT FALSE,
    generated_at      TIMESTAMP DEFAULT NOW(),
    bonita_opt_in     BOOLEAN DEFAULT FALSE
);

-- 3. Aggregate learning patterns
-- Rolled-up view of what works by subject + level
-- Populated by background job or on-demand aggregation
CREATE TABLE IF NOT EXISTS bonita_learning_patterns (
    id                   SERIAL PRIMARY KEY,
    subject_area         VARCHAR(100),
    course_level         VARCHAR(20),
    sample_size          INTEGER,       -- how many courses this is based on
    avg_week_count       NUMERIC(5,2),
    avg_objectives_count NUMERIC(5,2),
    common_content_mix   JSONB,         -- most common content type ratios
    top_difficulty_curve JSONB,         -- how difficulty typically progresses week by week
    rubric_adoption_rate NUMERIC(5,2),  -- % of courses that use rubrics
    computed_at          TIMESTAMP DEFAULT NOW()
);

-- Indexes for fast pattern lookups
CREATE INDEX IF NOT EXISTS idx_bonita_course_signals_subject
    ON bonita_course_signals(subject_area, course_level);

CREATE INDEX IF NOT EXISTS idx_bonita_content_signals_type
    ON bonita_content_signals(content_type, subject_area, course_level);

CREATE INDEX IF NOT EXISTS idx_bonita_content_signals_user
    ON bonita_content_signals(user_id);
