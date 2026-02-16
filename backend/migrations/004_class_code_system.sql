-- ReadySetClass Student Edition - Class Code System
-- Migration 004: Replace Canvas-dependent student model with Class Code system
-- Built by: Phife
-- Date: 2026-02-15

-- ============================================================================
-- 1. MODIFY USERS TABLE - Add .edu verification columns
-- ============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS edu_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS edu_verified_at TIMESTAMP;

-- ============================================================================
-- 2. STUDENT_COURSES - Course metadata that professors share with students
-- ============================================================================

CREATE TABLE IF NOT EXISTS student_courses (
    id SERIAL PRIMARY KEY,
    professor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_name VARCHAR(255) NOT NULL,
    course_code VARCHAR(50),
    section VARCHAR(50),
    semester VARCHAR(50),
    institution VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_courses_professor ON student_courses(professor_id);
CREATE INDEX IF NOT EXISTS idx_student_courses_institution ON student_courses(institution);

-- ============================================================================
-- 3. CLASS_CODES - Professor-generated class join codes (RSC-XXXX)
-- ============================================================================

CREATE TABLE IF NOT EXISTS class_codes (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    professor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code VARCHAR(8) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'deactivated')),
    expires_at TIMESTAMP,
    max_students INTEGER DEFAULT 200,
    current_students INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_class_codes_code ON class_codes(code);
CREATE INDEX IF NOT EXISTS idx_class_codes_course ON class_codes(course_id);
CREATE INDEX IF NOT EXISTS idx_class_codes_professor ON class_codes(professor_id);
CREATE INDEX IF NOT EXISTS idx_class_codes_status ON class_codes(status);

-- ============================================================================
-- 4. STUDENT_ENROLLMENTS - Student-to-Course links via class codes
-- ============================================================================

CREATE TABLE IF NOT EXISTS student_enrollments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    class_code_id INTEGER NOT NULL REFERENCES class_codes(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMP DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'active'
        CHECK (status IN ('active', 'dropped', 'removed')),
    UNIQUE(student_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_student_enrollments_student ON student_enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_student_enrollments_course ON student_enrollments(course_id);
CREATE INDEX IF NOT EXISTS idx_student_enrollments_status ON student_enrollments(status);

-- ============================================================================
-- 5. STUDENT_ANNOUNCEMENTS - Professor-to-Student announcements
-- ============================================================================

CREATE TABLE IF NOT EXISTS student_announcements (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    professor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_announcements_course ON student_announcements(course_id);
CREATE INDEX IF NOT EXISTS idx_student_announcements_created ON student_announcements(created_at DESC);

-- ============================================================================
-- 6. STUDENT_DEADLINES - Professor-to-Student deadline sharing
-- ============================================================================

CREATE TABLE IF NOT EXISTS student_deadlines (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    professor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    due_at TIMESTAMP NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_deadlines_course ON student_deadlines(course_id);
CREATE INDEX IF NOT EXISTS idx_student_deadlines_due ON student_deadlines(due_at);

-- ============================================================================
-- 7. STUDENT_GRADES - Manual grade entries by students
-- ============================================================================

CREATE TABLE IF NOT EXISTS student_grades (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    enrollment_id INTEGER NOT NULL REFERENCES student_enrollments(id) ON DELETE CASCADE,
    category_name VARCHAR(255) NOT NULL,
    assignment_name VARCHAR(500) NOT NULL,
    score FLOAT NOT NULL,
    points_possible FLOAT NOT NULL,
    weight FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_grades_student ON student_grades(student_id);
CREATE INDEX IF NOT EXISTS idx_student_grades_enrollment ON student_grades(enrollment_id);

-- ============================================================================

SELECT 'Migration 004: Class Code system created successfully!' as message;
