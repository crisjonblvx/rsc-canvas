"""
AI Grading API Routes

FastAPI endpoints for AI-powered grading workflow
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import logging
import os
import psycopg2

from database import get_db, AIGradingSession, AIGrade, CanvasCredentials
from ai_grading.grading_engine import AIGradingEngine
from ai_grading.canvas_integration import CanvasGradingIntegration
from canvas_auth import decrypt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-grading", tags=["ai-grading"])

_grading_security = HTTPBearer()


# ============================================================================
# Auth
# ============================================================================

def get_current_grading_user(
    credentials: HTTPAuthorizationCredentials = Depends(_grading_security)
) -> int:
    """Validate session token and return user_id."""
    token = credentials.credentials
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.user_id FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s
              AND u.is_active = TRUE
              AND s.expires_at > NOW()
        """, (token,))
        result = cursor.fetchone()
        cursor.close()
        if not result:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth error in grading routes: {e}")
        raise HTTPException(status_code=500, detail="Authentication error")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


# ============================================================================
# Request/Response Models
# ============================================================================

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


async def grade_submissions_background(
    session_id: int,
    submissions: List[Dict],
    rubric: Dict,
    preferences: Dict,
    db: Session
):
    """
    Background task to grade all submissions with AI
    """
    try:
        logger.info(f"Starting background grading for session {session_id}")

        # Initialize grading engine
        engine = AIGradingEngine(rubric=rubric, preferences=preferences)

        # Grade all submissions in parallel
        results = await engine.grade_batch(submissions)

        logger.info(f"Graded {len(results)} submissions for session {session_id}")

        # Save results to database
        for result in results:
            grade = AIGrade(
                session_id=session_id,
                student_id=result.get("student_id", ""),
                student_name=result.get("student_name"),
                submission_id=result.get("submission_id", ""),
                submission_text=result.get("submission_text", ""),
                ai_total_score=result.get("total_score"),
                ai_rubric_scores=result.get("rubric_scores"),
                ai_feedback=result.get("feedback"),
                ai_criterion_feedback=result.get("criterion_feedback"),
                ai_confidence=result.get("confidence", "low"),
                ai_flags=result.get("flags", [])
            )
            db.add(grade)

        # Update session
        session = db.query(AIGradingSession).filter_by(id=session_id).first()
        if session:
            session.graded_count = len(results)
            session.status = "completed"
            session.completed_at = datetime.utcnow()

            # Calculate session statistics
            scores = [r.get("total_score", 0) for r in results if r.get("total_score")]
            if scores:
                session.average_score = sum(scores) / len(scores)

            confidences = {
                "high": sum(1 for r in results if r.get("confidence") == "high"),
                "medium": sum(1 for r in results if r.get("confidence") == "medium"),
                "low": sum(1 for r in results if r.get("confidence") == "low")
            }
            session.flagged_count = sum(1 for r in results if r.get("flags"))

        db.commit()
        logger.info(f"Session {session_id} grading completed successfully")

    except Exception as e:
        logger.error(f"Error in background grading: {e}")
        # Update session to error state
        session = db.query(AIGradingSession).filter_by(id=session_id).first()
        if session:
            session.status = "error"
            db.commit()


# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/sessions/start")
async def start_grading_session(
    request: StartGradingRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """
    Start a new AI grading session

    1. Fetch submissions from Canvas
    2. Create grading session in database
    3. Start background task to grade all submissions
    4. Return session ID for progress tracking
    """

    # Get Canvas credentials
    canvas_creds = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

    if not canvas_creds:
        raise HTTPException(status_code=400, detail="Canvas not connected")

    try:
        # Decrypt Canvas token
        canvas_token = decrypt_token(canvas_creds.access_token_encrypted)

        # Initialize Canvas integration
        canvas = CanvasGradingIntegration(
            canvas_url=canvas_creds.canvas_url,
            canvas_token=canvas_token
        )

        # Fetch submissions
        submissions = canvas.get_assignment_submissions(
            course_id=request.course_id,
            assignment_id=request.assignment_id
        )

        if not submissions:
            raise HTTPException(status_code=404, detail="No submissions found for this assignment")

        # Get assignment details
        assignment = canvas.get_assignment_details(
            course_id=request.course_id,
            assignment_id=request.assignment_id
        )

        # Create grading session
        session = AIGradingSession(
            user_id=user_id,
            course_id=request.course_id,
            assignment_id=request.assignment_id,
            assignment_title=assignment.get("name", "Unknown Assignment"),
            rubric=request.rubric,
            preferences=request.preferences,
            status="in_progress",
            total_submissions=len(submissions)
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        logger.info(f"Created grading session {session.id} for {len(submissions)} submissions")

        # Start background grading
        background_tasks.add_task(
            grade_submissions_background,
            session_id=session.id,
            submissions=submissions,
            rubric=request.rubric,
            preferences=request.preferences,
            db=db
        )

        return {
            "session_id": session.id,
            "total_submissions": len(submissions),
            "status": "started",
            "assignment_title": assignment.get("name")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting grading session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start grading: {str(e)}")


@router.get("/sessions/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Get status of grading session (for progress tracking UI)"""

    session = db.query(AIGradingSession).filter_by(
        id=session_id,
        user_id=user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    progress_percent = 0
    if session.total_submissions > 0:
        progress_percent = (session.graded_count / session.total_submissions) * 100

    return SessionStatusResponse(
        session_id=session.id,
        status=session.status,
        total_submissions=session.total_submissions,
        graded_count=session.graded_count,
        progress_percent=round(progress_percent, 2)
    )


@router.get("/sessions/{session_id}/grades")
async def get_session_grades(
    session_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Get all grades for a session (for review interface)"""

    session = db.query(AIGradingSession).filter_by(
        id=session_id,
        user_id=user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    grades = db.query(AIGrade).filter_by(session_id=session_id).all()

    return {
        "session": {
            "id": session.id,
            "status": session.status,
            "assignment_title": session.assignment_title,
            "average_score": session.average_score,
            "total_submissions": session.total_submissions,
            "flagged_count": session.flagged_count,
            "reviewed_count": session.reviewed_count
        },
        "grades": [
            {
                "id": g.id,
                "student_id": g.student_id,
                "student_name": g.student_name,
                "submission_id": g.submission_id,
                "ai_total_score": g.ai_total_score,
                "ai_rubric_scores": g.ai_rubric_scores,
                "ai_feedback": g.ai_feedback,
                "ai_criterion_feedback": g.ai_criterion_feedback,
                "ai_confidence": g.ai_confidence,
                "ai_flags": g.ai_flags,
                "reviewed": g.reviewed,
                "final_score": g.final_score,
                "final_feedback": g.final_feedback
            }
            for g in grades
        ]
    }


@router.put("/grades/{grade_id}/review")
async def review_grade(
    grade_id: int,
    request: ReviewGradeRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Professor reviews and approves/edits a grade"""

    grade = db.query(AIGrade).filter_by(id=grade_id).first()

    if not grade:
        raise HTTPException(status_code=404, detail="Grade not found")

    # Verify ownership through session
    session = db.query(AIGradingSession).filter_by(
        id=grade.session_id,
        user_id=user_id
    ).first()

    if not session:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Update grade
    grade.reviewed = True
    grade.reviewed_at = datetime.utcnow()
    grade.final_score = request.final_score

    if request.final_feedback:
        grade.final_feedback = request.final_feedback
    else:
        grade.final_feedback = grade.ai_feedback

    if request.adjustments:
        grade.professor_adjustments = request.adjustments

    # Update session reviewed count
    session.reviewed_count = db.query(AIGrade).filter_by(
        session_id=session.id,
        reviewed=True
    ).count()

    db.commit()

    return {
        "status": "success",
        "grade_id": grade.id,
        "final_score": grade.final_score
    }


@router.post("/grades/{grade_id}/regenerate")
async def regenerate_grade(
    grade_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Regenerate AI grade for a submission"""

    grade = db.query(AIGrade).filter_by(id=grade_id).first()

    if not grade:
        raise HTTPException(status_code=404, detail="Grade not found")

    session = db.query(AIGradingSession).filter_by(
        id=grade.session_id,
        user_id=user_id
    ).first()

    if not session:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        # Re-grade the submission
        engine = AIGradingEngine(rubric=session.rubric, preferences=session.preferences)

        result = await engine.grade_submission(
            submission_text=grade.submission_text,
            student_name=grade.student_name
        )

        # Update grade with new results
        grade.ai_total_score = result.get("total_score")
        grade.ai_rubric_scores = result.get("rubric_scores")
        grade.ai_feedback = result.get("feedback")
        grade.ai_criterion_feedback = result.get("criterion_feedback")
        grade.ai_confidence = result.get("confidence", "low")
        grade.ai_flags = result.get("flags", [])

        db.commit()

        return {
            "status": "success",
            "ai_total_score": grade.ai_total_score,
            "ai_feedback": grade.ai_feedback
        }

    except Exception as e:
        logger.error(f"Error regenerating grade: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to regenerate: {str(e)}")


@router.post("/sessions/{session_id}/post-to-canvas")
async def post_grades_to_canvas(
    session_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Post all reviewed grades to Canvas"""

    session = db.query(AIGradingSession).filter_by(
        id=session_id,
        user_id=user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get all reviewed grades that haven't been posted
    grades = db.query(AIGrade).filter_by(
        session_id=session_id,
        reviewed=True,
        posted_to_canvas=False
    ).all()

    if not grades:
        raise HTTPException(status_code=400, detail="No reviewed grades to post")

    # Get Canvas credentials
    canvas_creds = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

    if not canvas_creds:
        raise HTTPException(status_code=400, detail="Canvas not connected")

    try:
        # Decrypt token
        canvas_token = decrypt_token(canvas_creds.access_token_encrypted)

        # Initialize Canvas integration
        canvas = CanvasGradingIntegration(
            canvas_url=canvas_creds.canvas_url,
            canvas_token=canvas_token
        )

        # Prepare grades for posting
        canvas_grades = [
            {
                "student_id": g.student_id,
                "score": g.final_score if g.final_score is not None else g.ai_total_score,
                "comment": g.final_feedback if g.final_feedback else g.ai_feedback
            }
            for g in grades
        ]

        # Post grades to Canvas
        results = canvas.post_grades_batch(
            course_id=session.course_id,
            assignment_id=session.assignment_id,
            grades=canvas_grades
        )

        # Update grades as posted
        for grade in grades:
            if any(r.get("student_id") == grade.student_id for r in results["success"]):
                grade.posted_to_canvas = True
                grade.posted_at = datetime.utcnow()

        # Update session
        session.posted_count = results["success_count"]
        if session.reviewed_count == session.posted_count:
            session.status = "posted"
            session.posted_at = datetime.utcnow()

        db.commit()

        return {
            "success_count": results["success_count"],
            "failed_count": results["failed_count"],
            "failed_submissions": results["failed"]
        }

    except Exception as e:
        logger.error(f"Error posting grades to Canvas: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to post grades: {str(e)}")


@router.get("/assignments/ready-to-grade")
async def get_assignments_ready_to_grade(
    course_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_grading_user)
):
    """Get list of assignments that have submissions ready to grade"""

    canvas_creds = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

    if not canvas_creds:
        raise HTTPException(status_code=400, detail="Canvas not connected")

    try:
        canvas_token = decrypt_token(canvas_creds.access_token_encrypted)
        canvas = CanvasGradingIntegration(
            canvas_url=canvas_creds.canvas_url,
            canvas_token=canvas_token
        )

        if course_id:
            # Get assignments for specific course
            assignments = canvas.get_course_assignments(course_id=course_id)

            # Filter to only those needing grading
            ready_to_grade = [
                a for a in assignments
                if a.get("needs_grading_count", 0) > 0
            ]

            return {"assignments": ready_to_grade}

        else:
            # Get all courses and their assignments
            # TODO: Implement when we have course listing
            return {"assignments": []}

    except Exception as e:
        logger.error(f"Error fetching assignments: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch assignments: {str(e)}")
