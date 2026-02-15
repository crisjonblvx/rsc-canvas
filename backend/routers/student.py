"""
ReadySetClass Student API Router
Student-facing endpoints for the Phife agent

Endpoints:
  POST /api/auth/register          - Student registration
  GET  /api/v1/student/courses     - Get enrolled courses
  GET  /api/v1/student/courses/{course_id}/assignments - Get assignments for a course
  POST /api/v1/student/assignments/sync - Sync assignments from Canvas
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
import secrets
import bcrypt
import os
import psycopg2


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter()
security = HTTPBearer()


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
# AUTH DEPENDENCY
# ============================================================================

async def get_current_student(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate session token and return student user"""
    token = credentials.credentials

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT s.user_id, s.expires_at, u.email, u.role, u.full_name,
                   u.canvas_url, u.canvas_token_encrypted
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s AND u.is_active = TRUE
        """, (token,))

        session = cursor.fetchone()

        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")

        user_id, expires_at, email, role, full_name, canvas_url, canvas_token = session

        if datetime.now() > expires_at:
            raise HTTPException(status_code=401, detail="Session expired")

        return {
            "user_id": user_id,
            "email": email,
            "role": role,
            "full_name": full_name,
            "canvas_url": canvas_url,
            "canvas_token": canvas_token
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

class CanvasConnectRequest(BaseModel):
    canvas_url: str
    access_token: str


# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@router.post("/api/auth/register")
async def register_student(request: StudentRegisterRequest):
    """Register a new student account"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check if email already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (request.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        # Hash password
        password_bytes = request.password.encode('utf-8')
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_bytes, salt).decode('utf-8')

        # Create user with student role
        cursor.execute("""
            INSERT INTO users (email, password_hash, full_name, role, institution, is_active)
            VALUES (%s, %s, %s, 'student', %s, TRUE)
            RETURNING id
        """, (request.email, password_hash, request.full_name, request.institution))

        user_id = cursor.fetchone()[0]

        # Create session
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)

        cursor.execute("""
            INSERT INTO sessions (user_id, session_token, expires_at)
            VALUES (%s, %s, %s)
        """, (user_id, session_token, expires_at))

        # Log activity
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
# CANVAS CONNECTION
# ============================================================================

@router.post("/api/v1/student/canvas/connect")
async def connect_canvas(request: CanvasConnectRequest, current_user=Depends(get_current_student)):
    """Connect student's Canvas account"""
    from canvas_auth import CanvasAuth

    # Validate Canvas credentials
    auth = CanvasAuth(request.canvas_url, request.access_token)
    success, user_data, error = auth.test_connection()

    if not success:
        raise HTTPException(status_code=400, detail=f"Canvas connection failed: {error}")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Save Canvas credentials to user record
        cursor.execute("""
            UPDATE users
            SET canvas_url = %s, canvas_token_encrypted = %s, updated_at = NOW()
            WHERE id = %s
        """, (request.canvas_url, request.access_token, current_user["user_id"]))

        conn.commit()

        return {
            "status": "connected",
            "canvas_url": request.canvas_url,
            "user_name": user_data.get("name", "Unknown") if user_data else "Connected"
        }

    except Exception as e:
        conn.rollback()
        print(f"Canvas connect error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save Canvas credentials")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# COURSE ENDPOINTS
# ============================================================================

@router.get("/api/v1/student/courses")
async def get_student_courses(current_user=Depends(get_current_student)):
    """Get all courses the student is enrolled in via Canvas"""
    from canvas_client import CanvasClient

    canvas_url = current_user.get("canvas_url")
    canvas_token = current_user.get("canvas_token")

    if not canvas_url or not canvas_token:
        raise HTTPException(
            status_code=400,
            detail="Canvas not connected. Please connect your Canvas account first."
        )

    client = CanvasClient(canvas_url, canvas_token)
    courses = client.get_student_courses()

    return {
        "courses": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "course_code": c.get("course_code"),
                "term": c.get("term", {}).get("name") if c.get("term") else None,
                "enrollments": c.get("enrollments", [])
            }
            for c in courses
        ],
        "total": len(courses)
    }


@router.get("/api/v1/student/courses/{course_id}/assignments")
async def get_course_assignments(course_id: int, current_user=Depends(get_current_student)):
    """Get all assignments for a specific course"""
    from canvas_client import CanvasClient

    canvas_url = current_user.get("canvas_url")
    canvas_token = current_user.get("canvas_token")

    if not canvas_url or not canvas_token:
        raise HTTPException(
            status_code=400,
            detail="Canvas not connected. Please connect your Canvas account first."
        )

    client = CanvasClient(canvas_url, canvas_token)
    assignments = client.get_student_assignments(course_id)

    return {
        "assignments": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "description": a.get("description"),
                "due_at": a.get("due_at"),
                "points_possible": a.get("points_possible"),
                "submission_types": a.get("submission_types", []),
                "has_submitted_submissions": a.get("has_submitted_submissions", False),
                "submission": a.get("submission"),
                "course_id": course_id
            }
            for a in assignments
        ],
        "total": len(assignments)
    }


# ============================================================================
# ASSIGNMENT SYNC
# ============================================================================

@router.post("/api/v1/student/assignments/sync")
async def sync_assignments(current_user=Depends(get_current_student)):
    """Sync all assignments from Canvas into local database"""
    from canvas_client import CanvasClient

    canvas_url = current_user.get("canvas_url")
    canvas_token = current_user.get("canvas_token")

    if not canvas_url or not canvas_token:
        raise HTTPException(
            status_code=400,
            detail="Canvas not connected. Please connect your Canvas account first."
        )

    client = CanvasClient(canvas_url, canvas_token)
    user_id = current_user["user_id"]

    # Get all courses
    courses = client.get_student_courses()
    synced_count = 0

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        for course in courses:
            course_id = course.get("id")
            course_name = course.get("name", "")
            assignments = client.get_student_assignments(course_id)

            for a in assignments:
                submission = a.get("submission", {}) or {}

                cursor.execute("""
                    INSERT INTO student_assignments
                        (user_id, course_id, assignment_id, title, description,
                         due_at, points_possible, submission_types, score,
                         submitted, submitted_at, workflow_state, course_name, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id, assignment_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        due_at = EXCLUDED.due_at,
                        points_possible = EXCLUDED.points_possible,
                        score = EXCLUDED.score,
                        submitted = EXCLUDED.submitted,
                        submitted_at = EXCLUDED.submitted_at,
                        workflow_state = EXCLUDED.workflow_state,
                        course_name = EXCLUDED.course_name,
                        synced_at = NOW()
                """, (
                    user_id,
                    str(course_id),
                    str(a.get("id")),
                    a.get("name"),
                    a.get("description"),
                    a.get("due_at"),
                    a.get("points_possible"),
                    ",".join(a.get("submission_types", [])),
                    submission.get("score"),
                    submission.get("workflow_state") == "submitted" or submission.get("submitted_at") is not None,
                    submission.get("submitted_at"),
                    a.get("workflow_state"),
                    course_name
                ))
                synced_count += 1

        conn.commit()

        return {
            "status": "success",
            "synced": synced_count,
            "courses": len(courses)
        }

    except Exception as e:
        conn.rollback()
        print(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail="Assignment sync failed")
    finally:
        cursor.close()
        conn.close()
