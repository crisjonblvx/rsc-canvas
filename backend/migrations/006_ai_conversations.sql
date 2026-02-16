-- ReadySetClass Student Edition - AI Conversations
-- Migration 006: Study Buddy conversation history
-- Built by: Phife
-- Date: 2026-02-16

CREATE TABLE IF NOT EXISTS student_ai_conversations (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    title VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_student ON student_ai_conversations(student_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_created ON student_ai_conversations(created_at DESC);

CREATE TABLE IF NOT EXISTS student_ai_messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES student_ai_conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON student_ai_messages(conversation_id);

SELECT 'Migration 006: AI conversation tables created!' as message;
