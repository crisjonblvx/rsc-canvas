"""
Canvas LMS API Client
Complete Canvas API wrapper for ReadySetClass

Built for: ReadySetClass v2.0
Based on: Official Canvas API documentation
Documentation: https://canvas.instructure.com/doc/api/
"""

import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
from rate_limiter import RateLimiter


class CanvasClient:
    """
    Canvas LMS API Client
    Provides methods for all Canvas operations needed by ReadySetClass
    """

    def __init__(self, base_url: str, access_token: str):
        """
        Initialize Canvas API client

        Args:
            base_url: Canvas instance URL (e.g., "https://vuu.instructure.com")
            access_token: Canvas API access token
        """
        self.base_url = base_url.rstrip('/')
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        self.rate_limiter = RateLimiter(max_requests=3000, window=3600)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Optional[Any]:
        """
        Make an API request with rate limiting

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., "/api/v1/courses")
            data: JSON data for POST/PUT requests
            params: Query parameters

        Returns:
            Response JSON or None if failed
        """
        # Rate limiting
        self.rate_limiter.wait_if_needed()

        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=data,
                params=params,
                timeout=30
            )

            # Handle different status codes
            if response.status_code == 200 or response.status_code == 201:
                return response.json()
            elif response.status_code == 401:
                print("ERROR: Unauthorized - Invalid or expired token")
                return None
            elif response.status_code == 403:
                print("ERROR: Forbidden - Insufficient permissions")
                return None
            elif response.status_code == 404:
                print("ERROR: Not found - Resource doesn't exist")
                return None
            elif response.status_code == 400:
                print(f"ERROR: Bad Request (400)")
                print(f"Response: {response.text}")
                try:
                    error_data = response.json()
                    print(f"Error details: {error_data}")
                except:
                    pass
                return None
            elif response.status_code == 422:
                print(f"ERROR: Validation error - {response.text}")
                return None
            elif response.status_code == 429:
                print("ERROR: Rate limited")
                return None
            else:
                print(f"ERROR: Unexpected status {response.status_code}")
                print(f"Response: {response.text}")
                return None

        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return None

    # ==========================================================================
    # COURSES
    # ==========================================================================

    def get_user_courses(self) -> List[Dict]:
        """
        Get all courses assigned to the authenticated user (professor)

        Returns:
            list: List of course objects
        """
        courses = self._make_request(
            method="GET",
            endpoint="/api/v1/courses",
            params={
                "enrollment_type": "teacher",  # Only courses they teach
                "enrollment_state": "active",  # Only active enrollments
                "state[]": ["available", "completed"],
                "include[]": ["term", "total_students"]
            }
        )
        return courses if courses else []

    # ==========================================================================
    # QUIZZES
    # ==========================================================================

    def create_quiz(self, course_id: int, quiz_data: Dict) -> Optional[int]:
        """
        Create a new quiz in the specified course

        Args:
            course_id: Canvas course ID
            quiz_data: Quiz configuration dict with keys:
                - title: Quiz title
                - quiz_type: "assignment", "practice_quiz", or "graded_survey"
                - time_limit: Time limit in minutes (optional)
                - allowed_attempts: Number of attempts (optional)
                - points_possible: Total points (optional)
                - due_at: Due date in ISO 8601 format (optional)

        Returns:
            int: Quiz ID if successful, None if failed
        """
        result = self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/quizzes",
            data={
                "quiz": {
                    "title": quiz_data["title"],
                    "quiz_type": quiz_data.get("quiz_type", "assignment"),
                    "time_limit": quiz_data.get("time_limit"),
                    "allowed_attempts": quiz_data.get("allowed_attempts", 1),
                    "points_possible": quiz_data.get("points_possible", 10),
                    "due_at": quiz_data.get("due_at"),
                    "published": False  # Create as draft
                }
            }
        )

        return result["id"] if result else None

    def add_quiz_question(
        self,
        course_id: int,
        quiz_id: int,
        question_data: Dict
    ) -> Optional[Dict]:
        """
        Add a question to a quiz

        Args:
            course_id: Canvas course ID
            quiz_id: Canvas quiz ID
            question_data: Question dict with keys:
                - name: Question name/title
                - text: Question text
                - type: Question type (e.g., "multiple_choice_question")
                - points: Points for this question
                - answers: List of answer dicts

        Returns:
            dict: Question object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions",
            data={
                "question": {
                    "question_name": question_data["name"],
                    "question_text": question_data["text"],
                    "question_type": question_data["type"],
                    "points_possible": question_data.get("points", 1),
                    "answers": question_data["answers"]
                }
            }
        )

    # ==========================================================================
    # ANNOUNCEMENTS
    # ==========================================================================

    def create_announcement(
        self,
        course_id: int,
        announcement_data: Dict
    ) -> Optional[Dict]:
        """
        Create an announcement in a course

        Args:
            course_id: Canvas course ID
            announcement_data: Announcement dict with keys:
                - title: Announcement title
                - message: Announcement message (HTML allowed)
                - schedule_for: Optional datetime to schedule for later

        Returns:
            dict: Announcement object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/discussion_topics",
            data={
                "title": announcement_data["title"],
                "message": announcement_data["message"],
                "is_announcement": True,
                "published": True,
                "delayed_post_at": announcement_data.get("schedule_for")
            }
        )

    # ==========================================================================
    # ASSIGNMENTS
    # ==========================================================================

    def create_assignment(
        self,
        course_id: int,
        assignment_data: Dict
    ) -> Optional[Dict]:
        """
        Create an assignment in a course

        Args:
            course_id: Canvas course ID
            assignment_data: Assignment dict with keys:
                - title: Assignment title
                - description: Assignment description (HTML allowed)
                - points: Points possible
                - due_date: Due date in ISO 8601 format

        Returns:
            dict: Assignment object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/assignments",
            data={
                "assignment": {
                    "name": assignment_data["title"],
                    "description": assignment_data.get("description", ""),
                    "points_possible": assignment_data.get("points", 100),
                    "due_at": assignment_data.get("due_date"),
                    "submission_types": ["online_text_entry", "online_upload"],
                    "published": False  # Draft first
                }
            }
        )

    # ==========================================================================
    # PAGES
    # ==========================================================================

    def create_page(self, course_id: int, page_data: Dict) -> Optional[Dict]:
        """
        Create a course page

        Args:
            course_id: Canvas course ID
            page_data: Page dict with keys:
                - title: Page title
                - content: Page content (HTML allowed)

        Returns:
            dict: Page object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/pages",
            data={
                "wiki_page": {
                    "title": page_data["title"],
                    "body": page_data["content"],
                    "published": False  # Draft first
                }
            }
        )

    # ==========================================================================
    # MODULES
    # ==========================================================================

    def get_modules(self, course_id: int) -> List[Dict]:
        """
        Get all modules in a course

        Args:
            course_id: Canvas course ID

        Returns:
            list: List of module objects
        """
        modules = self._make_request(
            method="GET",
            endpoint=f"/api/v1/courses/{course_id}/modules"
        )
        return modules if modules else []

    def create_module(self, course_id: int, module_data: Dict) -> Optional[Dict]:
        """
        Create a module in a course

        Args:
            course_id: Canvas course ID
            module_data: Module dict with keys:
                - name: Module name (e.g., "Week 1")
                - position: Module position (1, 2, 3...)

        Returns:
            dict: Module object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/modules",
            data={
                "module": {
                    "name": module_data["name"],
                    "position": module_data.get("position", 1)
                }
            }
        )

    def add_module_item(
        self,
        course_id: int,
        module_id: int,
        item_data: Dict
    ) -> Optional[Dict]:
        """
        Add an item to a module

        Args:
            course_id: Canvas course ID
            module_id: Canvas module ID
            item_data: Item dict with keys:
                - type: Item type ("Assignment", "Quiz", "Page", "Discussion", "File")
                - content_id: ID of the content (assignment_id, quiz_id, etc.)
                - title: Item title

        Returns:
            dict: Module item object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/modules/{module_id}/items",
            data={
                "module_item": {
                    "type": item_data["type"],
                    "content_id": item_data["content_id"],
                    "title": item_data.get("title", "")
                }
            }
        )

    # ==========================================================================
    # DISCUSSIONS
    # ==========================================================================

    def create_discussion(
        self,
        course_id: int,
        discussion_data: Dict
    ) -> Optional[Dict]:
        """
        Create a discussion topic (not an announcement)

        Args:
            course_id: Canvas course ID
            discussion_data: Discussion dict with keys:
                - title: Discussion title
                - message: Discussion message (HTML allowed)

        Returns:
            dict: Discussion object if successful, None if failed
        """
        return self._make_request(
            method="POST",
            endpoint=f"/api/v1/courses/{course_id}/discussion_topics",
            data={
                "title": discussion_data["title"],
                "message": discussion_data["message"],
                "is_announcement": False,  # Regular discussion
                "published": False  # Draft first
            }
        )

    # ==========================================================================
    # SYLLABUS
    # ==========================================================================

    def update_syllabus(self, course_id: int, syllabus_body: str) -> Optional[Dict]:
        """
        Update the course syllabus

        Args:
            course_id: Canvas course ID
            syllabus_body: Syllabus content (HTML allowed)

        Returns:
            dict: Course object if successful, None if failed
        """
        return self._make_request(
            method="PUT",
            endpoint=f"/api/v1/courses/{course_id}",
            data={
                "course": {
                    "syllabus_body": syllabus_body
                }
            }
        )
