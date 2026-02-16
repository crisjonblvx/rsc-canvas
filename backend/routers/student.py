"""
ReadySetClass Student API Router (Phife)
Class Code system — No Canvas for students

Student Endpoints:
  POST /api/auth/register              - Student registration (.edu)
  POST /api/auth/student/login         - Student login
  POST /api/class-codes/join           - Join class with RSC-XXXX code
  GET  /api/v1/student/courses         - List enrolled courses
  DELETE /api/v1/student/courses/{id}  - Drop a course
  GET  /api/v1/student/courses/{id}/announcements - Get announcements
  GET  /api/v1/student/dashboard       - Dashboard (deadlines + announcements)
  GET  /api/v1/student/dashboard/deadlines - Deadline dashboard (urgency groups)
  POST /api/v1/student/grades          - Save a grade entry
  GET  /api/v1/student/grades/{id}     - Get grades for a course
  PUT  /api/v1/student/grades/{id}     - Update a grade
  DELETE /api/v1/student/grades/{id}   - Delete a grade
  GET  /api/v1/student/grades/{id}/calculator - Grade calculator + scenarios
  GET  /api/v1/student/calendar/export    - Export deadlines as .ics file
  GET  /api/v1/student/calendar/subscribe - Calendar export instructions
  GET  /api/v1/student/notifications/preferences - Get notification prefs
  PUT  /api/v1/student/notifications/preferences/{id} - Update notification prefs

Professor Endpoints (for Q-tip coordination):
  POST /api/v1/professor/courses       - Create a course
  POST /api/class-codes/generate       - Generate RSC-XXXX code
  GET  /api/class-codes/{course_id}    - View codes for a course
  PUT  /api/class-codes/{code_id}      - Update/deactivate code
  POST /api/v1/professor/announcements - Create announcement
  POST /api/v1/professor/deadlines     - Create deadline
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import secrets
import bcrypt
import json
import stripe
import random
import os
import psycopg2


# ============================================================================
# ROUTER SETUP
# ============================================================================

STUDENT_APP_VERSION = "3.0.0"

router = APIRouter()
security = HTTPBearer()

# Unambiguous character set for class codes (no O/0/I/1/L)
CLASS_CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


# ============================================================================
# DATABASE
# ============================================================================

def get_db_connection():
    """Get direct database connection"""
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# AI CLIENT (GROQ)
# ============================================================================

groq_student_client = None
try:
    from groq import Groq
    if os.getenv("GROQ_API_KEY"):
        groq_student_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception:
    pass

GROQ_MODEL = "llama-3.3-70b-versatile"

# Stripe config
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STUDENT_PRICE_MONTHLY = os.getenv('STUDENT_STRIPE_PRICE_MONTHLY', '')
STUDENT_PRICE_YEARLY = os.getenv('STUDENT_STRIPE_PRICE_YEARLY', '')


# ============================================================================
# HELPERS
# ============================================================================

def _detect_institution_from_email(email: str) -> str:
    """Auto-detect institution name from .edu email domain"""
    domain = email.split('@')[-1].lower()

    institution_map = {
        "vuu.edu": "Virginia Union University",
        "vsu.edu": "Virginia State University",
        "uva.edu": "University of Virginia",
        "vt.edu": "Virginia Tech",
        "wm.edu": "College of William & Mary",
        "gmu.edu": "George Mason University",
        "odu.edu": "Old Dominion University",
        "jmu.edu": "James Madison University",
        "liberty.edu": "Liberty University",
        "howard.edu": "Howard University",
        "morehouse.edu": "Morehouse College",
        "spelman.edu": "Spelman College",
        "famu.edu": "Florida A&M University",
        "ncat.edu": "North Carolina A&T State University",
    }

    if domain in institution_map:
        return institution_map[domain]

    if domain.endswith(".edu"):
        name_part = domain.replace(".edu", "").replace(".", " ")
        known_acronyms = ["mit", "usc", "ucla", "nyu", "ucf", "unt", "utd", "uci", "ucsd"]
        if name_part.lower() in known_acronyms:
            return name_part.upper()
        return f"{name_part.title()} University"

    return None


def _get_letter_grade(percentage: float) -> str:
    """Convert percentage to letter grade"""
    if percentage >= 90:
        return "A"
    elif percentage >= 80:
        return "B"
    elif percentage >= 70:
        return "C"
    elif percentage >= 60:
        return "D"
    else:
        return "F"


def _generate_class_code(cursor) -> str:
    """Generate a unique RSC-XXXX class code. Retries on collision."""
    for _ in range(10):
        suffix = ''.join(random.choices(CLASS_CODE_CHARS, k=4))
        code = f"RSC-{suffix}"
        cursor.execute("SELECT id FROM class_codes WHERE code = %s", (code,))
        if not cursor.fetchone():
            return code
    raise HTTPException(status_code=500, detail="Failed to generate unique class code")


# ============================================================================
# AUTH DEPENDENCIES
# ============================================================================

async def get_current_student(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate session token and return student user"""
    token = credentials.credentials
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT s.user_id, s.expires_at, u.email, u.role, u.full_name,
                   u.institution, u.edu_verified
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s AND u.is_active = TRUE
        """, (token,))
        session = cursor.fetchone()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        user_id, expires_at, email, role, full_name, institution, edu_verified = session
        if datetime.now() > expires_at:
            raise HTTPException(status_code=401, detail="Session expired")
        return {
            "user_id": user_id,
            "email": email,
            "role": role,
            "full_name": full_name,
            "institution": institution,
            "edu_verified": edu_verified
        }
    finally:
        cursor.close()
        conn.close()


async def get_current_professor(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate session token and ensure user is a professor (customer/admin role)"""
    token = credentials.credentials
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT s.user_id, s.expires_at, u.email, u.role, u.full_name, u.institution
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s AND u.is_active = TRUE
        """, (token,))
        session = cursor.fetchone()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        user_id, expires_at, email, role, full_name, institution = session
        if datetime.now() > expires_at:
            raise HTTPException(status_code=401, detail="Session expired")
        if role not in ('customer', 'admin'):
            raise HTTPException(status_code=403, detail="Professor access required")
        return {
            "user_id": user_id,
            "email": email,
            "role": role,
            "full_name": full_name,
            "institution": institution
        }
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# REQUEST MODELS
# ============================================================================

class StudentRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    institution: Optional[str] = None

class StudentLoginRequest(BaseModel):
    email: str
    password: str

class JoinClassRequest(BaseModel):
    code: str

class CreateCourseRequest(BaseModel):
    course_name: str
    course_code: Optional[str] = None
    section: Optional[str] = None
    semester: Optional[str] = None

class GenerateCodeRequest(BaseModel):
    course_id: int
    max_students: Optional[int] = 200
    expires_in_days: Optional[int] = 120

class UpdateCodeRequest(BaseModel):
    status: Optional[str] = None
    max_students: Optional[int] = None

class SaveGradeRequest(BaseModel):
    enrollment_id: int
    category_name: str
    assignment_name: str
    score: float
    points_possible: float
    weight: Optional[float] = None

class UpdateGradeRequest(BaseModel):
    category_name: Optional[str] = None
    assignment_name: Optional[str] = None
    score: Optional[float] = None
    points_possible: Optional[float] = None
    weight: Optional[float] = None

class CreateAnnouncementRequest(BaseModel):
    course_id: int
    title: str
    content: str

class CreateDeadlineRequest(BaseModel):
    course_id: int
    title: str
    due_at: str
    description: Optional[str] = None

class StudyBuddyRequest(BaseModel):
    message: str
    course_id: Optional[int] = None
    conversation_id: Optional[int] = None

class LabReviewRequest(BaseModel):
    text: str
    course_id: Optional[int] = None
    assignment_type: Optional[str] = None

class StudentCheckoutRequest(BaseModel):
    plan: str  # "monthly" or "yearly"
    success_url: Optional[str] = "https://student.readysetclass.app/profile?upgraded=1"
    cancel_url: Optional[str] = "https://student.readysetclass.app/profile"


# ============================================================================
# PREMIUM HELPERS
# ============================================================================

def _check_premium(current_user: dict) -> bool:
    """Check if student has active premium subscription"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT subscription_tier, subscription_status, subscription_ends_at
            FROM users WHERE id = %s
        """, (current_user["user_id"],))
        row = cursor.fetchone()
        if not row:
            return False
        tier, status, ends_at = row
        if tier in ('pro', 'student_premium') and status == 'active':
            if ends_at and datetime.now() > ends_at:
                return False
            return True
        return False
    finally:
        cursor.close()
        conn.close()


def _require_premium(current_user: dict):
    """Raise 403 if user does not have premium"""
    if not _check_premium(current_user):
        raise HTTPException(
            status_code=403,
            detail="Premium subscription required. Upgrade at student.readysetclass.app/profile"
        )


def _call_groq(system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    """Call GROQ API with given prompts. Raises HTTPException on failure."""
    if not groq_student_client:
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable")
    try:
        response = groq_student_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"GROQ API error: {e}")
        raise HTTPException(status_code=503, detail="AI service error. Please try again.")


# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@router.post("/api/auth/student/login")
async def student_login(request: StudentLoginRequest):
    """Login for student accounts"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, email, password_hash, role, is_active, full_name,
                   institution, edu_verified
            FROM users WHERE email = %s
        """, (request.email,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user_id, email, password_hash, role, is_active, full_name, institution, edu_verified = user

        if not bcrypt.checkpw(request.password.encode('utf-8'), password_hash.encode('utf-8')):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not is_active:
            raise HTTPException(status_code=403, detail="Account disabled")

        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)

        cursor.execute("""
            INSERT INTO sessions (user_id, session_token, expires_at)
            VALUES (%s, %s, %s)
        """, (user_id, session_token, expires_at))
        cursor.execute("""
            INSERT INTO activity_log (user_id, action, details)
            VALUES (%s, 'login', '{"type": "student"}')
        """, (user_id,))
        cursor.execute("UPDATE users SET last_active_at = NOW() WHERE id = %s", (user_id,))
        conn.commit()

        return {
            "token": session_token,
            "user": {
                "id": user_id,
                "email": email,
                "full_name": full_name,
                "role": role,
                "institution": institution,
                "edu_verified": edu_verified,
                "is_demo": False
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Student login error: {e}")
        raise HTTPException(status_code=500, detail="Login failed")
    finally:
        cursor.close()
        conn.close()


@router.post("/api/auth/register")
async def register_student(request: StudentRegisterRequest):
    """Register a new student account"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (request.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        institution = request.institution
        if not institution:
            institution = _detect_institution_from_email(request.email)

        is_edu = request.email.lower().endswith('.edu')

        password_bytes = request.password.encode('utf-8')
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_bytes, salt).decode('utf-8')

        cursor.execute("""
            INSERT INTO users (email, password_hash, full_name, role, institution,
                               is_active, edu_verified, edu_verified_at)
            VALUES (%s, %s, %s, 'student', %s, TRUE, %s, %s)
            RETURNING id
        """, (request.email, password_hash, request.full_name, institution,
              is_edu, datetime.now() if is_edu else None))
        user_id = cursor.fetchone()[0]

        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)

        cursor.execute("""
            INSERT INTO sessions (user_id, session_token, expires_at)
            VALUES (%s, %s, %s)
        """, (user_id, session_token, expires_at))
        cursor.execute("""
            INSERT INTO activity_log (user_id, action, details)
            VALUES (%s, 'register', '{"type": "student"}')
        """, (user_id,))
        conn.commit()

        return {
            "token": session_token,
            "user": {
                "id": user_id,
                "email": request.email,
                "full_name": request.full_name,
                "role": "student",
                "institution": institution,
                "edu_verified": is_edu,
                "is_demo": False
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# CLASS CODE — STUDENT JOIN
# ============================================================================

@router.post("/api/class-codes/join")
async def join_class(request: JoinClassRequest, current_user=Depends(get_current_student)):
    """Student joins a class using RSC-XXXX code"""
    code = request.code.strip().upper()
    if not code.startswith("RSC-"):
        code = f"RSC-{code}"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Look up the code
        cursor.execute("""
            SELECT cc.id, cc.course_id, cc.status, cc.expires_at, cc.max_students,
                   cc.current_students, sc.course_name, sc.course_code, sc.section,
                   sc.semester, sc.institution, u.full_name as professor_name
            FROM class_codes cc
            JOIN student_courses sc ON cc.course_id = sc.id
            JOIN users u ON cc.professor_id = u.id
            WHERE cc.code = %s
        """, (code,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Invalid class code")

        code_id, course_id, status, expires_at, max_students, current_students, \
            course_name, course_code, section, semester, institution, professor_name = row

        if status != 'active':
            raise HTTPException(status_code=400, detail="This class code is no longer active")
        if expires_at and datetime.now() > expires_at:
            raise HTTPException(status_code=400, detail="This class code has expired")
        if max_students and current_students >= max_students:
            raise HTTPException(status_code=400, detail="This class is full")

        # Check if already enrolled
        cursor.execute("""
            SELECT id FROM student_enrollments
            WHERE student_id = %s AND course_id = %s AND status = 'active'
        """, (current_user["user_id"], course_id))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="You are already enrolled in this course")

        # Enroll the student
        cursor.execute("""
            INSERT INTO student_enrollments (student_id, course_id, class_code_id)
            VALUES (%s, %s, %s) RETURNING id
        """, (current_user["user_id"], course_id, code_id))
        enrollment_id = cursor.fetchone()[0]

        # Increment student count (with safety check)
        cursor.execute("""
            UPDATE class_codes SET current_students = current_students + 1,
                                   updated_at = NOW()
            WHERE id = %s
        """, (code_id,))

        cursor.execute("""
            INSERT INTO activity_log (user_id, action, details)
            VALUES (%s, 'join_class', %s)
        """, (current_user["user_id"], f'{{"course": "{course_name}", "code": "{code}"}}'))

        conn.commit()

        return {
            "enrollment_id": enrollment_id,
            "course": {
                "id": course_id,
                "course_name": course_name,
                "course_code": course_code,
                "section": section,
                "semester": semester,
                "professor_name": professor_name,
                "institution": institution
            },
            "message": f"Successfully joined {course_code or course_name}!"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"Join class error: {e}")
        raise HTTPException(status_code=500, detail="Failed to join class")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# STUDENT COURSE ENDPOINTS
# ============================================================================

@router.get("/api/v1/student/courses")
async def get_enrolled_courses(current_user=Depends(get_current_student)):
    """List all courses the student is enrolled in"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT se.id as enrollment_id, sc.id as course_id, sc.course_name,
                   sc.course_code, sc.section, sc.semester, sc.institution,
                   u.full_name as professor_name, se.enrolled_at
            FROM student_enrollments se
            JOIN student_courses sc ON se.course_id = sc.id
            JOIN users u ON sc.professor_id = u.id
            WHERE se.student_id = %s AND se.status = 'active'
            ORDER BY se.enrolled_at DESC
        """, (current_user["user_id"],))

        courses = []
        for row in cursor.fetchall():
            courses.append({
                "enrollment_id": row[0],
                "course_id": row[1],
                "course_name": row[2],
                "course_code": row[3],
                "section": row[4],
                "semester": row[5],
                "institution": row[6],
                "professor_name": row[7],
                "enrolled_at": row[8].isoformat() if row[8] else None
            })

        return {"courses": courses, "total": len(courses)}
    finally:
        cursor.close()
        conn.close()


@router.delete("/api/v1/student/courses/{enrollment_id}")
async def drop_course(enrollment_id: int, current_user=Depends(get_current_student)):
    """Drop an enrolled course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, class_code_id FROM student_enrollments
            WHERE id = %s AND student_id = %s AND status = 'active'
        """, (enrollment_id, current_user["user_id"]))
        enrollment = cursor.fetchone()
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found")

        cursor.execute("""
            UPDATE student_enrollments SET status = 'dropped' WHERE id = %s
        """, (enrollment_id,))
        cursor.execute("""
            UPDATE class_codes SET current_students = GREATEST(current_students - 1, 0)
            WHERE id = %s
        """, (enrollment[1],))
        conn.commit()

        return {"message": "Course dropped successfully"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to drop course")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/courses/{course_id}/announcements")
async def get_course_announcements(course_id: int, current_user=Depends(get_current_student)):
    """Get announcements for a specific course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify enrollment
        cursor.execute("""
            SELECT id FROM student_enrollments
            WHERE student_id = %s AND course_id = %s AND status = 'active'
        """, (current_user["user_id"], course_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not enrolled in this course")

        cursor.execute("""
            SELECT sa.id, sa.title, sa.content, u.full_name as professor_name,
                   sa.created_at
            FROM student_announcements sa
            JOIN users u ON sa.professor_id = u.id
            WHERE sa.course_id = %s
            ORDER BY sa.created_at DESC
        """, (course_id,))

        announcements = []
        for row in cursor.fetchall():
            announcements.append({
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "professor_name": row[3],
                "created_at": row[4].isoformat() if row[4] else None
            })

        return {"announcements": announcements, "total": len(announcements)}
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# STUDENT DASHBOARD
# ============================================================================

@router.get("/api/v1/student/dashboard")
async def get_dashboard(current_user=Depends(get_current_student)):
    """Student home dashboard — upcoming deadlines + recent announcements"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        student_id = current_user["user_id"]

        # Upcoming deadlines (next 14 days)
        cursor.execute("""
            SELECT sd.id, sd.title, sc.course_name, sd.due_at, sd.description
            FROM student_deadlines sd
            JOIN student_courses sc ON sd.course_id = sc.id
            JOIN student_enrollments se ON se.course_id = sc.id
            WHERE se.student_id = %s AND se.status = 'active'
              AND sd.due_at >= NOW()
              AND sd.due_at <= NOW() + INTERVAL '14 days'
            ORDER BY sd.due_at ASC
        """, (student_id,))

        deadlines = []
        for row in cursor.fetchall():
            due_at = row[3]
            days_until = (due_at - datetime.now()).days if due_at else None
            deadlines.append({
                "id": row[0],
                "title": row[1],
                "course_name": row[2],
                "due_at": due_at.isoformat() if due_at else None,
                "description": row[4],
                "days_until_due": days_until
            })

        # Recent announcements (last 7 days)
        cursor.execute("""
            SELECT sa.id, sa.title, sc.course_name, sa.content, sa.created_at
            FROM student_announcements sa
            JOIN student_courses sc ON sa.course_id = sc.id
            JOIN student_enrollments se ON se.course_id = sc.id
            WHERE se.student_id = %s AND se.status = 'active'
              AND sa.created_at >= NOW() - INTERVAL '7 days'
            ORDER BY sa.created_at DESC
            LIMIT 10
        """, (student_id,))

        announcements = []
        for row in cursor.fetchall():
            announcements.append({
                "id": row[0],
                "title": row[1],
                "course_name": row[2],
                "content": row[3],
                "created_at": row[4].isoformat() if row[4] else None
            })

        # Course count
        cursor.execute("""
            SELECT COUNT(*) FROM student_enrollments
            WHERE student_id = %s AND status = 'active'
        """, (student_id,))
        course_count = cursor.fetchone()[0]

        return {
            "upcoming_deadlines": deadlines,
            "recent_announcements": announcements,
            "enrolled_courses_count": course_count,
            "student_name": current_user["full_name"]
        }
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/dashboard/deadlines")
async def get_deadline_dashboard(current_user=Depends(get_current_student)):
    """Deadline dashboard grouped by urgency (overdue, this week, next week, later)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT sd.id, sd.title, sc.course_name, sd.due_at, sd.description
            FROM student_deadlines sd
            JOIN student_courses sc ON sd.course_id = sc.id
            JOIN student_enrollments se ON se.course_id = sc.id
            WHERE se.student_id = %s AND se.status = 'active'
            ORDER BY sd.due_at ASC
        """, (current_user["user_id"],))

        now = datetime.now()
        end_of_week = now + timedelta(days=7)
        end_of_next_week = now + timedelta(days=14)

        overdue = []
        this_week = []
        next_week = []
        later = []

        for row in cursor.fetchall():
            deadline = {
                "id": row[0],
                "title": row[1],
                "course_name": row[2],
                "due_at": row[3].isoformat() if row[3] else None,
                "description": row[4]
            }
            due_at = row[3]
            if due_at < now:
                deadline["urgency"] = "overdue"
                overdue.append(deadline)
            elif due_at <= end_of_week:
                deadline["urgency"] = "this_week"
                this_week.append(deadline)
            elif due_at <= end_of_next_week:
                deadline["urgency"] = "next_week"
                next_week.append(deadline)
            else:
                deadline["urgency"] = "later"
                later.append(deadline)

        return {
            "overdue": overdue,
            "this_week": this_week,
            "next_week": next_week,
            "later": later,
            "summary": {
                "overdue_count": len(overdue),
                "this_week_count": len(this_week),
                "next_week_count": len(next_week),
                "later_count": len(later)
            }
        }
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# MANUAL GRADE CALCULATOR
# ============================================================================

@router.post("/api/v1/student/grades")
async def save_grade(request: SaveGradeRequest, current_user=Depends(get_current_student)):
    """Save a manual grade entry"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify enrollment ownership
        cursor.execute("""
            SELECT id FROM student_enrollments
            WHERE id = %s AND student_id = %s AND status = 'active'
        """, (request.enrollment_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Enrollment not found")

        cursor.execute("""
            INSERT INTO student_grades
                (student_id, enrollment_id, category_name, assignment_name,
                 score, points_possible, weight)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (current_user["user_id"], request.enrollment_id, request.category_name,
              request.assignment_name, request.score, request.points_possible, request.weight))
        grade_id = cursor.fetchone()[0]
        conn.commit()

        pct = round((request.score / request.points_possible * 100), 2) if request.points_possible > 0 else 0

        return {
            "grade_id": grade_id,
            "category_name": request.category_name,
            "assignment_name": request.assignment_name,
            "score": request.score,
            "points_possible": request.points_possible,
            "weight": request.weight,
            "percentage": pct
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to save grade")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/grades/{enrollment_id}")
async def get_grades(enrollment_id: int, current_user=Depends(get_current_student)):
    """Get all grade entries for a course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify enrollment
        cursor.execute("""
            SELECT se.id, sc.course_name FROM student_enrollments se
            JOIN student_courses sc ON se.course_id = sc.id
            WHERE se.id = %s AND se.student_id = %s
        """, (enrollment_id, current_user["user_id"]))
        enrollment = cursor.fetchone()
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found")

        cursor.execute("""
            SELECT id, category_name, assignment_name, score, points_possible,
                   weight, created_at
            FROM student_grades
            WHERE enrollment_id = %s
            ORDER BY category_name, created_at
        """, (enrollment_id,))

        grades = []
        for row in cursor.fetchall():
            pct = round((row[3] / row[4] * 100), 2) if row[4] > 0 else 0
            grades.append({
                "id": row[0],
                "category_name": row[1],
                "assignment_name": row[2],
                "score": row[3],
                "points_possible": row[4],
                "weight": row[5],
                "percentage": pct,
                "created_at": row[6].isoformat() if row[6] else None
            })

        return {
            "enrollment_id": enrollment_id,
            "course_name": enrollment[1],
            "grades": grades,
            "total_entries": len(grades)
        }
    finally:
        cursor.close()
        conn.close()


@router.put("/api/v1/student/grades/{grade_id}")
async def update_grade(grade_id: int, request: UpdateGradeRequest, current_user=Depends(get_current_student)):
    """Update a grade entry"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id FROM student_grades WHERE id = %s AND student_id = %s
        """, (grade_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Grade not found")

        updates = []
        values = []
        if request.category_name is not None:
            updates.append("category_name = %s")
            values.append(request.category_name)
        if request.assignment_name is not None:
            updates.append("assignment_name = %s")
            values.append(request.assignment_name)
        if request.score is not None:
            updates.append("score = %s")
            values.append(request.score)
        if request.points_possible is not None:
            updates.append("points_possible = %s")
            values.append(request.points_possible)
        if request.weight is not None:
            updates.append("weight = %s")
            values.append(request.weight)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append("updated_at = NOW()")
        values.append(grade_id)

        cursor.execute(
            f"UPDATE student_grades SET {', '.join(updates)} WHERE id = %s",
            values
        )
        conn.commit()

        # Return updated grade
        cursor.execute("""
            SELECT id, category_name, assignment_name, score, points_possible, weight
            FROM student_grades WHERE id = %s
        """, (grade_id,))
        row = cursor.fetchone()
        pct = round((row[3] / row[4] * 100), 2) if row[4] > 0 else 0

        return {
            "grade_id": row[0],
            "category_name": row[1],
            "assignment_name": row[2],
            "score": row[3],
            "points_possible": row[4],
            "weight": row[5],
            "percentage": pct
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update grade")
    finally:
        cursor.close()
        conn.close()


@router.delete("/api/v1/student/grades/{grade_id}")
async def delete_grade(grade_id: int, current_user=Depends(get_current_student)):
    """Delete a grade entry"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM student_grades WHERE id = %s AND student_id = %s RETURNING id
        """, (grade_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Grade not found")
        conn.commit()
        return {"message": "Grade deleted"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete grade")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/grades/{enrollment_id}/calculator")
async def grade_calculator(enrollment_id: int, current_user=Depends(get_current_student)):
    """Calculate current grade + what-you-need scenarios from manual grade entries"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify enrollment
        cursor.execute("""
            SELECT se.id, sc.course_name FROM student_enrollments se
            JOIN student_courses sc ON se.course_id = sc.id
            WHERE se.id = %s AND se.student_id = %s
        """, (enrollment_id, current_user["user_id"]))
        enrollment = cursor.fetchone()
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found")

        cursor.execute("""
            SELECT category_name, assignment_name, score, points_possible, weight
            FROM student_grades WHERE enrollment_id = %s
        """, (enrollment_id,))
        rows = cursor.fetchall()

        if not rows:
            return {
                "enrollment_id": enrollment_id,
                "course_name": enrollment[1],
                "current_grade": {"percentage": None, "letter": None},
                "is_weighted": False,
                "categories": [],
                "totals": {"earned": 0, "possible": 0},
                "scenarios": [],
                "message": "No grades entered yet"
            }

        # Group by category
        categories = {}
        has_weights = False
        for row in rows:
            cat_name, _, score, possible, weight = row
            if weight is not None:
                has_weights = True
            if cat_name not in categories:
                categories[cat_name] = {"weight": weight, "earned": 0, "possible": 0, "count": 0}
            categories[cat_name]["earned"] += score
            categories[cat_name]["possible"] += possible
            categories[cat_name]["count"] += 1

        # Calculate grade
        total_earned = sum(c["earned"] for c in categories.values())
        total_possible = sum(c["possible"] for c in categories.values())

        if has_weights:
            # Weighted calculation
            total_weighted = 0
            total_weight_used = 0
            for cat_data in categories.values():
                if cat_data["possible"] > 0 and cat_data["weight"]:
                    cat_pct = cat_data["earned"] / cat_data["possible"] * 100
                    total_weighted += cat_pct * cat_data["weight"]
                    total_weight_used += cat_data["weight"]
            current_pct = total_weighted / total_weight_used if total_weight_used > 0 else 0
        else:
            current_pct = (total_earned / total_possible * 100) if total_possible > 0 else 0

        # Build category breakdown
        category_list = []
        for cat_name, cat_data in categories.items():
            cat_pct = round((cat_data["earned"] / cat_data["possible"] * 100), 2) if cat_data["possible"] > 0 else 0
            weighted_contribution = round(cat_pct * (cat_data["weight"] or 0), 2) if has_weights else None
            category_list.append({
                "name": cat_name,
                "weight": cat_data["weight"],
                "earned": cat_data["earned"],
                "possible": cat_data["possible"],
                "percentage": cat_pct,
                "weighted_contribution": weighted_contribution,
                "entry_count": cat_data["count"]
            })

        # What-you-need scenarios
        scenarios = []
        for letter, target_pct in [("A", 90), ("B", 80), ("C", 70), ("D", 60)]:
            if has_weights:
                remaining_weight = max(1.0 - sum(c["weight"] or 0 for c in categories.values()), 0)
                if remaining_weight > 0:
                    needed = (target_pct - current_pct * (1 - remaining_weight)) / remaining_weight
                else:
                    needed = None
            else:
                if total_possible > 0:
                    needed = target_pct
                else:
                    needed = None

            scenarios.append({
                "target_grade": letter,
                "target_percentage": target_pct,
                "needed_on_remaining": round(needed, 2) if needed is not None else None,
                "is_achievable": needed is not None and needed <= 100
            })

        return {
            "enrollment_id": enrollment_id,
            "course_name": enrollment[1],
            "current_grade": {
                "percentage": round(current_pct, 2),
                "letter": _get_letter_grade(current_pct)
            },
            "is_weighted": has_weights,
            "categories": category_list,
            "totals": {
                "earned": total_earned,
                "possible": total_possible
            },
            "scenarios": scenarios
        }
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# PROFESSOR ENDPOINTS (for Q-tip coordination)
# ============================================================================

@router.post("/api/v1/professor/courses")
async def create_course(request: CreateCourseRequest, current_user=Depends(get_current_professor)):
    """Professor creates a course that students can join via class code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO student_courses (professor_id, course_name, course_code,
                                         section, semester, institution)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (current_user["user_id"], request.course_name, request.course_code,
              request.section, request.semester, current_user.get("institution")))
        course_id = cursor.fetchone()[0]
        conn.commit()

        return {
            "course_id": course_id,
            "course_name": request.course_name,
            "course_code": request.course_code,
            "message": "Course created. Generate a class code to share with students."
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create course")
    finally:
        cursor.close()
        conn.close()


@router.post("/api/class-codes/generate")
async def generate_class_code(request: GenerateCodeRequest, current_user=Depends(get_current_professor)):
    """Professor generates a RSC-XXXX class code for a course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify professor owns the course
        cursor.execute("""
            SELECT course_name FROM student_courses
            WHERE id = %s AND professor_id = %s
        """, (request.course_id, current_user["user_id"]))
        course = cursor.fetchone()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        code = _generate_class_code(cursor)
        expires_at = datetime.now() + timedelta(days=request.expires_in_days) if request.expires_in_days else None

        cursor.execute("""
            INSERT INTO class_codes (course_id, professor_id, code, max_students, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (request.course_id, current_user["user_id"], code, request.max_students, expires_at))
        code_id = cursor.fetchone()[0]
        conn.commit()

        return {
            "code_id": code_id,
            "code": code,
            "course_name": course[0],
            "expires_at": expires_at.isoformat() if expires_at else None,
            "max_students": request.max_students,
            "share_instructions": f"Share this code with your students: {code}"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate code")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/class-codes/{course_id}")
async def get_class_codes(course_id: int, current_user=Depends(get_current_professor)):
    """Professor views class codes for a course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT course_name FROM student_courses
            WHERE id = %s AND professor_id = %s
        """, (course_id, current_user["user_id"]))
        course = cursor.fetchone()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        cursor.execute("""
            SELECT id, code, status, current_students, max_students,
                   expires_at, created_at
            FROM class_codes WHERE course_id = %s
            ORDER BY created_at DESC
        """, (course_id,))

        codes = []
        for row in cursor.fetchall():
            codes.append({
                "id": row[0],
                "code": row[1],
                "status": row[2],
                "current_students": row[3],
                "max_students": row[4],
                "expires_at": row[5].isoformat() if row[5] else None,
                "created_at": row[6].isoformat() if row[6] else None
            })

        return {"course_name": course[0], "codes": codes}
    finally:
        cursor.close()
        conn.close()


@router.put("/api/class-codes/{code_id}")
async def update_class_code(code_id: int, request: UpdateCodeRequest, current_user=Depends(get_current_professor)):
    """Professor updates or deactivates a class code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT cc.id FROM class_codes cc
            JOIN student_courses sc ON cc.course_id = sc.id
            WHERE cc.id = %s AND sc.professor_id = %s
        """, (code_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Code not found")

        updates = []
        values = []
        if request.status:
            if request.status not in ('active', 'deactivated'):
                raise HTTPException(status_code=400, detail="Status must be 'active' or 'deactivated'")
            updates.append("status = %s")
            values.append(request.status)
        if request.max_students is not None:
            updates.append("max_students = %s")
            values.append(request.max_students)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append("updated_at = NOW()")
        values.append(code_id)

        cursor.execute(
            f"UPDATE class_codes SET {', '.join(updates)} WHERE id = %s",
            values
        )
        conn.commit()
        return {"message": "Class code updated"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update code")
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/professor/announcements")
async def create_announcement(request: CreateAnnouncementRequest, current_user=Depends(get_current_professor)):
    """Professor creates an announcement for enrolled students"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id FROM student_courses
            WHERE id = %s AND professor_id = %s
        """, (request.course_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Course not found")

        cursor.execute("""
            INSERT INTO student_announcements (course_id, professor_id, title, content)
            VALUES (%s, %s, %s, %s) RETURNING id, created_at
        """, (request.course_id, current_user["user_id"], request.title, request.content))
        row = cursor.fetchone()
        conn.commit()

        return {
            "announcement_id": row[0],
            "title": request.title,
            "created_at": row[1].isoformat() if row[1] else None,
            "message": "Announcement posted to enrolled students"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create announcement")
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/professor/deadlines")
async def create_deadline(request: CreateDeadlineRequest, current_user=Depends(get_current_professor)):
    """Professor shares a deadline with enrolled students"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id FROM student_courses
            WHERE id = %s AND professor_id = %s
        """, (request.course_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Course not found")

        cursor.execute("""
            INSERT INTO student_deadlines (course_id, professor_id, title, due_at, description)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (request.course_id, current_user["user_id"], request.title,
              request.due_at, request.description))
        deadline_id = cursor.fetchone()[0]
        conn.commit()

        return {
            "deadline_id": deadline_id,
            "title": request.title,
            "due_at": request.due_at,
            "message": "Deadline shared with enrolled students"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create deadline")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# CALENDAR SYNC (.ics export)
# ============================================================================

@router.get("/api/v1/student/calendar/export")
async def export_calendar(current_user=Depends(get_current_student)):
    """Export all enrolled course deadlines as .ics file for Apple/Google Calendar"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT sd.title, sd.due_at, sd.description, sc.course_name, sc.course_code
            FROM student_deadlines sd
            JOIN student_courses sc ON sd.course_id = sc.id
            JOIN student_enrollments se ON se.course_id = sc.id
            WHERE se.student_id = %s AND se.status = 'active'
            ORDER BY sd.due_at ASC
        """, (current_user["user_id"],))

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ReadySetClass//Student//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:ReadySetClass - {current_user.get('full_name', 'Student')}",
        ]

        for row in cursor.fetchall():
            title, due_at, description, course_name, course_code = row
            if not due_at:
                continue
            uid = f"{due_at.strftime('%Y%m%dT%H%M%S')}-{hash(title) & 0xFFFFFFFF}@readysetclass.app"
            dtstart = due_at.strftime("%Y%m%dT%H%M%S")
            summary = f"[{course_code or course_name}] {title}"
            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:{uid}")
            lines.append(f"DTSTART:{dtstart}")
            lines.append(f"DTEND:{dtstart}")
            lines.append(f"SUMMARY:{summary}")
            if description:
                lines.append(f"DESCRIPTION:{description[:500]}")
            lines.append(f"CATEGORIES:{course_name}")
            lines.append("BEGIN:VALARM")
            lines.append("TRIGGER:-P1D")
            lines.append("ACTION:DISPLAY")
            lines.append(f"DESCRIPTION:Due tomorrow: {title}")
            lines.append("END:VALARM")
            lines.append("BEGIN:VALARM")
            lines.append("TRIGGER:-PT3H")
            lines.append("ACTION:DISPLAY")
            lines.append(f"DESCRIPTION:Due in 3 hours: {title}")
            lines.append("END:VALARM")
            lines.append("END:VEVENT")

        lines.append("END:VCALENDAR")
        ics_content = "\r\n".join(lines)

        return Response(
            content=ics_content,
            media_type="text/calendar",
            headers={
                "Content-Disposition": "attachment; filename=readysetclass-deadlines.ics",
                "Cache-Control": "no-cache"
            }
        )
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/calendar/subscribe")
async def calendar_subscribe_url(current_user=Depends(get_current_student)):
    """Get calendar export info for this student"""
    return {
        "download_url": "https://facultyflow-production.up.railway.app/api/v1/student/calendar/export",
        "instructions": {
            "apple": "Download the .ics file, then open it — Calendar will prompt you to add the events",
            "google": "Go to calendar.google.com > Settings > Import & export > Import, then upload the .ics file"
        },
        "note": "Download the .ics file and import into your calendar app. Re-download anytime for updated deadlines."
    }


# ============================================================================
# NOTIFICATION PREFERENCES
# ============================================================================

@router.get("/api/v1/student/notifications/preferences")
async def get_notification_preferences(current_user=Depends(get_current_student)):
    """Get notification preferences for all enrolled courses"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Get all enrolled courses with their preferences (if set)
        cursor.execute("""
            SELECT se.id as enrollment_id, sc.course_name, sc.course_code,
                   snp.announcements_enabled, snp.deadlines_enabled,
                   snp.reminder_hours
            FROM student_enrollments se
            JOIN student_courses sc ON se.course_id = sc.id
            LEFT JOIN student_notification_prefs snp ON snp.enrollment_id = se.id
            WHERE se.student_id = %s AND se.status = 'active'
            ORDER BY sc.course_name
        """, (current_user["user_id"],))

        prefs = []
        for row in cursor.fetchall():
            prefs.append({
                "enrollment_id": row[0],
                "course_name": row[1],
                "course_code": row[2],
                "announcements_enabled": row[3] if row[3] is not None else True,
                "deadlines_enabled": row[4] if row[4] is not None else True,
                "reminder_hours": row[5] if row[5] is not None else 24
            })

        return {"preferences": prefs}
    finally:
        cursor.close()
        conn.close()


class UpdateNotificationPrefsRequest(BaseModel):
    announcements_enabled: Optional[bool] = None
    deadlines_enabled: Optional[bool] = None
    reminder_hours: Optional[int] = None


@router.put("/api/v1/student/notifications/preferences/{enrollment_id}")
async def update_notification_preferences(
    enrollment_id: int,
    request: UpdateNotificationPrefsRequest,
    current_user=Depends(get_current_student)
):
    """Update notification preferences for a specific course"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify enrollment
        cursor.execute("""
            SELECT id FROM student_enrollments
            WHERE id = %s AND student_id = %s AND status = 'active'
        """, (enrollment_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Enrollment not found")

        # Upsert preferences
        cursor.execute("""
            INSERT INTO student_notification_prefs (enrollment_id, announcements_enabled,
                deadlines_enabled, reminder_hours)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (enrollment_id)
            DO UPDATE SET
                announcements_enabled = COALESCE(%s, student_notification_prefs.announcements_enabled),
                deadlines_enabled = COALESCE(%s, student_notification_prefs.deadlines_enabled),
                reminder_hours = COALESCE(%s, student_notification_prefs.reminder_hours),
                updated_at = NOW()
        """, (
            enrollment_id,
            request.announcements_enabled if request.announcements_enabled is not None else True,
            request.deadlines_enabled if request.deadlines_enabled is not None else True,
            request.reminder_hours if request.reminder_hours is not None else 24,
            request.announcements_enabled,
            request.deadlines_enabled,
            request.reminder_hours
        ))
        conn.commit()

        return {"message": "Notification preferences updated"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update preferences")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# AI STUDY BUDDY (Premium)
# ============================================================================

STUDY_BUDDY_SYSTEM = """You are the ReadySetClass Study Buddy — a friendly, knowledgeable AI tutor for college students.

Rules:
- You help students UNDERSTAND concepts, you do NOT do their work for them
- Explain clearly, use analogies, break complex ideas into steps
- If a student asks you to write their paper or solve their homework, redirect: "I can help you understand the concepts, but the work should be yours"
- Be encouraging but honest. If something is wrong, explain why
- Keep responses concise (2-4 paragraphs max unless the student asks for more detail)
- You can use examples, bullet points, and simple formatting
- Match the energy: warm, clear, never condescending. Think "cool TA" not "robot professor"
- If you don't know something, say so honestly

Context: You're helping a college student. Be aware of academic integrity — guide, don't solve."""


@router.post("/api/v1/student/ai/study-buddy")
async def study_buddy(request: StudyBuddyRequest, current_user=Depends(get_current_student)):
    """AI Study Buddy — conversational tutor. Premium feature."""
    _require_premium(current_user)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Build context
        context_parts = [STUDY_BUDDY_SYSTEM]
        context_parts.append(f"\nStudent name: {current_user.get('full_name', 'Student')}")

        if current_user.get("institution"):
            context_parts.append(f"Institution: {current_user['institution']}")

        # Add course context if provided
        if request.course_id:
            cursor.execute("""
                SELECT sc.course_name, sc.course_code, u.full_name
                FROM student_enrollments se
                JOIN student_courses sc ON se.course_id = sc.id
                JOIN users u ON sc.professor_id = u.id
                WHERE se.student_id = %s AND sc.id = %s AND se.status = 'active'
            """, (current_user["user_id"], request.course_id))
            course = cursor.fetchone()
            if course:
                context_parts.append(f"\nCurrent course context: {course[1] or course[0]} ({course[0]}) with Prof. {course[2]}")

        # Load conversation history if continuing
        history = []
        conversation_id = request.conversation_id
        if conversation_id:
            cursor.execute("""
                SELECT role, content FROM student_ai_messages
                WHERE conversation_id = %s ORDER BY created_at ASC
                LIMIT 20
            """, (conversation_id,))
            for row in cursor.fetchall():
                history.append({"role": row[0], "content": row[1]})

        # Build messages
        system_prompt = "\n".join(context_parts)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": request.message})

        # Call GROQ
        if not groq_student_client:
            raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

        response = groq_student_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )
        reply = response.choices[0].message.content

        # Save conversation
        if not conversation_id:
            cursor.execute("""
                INSERT INTO student_ai_conversations (student_id, course_id, title)
                VALUES (%s, %s, %s) RETURNING id
            """, (current_user["user_id"], request.course_id,
                  request.message[:80]))
            conversation_id = cursor.fetchone()[0]

        # Save messages
        cursor.execute("""
            INSERT INTO student_ai_messages (conversation_id, role, content)
            VALUES (%s, 'user', %s)
        """, (conversation_id, request.message))
        cursor.execute("""
            INSERT INTO student_ai_messages (conversation_id, role, content)
            VALUES (%s, 'assistant', %s)
        """, (conversation_id, reply))

        # Track usage
        cursor.execute("""
            UPDATE users SET ai_generations_this_month = COALESCE(ai_generations_this_month, 0) + 1
            WHERE id = %s
        """, (current_user["user_id"],))

        conn.commit()

        return {
            "reply": reply,
            "conversation_id": conversation_id
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"Study buddy error: {e}")
        raise HTTPException(status_code=503, detail="AI service error. Please try again.")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/ai/conversations")
async def list_conversations(current_user=Depends(get_current_student)):
    """List recent Study Buddy conversations"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT c.id, c.title, c.course_id, sc.course_name, c.created_at
            FROM student_ai_conversations c
            LEFT JOIN student_courses sc ON c.course_id = sc.id
            WHERE c.student_id = %s
            ORDER BY c.created_at DESC LIMIT 20
        """, (current_user["user_id"],))

        convos = []
        for row in cursor.fetchall():
            convos.append({
                "id": row[0], "title": row[1], "course_id": row[2],
                "course_name": row[3],
                "created_at": row[4].isoformat() if row[4] else None
            })
        return {"conversations": convos}
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/ai/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, current_user=Depends(get_current_student)):
    """Get full message history for a conversation"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id FROM student_ai_conversations
            WHERE id = %s AND student_id = %s
        """, (conversation_id, current_user["user_id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")

        cursor.execute("""
            SELECT role, content, created_at FROM student_ai_messages
            WHERE conversation_id = %s ORDER BY created_at ASC
        """, (conversation_id,))

        messages = []
        for row in cursor.fetchall():
            messages.append({
                "role": row[0], "content": row[1],
                "created_at": row[2].isoformat() if row[2] else None
            })
        return {"conversation_id": conversation_id, "messages": messages}
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# THE LAB — AI ASSIGNMENT REVIEWER (Premium)
# ============================================================================

LAB_SYSTEM = """You are The Lab — ReadySetClass's AI Assignment Reviewer. You help students improve their work BEFORE they submit it.

You REVIEW and COACH. You do NOT rewrite their work.

Your output must be valid JSON with this exact structure:
{
  "readiness_score": <integer 0-100>,
  "overall_assessment": "<1-2 sentence summary>",
  "strengths": ["<strength 1>", "<strength 2>"],
  "improvements": [
    {"area": "<area name>", "issue": "<what's wrong>", "suggestion": "<how to fix>"},
  ],
  "grammar_issues": <integer count of grammar/spelling issues spotted>,
  "structure_score": <integer 0-100>,
  "argument_score": <integer 0-100>,
  "clarity_score": <integer 0-100>,
  "next_steps": ["<actionable step 1>", "<actionable step 2>"]
}

Scoring guide:
- 90-100: Ready to submit. Minor polish only.
- 70-89: Good foundation. A few areas need work.
- 50-69: Needs significant revision. Key gaps identified.
- Below 50: Early draft. Major rework needed.

Be encouraging but honest. Students want real feedback, not participation trophies."""


@router.post("/api/v1/student/ai/lab/review")
async def lab_review(request: LabReviewRequest, current_user=Depends(get_current_student)):
    """The Lab — AI reviews student's draft and returns readiness score + feedback. Premium feature."""
    _require_premium(current_user)

    if len(request.text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Please provide at least 50 characters of text to review")

    # Build prompt
    user_prompt = f"Review this student submission:\n\n{request.text[:5000]}"
    if request.assignment_type:
        user_prompt = f"Assignment type: {request.assignment_type}\n\n{user_prompt}"

    raw_response = _call_groq(LAB_SYSTEM, user_prompt, temperature=0.3, max_tokens=1500)

    # Parse JSON from response
    try:
        # Try to extract JSON from the response
        json_start = raw_response.find('{')
        json_end = raw_response.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            review = json.loads(raw_response[json_start:json_end])
        else:
            review = json.loads(raw_response)
    except (json.JSONDecodeError, ValueError):
        # Fallback if AI didn't return clean JSON
        review = {
            "readiness_score": 50,
            "overall_assessment": raw_response[:300],
            "strengths": [],
            "improvements": [],
            "grammar_issues": 0,
            "structure_score": 50,
            "argument_score": 50,
            "clarity_score": 50,
            "next_steps": ["Review the AI feedback above and revise your draft"]
        }

    # Track usage
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users SET ai_generations_this_month = COALESCE(ai_generations_this_month, 0) + 1
            WHERE id = %s
        """, (current_user["user_id"],))
        cursor.execute("""
            INSERT INTO activity_log (user_id, action, details)
            VALUES (%s, 'lab_review', %s)
        """, (current_user["user_id"],
              json.dumps({"readiness_score": review.get("readiness_score", 0),
                          "text_length": len(request.text)})))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"review": review}


# ============================================================================
# GRADE PREDICTOR AI (Premium)
# ============================================================================

@router.get("/api/v1/student/ai/grade-predictor/{enrollment_id}")
async def grade_predictor(enrollment_id: int, current_user=Depends(get_current_student)):
    """AI-powered grade prediction with scenarios. Premium feature."""
    _require_premium(current_user)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Get current grades
        cursor.execute("""
            SELECT se.id, sc.course_name FROM student_enrollments se
            JOIN student_courses sc ON se.course_id = sc.id
            WHERE se.id = %s AND se.student_id = %s
        """, (enrollment_id, current_user["user_id"]))
        enrollment = cursor.fetchone()
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found")

        cursor.execute("""
            SELECT category_name, assignment_name, score, points_possible, weight
            FROM student_grades WHERE enrollment_id = %s
            ORDER BY category_name, created_at
        """, (enrollment_id,))
        grades = cursor.fetchall()

        if not grades:
            return {
                "enrollment_id": enrollment_id,
                "course_name": enrollment[1],
                "prediction": None,
                "message": "Add some grades first so the AI can predict your final grade"
            }

        # Build grade summary for AI
        grade_summary = []
        for g in grades:
            pct = round(g[2] / g[3] * 100, 1) if g[3] > 0 else 0
            grade_summary.append(f"- {g[0]}: {g[1]} — {g[2]}/{g[3]} ({pct}%)" +
                                (f" [weight: {g[4]*100}%]" if g[4] else ""))

        system_prompt = """You are a grade prediction AI. Given a student's current grades, predict their final grade and provide scenarios.

Return valid JSON:
{
  "predicted_final": {"percentage": <float>, "letter": "<A/B/C/D/F>", "confidence": "<high/medium/low>"},
  "analysis": "<2-3 sentence analysis of current standing>",
  "scenarios": [
    {"name": "<scenario name>", "description": "<what happens>", "predicted_grade": "<letter>", "predicted_percentage": <float>},
  ],
  "recommendation": "<1-2 sentence personalized recommendation>"
}

Provide 3-4 scenarios: "Keep current pace", "Strong finish", "Minimum effort", and optionally "Perfect from here"."""

        user_prompt = f"Course: {enrollment[1]}\n\nCurrent grades:\n" + "\n".join(grade_summary)

        raw = _call_groq(system_prompt, user_prompt, temperature=0.3, max_tokens=1200)

        try:
            json_start = raw.find('{')
            json_end = raw.rfind('}') + 1
            prediction = json.loads(raw[json_start:json_end]) if json_start >= 0 else json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            prediction = {
                "predicted_final": {"percentage": None, "letter": None, "confidence": "low"},
                "analysis": raw[:300],
                "scenarios": [],
                "recommendation": "Add more grades for a more accurate prediction."
            }

        # Track usage
        cursor.execute("""
            UPDATE users SET ai_generations_this_month = COALESCE(ai_generations_this_month, 0) + 1
            WHERE id = %s
        """, (current_user["user_id"],))
        conn.commit()

        return {
            "enrollment_id": enrollment_id,
            "course_name": enrollment[1],
            "prediction": prediction,
            "grades_analyzed": len(grades)
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Grade predictor error: {e}")
        raise HTTPException(status_code=503, detail="AI prediction service error")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# STUDENT PREMIUM (Stripe)
# ============================================================================

@router.get("/api/v1/student/premium/status")
async def premium_status(current_user=Depends(get_current_student)):
    """Check student's premium subscription status"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT subscription_tier, subscription_status, subscription_ends_at,
                   ai_generations_this_month, stripe_customer_id
            FROM users WHERE id = %s
        """, (current_user["user_id"],))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        tier, status, ends_at, ai_gens, stripe_id = row
        is_premium = tier in ('pro', 'student_premium') and status == 'active'
        if is_premium and ends_at and datetime.now() > ends_at:
            is_premium = False

        return {
            "is_premium": is_premium,
            "tier": tier or "free",
            "status": status or "none",
            "subscription_ends_at": ends_at.isoformat() if ends_at else None,
            "ai_generations_this_month": ai_gens or 0,
            "features": {
                "study_buddy": is_premium,
                "the_lab": is_premium,
                "grade_predictor": is_premium,
                "advanced_calculator": is_premium
            },
            "pricing": {
                "monthly": "$3.99/month",
                "yearly": "$29.99/year (save 37%)"
            }
        }
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/student/premium/checkout")
async def premium_checkout(request: StudentCheckoutRequest, current_user=Depends(get_current_student)):
    """Create Stripe checkout session for student premium"""
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    price_id = STUDENT_PRICE_MONTHLY if request.plan == "monthly" else STUDENT_PRICE_YEARLY
    if not price_id:
        raise HTTPException(status_code=503,
                            detail="Student pricing not configured. Contact support.")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Get or create Stripe customer
        cursor.execute("SELECT stripe_customer_id FROM users WHERE id = %s",
                       (current_user["user_id"],))
        row = cursor.fetchone()
        stripe_customer_id = row[0] if row and row[0] else None

        if not stripe_customer_id:
            customer = stripe.Customer.create(
                email=current_user["email"],
                name=current_user.get("full_name"),
                metadata={"user_id": str(current_user["user_id"]), "role": "student"}
            )
            stripe_customer_id = customer.id
            cursor.execute("UPDATE users SET stripe_customer_id = %s WHERE id = %s",
                           (stripe_customer_id, current_user["user_id"]))
            conn.commit()

        session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=request.success_url,
            cancel_url=request.cancel_url,
            metadata={"user_id": str(current_user["user_id"]), "tier": "student_premium"}
        )

        return {"checkout_url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")
    except Exception as e:
        print(f"Checkout error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# NUCLEAR CACHE SYSTEM
# ============================================================================

@router.get("/api/v1/student/version")
async def get_version():
    """Version check endpoint. No auth required."""
    return {"version": STUDENT_APP_VERSION, "action": "check", "clear_cache": False}


@router.get("/api/v1/student/boot.js")
async def boot_script():
    """Auto-clearing boot script for student frontend <head>"""
    js = f"""// ReadySetClass Student Edition - Auto Cache Manager v{STUDENT_APP_VERSION}
(function() {{
    var SERVER_VERSION = '{STUDENT_APP_VERSION}';
    var LOCAL_VERSION = localStorage.getItem('rsc_student_version');
    if ('serviceWorker' in navigator) {{
        navigator.serviceWorker.getRegistrations().then(function(regs) {{
            regs.forEach(function(r) {{ r.unregister(); }});
        }});
    }}
    if (LOCAL_VERSION && LOCAL_VERSION !== SERVER_VERSION) {{
        console.log('[RSC] Version changed: ' + LOCAL_VERSION + ' -> ' + SERVER_VERSION);
        if ('caches' in window) {{
            caches.keys().then(function(names) {{
                names.forEach(function(name) {{ caches.delete(name); }});
            }});
        }}
        var authToken = localStorage.getItem('student_auth_token');
        localStorage.clear();
        sessionStorage.clear();
        if (authToken) localStorage.setItem('student_auth_token', authToken);
        document.cookie.split(';').forEach(function(c) {{
            document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/';
        }});
        localStorage.setItem('rsc_student_version', SERVER_VERSION);
        window.location.reload(true);
        return;
    }}
    localStorage.setItem('rsc_student_version', SERVER_VERSION);
}})();
"""
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


@router.get("/api/v1/student/nuclear-cache")
async def nuclear_cache():
    """Returns cache-clearing script. No auth required."""
    return {
        "version": STUDENT_APP_VERSION,
        "action": "nuclear_cache_clear",
        "instructions": "Visit /api/v1/student/nuclear-cache/html to clear everything",
        "script": """
            if ('serviceWorker' in navigator) { navigator.serviceWorker.getRegistrations().then(function(r) { r.forEach(function(sw) { sw.unregister(); }); }); }
            if ('caches' in window) { caches.keys().then(function(n) { n.forEach(function(name) { caches.delete(name); }); }); }
            localStorage.clear(); sessionStorage.clear();
            document.cookie.split(';').forEach(function(c) { document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/'; });
            setTimeout(function() { window.location.reload(true); }, 500);
        """
    }


@router.get("/api/v1/student/nuclear-cache/html")
async def nuclear_cache_html():
    """Self-contained HTML page that nukes everything and redirects to login."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>ReadySetClass - Cache Reset</title>
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <style>
        body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: linear-gradient(135deg, #1B3A52, #2A5478); color: white; }}
        .container {{ text-align: center; padding: 2rem; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
        .check {{ color: #4ade80; padding: 4px 0; }}
        .version {{ opacity: 0.5; font-size: 0.75rem; margin-top: 2rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Resetting ReadySetClass</h1>
        <p>Clearing cached data...</p>
        <div id="status"></div>
        <div class="version">v{STUDENT_APP_VERSION}</div>
    </div>
    <script>
        var s = document.getElementById('status');
        function log(m) {{ s.innerHTML += '<div class="check">' + m + '</div>'; }}
        async function go() {{
            if ('serviceWorker' in navigator) {{ var r = await navigator.serviceWorker.getRegistrations(); for (var i=0;i<r.length;i++) await r[i].unregister(); }}
            log('Service workers cleared');
            if ('caches' in window) {{ var n = await caches.keys(); for (var i=0;i<n.length;i++) await caches.delete(n[i]); }}
            log('Browser caches cleared');
            localStorage.clear(); sessionStorage.clear(); log('Storage cleared');
            document.cookie.split(';').forEach(function(c) {{ document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/'; }});
            log('Cookies cleared');
            localStorage.setItem('rsc_student_version', '{STUDENT_APP_VERSION}');
            log(''); log('All clear! Redirecting...');
            setTimeout(function() {{ window.location.href = '/login'; }}, 1200);
        }}
        go();
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
