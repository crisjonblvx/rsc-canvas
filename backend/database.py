"""
Database Models for ReadySetClass v2.0
SQLAlchemy models for Canvas integration

Built for: ReadySetClass v2.0
Database: PostgreSQL (Railway)
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # Railway uses postgres://, but SQLAlchemy needs postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL) if DATABASE_URL else None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None
Base = declarative_base()


class CanvasCredentials(Base):
    """
    Stores encrypted Canvas API credentials for each user
    """
    __tablename__ = "canvas_credentials"

    user_id = Column(Integer, primary_key=True, index=True)
    canvas_url = Column(String(255), nullable=False)
    access_token_encrypted = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_verified = Column(DateTime, default=datetime.utcnow)

    # Relationship to courses
    courses = relationship("UserCourse", back_populates="canvas_account")


class UserCourse(Base):
    """
    Stores cached course data from Canvas
    """
    __tablename__ = "user_courses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("canvas_credentials.user_id"), nullable=False)
    course_id = Column(Integer, nullable=False)  # Canvas course ID
    course_name = Column(String(255), nullable=False)
    course_code = Column(String(100))
    total_students = Column(Integer)
    synced_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to canvas credentials
    canvas_account = relationship("CanvasCredentials", back_populates="courses")


class ReferenceMaterial(Base):
    """
    Stores uploaded reference materials (syllabi, documents)
    Used to train AI to match professor's style
    """
    __tablename__ = "reference_materials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)  # Will link to users table later
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)  # pdf, docx, txt
    extracted_text = Column(Text, nullable=False)
    course_name = Column(String(255))  # Optional: which course this is for
    upload_date = Column(DateTime, default=datetime.utcnow)


class AIGradingSession(Base):
    """
    Tracks AI grading sessions
    One session = grading all submissions for one assignment
    """
    __tablename__ = "ai_grading_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)  # Professor who started grading
    course_id = Column(String(50), nullable=False)  # Canvas course ID
    assignment_id = Column(String(50), nullable=False)  # Canvas assignment ID
    assignment_title = Column(String(255))

    # Session configuration
    rubric = Column(JSON)  # The rubric used for grading
    preferences = Column(JSON)  # Grading preferences (strictness, flags, etc.)

    # Status tracking
    status = Column(String(50), default="in_progress")  # in_progress, completed, posted
    total_submissions = Column(Integer, default=0)
    graded_count = Column(Integer, default=0)
    reviewed_count = Column(Integer, default=0)
    posted_count = Column(Integer, default=0)

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    posted_at = Column(DateTime)

    # Metadata
    average_score = Column(Float)
    average_confidence = Column(Float)
    flagged_count = Column(Integer, default=0)

    # Relationship to individual grades
    grades = relationship("AIGrade", back_populates="session")


class AIGrade(Base):
    """
    Individual AI-generated grade for one student submission
    """
    __tablename__ = "ai_grades"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("ai_grading_sessions.id"), nullable=False)

    # Student info
    student_id = Column(String(50), nullable=False)  # Canvas user ID
    student_name = Column(String(255))
    submission_id = Column(String(50), nullable=False)  # Canvas submission ID
    submission_text = Column(Text)
    submitted_at = Column(DateTime)

    # AI Grading Results
    ai_total_score = Column(Float)
    ai_rubric_scores = Column(JSON)  # Score for each rubric criterion
    ai_feedback = Column(Text)  # Overall feedback
    ai_criterion_feedback = Column(JSON)  # Feedback for each criterion
    ai_confidence = Column(String(20))  # 'high', 'medium', 'low'
    ai_flags = Column(JSON)  # Array of flags (plagiarism, ai-generated, etc.)

    # Professor Review
    reviewed = Column(Boolean, default=False)
    reviewed_at = Column(DateTime)
    final_score = Column(Float)  # May differ from ai_total_score after review
    final_feedback = Column(Text)  # May be edited
    professor_adjustments = Column(JSON)  # What professor changed

    # Canvas Posting
    posted_to_canvas = Column(Boolean, default=False)
    posted_at = Column(DateTime)
    canvas_grade_id = Column(String(50))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to session
    session = relationship("AIGradingSession", back_populates="grades")


class AIGradingAnalytics(Base):
    """
    Analytics for improving AI grading over time
    Tracks accuracy, time savings, user satisfaction
    """
    __tablename__ = "ai_grading_analytics"

    id = Column(Integer, primary_key=True, index=True)
    grade_id = Column(Integer, ForeignKey("ai_grades.id"))

    # Accuracy tracking
    ai_score = Column(Float)
    final_score = Column(Float)
    score_difference = Column(Float)  # How much professor adjusted

    # Feedback quality
    feedback_edited = Column(Boolean, default=False)
    feedback_regenerated = Column(Boolean, default=False)

    # Time savings
    estimated_manual_time = Column(Integer)  # Minutes
    actual_review_time = Column(Integer)  # Minutes
    time_saved = Column(Integer)  # Minutes

    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    """
    Initialize database tables
    Call this on app startup
    """
    if engine:
        Base.metadata.create_all(bind=engine)

        # Create referral/affiliate tables using raw SQL
        import psycopg2
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        try:
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS referral_codes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    code VARCHAR(50) UNIQUE NOT NULL,
                    tier VARCHAR(20) DEFAULT 'ambassador',
                    commission_rate DECIMAL(5,2) DEFAULT 15.00,
                    total_referrals INTEGER DEFAULT 0,
                    successful_referrals INTEGER DEFAULT 0,
                    total_earnings DECIMAL(10,2) DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    referrer_id INTEGER REFERENCES users(id),
                    referred_user_id INTEGER REFERENCES users(id),
                    referral_code VARCHAR(50),
                    status VARCHAR(20) DEFAULT 'pending',
                    converted_at TIMESTAMP,
                    commission_amount DECIMAL(10,2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by VARCHAR(50)")
            cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(50)")

            conn.commit()
            cursor.close()
            conn.close()
            print("✅ Referral tables created")
        except Exception as e:
            print(f"⚠️  Referral table creation error: {e}")

        print("✅ Database tables created")
    else:
        print("⚠️  No DATABASE_URL - running without database")


def get_db():
    """
    Get database session
    Use as dependency in FastAPI endpoints
    """
    if SessionLocal:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    else:
        yield None
