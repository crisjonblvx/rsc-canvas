-- ReadySetClass Student Edition
-- Migration 003: Add student support

-- Update users role constraint to include 'student'
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('admin', 'demo', 'customer', 'student'));

-- Student assignments table - caches assignment data from Canvas
CREATE TABLE IF NOT EXISTS student_assignments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    course_id VARCHAR(50) NOT NULL,
    assignment_id VARCHAR(50) NOT NULL,
    title VARCHAR(500),
    description TEXT,
    due_at TIMESTAMP,
    points_possible FLOAT,
    submission_types TEXT,           -- comma-separated: online_text_entry,online_upload
    score FLOAT,                    -- student's score (NULL if not graded)
    grade VARCHAR(20),              -- letter grade if available
    submitted BOOLEAN DEFAULT FALSE,
    submitted_at TIMESTAMP,
    workflow_state VARCHAR(50),     -- published, unpublished, etc.
    assignment_group_name VARCHAR(255),
    course_name VARCHAR(255),
    synced_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, assignment_id)
);

-- Indexes for student queries
CREATE INDEX IF NOT EXISTS idx_student_assignments_user ON student_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_student_assignments_due ON student_assignments(due_at);
CREATE INDEX IF NOT EXISTS idx_student_assignments_course ON student_assignments(user_id, course_id);

-- Success message
SELECT 'Student tables created successfully!' as message;
