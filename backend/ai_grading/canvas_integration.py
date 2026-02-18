"""
Canvas LMS Integration for AI Grading

Handles fetching submissions from Canvas and posting grades back.
"""

import requests
import io
import re
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


def _parse_pdf_bytes(data: bytes) -> str:
    """Extract text from PDF bytes using PyPDF2."""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(data))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except Exception as e:
        logger.warning(f"PDF parsing failed: {e}")
        return ""


def _parse_docx_bytes(data: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    try:
        import docx
        doc = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs).strip()
    except Exception as e:
        logger.warning(f"DOCX parsing failed: {e}")
        return ""


def _parse_xlsx_bytes(data: bytes) -> str:
    """Extract text from Excel bytes - read cell values as plain text."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                row_text = "\t".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    lines.append(row_text)
        return "\n".join(lines).strip()
    except Exception as e:
        logger.warning(f"Excel parsing failed: {e}")
        return ""


class CanvasGradingIntegration:
    """
    Handles Canvas API interactions for AI grading workflow

    Responsibilities:
    - Fetch assignment details and submissions
    - Post grades and comments to Canvas
    - Handle Canvas API authentication and errors
    """

    def __init__(self, canvas_url: str, canvas_token: str):
        self.canvas_url = canvas_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {canvas_token}",
            "Content-Type": "application/json"
        }
        self.api_base = f"{self.canvas_url}/api/v1"

    def get_assignment_details(self, course_id: str, assignment_id: str) -> Dict:
        """Get assignment details including rubric"""
        url = f"{self.api_base}/courses/{course_id}/assignments/{assignment_id}"
        params = {"include[]": ["rubric", "rubric_assessment"]}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            assignment = response.json()

            return {
                "id": assignment.get("id"),
                "name": assignment.get("name"),
                "description": assignment.get("description"),
                "points_possible": assignment.get("points_possible"),
                "due_at": assignment.get("due_at"),
                "rubric": assignment.get("rubric", []),
                "submission_types": assignment.get("submission_types", [])
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch assignment details: {e}")
            raise Exception(f"Canvas API error: {str(e)}")

    def get_assignment_submissions(
        self,
        course_id: str,
        assignment_id: str,
        include_unsubmitted: bool = False
    ) -> List[Dict]:
        """Fetch all submissions for an assignment, parsing file attachments."""
        url = f"{self.api_base}/courses/{course_id}/assignments/{assignment_id}/submissions"
        params = {
            "include[]": ["user", "submission_comments", "rubric_assessment"],
            "per_page": 100
        }

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            submissions = response.json()

            formatted = []
            for sub in submissions:
                workflow_state = sub.get("workflow_state", "")

                if workflow_state not in ["submitted", "graded"] and not include_unsubmitted:
                    continue

                submission_text = self._extract_submission_text(sub)

                if not submission_text or len(submission_text.strip()) < 10:
                    continue

                formatted.append({
                    "submission_id": str(sub.get("id")),
                    "student_id": str(sub.get("user_id")),
                    "student_name": sub.get("user", {}).get("name", "Unknown Student"),
                    "student_email": sub.get("user", {}).get("email"),
                    "submission_text": submission_text,
                    "submitted_at": sub.get("submitted_at"),
                    "workflow_state": workflow_state,
                    "score": sub.get("score"),
                    "attachments": sub.get("attachments", []),
                    "submission_type": sub.get("submission_type")
                })

            logger.info(f"Fetched {len(formatted)} submissions for assignment {assignment_id}")
            return formatted

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch submissions: {e}")
            raise Exception(f"Canvas API error: {str(e)}")

    def _extract_submission_text(self, submission: Dict) -> str:
        """
        Extract text content from various submission types.
        Supports: text entry, PDF, DOCX, TXT, Excel, URL submissions.
        """
        submission_type = submission.get("submission_type")

        # ── Plain text submission ──────────────────────────────────────────
        if submission_type == "online_text_entry":
            body = submission.get("body", "")
            text = re.sub(r'<[^>]+>', ' ', body)
            text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            return re.sub(r'\s+', ' ', text).strip()

        # ── File upload ────────────────────────────────────────────────────
        elif submission_type == "online_upload":
            attachments = submission.get("attachments", [])
            all_text = []

            for att in attachments:
                file_url = att.get("url") or att.get("download_url")
                filename = att.get("filename", "").lower()
                content_type = att.get("content-type", "").lower()

                if not file_url:
                    continue

                try:
                    file_resp = requests.get(
                        file_url,
                        headers={"Authorization": self.headers["Authorization"]},
                        timeout=60
                    )
                    file_resp.raise_for_status()
                    data = file_resp.content

                    # PDF
                    if "pdf" in content_type or filename.endswith(".pdf"):
                        text = _parse_pdf_bytes(data)
                        if text:
                            all_text.append(f"[PDF: {att.get('filename')}]\n{text}")

                    # DOCX / DOC
                    elif "word" in content_type or filename.endswith((".docx", ".doc")):
                        text = _parse_docx_bytes(data)
                        if text:
                            all_text.append(f"[DOCX: {att.get('filename')}]\n{text}")

                    # Excel / CSV
                    elif "spreadsheet" in content_type or "excel" in content_type or filename.endswith((".xlsx", ".xls")):
                        text = _parse_xlsx_bytes(data)
                        if text:
                            all_text.append(f"[Excel: {att.get('filename')}]\n{text}")

                    elif filename.endswith(".csv"):
                        text = data.decode("utf-8", errors="ignore")
                        if text.strip():
                            all_text.append(f"[CSV: {att.get('filename')}]\n{text.strip()}")

                    # Plain text
                    elif "text/plain" in content_type or filename.endswith(".txt"):
                        text = data.decode("utf-8", errors="ignore").strip()
                        if text:
                            all_text.append(f"[TXT: {att.get('filename')}]\n{text}")

                    else:
                        logger.info(f"Unsupported file type: {filename} ({content_type})")

                except Exception as e:
                    logger.warning(f"Could not parse attachment {filename}: {e}")
                    all_text.append(f"[Could not read: {att.get('filename')}]")

            return "\n\n".join(all_text) if all_text else ""

        # ── URL submission ─────────────────────────────────────────────────
        elif submission_type == "online_url":
            url = submission.get("url", "")
            return f"[URL submission: {url}]\n(URL content not fetched — professor should review manually)"

        return ""

    def post_grade(
        self,
        course_id: str,
        assignment_id: str,
        student_id: str,
        score: float,
        comment: Optional[str] = None,
        rubric_assessment: Optional[Dict] = None
    ) -> Dict:
        """Post a grade to Canvas for a specific student"""
        url = f"{self.api_base}/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}"

        data = {"submission": {"posted_grade": str(score)}}

        if comment:
            data["comment"] = {"text_comment": comment}

        if rubric_assessment:
            data["rubric_assessment"] = rubric_assessment

        try:
            response = requests.put(url, headers=self.headers, json=data, timeout=30)
            response.raise_for_status()
            logger.info(f"Posted grade {score} for student {student_id}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to post grade for student {student_id}: {e}")
            raise Exception(f"Canvas API error: {str(e)}")

    def post_grades_batch(self, course_id: str, assignment_id: str, grades: List[Dict]) -> Dict:
        """Post multiple grades at once"""
        results = {"success": [], "failed": [], "success_count": 0, "failed_count": 0}

        for grade in grades:
            try:
                result = self.post_grade(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    student_id=grade["student_id"],
                    score=grade["score"],
                    comment=grade.get("comment"),
                    rubric_assessment=grade.get("rubric_assessment")
                )
                results["success"].append({"student_id": grade["student_id"], "score": grade["score"], "canvas_response": result})
                results["success_count"] += 1
            except Exception as e:
                logger.error(f"Failed to post grade for {grade['student_id']}: {e}")
                results["failed"].append({"student_id": grade["student_id"], "score": grade["score"], "error": str(e)})
                results["failed_count"] += 1

        logger.info(f"Batch grading complete: {results['success_count']} success, {results['failed_count']} failed")
        return results

    def get_teacher_courses(self) -> List[Dict]:
        """Get all courses where the user is a teacher/TA."""
        url = f"{self.api_base}/courses"
        params = {
            "enrollment_type": "teacher",
            "enrollment_state": "active",
            "state[]": ["available"],
            "per_page": 100,
        }
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            courses = response.json()
            return [
                {
                    "id": str(c.get("id")),
                    "name": c.get("name", "Unnamed Course"),
                    "course_code": c.get("course_code", ""),
                }
                for c in courses
                if c.get("id")
            ]
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch teacher courses: {e}")
            raise Exception(f"Canvas API error: {str(e)}")

    def get_course_assignments(self, course_id: str, include_ungraded: bool = True) -> List[Dict]:
        """Get all assignments for a course"""
        url = f"{self.api_base}/courses/{course_id}/assignments"
        params = {"per_page": 100, "order_by": "due_at"}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            assignments = response.json()

            formatted = []
            for assignment in assignments:
                if not include_ungraded and not assignment.get("has_submitted_submissions", False):
                    continue
                formatted.append({
                    "id": assignment.get("id"),
                    "name": assignment.get("name"),
                    "points_possible": assignment.get("points_possible"),
                    "due_at": assignment.get("due_at"),
                    "needs_grading_count": assignment.get("needs_grading_count", 0),
                    "published": assignment.get("published", False)
                })
            return formatted

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch course assignments: {e}")
            raise Exception(f"Canvas API error: {str(e)}")

    def get_submission_count(self, course_id: str, assignment_id: str) -> Dict:
        """Get submission statistics for an assignment"""
        try:
            submissions = self.get_assignment_submissions(
                course_id=course_id,
                assignment_id=assignment_id,
                include_unsubmitted=True
            )
            return {
                "total_students": len(submissions),
                "submitted": sum(1 for s in submissions if s["workflow_state"] in ["submitted", "graded"]),
                "graded": sum(1 for s in submissions if s.get("score") is not None),
                "needs_grading": sum(1 for s in submissions if s["workflow_state"] == "submitted" and s.get("score") is None)
            }
        except Exception as e:
            logger.error(f"Failed to get submission count: {e}")
            raise
