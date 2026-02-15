# ReadySetClass Backend API Documentation

**Version:** 2.0.0
**Purpose:** AI-powered Canvas LMS course builder and grading assistant
**Built for:** Professors and educators
**Tagline:** Less time setting up. More time teaching.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Environment Variables](#environment-variables)
3. [Database Schema](#database-schema)
4. [Authentication & Authorization](#authentication--authorization)
5. [API Endpoints](#api-endpoints)
6. [AI Integration (The Agents)](#ai-integration-the-agents)
7. [Data Models](#data-models)
8. [Canvas LMS Integration](#canvas-lms-integration)
9. [Payment Integration (Stripe)](#payment-integration-stripe)

---

## Architecture Overview

ReadySetClass is built with:
- **Framework:** FastAPI (Python)
- **Database:** PostgreSQL
- **AI Providers:** OpenAI (preferred), Groq (free), Anthropic Claude (fallback)
- **LMS:** Canvas via REST API
- **Payments:** Stripe
- **Authentication:** JWT tokens + session-based auth

### Key Features
- AI-powered quiz, assignment, and syllabus generation
- Canvas course content creation and upload
- AI grading with professor review workflow
- Automated grading setup (45 min → 2 min)
- Multi-language support (EN, ES, FR, PT, AR, ZH)
- Grade-level appropriate content (K-2 through College)
- Demo account system (24-hour temporary accounts)

---

## Environment Variables

### Required Variables

```bash
# AI API Keys (at least one required)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPENAI_API_KEY=your_openai_api_key_here        # Preferred (cheap)
GROQ_API_KEY=your_groq_api_key_here            # Alternative (free!)

# Database
DATABASE_URL=postgresql://user:password@host:5432/readysetclass

# Security
JWT_SECRET=your_jwt_secret_here                 # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"

# Stripe Payment Processing
STRIPE_SECRET_KEY=sk_test_your_stripe_key
STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret

# Optional - Local AI
OLLAMA_URL=http://localhost:11434              # For Qwen local model

# Environment
ENVIRONMENT=development                         # development, staging, production
DEBUG=True
```

### Canvas Credentials
Canvas credentials are stored **per-user** in the database (encrypted). Users provide their own Canvas URL and API token through the UI.

---

## Database Schema

### Core Tables

#### `users`
Stores user accounts, subscription info, and Canvas credentials.

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(20) DEFAULT 'demo' CHECK (role IN ('admin', 'demo', 'customer')),

    -- Account Status
    is_active BOOLEAN DEFAULT TRUE,
    is_demo BOOLEAN DEFAULT FALSE,
    demo_expires_at TIMESTAMP,

    -- Canvas Connection
    canvas_url VARCHAR(255),
    canvas_token_encrypted TEXT,

    -- Subscription (Stripe)
    subscription_tier VARCHAR(20) DEFAULT 'trial',  -- trial, pro, team, enterprise
    subscription_status VARCHAR(20) DEFAULT 'active', -- active, canceled, past_due, trialing
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    trial_ends_at TIMESTAMP,
    subscription_ends_at TIMESTAMP,

    -- Usage Tracking
    content_created_count INTEGER DEFAULT 0,
    ai_generations_count INTEGER DEFAULT 0,
    ai_generations_this_month INTEGER DEFAULT 0,
    billing_cycle_start TIMESTAMP,
    last_active_at TIMESTAMP,

    -- Institution
    institution VARCHAR(255),
    notes TEXT,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### `sessions`
Stores active user sessions (JWT alternative for better control).

```sql
CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_token VARCHAR(255) UNIQUE NOT NULL,
    ip_address VARCHAR(45),
    user_agent TEXT,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `canvas_credentials`
Stores encrypted Canvas API credentials (SQLAlchemy model).

```python
class CanvasCredentials:
    user_id = Column(Integer, primary_key=True)
    canvas_url = Column(String(255), nullable=False)
    access_token_encrypted = Column(Text, nullable=False)  # Encrypted with Fernet
    created_at = Column(DateTime, default=datetime.utcnow)
    last_verified = Column(DateTime, default=datetime.utcnow)
```

#### `user_courses`
Caches Canvas course data to reduce API calls.

```python
class UserCourse:
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("canvas_credentials.user_id"))
    course_id = Column(Integer, nullable=False)     # Canvas course ID
    course_name = Column(String(255))
    course_code = Column(String(100))
    total_students = Column(Integer)
    synced_at = Column(DateTime, default=datetime.utcnow)
```

#### `reference_materials`
Stores uploaded syllabi/documents for AI style matching.

```python
class ReferenceMaterial:
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    file_name = Column(String(255))
    file_type = Column(String(10))          # pdf, docx, txt
    extracted_text = Column(Text)           # Full text extracted from file
    course_name = Column(String(255))       # Optional: which course
    upload_date = Column(DateTime)
```

### AI Grading Tables

#### `ai_grading_sessions`
Tracks one grading session (one assignment, all submissions).

```python
class AIGradingSession:
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)               # Professor
    course_id = Column(String(50))
    assignment_id = Column(String(50))
    assignment_title = Column(String(255))

    # Configuration
    rubric = Column(JSON)                   # Grading rubric
    preferences = Column(JSON)              # Strictness, flags, etc.

    # Status
    status = Column(String(50))             # in_progress, completed, posted
    total_submissions = Column(Integer)
    graded_count = Column(Integer, default=0)
    reviewed_count = Column(Integer, default=0)
    posted_count = Column(Integer, default=0)

    # Analytics
    average_score = Column(Float)
    average_confidence = Column(Float)
    flagged_count = Column(Integer, default=0)

    # Timestamps
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    posted_at = Column(DateTime)
```

#### `ai_grades`
Individual grade for one student submission.

```python
class AIGrade:
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("ai_grading_sessions.id"))

    # Student Info
    student_id = Column(String(50))         # Canvas user ID
    student_name = Column(String(255))
    submission_id = Column(String(50))
    submission_text = Column(Text)
    submitted_at = Column(DateTime)

    # AI Results
    ai_total_score = Column(Float)
    ai_rubric_scores = Column(JSON)         # Score per rubric criterion
    ai_feedback = Column(Text)
    ai_criterion_feedback = Column(JSON)    # Feedback per criterion
    ai_confidence = Column(String(20))      # high, medium, low
    ai_flags = Column(JSON)                 # Plagiarism, AI-generated, etc.

    # Professor Review
    reviewed = Column(Boolean, default=False)
    reviewed_at = Column(DateTime)
    final_score = Column(Float)
    final_feedback = Column(Text)
    professor_adjustments = Column(JSON)

    # Canvas Upload
    posted_to_canvas = Column(Boolean, default=False)
    posted_at = Column(DateTime)
    canvas_grade_id = Column(String(50))

    created_at = Column(DateTime)
```

### Activity & Analytics Tables

#### `activity_log`
Tracks user actions for analytics and debugging.

```sql
CREATE TABLE activity_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(100) NOT NULL,           -- login, quiz_created, assignment_created, etc.
    details JSONB,                          -- Additional metadata
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `user_analytics`
Tracks detailed feature usage and timing.

```sql
CREATE TABLE user_analytics (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    event_type VARCHAR(100) NOT NULL,       -- page_view, feature_used, session_end
    feature VARCHAR(100),                   -- quiz_generator, ai_grading, etc.
    duration INTEGER,                       -- Time in seconds
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `feedback`
Stores user feedback and support requests.

```sql
CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    message TEXT,
    feature VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Subscription Tables

#### `subscription_plans`
Defines available subscription tiers.

```sql
CREATE TABLE subscription_plans (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,       -- trial, pro, team, enterprise
    display_name VARCHAR(100),
    price_monthly DECIMAL(10, 2),
    price_yearly DECIMAL(10, 2),
    stripe_price_id_monthly VARCHAR(255),
    stripe_price_id_yearly VARCHAR(255),
    ai_generations_limit INTEGER,           -- NULL = unlimited
    max_users INTEGER DEFAULT 1,
    features JSONB,                         -- {"priority_support": true, ...}
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `payment_transactions`
Logs all payment events from Stripe.

```sql
CREATE TABLE payment_transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    stripe_payment_intent_id VARCHAR(255),
    amount DECIMAL(10, 2),
    currency VARCHAR(3) DEFAULT 'USD',
    status VARCHAR(20),
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## Authentication & Authorization

### Authentication Flow

1. **User Registration/Login**
   - POST `/api/auth/login` with email + password
   - Server validates credentials against `users` table (bcrypt hashed passwords)
   - Server creates session token and stores in `sessions` table
   - Returns session token to client

2. **Session Validation**
   - Client includes token in `Authorization: Bearer <token>` header
   - `get_current_user_from_token()` validates token:
     - Checks token exists in `sessions` table
     - Verifies `expires_at` hasn't passed
     - Checks user is active (`is_active = TRUE`)
     - For demo accounts, checks `demo_expires_at`
   - Returns user object: `{user_id, email, role, is_demo}`

3. **Session Expiration**
   - Sessions expire after 24 hours
   - Demo accounts expire after 24 hours (auto-cleanup endpoint available)

### Role-Based Access Control

Three roles defined in database:

| Role | Description | Access Level |
|------|-------------|--------------|
| `demo` | Temporary 24-hour trial account | Limited (can test features, no billing) |
| `customer` | Paid subscriber | Full access to all features |
| `admin` | System administrator | Full access + admin dashboard |

### Protected Endpoints

Most endpoints require authentication via `Depends(get_current_user_from_token)`.

Admin-only endpoints (check `role == 'admin'`):
- `/api/admin/users` - View all users
- `/api/admin/users/{id}/role` - Change user role
- `/api/admin/users/{id}/status` - Enable/disable accounts
- `/api/admin/stats` - System statistics
- `/api/admin/analytics` - Usage analytics
- `/api/demo/cleanup` - Delete expired demos

### Demo Account System

Create instant demo accounts without signup:

```python
POST /api/demo/create

Response:
{
    "email": "demo-abc123@readysetclass.com",
    "password": "demo2026",
    "token": "jwt_token_here",
    "expires_in_hours": 24
}
```

- No email verification required
- Auto-generated unique email
- Same password for all demos: `demo2026`
- Expires in 24 hours
- Marked with `is_demo = TRUE`

---

## API Endpoints

### Authentication Endpoints

#### `POST /api/auth/login`
Login with email and password.

**Request:**
```json
{
    "email": "professor@university.edu",
    "password": "secure_password"
}
```

**Response:**
```json
{
    "token": "session_token_here",
    "user": {
        "id": 1,
        "email": "professor@university.edu",
        "role": "customer",
        "full_name": "Dr. Smith",
        "is_demo": false,
        "demo_expires_at": null
    }
}
```

#### `POST /api/auth/logout`
Logout current user (invalidate session).

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "message": "Logged out successfully"
}
```

#### `GET /api/auth/me`
Get current user info.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "user_id": 1,
    "email": "professor@university.edu",
    "role": "customer",
    "is_demo": false
}
```

---

### Canvas Integration Endpoints

#### `POST /api/v2/canvas/connect`
Connect user's Canvas account (saves encrypted credentials).

**Request:**
```json
{
    "canvas_url": "https://university.instructure.com",
    "access_token": "your_canvas_api_token"
}
```

**Response:**
```json
{
    "status": "connected",
    "canvas_url": "https://university.instructure.com",
    "user_name": "Dr. Smith"
}
```

**Note:** Token is encrypted using Fernet encryption before storage.

#### `GET /api/v2/canvas/courses`
Fetch all Canvas courses for the authenticated user.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "courses": [
        {
            "id": 6355,
            "name": "Introduction to Psychology",
            "course_code": "PSY101",
            "total_students": 45
        }
    ],
    "total": 1
}
```

---

### Quiz Generation Endpoints

#### `POST /api/v2/canvas/quiz/generate`
Generate quiz questions with AI (preview only, no Canvas upload).

**Request:**
```json
{
    "topic": "Classical Conditioning",
    "description": "Quiz covering Pavlov's experiments, UCS/UCR/CS/CR, extinction, and spontaneous recovery",
    "num_questions": 10,
    "difficulty": "medium",
    "grade_level": "college",
    "language": "en"
}
```

**Parameters:**
- `topic` (required): Quiz topic/title
- `description` (required): Detailed description of what to cover
- `num_questions` (default: 10): Number of questions
- `difficulty` (default: "medium"): easy, medium, or hard
- `grade_level` (default: "college"): elementary-k2, elementary-35, middle-68, high-912, college
- `language` (default: "en"): en, es, fr, pt, ar, zh

**Response:**
```json
{
    "status": "success",
    "topic": "Classical Conditioning",
    "questions": [
        {
            "question_text": "What is classical conditioning?",
            "answers": [
                {"text": "A. Learning through association", "correct": true},
                {"text": "B. Learning through rewards", "correct": false},
                {"text": "C. Learning through observation", "correct": false},
                {"text": "D. Learning through trial and error", "correct": false}
            ]
        }
    ],
    "num_questions": 10,
    "message": "Quiz questions generated! Review and upload to Canvas."
}
```

#### `POST /api/v2/canvas/quiz/upload`
Upload generated quiz to Canvas.

**Request:**
```json
{
    "course_id": 6355,
    "topic": "Classical Conditioning",
    "questions": [...],
    "num_questions": 10,
    "due_date": "2026-03-15T23:59:59Z"
}
```

**Response:**
```json
{
    "status": "success",
    "quiz_id": 12345,
    "quiz_title": "Quiz: Classical Conditioning",
    "questions_added": 10,
    "preview_url": "https://university.instructure.com/courses/6355/quizzes/12345",
    "message": "Quiz uploaded to Canvas successfully!"
}
```

---

### Assignment Generation Endpoints

#### `POST /api/v2/canvas/generate-assignment`
Generate assignment description with AI.

**Request:**
```json
{
    "topic": "Research Paper on Cognitive Biases",
    "assignment_type": "research",
    "requirements": "5-7 pages, APA format, 5+ peer-reviewed sources, analyze 3 cognitive biases",
    "points": 100,
    "language": "en"
}
```

**Assignment Types:**
- `essay` - Essay or written paper
- `discussion` - Discussion post
- `project` - Project or presentation
- `research` - Research assignment
- `case_study` - Case study analysis
- `lab` - Lab or practical work
- `reflection` - Reflection assignment
- `group` - Group collaborative assignment
- `other` - General assignment

**Response:**
```json
{
    "status": "success",
    "generated_content": "<h3>Assignment Overview</h3><p>...</p>",
    "cost": 0.0234
}
```

#### `POST /api/v2/canvas/assignment`
Upload assignment to Canvas.

**Request:**
```json
{
    "course_id": 6355,
    "title": "Research Paper on Cognitive Biases",
    "description": "<html content from generate-assignment>",
    "points": 100,
    "due_date": "2026-04-01T23:59:59Z"
}
```

**Response:**
```json
{
    "status": "success",
    "assignment_id": 98765,
    "title": "Research Paper on Cognitive Biases",
    "points": 100,
    "preview_url": "https://university.instructure.com/courses/6355/assignments/98765",
    "message": "Assignment created successfully!"
}
```

---

### Page Generation Endpoints

#### `POST /api/v2/canvas/generate-page`
Generate course page content with AI.

**Request:**
```json
{
    "title": "Introduction to Neural Networks",
    "page_type": "tutorial",
    "description": "Overview of artificial neural networks, perceptrons, activation functions, and backpropagation",
    "objectives": "Students will understand basic neural network architecture and training",
    "language": "en"
}
```

**Page Types:**
- `overview` - Course or unit overview
- `resource_list` - Resource list with links
- `study_guide` - Study guide with key concepts
- `tutorial` - Tutorial or how-to guide
- `reading` - Reading material
- `reference` - Reference material
- `other` - General page

**Response:**
```json
{
    "status": "success",
    "generated_content": "<h3>Introduction</h3><p>...</p>",
    "cost": 0.0187
}
```

#### `POST /api/v2/canvas/page`
Upload page to Canvas.

**Request:**
```json
{
    "course_id": 6355,
    "title": "Introduction to Neural Networks",
    "content": "<html content from generate-page>"
}
```

**Response:**
```json
{
    "status": "success",
    "page_url": "introduction-to-neural-networks",
    "title": "Introduction to Neural Networks",
    "preview_url": "https://university.instructure.com/courses/6355/pages/introduction-to-neural-networks",
    "message": "Page created successfully!"
}
```

---

### Discussion & Announcement Endpoints

#### `POST /api/v2/canvas/generate-discussion`
Generate discussion topic with AI.

**Request:**
```json
{
    "topic": "Ethics in AI Development",
    "discussion_type": "debate",
    "goals": "Students will critically analyze ethical considerations in AI and form evidence-based arguments",
    "language": "en"
}
```

**Response:**
```json
{
    "status": "success",
    "generated_content": "<html>...",
    "cost": 0.0156
}
```

#### `POST /api/v2/canvas/discussion`
Create discussion topic in Canvas.

**Request:**
```json
{
    "course_id": 6355,
    "topic": "Ethics in AI Development",
    "prompt": "<html content from generate-discussion>"
}
```

**Response:**
```json
{
    "status": "success",
    "discussion_id": 45678,
    "title": "Ethics in AI Development",
    "preview_url": "https://university.instructure.com/courses/6355/discussion_topics/45678",
    "message": "Discussion created successfully!"
}
```

#### `POST /api/v2/canvas/announcement`
Create announcement in Canvas course.

**Request:**
```json
{
    "course_id": 6355,
    "topic": "Office Hours Update",
    "details": "Additional office hours available Thursday 3-5pm due to midterm",
    "language": "en"
}
```

**Response:**
```json
{
    "status": "success",
    "announcement_id": 23456,
    "title": "Office Hours Update",
    "preview_url": "https://university.instructure.com/courses/6355/discussion_topics/23456",
    "message": "Announcement posted successfully!"
}
```

---

### Syllabus Generation Endpoints

#### `POST /api/v2/canvas/generate-syllabus`
Generate complete course syllabus with AI.

**Request:**
```json
{
    "course_name": "Introduction to Machine Learning",
    "description": "Survey course covering supervised/unsupervised learning, neural networks, and practical applications",
    "objectives": "Master fundamental ML algorithms, implement models in Python, evaluate model performance",
    "grading": "Homework 40%, Midterm 25%, Final Project 35%",
    "language": "en"
}
```

**Response:**
```json
{
    "status": "success",
    "generated_content": "<h3>Course Overview</h3>...",
    "cost": 0.0456
}
```

#### `PUT /api/v2/canvas/syllabus`
Upload syllabus to Canvas course.

**Request:**
```json
{
    "course_id": 6355,
    "syllabus_body": "<html content from generate-syllabus>"
}
```

**Response:**
```json
{
    "status": "success",
    "course_id": 6355,
    "preview_url": "https://university.instructure.com/courses/6355/assignments/syllabus",
    "message": "Syllabus uploaded successfully!"
}
```

---

### Module Management Endpoints

#### `GET /api/v2/canvas/modules/{course_id}`
Get all modules in a course.

**Response:**
```json
{
    "modules": [
        {
            "id": 123,
            "name": "Week 1: Introduction",
            "position": 1
        }
    ],
    "total": 1
}
```

#### `POST /api/v2/canvas/module`
Create module in Canvas course.

**Request:**
```json
{
    "course_id": 6355,
    "name": "Week 2: Neural Networks",
    "position": 2
}
```

**Response:**
```json
{
    "status": "success",
    "module_id": 124,
    "name": "Week 2: Neural Networks",
    "preview_url": "https://university.instructure.com/courses/6355/modules",
    "message": "Module 'Week 2: Neural Networks' created successfully!"
}
```

---

### Grading Setup Endpoints (Q-Tip Agent)

These endpoints automate Canvas grading setup, reducing 45 minutes to 2 minutes.

#### `GET /api/grading/templates`
Get all available grading templates by subject.

**Response:**
```json
{
    "templates": {
        "Mass Communications": [
            {"name": "Quizzes", "weight": 30, "rules": {"drop_lowest": {"enabled": true, "count": 1}}},
            {"name": "Assignments", "weight": 40, "rules": {}},
            {"name": "Exams", "weight": 30, "rules": {}}
        ],
        "Mathematics": [...],
        "Science": [...]
    }
}
```

#### `GET /api/grading/template/{subject}`
Get template for specific subject.

**Example:** `GET /api/grading/template/Mathematics`

**Response:**
```json
{
    "subject": "Mathematics",
    "categories": [
        {"name": "Homework", "weight": 25, "rules": {"drop_lowest": {"enabled": true, "count": 2}}},
        {"name": "Quizzes", "weight": 25, "rules": {"drop_lowest": {"enabled": true, "count": 1}}},
        {"name": "Tests", "weight": 40, "rules": {}},
        {"name": "Final Exam", "weight": 10, "rules": {}}
    ]
}
```

#### `POST /api/grading/setup`
Complete grading setup for a Canvas course.

**Request:**
```json
{
    "course_id": 6355,
    "grading_method": "weighted",
    "categories": [
        {"name": "Quizzes", "weight": 30, "rules": {"drop_lowest": {"enabled": true, "count": 1}}},
        {"name": "Assignments", "weight": 40, "rules": {}},
        {"name": "Exams", "weight": 30, "rules": {}}
    ],
    "global_rules": {
        "late_penalty": {"enabled": true, "percent_per_day": 10}
    }
}
```

**Response:**
```json
{
    "status": "success",
    "groups_created": 3,
    "weighted_grading_enabled": true,
    "assignment_groups": [
        {"id": 101, "name": "Quizzes", "group_weight": 30},
        {"id": 102, "name": "Assignments", "group_weight": 40},
        {"id": 103, "name": "Exams", "group_weight": 30}
    ],
    "verification": {...}
}
```

#### `GET /api/grading/analyze/{course_id}`
Analyze existing Canvas grading setup and detect issues.

**Response:**
```json
{
    "has_groups": true,
    "groups": [...],
    "weighted_grading_enabled": true,
    "total_weight": 97,
    "orphan_assignments": 3,
    "issues": [
        "Weights don't add to 100% (currently 97%)",
        "3 assignments not in any category"
    ],
    "suggestions": [
        "Adjust weights to total 100%",
        "Assign orphaned assignments to categories"
    ],
    "health": "needs_attention"
}
```

**Health Values:**
- `healthy` - No issues found
- `needs_attention` - Minor issues that should be fixed
- `critical` - Major issues preventing proper grading

#### `POST /api/grading/fix`
Automatically fix common grading setup issues.

**Request:**
```json
{
    "course_id": 6355,
    "fix_type": "auto"
}
```

**Fix Types:**
- `auto` - Fix issues while preserving structure (adjust weights, enable weighted grading)
- `reset` - Delete all groups and start fresh

**Response:**
```json
{
    "status": "success",
    "message": "Grading setup fixed automatically",
    "groups_adjusted": 3
}
```

---

### AI Grading Endpoints

AI-powered grading workflow for assignments.

#### `POST /api/ai-grading/sessions/start`
Start AI grading session for an assignment.

**Request:**
```json
{
    "course_id": "6355",
    "assignment_id": "98765",
    "rubric": {
        "criteria": [
            {"name": "Thesis Statement", "points": 20, "description": "Clear and arguable thesis"},
            {"name": "Evidence", "points": 30, "description": "Supporting evidence from sources"},
            {"name": "Analysis", "points": 30, "description": "Critical analysis and interpretation"},
            {"name": "Writing Quality", "points": 20, "description": "Grammar, clarity, organization"}
        ]
    },
    "preferences": {
        "strictness": "medium",
        "check_plagiarism": true,
        "check_ai_generated": true
    }
}
```

**Response:**
```json
{
    "session_id": 42,
    "total_submissions": 45,
    "status": "started",
    "assignment_title": "Research Paper on Cognitive Biases"
}
```

#### `GET /api/ai-grading/sessions/{session_id}/status`
Check grading progress (for progress bar UI).

**Response:**
```json
{
    "session_id": 42,
    "status": "in_progress",
    "total_submissions": 45,
    "graded_count": 23,
    "progress_percent": 51.11
}
```

**Status Values:**
- `in_progress` - AI is currently grading
- `completed` - All submissions graded
- `posted` - Grades posted to Canvas
- `error` - Error occurred during grading

#### `GET /api/ai-grading/sessions/{session_id}/grades`
Get all grades for review.

**Response:**
```json
{
    "session": {
        "id": 42,
        "status": "completed",
        "assignment_title": "Research Paper on Cognitive Biases",
        "average_score": 82.5,
        "total_submissions": 45,
        "flagged_count": 3,
        "reviewed_count": 0
    },
    "grades": [
        {
            "id": 1,
            "student_id": "12345",
            "student_name": "John Smith",
            "submission_id": "67890",
            "ai_total_score": 85,
            "ai_rubric_scores": {
                "Thesis Statement": 18,
                "Evidence": 27,
                "Analysis": 25,
                "Writing Quality": 15
            },
            "ai_feedback": "Strong thesis and good use of evidence. Analysis could be deeper. Watch for grammatical errors.",
            "ai_criterion_feedback": {
                "Thesis Statement": "Clear and specific thesis...",
                "Evidence": "Good variety of sources...",
                "Analysis": "More critical analysis needed...",
                "Writing Quality": "Several run-on sentences..."
            },
            "ai_confidence": "high",
            "ai_flags": [],
            "reviewed": false,
            "final_score": null,
            "final_feedback": null
        }
    ]
}
```

**AI Confidence Levels:**
- `high` - AI is confident in the grade (clear rubric match)
- `medium` - Some uncertainty (subjective criteria)
- `low` - Low confidence (ambiguous submission or unclear rubric)

**AI Flags:**
- `plagiarism_suspected` - Potential plagiarism detected
- `ai_generated_suspected` - May be AI-generated text
- `incomplete` - Submission appears incomplete
- `off_topic` - Doesn't address assignment prompt
- `requires_manual_review` - AI recommends professor review

#### `PUT /api/ai-grading/grades/{grade_id}/review`
Review and adjust AI-generated grade.

**Request:**
```json
{
    "final_score": 88,
    "final_feedback": "Excellent work! I bumped up the analysis score.",
    "adjustments": {
        "Analysis": 28
    }
}
```

**Response:**
```json
{
    "status": "success",
    "grade_id": 1,
    "final_score": 88,
    "reviewed": true
}
```

#### `POST /api/ai-grading/sessions/{session_id}/post`
Post reviewed grades to Canvas.

**Response:**
```json
{
    "status": "success",
    "posted_count": 45,
    "message": "All grades posted to Canvas successfully"
}
```

---

### Reference Materials Endpoints

Upload syllabi/documents for AI to match writing style.

#### `POST /api/v2/reference-materials/upload`
Upload reference document (PDF, DOCX, TXT).

**Request:** `multipart/form-data`
- `file`: File upload
- `course_name` (optional): Which course this is for

**Response:**
```json
{
    "status": "success",
    "message": "Reference material uploaded successfully",
    "material_id": 5,
    "file_name": "syllabus.pdf",
    "extracted_length": 5432,
    "course_name": "Introduction to Psychology"
}
```

#### `GET /api/v2/reference-materials`
Get all uploaded reference materials.

**Response:**
```json
{
    "status": "success",
    "materials": [
        {
            "id": 5,
            "file_name": "syllabus.pdf",
            "file_type": "pdf",
            "course_name": "Introduction to Psychology",
            "upload_date": "2026-02-15T10:30:00",
            "text_length": 5432
        }
    ],
    "total": 1
}
```

#### `DELETE /api/v2/reference-materials/{material_id}`
Delete reference material.

**Response:**
```json
{
    "status": "success",
    "message": "Deleted syllabus.pdf"
}
```

---

### Subscription & Billing Endpoints (Stripe)

#### `GET /api/subscription/status`
Get current subscription status.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "tier": "pro",
    "status": "active",
    "trial_ends_at": null,
    "subscription_ends_at": "2027-02-15T00:00:00Z",
    "ai_generations_used": 156,
    "has_active_subscription": true,
    "stripe_subscription_id": "sub_abc123"
}
```

#### `POST /api/stripe/create-checkout`
Create Stripe checkout session for subscription.

**Request:**
```json
{
    "price_id": "price_abc123",
    "success_url": "https://readysetclass.app/success",
    "cancel_url": "https://readysetclass.app/pricing"
}
```

**Response:**
```json
{
    "checkout_url": "https://checkout.stripe.com/..."
}
```

#### `POST /api/subscription/cancel`
Cancel subscription.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "status": "canceled"
}
```

#### `POST /api/billing/customer-portal`
Get Stripe Customer Portal URL for subscription management.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "url": "https://billing.stripe.com/..."
}
```

#### `POST /api/stripe/webhook`
Stripe webhook endpoint (handles subscription events).

**Events Handled:**
- `checkout.session.completed` - Subscription activated
- `customer.subscription.updated` - Subscription status changed
- `customer.subscription.deleted` - Subscription canceled

---

### Admin Endpoints

#### `GET /api/admin/users`
Get all users (admin only).

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
    "users": [
        {
            "id": 1,
            "email": "professor@university.edu",
            "full_name": "Dr. Smith",
            "role": "customer",
            "institution": "State University",
            "is_active": true,
            "is_demo": false,
            "demo_expires_at": null,
            "created_at": "2026-01-15T10:00:00Z",
            "last_active_at": "2026-02-15T14:30:00Z"
        }
    ]
}
```

#### `PATCH /api/admin/users/{user_id}/role`
Update user role (admin only).

**Request:**
```json
{
    "role": "admin"
}
```

**Response:**
```json
{
    "message": "Updated role to admin",
    "email": "professor@university.edu"
}
```

#### `PATCH /api/admin/users/{user_id}/status`
Enable/disable user account (admin only).

**Request:**
```json
{
    "is_active": false
}
```

**Response:**
```json
{
    "message": "Account disabled",
    "email": "professor@university.edu"
}
```

#### `GET /api/admin/stats`
Get system statistics (admin only).

**Response:**
```json
{
    "total_users": 1234,
    "active_users_7d": 456,
    "demo_accounts": 89,
    "active_sessions": 123,
    "canvas_connected": 890,
    "content_created": 5678
}
```

#### `GET /api/admin/analytics`
Get usage analytics (admin only).

**Response:**
```json
{
    "top_features": [
        {"feature": "quiz_generator", "count": 1234},
        {"feature": "assignment_generator", "count": 890}
    ],
    "avg_session_duration_seconds": 1280,
    "daily_active_users": [
        {"date": "2026-02-15", "users": 45},
        {"date": "2026-02-14", "users": 52}
    ],
    "total_events": 45678
}
```

---

### Analytics & Tracking Endpoints

#### `POST /api/analytics/track`
Track user activity and feature usage.

**Request:**
```json
{
    "event_type": "feature_used",
    "feature": "quiz_generator",
    "duration": 120,
    "metadata": {
        "questions": 10,
        "difficulty": "medium"
    }
}
```

**Response:**
```json
{
    "status": "tracked"
}
```

#### `POST /api/feedback`
Submit user feedback.

**Request:**
```json
{
    "message": "Love the quiz generator! Would be great to have more templates."
}
```

**Response:**
```json
{
    "message": "Thank you for your feedback!"
}
```

---

### Demo & Testing Endpoints

#### `POST /api/demo/create`
Create temporary demo account (no auth required).

**Response:**
```json
{
    "email": "demo-abc123@readysetclass.com",
    "password": "demo2026",
    "token": "session_token_here",
    "expires_in_hours": 24
}
```

#### `DELETE /api/demo/cleanup`
Delete expired demo accounts (admin only).

**Response:**
```json
{
    "deleted_count": 15,
    "message": "Cleaned up 15 expired demo accounts"
}
```

---

### Health & Status Endpoints

#### `GET /`
API root endpoint.

**Response:**
```json
{
    "service": "ReadySetClass API",
    "version": "2.0.0",
    "status": "operational",
    "tagline": "Less time setting up. More time teaching."
}
```

#### `GET /api/health`
Health check endpoint.

**Response:**
```json
{
    "status": "healthy",
    "timestamp": "2026-02-15T15:30:00Z",
    "bonita": "online"
}
```

---

## AI Integration (The Agents)

ReadySetClass uses multiple AI providers with automatic fallback:

### Provider Priority

1. **OpenAI (GPT-4o-mini)** - Preferred (cheap, $0.002/assignment)
2. **Groq (Llama 3.3 70B)** - Secondary (FREE!)
3. **Anthropic Claude (Sonnet 4)** - Fallback (expensive, $0.05/assignment)

### The AI Agents

ReadySetClass uses specialized AI agents for different tasks:

#### **Bonita** - Main Content Generator
- **Provider:** OpenAI → Groq → Anthropic (automatic fallback)
- **Purpose:** Generate all course content
- **Tasks:**
  - Quiz questions (10 questions ~$0.002)
  - Assignment descriptions
  - Syllabus generation
  - Discussion topics
  - Announcements
  - Course pages
  - Study materials
- **Features:**
  - Multi-language support (EN, ES, FR, PT, AR, ZH)
  - Grade-level appropriate content (K-2 through College)
  - Lexile reading level matching
  - Style matching from uploaded syllabi
- **Cost Tracking:** Tracks cost per content type

#### **Q-Tip** - Grading Setup Agent
- **Provider:** Internal (Canvas API automation)
- **Purpose:** Automate grading configuration
- **Tasks:**
  - Create assignment groups
  - Enable weighted grading
  - Apply drop rules
  - Set late penalties
  - Analyze existing setups
  - Fix configuration issues
- **Impact:** Reduces 45-minute setup to 2 minutes
- **Strategy by:** Sunni
- **Implementation by:** Q-Tip

#### **Jarobi** - AI Grading Agent
- **Provider:** OpenAI/Anthropic
- **Purpose:** Grade student submissions with AI
- **Tasks:**
  - Read student submissions
  - Apply rubric criteria
  - Generate scores and feedback
  - Flag suspicious content (plagiarism, AI-generated)
  - Provide confidence ratings
- **Features:**
  - Batch grading (parallel processing)
  - Criterion-level feedback
  - Professor review workflow
  - Analytics tracking
- **Flags:**
  - `plagiarism_suspected`
  - `ai_generated_suspected`
  - `incomplete`
  - `off_topic`
  - `requires_manual_review`

#### **Phife** - Student Edition Agent (Future)
- **Status:** Not yet implemented
- **Purpose:** Student-facing features
- **Planned Tasks:**
  - Study guide generation
  - Quiz practice
  - Assignment help (NOT doing homework)
  - Concept explanations
  - Resource recommendations

### BonitaEngine Class

```python
class BonitaEngine:
    """
    Smart AI routing for ReadySetClass
    Supports: OpenAI (cheap), Groq (FREE!), Anthropic (premium)
    Provider priority: OpenAI > Groq > Anthropic
    """

    def call_ai(self, prompt: str, system: str = "") -> tuple[str, float]:
        """
        Call AI provider with automatic fallback
        Returns: (response_text, cost)
        """
        # Try OpenAI first
        # Try Groq second
        # Fallback to Claude

    def generate_quiz(self, week, topic, description, num_questions, difficulty, grade_level, language):
        """Generate quiz questions with difficulty distribution"""

    def generate_syllabus(self, course_data):
        """Generate complete course syllabus"""

    def generate_lesson_plan(self, week, topic, objectives):
        """Generate lesson plan for week"""

    def generate_study_pack(self, week, topic):
        """Generate study materials with real resources"""
```

### Grade Level Support

Bonita generates content appropriate for different grade levels:

| Grade Level | Code | Lexile Range | Description |
|-------------|------|--------------|-------------|
| K-2 | `elementary-k2` | 0-300 | Beginning reader, very simple words |
| 3-5 | `elementary-35` | 300-700 | Elementary, clear language |
| 6-8 | `middle-68` | 700-1000 | Middle school, age-appropriate |
| 9-12 | `high-912` | 1000-1300 | High school, college-prep |
| College | `college` | 1300+ | University level, academic |

### Language Support

All content can be generated in:
- `en` - English
- `es` - Spanish
- `fr` - French
- `pt` - Portuguese
- `ar` - Arabic
- `zh` - Chinese

### AI Cost Tracking

The `BonitaEngine` tracks costs by content type:

```python
cost_tracker = {
    "syllabus": 0.0,
    "lesson_plans": 0.0,
    "quizzes": 0.0,
    "study_packs": 0.0,
    "total": 0.0
}
```

### AI Prompt Engineering

Bonita uses structured prompts with:
1. **System message:** Defines Bonita's role and output format
2. **User prompt:** Detailed instructions with examples
3. **Context injection:** Grade level, language, reading level
4. **Style matching:** Uses uploaded reference materials

Example prompt structure:
```
System: You are Bonita, helping professors create [content type].
        Your output should be [format requirements].

User:   Create a [content type] for [grade level] students.

        IMPORTANT: Generate ALL content in [language].

        [Grade level requirements]
        [Lexile level instructions]

        [Detailed requirements]

        Format as [HTML/JSON/etc.]
```

---

## Data Models

### Pydantic Models (Request/Response)

#### Authentication Models

```python
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    token: str
    user: Dict[str, Any]
```

#### Canvas Connection Models

```python
class CanvasConnectionRequest(BaseModel):
    canvas_url: str
    access_token: str
```

#### Quiz Models

```python
class QuizGenerateRequest(BaseModel):
    topic: str
    description: str
    num_questions: int = 10
    difficulty: str = "medium"      # easy, medium, hard
    grade_level: str = "college"    # elementary-k2, elementary-35, middle-68, high-912, college
    language: str = "en"            # en, es, fr, pt, ar, zh

class QuizUploadRequest(BaseModel):
    course_id: int
    topic: str
    questions: list
    num_questions: int
    due_date: Optional[str] = None
```

#### Assignment Models

```python
class AIAssignmentRequest(BaseModel):
    topic: str
    assignment_type: str            # essay, discussion, project, research, etc.
    requirements: str
    points: int = 100
    language: str = "en"

class AssignmentRequest(BaseModel):
    course_id: int
    title: str
    description: str
    points: int = 100
    due_date: Optional[str] = None
```

#### Page Models

```python
class AIPageRequest(BaseModel):
    title: str
    page_type: str                  # overview, resource_list, study_guide, tutorial, etc.
    description: str
    objectives: Optional[str] = None
    language: str = "en"

class PageRequest(BaseModel):
    course_id: int
    title: str
    content: str
```

#### Discussion Models

```python
class AIDiscussionRequest(BaseModel):
    topic: str
    discussion_type: str
    goals: str
    language: str = "en"

class DiscussionRequest(BaseModel):
    course_id: int
    topic: str
    prompt: str
```

#### Syllabus Models

```python
class AISyllabusRequest(BaseModel):
    course_name: str
    description: str
    objectives: str
    grading: str
    language: str = "en"

class SyllabusRequest(BaseModel):
    course_id: int
    syllabus_body: str
```

#### Grading Setup Models

```python
class GradingCategory(BaseModel):
    name: str
    weight: float
    rules: Optional[Dict[str, Any]] = {}

class GradingSetupRequest(BaseModel):
    course_id: int
    grading_method: str             # "total_points" or "weighted"
    categories: List[GradingCategory]
    global_rules: Optional[Dict[str, Any]] = None

class GradingFixRequest(BaseModel):
    course_id: int
    fix_type: str = "auto"          # "auto" or "reset"
```

#### AI Grading Models

```python
class StartGradingRequest(BaseModel):
    course_id: str
    assignment_id: str
    rubric: Dict
    preferences: Optional[Dict] = {}

class ReviewGradeRequest(BaseModel):
    final_score: float
    final_feedback: Optional[str] = None
    adjustments: Optional[Dict] = None

class SessionStatusResponse(BaseModel):
    session_id: int
    status: str
    total_submissions: int
    graded_count: int
    progress_percent: float
```

---

## Canvas LMS Integration

### Canvas API Client

```python
class CanvasClient:
    """Canvas LMS API integration"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # Course methods
    def get_user_courses(self) -> List[Dict]

    # Quiz methods
    def create_quiz(self, course_id: int, quiz_data: Dict) -> int
    def add_quiz_question(self, course_id: int, quiz_id: int, question_data: Dict)

    # Assignment methods
    def create_assignment(self, course_id: int, assignment_data: Dict) -> Dict
    def get_assignment_submissions(self, course_id: str, assignment_id: str) -> List[Dict]

    # Page methods
    def create_page(self, course_id: int, page_data: Dict) -> Dict

    # Module methods
    def get_modules(self, course_id: int) -> List[Dict]
    def create_module(self, course_id: int, module_data: Dict) -> Dict

    # Discussion methods
    def create_discussion(self, course_id: int, discussion_data: Dict) -> Dict
    def create_announcement(self, course_id: int, announcement_data: Dict) -> Dict

    # Syllabus methods
    def update_syllabus(self, course_id: int, syllabus_body: str) -> Dict
```

### Canvas Authentication

```python
class CanvasAuth:
    """Canvas authentication and token validation"""

    def __init__(self, canvas_url: str, access_token: str):
        self.canvas_url = canvas_url
        self.access_token = access_token

    def test_connection(self) -> tuple[bool, Optional[Dict], Optional[str]]:
        """Test Canvas connection and credentials"""
        # Returns: (success, user_data, error_message)

# Token encryption/decryption
def encrypt_token(token: str) -> str:
    """Encrypt Canvas token with Fernet"""

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt Canvas token"""
```

### Canvas Rate Limiting

Canvas API has rate limits:
- Default: 3000 requests per hour per user
- Burst: 100 requests per 10 seconds

The backend does not currently implement rate limiting. For production, add rate limiting middleware.

---

## Payment Integration (Stripe)

### Subscription Tiers

| Tier | Price | Features |
|------|-------|----------|
| **Trial** | Free | 14 days, 50 AI generations, email support |
| **Pro** | $29/mo | Unlimited AI, priority support, all grade levels |
| **Team** | $99/mo | Up to 10 users, team sharing, admin dashboard |
| **Enterprise** | $299/mo | Unlimited users, custom branding, dedicated support |

### Stripe Integration Flow

1. **User selects plan** → Frontend calls `/api/stripe/create-checkout`
2. **Backend creates Stripe customer** (if first time)
3. **Backend creates checkout session** → Returns Stripe checkout URL
4. **User completes payment** on Stripe
5. **Stripe sends webhook** → Backend receives `checkout.session.completed`
6. **Backend updates user subscription** in database
7. **User redirected** to success page

### Webhook Events

The backend handles these Stripe webhook events:

```python
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Handle Stripe webhook events

    Events:
    - checkout.session.completed → Activate subscription
    - customer.subscription.updated → Update subscription status
    - customer.subscription.deleted → Cancel subscription
    """
```

### Subscription Status Values

- `trialing` - In free trial period
- `active` - Paid and active
- `past_due` - Payment failed
- `canceled` - User canceled

### Customer Portal

Users can manage subscriptions via Stripe Customer Portal:

```python
POST /api/billing/customer-portal

# Returns Stripe-hosted page where users can:
# - View subscription details
# - Update payment method
# - Cancel subscription
# - View invoices
```

---

## Getting Started for Phife (Student Edition)

### Key Files to Review

1. **`backend/main.py`** - All API endpoints (3,264 lines)
2. **`backend/database.py`** - Database models
3. **`backend/canvas_client.py`** - Canvas API integration
4. **`backend/canvas_auth.py`** - Canvas authentication
5. **`backend/grading_setup.py`** - Grading automation (Q-Tip)
6. **`backend/routes_ai_grading.py`** - AI grading endpoints (Jarobi)
7. **`backend/ai_grading/grading_engine.py`** - AI grading logic

### Student Edition Recommendations

For building the student edition, consider:

1. **Separate Database Tables**
   - `student_users` - Student accounts (separate from professor accounts)
   - `student_courses` - Courses students are enrolled in
   - `student_study_sessions` - Track study activity
   - `student_quiz_practice` - Practice quiz results

2. **New API Endpoints**
   - `POST /api/student/auth/register` - Student registration
   - `GET /api/student/courses` - Get enrolled courses
   - `POST /api/student/practice-quiz` - Generate practice quiz
   - `POST /api/student/study-guide` - Generate study guide
   - `GET /api/student/progress` - Get study progress

3. **Phife Agent Features**
   - Generate practice questions from course materials
   - Create personalized study guides
   - Explain difficult concepts
   - Recommend study strategies
   - Track learning progress

4. **Important Distinctions**
   - Students should NOT be able to:
     - Access professor accounts or data
     - Create/modify Canvas assignments
     - See other students' grades
     - Generate assignment solutions (academic integrity!)
   - Students SHOULD be able to:
     - Practice with sample questions
     - Get concept explanations
     - Create study materials
     - Track their own progress

5. **Reusable Components**
   - Authentication system (modify for students)
   - BonitaEngine (adjust prompts for student-focused content)
   - Canvas integration (read-only access for students)
   - Database infrastructure

### Example Student Endpoints

```python
# Generate practice quiz from course content
POST /api/student/practice-quiz
{
    "topic": "Classical Conditioning",
    "difficulty": "medium",
    "num_questions": 10,
    "show_explanations": true
}

# Get concept explanation
POST /api/student/explain
{
    "concept": "Neural Networks",
    "detail_level": "intermediate"
}

# Generate study guide
POST /api/student/study-guide
{
    "topics": ["Pavlov's Dogs", "Operant Conditioning", "Classical Conditioning"],
    "format": "outline"
}
```

### Security Considerations

- Students should have separate authentication (student role)
- No access to professor features
- Rate limiting for AI generations (prevent abuse)
- Content filtering (ensure appropriate responses)

---

## Quick Reference

### Most Used Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/auth/login` | User login |
| `POST /api/v2/canvas/connect` | Connect Canvas |
| `GET /api/v2/canvas/courses` | Get courses |
| `POST /api/v2/canvas/quiz/generate` | Generate quiz |
| `POST /api/v2/canvas/quiz/upload` | Upload quiz |
| `POST /api/v2/canvas/generate-assignment` | Generate assignment |
| `POST /api/grading/setup` | Setup grading |
| `POST /api/ai-grading/sessions/start` | Start AI grading |

### Environment Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
export DATABASE_URL="postgresql://..."
export OPENAI_API_KEY="sk-..."
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export STRIPE_SECRET_KEY="sk_test_..."

# 3. Run migrations
python backend/run_migration.py

# 4. Start server
cd backend
python main.py

# Server runs on http://localhost:8000
```

### Database Connection String

```python
DATABASE_URL = os.getenv("DATABASE_URL")

# Railway uses postgres://, convert to postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
```

### CORS Configuration

Allowed origins:
- `https://www.readysetclass.app`
- `https://readysetclass.app`
- `https://www.readysetclass.com`
- `https://readysetclass.com`
- `http://localhost:3000`
- `http://localhost:5173`
- `*` (development only)

---

## Support & Contact

- **Documentation:** This file
- **Source Code:** `/Users/crisjon/conductor/workspaces/facultyflow/davis/backend/`
- **API Base URL (Production):** `https://api.readysetclass.app`
- **API Base URL (Development):** `http://localhost:8000`

---

**Built with care by Claude (Sonnet 4.5) for CJ**
**ReadySetClass™ - Less time setting up. More time teaching.**
