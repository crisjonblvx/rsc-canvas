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
# HELPER FUNCTIONS
# ============================================================================

def _detect_institution_from_email(email: str) -> tuple[str, str]:
    """
    Auto-detect institution and Canvas URL from email domain

    Returns:
        tuple: (institution_name, canvas_url) or (None, None)
    """
    domain = email.split('@')[-1].lower()

    # Institution mapping with Canvas URLs
    institution_map = {
        # Virginia
        "vuu.edu": ("Virginia Union University", "https://vuu.instructure.com"),
        "vsu.edu": ("Virginia State University", "https://vsu.instructure.com"),
        "uva.edu": ("University of Virginia", "https://canvas.its.virginia.edu"),
        "vt.edu": ("Virginia Tech", "https://canvas.vt.edu"),
        "wm.edu": ("College of William & Mary", "https://canvas.wm.edu"),
        "gmu.edu": ("George Mason University", "https://mymasonportal.gmu.edu"),
        "odu.edu": ("Old Dominion University", "https://canvas.odu.edu"),
        "jmu.edu": ("James Madison University", "https://canvas.jmu.edu"),
        "liberty.edu": ("Liberty University", "https://liberty.instructure.com"),

        # HBCUs
        "howard.edu": ("Howard University", "https://howard.instructure.com"),
        "morehouse.edu": ("Morehouse College", "https://morehouse.instructure.com"),
        "spelman.edu": ("Spelman College", "https://spelman.instructure.com"),
        "famu.edu": ("Florida A&M University", "https://famu.instructure.com"),
        "ncat.edu": ("North Carolina A&T State University", "https://ncat.instructure.com"),
    }

    # Try exact match first
    if domain in institution_map:
        return institution_map[domain]

    # Try pattern matching for .edu domains
    if domain.endswith(".edu"):
        # Extract institution name from domain
        name_part = domain.replace(".edu", "").replace(".", " ")

        # Handle common acronyms (all caps)
        known_acronyms = ["mit", "usc", "ucla", "nyu", "ucf", "unt", "utd", "uci", "ucsd"]
        if name_part.lower() in known_acronyms:
            institution_name = name_part.upper()
        else:
            institution_name = f"{name_part.title()} University"

        # Generate Canvas URL guess
        canvas_subdomain = domain.replace(".edu", "")
        canvas_url = f"https://{canvas_subdomain}.instructure.com"

        return (institution_name, canvas_url)

    # Fallback for non-.edu domains
    return (None, None)


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

class StudentLoginRequest(BaseModel):
    email: str
    password: str

class CanvasConnectRequest(BaseModel):
    canvas_url: str
    access_token: str


# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@router.post("/api/auth/student/login")
async def student_login(request: StudentLoginRequest):
    """Login for student accounts"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get user
        cursor.execute("""
            SELECT id, email, password_hash, role, is_active, full_name,
                   institution, canvas_url, canvas_token_encrypted
            FROM users
            WHERE email = %s
        """, (request.email,))

        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user_id, email, password_hash, role, is_active, full_name, institution, canvas_url, canvas_token = user

        # Verify password
        password_bytes = request.password.encode('utf-8')
        stored_hash_bytes = password_hash.encode('utf-8')
        if not bcrypt.checkpw(password_bytes, stored_hash_bytes):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not is_active:
            raise HTTPException(status_code=403, detail="Account disabled")

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
            VALUES (%s, 'login', '{"type": "student"}')
        """, (user_id,))

        # Update last active
        cursor.execute("""
            UPDATE users SET last_active_at = NOW() WHERE id = %s
        """, (user_id,))

        conn.commit()

        # Detect Canvas URL if not set
        _, suggested_canvas_url = _detect_institution_from_email(email)

        return {
            "token": session_token,
            "user": {
                "id": user_id,
                "email": email,
                "full_name": full_name,
                "role": role,
                "institution": institution,
                "is_demo": False
            },
            "canvas_connected": canvas_url is not None and canvas_token is not None,
            "suggested_canvas_url": suggested_canvas_url if not canvas_url else canvas_url
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
        # Check if email already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (request.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        # Auto-detect institution and Canvas URL from email domain
        institution = request.institution
        suggested_canvas_url = None
        if not institution:
            institution, suggested_canvas_url = _detect_institution_from_email(request.email)
        else:
            # If institution provided manually, still try to detect Canvas URL
            _, suggested_canvas_url = _detect_institution_from_email(request.email)

        # Hash password
        password_bytes = request.password.encode('utf-8')
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_bytes, salt).decode('utf-8')

        # Create user with student role
        cursor.execute("""
            INSERT INTO users (email, password_hash, full_name, role, institution, is_active)
            VALUES (%s, %s, %s, 'student', %s, TRUE)
            RETURNING id
        """, (request.email, password_hash, request.full_name, institution))

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
                "institution": institution,
                "is_demo": False
            },
            "suggested_canvas_url": suggested_canvas_url
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


# ============================================================================
# DEADLINE DASHBOARD ENDPOINTS (Phase 2.1)
# ============================================================================

@router.get("/api/v1/student/assignments/upcoming")
async def get_upcoming_assignments(
    days: int = 7,
    current_user=Depends(get_current_student)
):
    """Get assignments due in the next N days"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT assignment_id, title, course_name, due_at, points_possible,
                   score, submitted, workflow_state
            FROM student_assignments
            WHERE user_id = %s
              AND due_at IS NOT NULL
              AND due_at >= NOW()
              AND due_at <= NOW() + INTERVAL '%s days'
            ORDER BY due_at ASC
        """, (current_user["user_id"], days))

        assignments = []
        for row in cursor.fetchall():
            assignments.append({
                "assignment_id": row[0],
                "title": row[1],
                "course_name": row[2],
                "due_at": row[3].isoformat() if row[3] else None,
                "points_possible": row[4],
                "score": row[5],
                "submitted": row[6],
                "workflow_state": row[7]
            })

        return {
            "assignments": assignments,
            "total": len(assignments),
            "days": days
        }

    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/student/dashboard/deadlines")
async def get_deadline_dashboard(current_user=Depends(get_current_student)):
    """Get deadline dashboard with assignments grouped by urgency"""
    from datetime import datetime, timedelta

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get all assignments
        cursor.execute("""
            SELECT assignment_id, title, course_name, due_at, points_possible,
                   score, submitted, workflow_state
            FROM student_assignments
            WHERE user_id = %s
            ORDER BY due_at ASC NULLS LAST
        """, (current_user["user_id"],))

        now = datetime.now()
        end_of_this_week = now + timedelta(days=7)
        end_of_next_week = now + timedelta(days=14)

        this_week = []
        next_week = []
        overdue = []
        no_due_date = []

        for row in cursor.fetchall():
            assignment = {
                "assignment_id": row[0],
                "title": row[1],
                "course_name": row[2],
                "due_at": row[3].isoformat() if row[3] else None,
                "points_possible": row[4],
                "score": row[5],
                "submitted": row[6],
                "workflow_state": row[7]
            }

            due_at = row[3]

            if due_at is None:
                no_due_date.append(assignment)
            elif due_at < now and not row[6]:  # Overdue and not submitted
                assignment["urgency"] = "overdue"
                overdue.append(assignment)
            elif due_at <= end_of_this_week:
                assignment["urgency"] = "this_week"
                this_week.append(assignment)
            elif due_at <= end_of_next_week:
                assignment["urgency"] = "next_week"
                next_week.append(assignment)

        return {
            "this_week": this_week,
            "next_week": next_week,
            "overdue": overdue,
            "no_due_date": no_due_date,
            "summary": {
                "total": len(this_week) + len(next_week) + len(overdue) + len(no_due_date),
                "this_week_count": len(this_week),
                "next_week_count": len(next_week),
                "overdue_count": len(overdue),
                "no_due_date_count": len(no_due_date)
            }
        }

    finally:
        cursor.close()
        conn.close()


# ============================================================================
# GRADE CALCULATOR ENDPOINTS (Phase 2.2)
# ============================================================================

@router.get("/api/v1/student/courses/{course_id}/grade-calculator")
async def get_grade_calculator(course_id: int, current_user=Depends(get_current_student)):
    """
    Calculate current grade and "what you need" scenarios

    Returns:
        - Current grade (percentage and letter)
        - Points earned vs possible
        - Scenarios: what score needed on remaining work for target grade
    """
    from canvas_client import CanvasClient

    canvas_url = current_user.get("canvas_url")
    canvas_token = current_user.get("canvas_token")

    if not canvas_url or not canvas_token:
        raise HTTPException(400, "Canvas not connected")

    client = CanvasClient(canvas_url, canvas_token)

    # Get student's enrollment/grades
    enrollment = client.get_student_grades(course_id)

    # Get assignment groups for breakdown
    assignment_groups = client.get_assignment_groups(course_id)

    # Get grading scheme (A/B/C cutoffs)
    course_info = client.get_course_grading_scheme(course_id)

    # Calculate current grade
    current_score = enrollment.get("grades", {}).get("current_score")
    final_score = enrollment.get("grades", {}).get("final_score")

    # Calculate points earned and possible
    total_earned = 0
    total_possible = 0
    remaining_points = 0

    for group in assignment_groups:
        for assignment in group.get("assignments", []):
            points = assignment.get("points_possible", 0)
            submission = assignment.get("submission", {})
            score = submission.get("score")

            total_possible += points
            if score is not None:
                total_earned += score
            else:
                remaining_points += points

    # "What you need" scenarios
    scenarios = []
    target_grades = [
        ("A", 90),
        ("B", 80),
        ("C", 70),
        ("D", 60)
    ]

    for letter, target_pct in target_grades:
        target_points = (target_pct / 100) * total_possible
        points_needed = target_points - total_earned

        if remaining_points > 0:
            pct_needed = (points_needed / remaining_points) * 100
            scenarios.append({
                "target_grade": letter,
                "target_percentage": target_pct,
                "points_needed": round(points_needed, 2),
                "percentage_needed_on_remaining": round(pct_needed, 2),
                "is_achievable": pct_needed <= 100
            })

    return {
        "current_grade": {
            "score": current_score,
            "final_score": final_score,
            "letter": _get_letter_grade(current_score) if current_score else None
        },
        "points": {
            "earned": total_earned,
            "possible": total_possible,
            "remaining": remaining_points
        },
        "scenarios": scenarios,
        "grading_scheme": course_info.get("grading_standard")
    }


@router.get("/api/v1/student/courses/{course_id}/grade-breakdown")
async def get_grade_breakdown(course_id: int, current_user=Depends(get_current_student)):
    """
    Get grade breakdown by assignment category/group

    Returns:
        - Grade per assignment group (Quizzes 85%, Homework 92%, etc.)
        - Weighted vs total points
    """
    from canvas_client import CanvasClient

    canvas_url = current_user.get("canvas_url")
    canvas_token = current_user.get("canvas_token")

    if not canvas_url or not canvas_token:
        raise HTTPException(400, "Canvas not connected")

    client = CanvasClient(canvas_url, canvas_token)
    assignment_groups = client.get_assignment_groups(course_id)
    course_info = client.get_course_grading_scheme(course_id)

    # Check if weighted grading
    is_weighted = course_info.get("apply_assignment_group_weights", False)

    breakdown = []
    for group in assignment_groups:
        group_earned = 0
        group_possible = 0
        assignments_in_group = []

        for assignment in group.get("assignments", []):
            points = assignment.get("points_possible", 0)
            submission = assignment.get("submission", {})
            score = submission.get("score")

            group_possible += points
            if score is not None:
                group_earned += score

            assignments_in_group.append({
                "name": assignment.get("name"),
                "points_possible": points,
                "score": score,
                "percentage": round((score / points * 100), 2) if score and points else None
            })

        group_percentage = round((group_earned / group_possible * 100), 2) if group_possible > 0 else 0

        breakdown.append({
            "name": group.get("name"),
            "weight": group.get("group_weight") if is_weighted else None,
            "earned": group_earned,
            "possible": group_possible,
            "percentage": group_percentage,
            "assignments": assignments_in_group
        })

    return {
        "is_weighted": is_weighted,
        "groups": breakdown
    }


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


# ============================================================================
# NUCLEAR CACHE CLEAR (Phase 1 utility)
# ============================================================================

@router.get("/api/v1/student/nuclear-cache")
async def nuclear_cache():
    """
    Returns JavaScript to nuke all caches, service workers, and local storage.
    Frontend should call this endpoint and eval the response, or redirect to
    the HTML version at /nuclear-cache.

    This is a no-auth endpoint so it works even when auth is broken.
    """
    return {
        "action": "nuclear_cache_clear",
        "instructions": "Execute the 'script' field in your browser console, or visit /api/v1/student/nuclear-cache/html",
        "script": """
            // Unregister all service workers
            if ('serviceWorker' in navigator) {
                navigator.serviceWorker.getRegistrations().then(function(registrations) {
                    for (let registration of registrations) {
                        registration.unregister();
                        console.log('Unregistered service worker:', registration.scope);
                    }
                });
            }
            // Clear all caches
            if ('caches' in window) {
                caches.keys().then(function(names) {
                    for (let name of names) {
                        caches.delete(name);
                        console.log('Deleted cache:', name);
                    }
                });
            }
            // Clear localStorage and sessionStorage
            localStorage.clear();
            sessionStorage.clear();
            console.log('Cleared localStorage and sessionStorage');
            // Force reload without cache
            setTimeout(function() { window.location.reload(true); }, 500);
        """
    }


@router.get("/api/v1/student/nuclear-cache/html")
async def nuclear_cache_html():
    """
    Self-contained HTML page that nukes all caches and redirects to setup.
    Visit this URL directly in the browser to clear everything.
    """
    from fastapi.responses import HTMLResponse

    html = """<!DOCTYPE html>
<html>
<head>
    <title>ReadySetClass - Cache Reset</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            text-align: center;
            padding: 2rem;
        }
        h1 { font-size: 2rem; margin-bottom: 0.5rem; }
        p { font-size: 1.1rem; opacity: 0.9; }
        .status { margin-top: 1.5rem; font-size: 0.95rem; }
        .check { color: #4ade80; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Clearing Cache...</h1>
        <p>Resetting ReadySetClass Student Edition</p>
        <div class="status" id="status"></div>
    </div>
    <script>
        const status = document.getElementById('status');
        function log(msg) {
            status.innerHTML += '<div class="check">' + msg + '</div>';
        }

        async function nuclearClear() {
            // 1. Unregister service workers
            if ('serviceWorker' in navigator) {
                const regs = await navigator.serviceWorker.getRegistrations();
                for (const reg of regs) {
                    await reg.unregister();
                    log('Unregistered service worker');
                }
            }
            log('Service workers cleared');

            // 2. Delete all caches
            if ('caches' in window) {
                const names = await caches.keys();
                for (const name of names) {
                    await caches.delete(name);
                    log('Deleted cache: ' + name);
                }
            }
            log('Browser caches cleared');

            // 3. Clear storage
            localStorage.clear();
            sessionStorage.clear();
            log('Local storage cleared');

            // 4. Clear cookies for this domain
            document.cookie.split(';').forEach(function(c) {
                document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/';
            });
            log('Cookies cleared');

            log('');
            log('All clear! Redirecting...');

            // 5. Redirect after a moment
            setTimeout(function() {
                window.location.href = '/setup';
            }, 1500);
        }

        nuclearClear();
    </script>
</body>
</html>"""

    return HTMLResponse(content=html)
