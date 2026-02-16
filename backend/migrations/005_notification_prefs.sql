-- ReadySetClass Student Edition - Notification Preferences
-- Migration 005: Per-course notification settings
-- Built by: Phife
-- Date: 2026-02-15

CREATE TABLE IF NOT EXISTS student_notification_prefs (
    id SERIAL PRIMARY KEY,
    enrollment_id INTEGER NOT NULL UNIQUE REFERENCES student_enrollments(id) ON DELETE CASCADE,
    announcements_enabled BOOLEAN DEFAULT TRUE,
    deadlines_enabled BOOLEAN DEFAULT TRUE,
    reminder_hours INTEGER DEFAULT 24,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notification_prefs_enrollment ON student_notification_prefs(enrollment_id);

SELECT 'Migration 005: Notification preferences table created!' as message;
