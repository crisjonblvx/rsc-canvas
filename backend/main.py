"""
ReadySetClass Backend API
FastAPI server with Bonita AI integration

Built by: Sonny (Claude) for CJ
Purpose: Power ReadySetClass SaaS with smart AI routing
Less time setting up. More time teaching.
"""

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any
import os
import anthropic
import requests
import hashlib
import secrets
from datetime import datetime, timedelta
from jose import jwt
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, TIMESTAMP, text
from sqlalchemy.ext.declarative import declarative_base
from openai import OpenAI
from groq import Groq
import bcrypt
import psycopg2
import stripe

# ReadySetClass v2.0 imports
from database import init_db, get_db, CanvasCredentials, UserCourse
from canvas_client import CanvasClient
from canvas_auth import CanvasAuth, encrypt_token, decrypt_token
from grading_setup import GradingSetupService, GRADING_TEMPLATES, get_template

# Initialize FastAPI
app = FastAPI(
    title="ReadySetClass API",
    description="AI Course Builder for Canvas - Less time setting up. More time teaching.",
    version="2.0.0"
)

# Database initialization on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database tables on startup"""
    init_db()

# CORS middleware - Allow readysetclass.app and all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.readysetclass.app",
        "https://readysetclass.app",
        "https://student.readysetclass.app",
        "https://www.readysetclass.com",
        "https://readysetclass.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "*"  # Allow all for development
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Cache-control middleware - prevent stale responses
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Security
security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"

# AI Clients - Support OpenAI, Groq (FREE!), and Anthropic
openai_client = None
groq_client = None
anthropic_client = None

# Initialize OpenAI (preferred - cheap and high quality)
if os.getenv("OPENAI_API_KEY"):
    try:
        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print("✅ OpenAI client initialized (GPT-4o-mini - $0.002/assignment)")
    except Exception as e:
        print(f"⚠️  OpenAI initialization failed: {e}")

# Initialize Groq (second choice - FREE!)
if os.getenv("GROQ_API_KEY"):
    try:
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        print("✅ Groq client initialized (Llama 3.3 70B - FREE!)")
    except Exception as e:
        print(f"⚠️  Groq initialization failed: {e}")
        print("   This is a known compatibility issue with Python 3.13")
        print("   Using OpenAI or Anthropic instead")

# Initialize Anthropic (fallback - expensive but highest quality)
if os.getenv("ANTHROPIC_API_KEY"):
    try:
        anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        print("✅ Anthropic client initialized (Claude Sonnet - $0.05/assignment)")
    except Exception as e:
        print(f"⚠️  Anthropic initialization failed: {e}")

if not openai_client and not groq_client and not anthropic_client:
    print("⚠️  No AI API keys found. AI features will not work.")
    print("   Set one of: OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY")

# ============================================================================
# LANGUAGE SUPPORT
# ============================================================================

# Language code to full name mapping for AI content generation
LANGUAGE_MAP = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "ar": "Arabic",
    "zh": "Chinese"
}

# ============================================================================
# DATA MODELS
# ============================================================================

class CourseRequest(BaseModel):
    course_name: str
    course_code: str
    credits: int
    description: str
    objectives: List[str]
    weeks: int
    schedule: str
    canvas_course_id: str

class UserSignup(BaseModel):
    email: EmailStr
    password: str
    full_name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class CanvasToken(BaseModel):
    canvas_url: str
    api_token: str

class CourseResponse(BaseModel):
    course_id: str
    canvas_course_id: str
    status: str
    cost: float
    time_saved_hours: int
    created_at: str

# ============================================================================
# AUTHENTICATION
# ============================================================================

def create_access_token(data: dict, expires_delta: timedelta = timedelta(days=30)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============================================================================
# GRADE LEVEL HELPER
# ============================================================================

def get_reading_level_instructions(grade_level: str) -> Dict[str, str]:
    """Return detailed instructions for AI based on grade level"""

    levels = {
        'elementary-k2': {
            'lexile': 'Lexile 0-300 (Beginning Reader)',
            'instructions': """
- Use VERY simple words (one or two syllables)
- Write SHORT sentences (5-8 words max)
- Use concrete, relatable examples (toys, pets, family, school)
- Avoid abstract concepts
- Include visual descriptions
- Use encouraging, friendly language
- No complex vocabulary
- Reading level: Ages 5-7""",
            'example_words': 'Use words like: see, run, play, big, small, happy, sad'
        },

        'elementary-35': {
            'lexile': 'Lexile 300-700 (Elementary)',
            'instructions': """
- Use clear, simple language
- Short to medium sentences (8-12 words)
- Examples relevant to elementary students (playground, classrooms, cartoons)
- Limited complex vocabulary (define new words)
- Step-by-step instructions
- Positive, encouraging tone
- Reading level: Ages 8-10""",
            'example_words': 'Use words like: understand, explain, compare, describe, identify'
        },

        'middle-68': {
            'lexile': 'Lexile 700-1000 (Middle School)',
            'instructions': """
- Age-appropriate vocabulary for pre-teens
- Medium sentences (10-15 words)
- Examples relevant to middle schoolers (social media, sports, music, friends)
- Can introduce some academic vocabulary
- Clear but less simplified
- Respectful, not condescending
- Reading level: Ages 11-13""",
            'example_words': 'Use words like: analyze, evaluate, interpret, demonstrate, illustrate'
        },

        'high-912': {
            'lexile': 'Lexile 1000-1300 (High School)',
            'instructions': """
- High school appropriate vocabulary
- College-prep level content
- Examples relevant to teens (college, careers, current events)
- Academic language acceptable
- Can assume background knowledge
- Professional but not overly formal
- Reading level: Ages 14-18""",
            'example_words': 'Use words like: synthesize, critique, justify, formulate, assess'
        },

        'college': {
            'lexile': 'Lexile 1300+ (College/University)',
            'instructions': """
- Academic/professional vocabulary
- Complex sentence structures acceptable
- University-level examples and concepts
- Discipline-specific terminology
- Assumes advanced background knowledge
- Formal academic tone
- Reading level: University students""",
            'example_words': 'Use words like: paradigm, methodology, theoretical framework, empirical'
        }
    }

    return levels.get(grade_level, levels['college'])

# ============================================================================
# BONITA AI ENGINE
# ============================================================================

class BonitaEngine:
    """
    Smart AI routing for ReadySetClass
    Supports: OpenAI (cheap), Groq (FREE!), Anthropic (premium)
    Provider priority: OpenAI > Groq > Anthropic
    """

    def __init__(self):
        self.openai_client = openai_client
        self.groq_client = groq_client
        self.anthropic_client = anthropic_client
        self.cost_tracker = {
            "syllabus": 0,
            "lesson_plans": 0,
            "quizzes": 0,
            "study_packs": 0,
            "total": 0
        }

    def call_ai(self, prompt: str, system: str = "") -> tuple[str, float]:
        """
        Call AI provider with automatic fallback
        Priority: OpenAI > Groq (FREE!) > Anthropic
        Returns: (response_text, cost)
        """
        # Try OpenAI first (cheapest paid option - $0.002/assignment)
        if self.openai_client:
            try:
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system} if system else {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2048,
                    temperature=0.7
                )

                # Calculate cost for GPT-4o-mini
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens
                cost = (input_tokens / 1_000_000 * 0.15) + (output_tokens / 1_000_000 * 0.60)

                print(f"✅ OpenAI response (cost: ${cost:.4f})")
                return response.choices[0].message.content, cost
            except Exception as e:
                print(f"⚠️  OpenAI failed: {e}, trying Groq...")

        # Try Groq second (FREE! 🎉)
        if self.groq_client:
            try:
                response = self.groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",  # Fast, high quality, FREE!
                    messages=[
                        {"role": "system", "content": system} if system else {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2048,
                    temperature=0.7
                )

                # Groq is FREE!
                cost = 0.0

                print(f"✅ Groq response (cost: FREE!)")
                return response.choices[0].message.content, cost
            except Exception as e:
                print(f"⚠️  Groq failed: {e}, falling back to Claude...")

        # Fallback to Claude (most expensive but highest quality)
        if self.anthropic_client:
            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": prompt}]
            )

            # Calculate cost for Claude
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens / 1_000_000 * 3) + (output_tokens / 1_000_000 * 15)

            print(f"✅ Claude response (cost: ${cost:.4f})")
            return response.content[0].text, cost

        raise Exception("No AI provider available. Please set OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY")

    # Keep old method name for backward compatibility
    def call_claude(self, prompt: str, system: str = "") -> tuple[str, float]:
        """Alias for call_ai() for backward compatibility"""
        return self.call_ai(prompt, system)
    
    def call_qwen_local(self, prompt: str) -> tuple[str, float]:
        """Use Qwen local for structured content (FREE!)"""
        try:
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "qwen2.5:32b",
                    "prompt": prompt,
                    "stream": False
                },
                timeout=60
            )
            response.raise_for_status()
            return response.json()["response"], 0.0  # FREE!
        except Exception as e:
            # Fallback to Claude if local fails
            print(f"Qwen local failed: {e}, falling back to Claude")
            return self.call_claude(prompt)
    
    def generate_syllabus(self, course_data: Dict) -> str:
        """Generate complete syllabus (Claude - quality matters)"""
        system = """You are Bonita, an AI assistant helping professors create course content.
Your output should be professional, engaging, and practical - written for college students."""
        
        prompt = f"""Create a complete course syllabus for:

Course: {course_data['course_name']}
Code: {course_data['course_code']}
Credits: {course_data['credits']}
Description: {course_data['description']}
Learning Objectives: {', '.join(course_data['objectives'])}
Duration: {course_data['weeks']} weeks
Schedule: {course_data['schedule']}

Include:
1. Course Overview (2-3 engaging paragraphs)
2. Learning Objectives (formatted list)
3. Weekly Schedule Overview
4. Grading Breakdown (suggest reasonable percentages)
5. Attendance Policy (professional but fair)
6. Academic Integrity Statement
7. Required Materials (if applicable)

Format in clean HTML suitable for Canvas. Use headers, lists, and tables for clarity.
Keep it practical and student-focused - no academic jargon."""
        
        syllabus, cost = self.call_claude(prompt, system)
        self.cost_tracker["syllabus"] += cost
        return syllabus
    
    def generate_lesson_plan(self, week: int, topic: str, objectives: List[str]) -> Dict:
        """Generate lesson plan (Qwen - structured task, FREE!)"""
        prompt = f"""Create a lesson plan for Week {week}:

Topic: {topic}
Learning Objectives:
{chr(10).join(f"- {obj}" for obj in objectives)}

Include:
1. Week Overview (2-3 sentences)
2. Key Concepts (5-7 bullet points)
3. In-Class Activities (2-3 activities)
4. Discussion Prompts (3 thought-provoking questions)
5. Homework/Assignment (if applicable)

Format in clean HTML for Canvas. Keep it practical and actionable."""
        
        lesson, cost = self.call_qwen_local(prompt)
        self.cost_tracker["lesson_plans"] += cost
        return {
            "week": week,
            "topic": topic,
            "content": lesson
        }
    
    def generate_quiz(
        self,
        week: int,
        topic: str,
        description: str = "",
        num_questions: int = 10,
        difficulty: str = "medium",
        grade_level: str = "college",
        language: str = "en"
    ) -> Dict:
        """Generate quiz questions with detailed context and grade-appropriate language (Groq - FREE!)"""

        # Get reading level instructions
        level_info = get_reading_level_instructions(grade_level)

        # Get language name
        language_name = LANGUAGE_MAP.get(language, "English")

        system = f"You are Bonita, an AI assistant helping educators create {grade_level} quiz questions that assess student understanding at the appropriate reading level."

        # Build difficulty distribution based on difficulty level
        if difficulty == "easy":
            diff_mix = f"{num_questions} easy questions"
        elif difficulty == "hard":
            diff_mix = f"{num_questions} challenging questions"
        else:  # medium (default)
            easy_count = max(1, num_questions // 3)
            hard_count = max(1, num_questions // 5)
            medium_count = num_questions - easy_count - hard_count
            diff_mix = f"{easy_count} easy, {medium_count} medium, {hard_count} hard questions"

        prompt = f"""Create a {num_questions}-question multiple choice quiz for {grade_level} students.

IMPORTANT: Generate ALL content in {language_name}.
The entire quiz must be in {language_name}, including questions, answer options, and all text.

TOPIC: {topic}

DETAILED CONTEXT:
{description}

GRADE LEVEL: {grade_level}
{level_info['lexile']}

CRITICAL READING LEVEL REQUIREMENTS:
{level_info['instructions']}

{level_info['example_words']}

Requirements:
- {num_questions} questions total
- 4 answer options each (A, B, C, D)
- Difficulty distribution: {diff_mix}
- Questions should test understanding of the concepts described above
- Clear, unambiguous questions at {grade_level} reading level
- One correct answer per question
- Use the detailed context to create relevant, targeted questions
- IMPORTANT: Match vocabulary and complexity to the grade level above
- CRITICAL: All content MUST be in {language_name}

Format as JSON:
{{
  "questions": [
    {{
      "question_text": "Question here?",
      "answers": [
        {{"text": "A. Option 1", "correct": false}},
        {{"text": "B. Option 2", "correct": true}},
        {{"text": "C. Option 3", "correct": false}},
        {{"text": "D. Option 4", "correct": false}}
      ]
    }}
  ]
}}

Return ONLY valid JSON, no markdown code blocks."""

        quiz_json, cost = self.call_ai(prompt, system)
        self.cost_tracker["quizzes"] += cost

        try:
            import json
            # Clean up any markdown formatting
            cleaned_json = quiz_json.strip()
            if cleaned_json.startswith("```"):
                # Remove markdown code blocks
                cleaned_json = cleaned_json.split("```")[1]
                if cleaned_json.startswith("json"):
                    cleaned_json = cleaned_json[4:]
                cleaned_json = cleaned_json.strip()

            quiz_data = json.loads(cleaned_json)
            return quiz_data
        except Exception as e:
            print(f"Error parsing quiz JSON: {e}")
            print(f"Raw response: {quiz_json}")
            return {"questions": [], "error": f"Failed to parse quiz: {str(e)}"}
    
    def generate_study_pack(self, week: int, topic: str) -> str:
        """Generate study pack with real resources (Claude + search intent)"""
        system = """You are Bonita, creating study materials for college students.
Find REAL, working resources. Do not make up URLs."""
        
        prompt = f"""Create a study pack for Week {week}: {topic}

Include:
1. Key Concepts Summary (3-5 bullet points)
2. Real-World Examples (2-3 relevant examples)
3. Discussion Questions (3-5 thought-provoking questions)
4. External Resources:
   - 2-3 articles (from reputable sources with REAL URLs)
   - 1-2 videos (YouTube links that actually exist)
   - 1-2 tools/platforms students can explore

Format as HTML for Canvas. Use embedded links. Make it engaging and practical.
Prefer recent (2020+) resources. Cite sources properly."""
        
        study_pack, cost = self.call_claude(prompt, system)
        self.cost_tracker["study_packs"] += cost
        return study_pack

# Initialize Bonita
bonita = BonitaEngine()

# ============================================================================
# CANVAS API INTEGRATION
# ============================================================================

class CanvasAPI:
    """Canvas LMS API integration"""
    
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def create_module(self, course_id: str, name: str, position: int) -> Dict:
        """Create a Canvas module"""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules"
        data = {"module": {"name": name, "position": position}}
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        return response.json()
    
    def create_assignment(self, course_id: str, name: str, description: str, 
                         points: int = 0, due_date: str = None) -> Dict:
        """Create a Canvas assignment"""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        data = {
            "assignment": {
                "name": name,
                "description": description,
                "points_possible": points,
                "due_at": due_date,
                "submission_types": ["none"],
                "published": False
            }
        }
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        return response.json()
    
    def create_quiz(self, course_id: str, title: str, description: str,
                   quiz_data: Dict, due_date: str = None) -> Dict:
        """Create a Canvas quiz with questions"""
        # 1. Create quiz
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes"
        data = {
            "quiz": {
                "title": title,
                "quiz_type": "assignment",
                "points_possible": 100,
                "time_limit": 30,
                "allowed_attempts": 2,
                "due_at": due_date,
                "description": description,
                "published": False
            }
        }
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        quiz = response.json()
        
        # 2. Add questions
        for i, q in enumerate(quiz_data.get("questions", []), 1):
            question_url = f"{url}/{quiz['id']}/questions"
            question_data = {
                "question": {
                    "question_name": f"Q{i}",
                    "question_text": q["question_text"],
                    "question_type": "multiple_choice_question",
                    "points_possible": 10,
                    "answers": [
                        {
                            "answer_text": ans["text"],
                            "answer_weight": 100 if ans.get("correct") else 0
                        }
                        for ans in q["answers"]
                    ]
                }
            }
            requests.post(question_url, headers=self.headers, json=question_data)
        
        return quiz

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    return {
        "service": "ReadySetClass API",
        "version": "2.0.0",
        "status": "operational",
        "tagline": "Less time setting up. More time teaching."
    }

@app.post("/api/auth/signup")
async def signup(user: UserSignup):
    """User signup"""
    # In production: save to database with hashed password
    # For now: return token
    token = create_access_token({"email": user.email, "name": user.full_name})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": user.email,
            "name": user.full_name
        }
    }

@app.post("/api/canvas/connect")
async def connect_canvas(canvas_data: CanvasToken, user=Depends(verify_token)):
    """Save user's Canvas credentials (encrypted)"""
    # In production: encrypt and save to database
    return {
        "status": "connected",
        "canvas_url": canvas_data.canvas_url
    }

@app.post("/api/build-course", response_model=CourseResponse)
async def build_course(course: CourseRequest, user=Depends(verify_token)):
    """
    Main endpoint: Build complete Canvas course
    This is where the magic happens!
    """
    try:
        # 1. Generate syllabus
        print(f"📋 Generating syllabus for {course.course_name}...")
        syllabus = bonita.generate_syllabus(course.dict())
        
        # 2. Generate lesson plans
        print(f"📚 Generating {course.weeks} lesson plans...")
        lesson_plans = []
        for week in range(1, course.weeks + 1):
            topic = f"Week {week} Content"  # In production: extract from syllabus or ask user
            lesson = bonita.generate_lesson_plan(week, topic, course.objectives[:2])
            lesson_plans.append(lesson)
        
        # 3. Generate quizzes
        print(f"🧪 Generating {course.weeks} quizzes...")
        quizzes = []
        for week in range(1, course.weeks + 1):
            topic = f"Week {week}"
            quiz = bonita.generate_quiz(week, topic)
            quizzes.append(quiz)
        
        # 4. Generate study packs
        print(f"📦 Generating {course.weeks} study packs...")
        study_packs = []
        for week in range(1, course.weeks + 1):
            topic = f"Week {week}"
            study_pack = bonita.generate_study_pack(week, topic)
            study_packs.append(study_pack)
        
        # 5. Upload to Canvas
        print(f"📤 Uploading to Canvas course {course.canvas_course_id}...")
        # In production: get user's Canvas credentials from database
        canvas = CanvasAPI(
            base_url="https://vuu.instructure.com",
            token=os.getenv("CANVAS_API_TOKEN")  # In production: from user's encrypted storage
        )
        
        # Create modules
        for week in range(1, course.weeks + 1):
            canvas.create_module(course.canvas_course_id, f"Week {week}", week)
        
        # Create study pack assignments
        for week, study_pack in enumerate(study_packs, 1):
            canvas.create_assignment(
                course.canvas_course_id,
                f"Week {week} Study Pack",
                study_pack,
                points=0
            )
        
        # Create quizzes
        for week, quiz_data in enumerate(quizzes, 1):
            canvas.create_quiz(
                course.canvas_course_id,
                f"Quiz {week}",
                f"Quiz covering Week {week} material",
                quiz_data
            )
        
        # Calculate total cost
        bonita.cost_tracker["total"] = sum(bonita.cost_tracker.values())
        
        print(f"✅ Course build complete! Cost: ${bonita.cost_tracker['total']:.2f}")
        
        return CourseResponse(
            course_id=secrets.token_urlsafe(16),
            canvas_course_id=course.canvas_course_id,
            status="complete",
            cost=bonita.cost_tracker["total"],
            time_saved_hours=25,
            created_at=datetime.utcnow().isoformat()
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "bonita": "online"
    }

# ============================================================================
# READYSETCLASS V2.0 - CANVAS INTEGRATION
# ============================================================================

class CanvasConnectionRequest(BaseModel):
    canvas_url: str
    access_token: str

class QuizGenerateRequest(BaseModel):
    """Request to generate quiz questions (preview only, no upload)"""
    topic: str
    description: str
    num_questions: int = 10
    difficulty: str = "medium"
    grade_level: str = "college"  # NEW: elementary-k2, elementary-35, middle-68, high-912, college
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh

class QuizUploadRequest(BaseModel):
    """Request to upload generated quiz to Canvas"""
    course_id: int
    topic: str
    questions: list  # The generated questions from preview
    num_questions: int
    due_date: Optional[str] = None

class QuizRequest(BaseModel):
    course_id: int
    topic: str
    description: str  # NEW - detailed description of what quiz should cover
    num_questions: int = 10
    difficulty: str = "medium"
    due_date: Optional[str] = None
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh

# ============================================================================
# AUTH MODELS
# ============================================================================

class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    token: str
    user: Dict[str, Any]

# ============================================================================
# AUTH HELPERS
# ============================================================================

def get_db_connection():
    """Get direct database connection"""
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(DATABASE_URL)

async def get_current_user_from_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from session token"""
    token = credentials.credentials

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT s.user_id, s.expires_at, u.email, u.role, u.is_demo, u.demo_expires_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s AND u.is_active = TRUE
        """, (token,))

        session = cursor.fetchone()

        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")

        user_id, expires_at, email, role, is_demo, demo_expires_at = session

        # Check session expiry
        if datetime.now() > expires_at:
            raise HTTPException(status_code=401, detail="Session expired")

        # Check demo expiry
        if is_demo and demo_expires_at and datetime.now() > demo_expires_at:
            raise HTTPException(status_code=403, detail="Demo account expired")

        return {
            "user_id": user_id,
            "email": email,
            "role": role,
            "is_demo": is_demo
        }

    finally:
        cursor.close()
        conn.close()

# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Login endpoint"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get user
        cursor.execute("""
            SELECT id, email, password_hash, role, is_active, is_demo, demo_expires_at, full_name
            FROM users
            WHERE email = %s
        """, (request.email,))

        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user_id, email, password_hash, role, is_active, is_demo, demo_expires_at, full_name = user

        # Check password
        password_bytes = request.password.encode('utf-8')
        stored_hash_bytes = password_hash.encode('utf-8')
        if not bcrypt.checkpw(password_bytes, stored_hash_bytes):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Check if active
        if not is_active:
            raise HTTPException(status_code=403, detail="Account disabled")

        # Check if demo expired
        if is_demo and demo_expires_at and datetime.now() > demo_expires_at:
            raise HTTPException(status_code=403, detail="Demo account expired")

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
            VALUES (%s, 'login', %s)
        """, (user_id, '{"timestamp": "' + datetime.now().isoformat() + '"}'))

        # Update last active
        cursor.execute("""
            UPDATE users SET last_active_at = NOW() WHERE id = %s
        """, (user_id,))

        conn.commit()

        return {
            "token": session_token,
            "user": {
                "id": user_id,
                "email": email,
                "role": role,
                "full_name": full_name,
                "is_demo": is_demo,
                "demo_expires_at": demo_expires_at.isoformat() if demo_expires_at else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Login failed")
    finally:
        cursor.close()
        conn.close()


@app.post("/api/auth/logout")
async def logout(current_user = Depends(get_current_user_from_token)):
    """Logout endpoint"""

    # Note: In a real app, we'd delete the session token here
    # For simplicity, we'll just return success
    return {"message": "Logged out successfully"}


@app.get("/api/auth/me")
async def get_current_user_info(current_user = Depends(get_current_user_from_token)):
    """Get current user info"""
    return current_user

# ============================================================================
# STRIPE SUBSCRIPTION ENDPOINTS
# ============================================================================

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

if stripe.api_key:
    print(f"Stripe configured with key ending in ...{stripe.api_key[-4:]}")
else:
    print("WARNING: STRIPE_SECRET_KEY is not set! Payment features will not work.")

@app.get("/api/stripe/status")
async def stripe_status():
    """Check if Stripe is configured (no auth required for diagnostics)"""
    return {
        "configured": bool(stripe.api_key),
        "webhook_configured": bool(STRIPE_WEBHOOK_SECRET)
    }

class CheckoutRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str

@app.post("/api/stripe/create-checkout")
async def create_checkout_session(
    request: CheckoutRequest,
    current_user = Depends(get_current_user_from_token)
):
    """Create Stripe checkout session"""
    # Check if Stripe is configured
    if not stripe.api_key:
        print("ERROR: STRIPE_SECRET_KEY environment variable is not set!")
        raise HTTPException(status_code=500, detail="Payment system is not configured. Please contact support.")

    print(f"Stripe checkout request: price_id={request.price_id}, user_id={current_user['user_id']}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Ensure stripe columns exist
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)")
        conn.commit()

        # Get user email and stripe_customer_id in one query
        cursor.execute("SELECT email, stripe_customer_id FROM users WHERE id = %s", (current_user['user_id'],))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
        email = result[0]
        stripe_customer_id = result[1]

        if not stripe_customer_id:
            # Create Stripe customer
            print(f"Creating Stripe customer for {email}")
            customer = stripe.Customer.create(
                email=email,
                metadata={"user_id": str(current_user['user_id'])}
            )
            stripe_customer_id = customer.id

            # Save customer ID
            cursor.execute(
                "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
                (stripe_customer_id, current_user['user_id'])
            )
            conn.commit()

        # Create checkout session
        print(f"Creating Stripe checkout session for customer {stripe_customer_id}, price {request.price_id}")
        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': request.price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request.success_url,
            cancel_url=request.cancel_url,
            metadata={
                'user_id': str(current_user['user_id'])
            }
        )

        cursor.close()
        conn.close()

        print(f"Checkout session created successfully: {checkout_session.id}")
        return {"checkout_url": checkout_session.url}

    except HTTPException:
        raise
    except stripe.error.InvalidRequestError as e:
        print(f"Stripe invalid request error: {e}")
        if conn:
            try: conn.close()
            except Exception: pass
        raise HTTPException(status_code=400, detail=f"Invalid payment request: {str(e)}")
    except stripe.error.AuthenticationError as e:
        print(f"Stripe authentication error: {e}")
        if conn:
            try: conn.close()
            except Exception: pass
        raise HTTPException(status_code=500, detail="Payment system authentication failed. Please contact support.")
    except Exception as e:
        print(f"Checkout error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try: conn.close()
            except Exception: pass
        raise HTTPException(status_code=500, detail=f"Error creating checkout session: {str(e)}")


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )

        conn = get_db_connection()
        cursor = conn.cursor()

        # Handle different event types
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            user_id = session['metadata']['user_id']

            # Update user subscription
            cursor.execute("""
                UPDATE users
                SET subscription_status = 'active',
                    subscription_tier = 'pro',
                    stripe_subscription_id = %s,
                    trial_ends_at = NULL
                WHERE id = %s
            """, (session['subscription'], user_id))

        elif event['type'] == 'customer.subscription.updated':
            subscription = event['data']['object']
            stripe_customer_id = subscription['customer']

            cursor.execute("""
                UPDATE users
                SET subscription_status = %s
                WHERE stripe_customer_id = %s
            """, (subscription['status'], stripe_customer_id))

        elif event['type'] == 'customer.subscription.deleted':
            subscription = event['data']['object']
            stripe_customer_id = subscription['customer']

            cursor.execute("""
                UPDATE users
                SET subscription_status = 'canceled',
                    subscription_tier = 'trial'
                WHERE stripe_customer_id = %s
            """, (stripe_customer_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {"status": "success"}

    except Exception as e:
        print(f"Webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/subscription/status")
async def get_subscription_status(current_user = Depends(get_current_user_from_token)):
    """Get current subscription status"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT subscription_tier, subscription_status, trial_ends_at,
               subscription_ends_at, ai_generations_this_month, stripe_subscription_id
        FROM users
        WHERE id = %s
    """, (current_user['user_id'],))

    result = cursor.fetchone()
    cursor.close()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="User not found")

    tier, status, trial_ends, sub_ends, ai_gens, stripe_sub_id = result

    return {
        "tier": tier,
        "status": status,
        "trial_ends_at": trial_ends.isoformat() if trial_ends else None,
        "subscription_ends_at": sub_ends.isoformat() if sub_ends else None,
        "ai_generations_used": ai_gens,
        "has_active_subscription": status == 'active' and tier != 'trial',
        "stripe_subscription_id": stripe_sub_id
    }


@app.post("/api/subscription/cancel")
async def cancel_subscription(current_user = Depends(get_current_user_from_token)):
    """Cancel subscription"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT stripe_subscription_id FROM users WHERE id = %s",
        (current_user['user_id'],)
    )
    result = cursor.fetchone()

    if not result or not result[0]:
        raise HTTPException(status_code=404, detail="No active subscription")

    stripe_subscription_id = result[0]

    try:
        # Cancel in Stripe
        stripe.Subscription.delete(stripe_subscription_id)

        # Update in database
        cursor.execute("""
            UPDATE users
            SET subscription_status = 'canceled'
            WHERE id = %s
        """, (current_user['user_id'],))

        conn.commit()
        cursor.close()
        conn.close()

        return {"status": "canceled"}

    except Exception as e:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# CANVAS INTEGRATION ENDPOINTS
# ============================================================================

@app.get("/api/v2/canvas/status")
async def canvas_status(current_user=Depends(get_current_user_from_token), db: Session = Depends(get_db)):
    """Check if user has saved Canvas credentials"""
    try:
        user_id = current_user['user_id']

        if not db:
            return {"connected": False}

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

        if not credentials:
            return {"connected": False}

        return {
            "connected": True,
            "canvas_url": credentials.canvas_url
        }
    except Exception as e:
        print(f"Canvas status check error: {e}")
        return {"connected": False}

@app.post("/api/v2/canvas/connect")
async def connect_canvas_v2(
    request: CanvasConnectionRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Phase 1: Connect professor's Canvas account
    Saves encrypted credentials to database
    """
    try:
        # Aggressively clean inputs - remove ALL whitespace including hidden chars
        import re
        canvas_url = re.sub(r'\s+', '', request.canvas_url)  # Remove all whitespace
        access_token = re.sub(r'\s+', '', request.access_token)  # Remove all whitespace

        print(f"\n{'='*60}")
        print(f"CANVAS CONNECTION ATTEMPT")
        print(f"{'='*60}")
        print(f"Raw URL length: {len(request.canvas_url)}")
        print(f"Cleaned URL: {canvas_url}")
        print(f"Raw token length: {len(request.access_token)}")
        print(f"Cleaned token length: {len(access_token)}")
        print(f"Token first 15 chars: {access_token[:15]}...")
        print(f"Token last 10 chars: ...{access_token[-10:]}")

        # Validate URL format
        if not canvas_url.startswith('http'):
            print("❌ URL doesn't start with http")
            raise HTTPException(
                status_code=400,
                detail="Canvas URL must start with https:// (example: https://vuu.instructure.com)"
            )

        # Validate token format (Canvas tokens are typically 50-70 characters)
        if len(access_token) < 20:
            print("❌ Token too short")
            raise HTTPException(
                status_code=400,
                detail=f"Canvas API token seems too short ({len(access_token)} chars). Typical tokens are 50-70 characters. Please check you copied the full token."
            )

        # Check for suspicious characters
        if not re.match(r'^[A-Za-z0-9~_-]+$', access_token):
            suspicious_chars = re.findall(r'[^A-Za-z0-9~_-]', access_token)
            print(f"❌ Token contains suspicious characters: {suspicious_chars}")
            raise HTTPException(
                status_code=400,
                detail=f"Canvas token contains invalid characters. Please copy only the token (no quotes, spaces, or special characters)."
            )

        print(f"✓ URL format valid")
        print(f"✓ Token format valid")
        print(f"Attempting Canvas API connection...")

        # Test the connection
        canvas_auth = CanvasAuth(canvas_url, access_token)
        success, user_data, error_message = canvas_auth.test_connection()

        print(f"\nConnection result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        if error_message:
            print(f"Error: {error_message}")

        if not success:
            raise HTTPException(
                status_code=401,
                detail=error_message or "Invalid Canvas credentials. Please check your URL and API token."
            )

        # Encrypt and save to database
        encrypted_token = encrypt_token(access_token)
        user_id = current_user['user_id']

        if db:
            # Check if credentials already exist
            existing = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

            if existing:
                # Update existing
                existing.canvas_url = canvas_url
                existing.access_token_encrypted = encrypted_token
                existing.last_verified = datetime.utcnow()
            else:
                # Create new
                credentials = CanvasCredentials(
                    user_id=user_id,
                    canvas_url=canvas_url,
                    access_token_encrypted=encrypted_token
                )
                db.add(credentials)

            db.commit()

        return {
            "status": "connected",
            "canvas_url": canvas_url,
            "user_name": user_data.get("name") if user_data else "Unknown"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/canvas/courses")
async def get_courses_v2(
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Phase 1: Get professor's Canvas courses
    Returns list of courses they teach
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials from database
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

        if not credentials:
            raise HTTPException(
                status_code=404,
                detail="Canvas not connected. Please connect your Canvas account first."
            )

        # Decrypt token and fetch courses
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        courses = canvas_client.get_user_courses()

        # Cache courses in database
        for course in courses:
            existing = db.query(UserCourse).filter_by(
                user_id=user_id,
                course_id=course["id"]
            ).first()

            if existing:
                existing.course_name = course["name"]
                existing.course_code = course.get("course_code")
                existing.total_students = course.get("total_students")
                existing.synced_at = datetime.utcnow()
            else:
                user_course = UserCourse(
                    user_id=user_id,
                    course_id=course["id"],
                    course_name=course["name"],
                    course_code=course.get("course_code"),
                    total_students=course.get("total_students")
                )
                db.add(user_course)

        db.commit()

        return {
            "courses": courses,
            "total": len(courses)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/quiz/generate")
async def generate_quiz_questions(request: QuizGenerateRequest):
    """
    Generate quiz questions with AI (preview only, no Canvas upload)
    Returns questions for user to review before uploading
    """
    try:
        print(f"🧠 Generating {request.grade_level} quiz questions: {request.topic}")

        quiz_data = bonita.generate_quiz(
            week=1,
            topic=request.topic,
            description=request.description,
            num_questions=request.num_questions,
            difficulty=request.difficulty,
            grade_level=request.grade_level,
            language=request.language
        )

        return {
            "status": "success",
            "topic": request.topic,
            "questions": quiz_data.get("questions", []),
            "num_questions": len(quiz_data.get("questions", [])),
            "message": "Quiz questions generated! Review and upload to Canvas."
        }

    except Exception as e:
        print(f"❌ Error generating quiz: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/quiz/upload")
async def upload_quiz_to_canvas(
    request: QuizUploadRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Upload generated quiz questions to Canvas
    Takes questions from preview and creates quiz in Canvas
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

        if not credentials:
            raise HTTPException(
                status_code=404,
                detail="Canvas not connected"
            )

        # Upload to Canvas
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        quiz_title = f"Quiz: {request.topic}"
        quiz_id = canvas_client.create_quiz(
            course_id=request.course_id,
            quiz_data={
                "title": quiz_title,
                "quiz_type": "assignment",
                "time_limit": 20,
                "allowed_attempts": 1,
                "points_possible": request.num_questions * 10,
                "due_at": request.due_date
            }
        )

        if not quiz_id:
            raise HTTPException(status_code=500, detail="Failed to create quiz in Canvas")

        # Add questions to Canvas quiz
        for i, question in enumerate(request.questions, 1):
            canvas_client.add_quiz_question(
                course_id=request.course_id,
                quiz_id=quiz_id,
                question_data={
                    "name": f"Question {i}",
                    "text": question["question_text"],
                    "type": "multiple_choice_question",
                    "points": 10,
                    "answers": [
                        {
                            "answer_text": ans["text"],
                            "answer_weight": 100 if ans.get("correct") else 0
                        }
                        for ans in question["answers"]
                    ]
                }
            )

        # Return success with Canvas preview URL
        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/quizzes/{quiz_id}"

        return {
            "status": "success",
            "quiz_id": quiz_id,
            "quiz_title": quiz_title,
            "questions_added": len(request.questions),
            "preview_url": preview_url,
            "message": "Quiz uploaded to Canvas successfully!"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error uploading quiz: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/quiz")
async def create_quiz_v2(
    request: QuizRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    LEGACY: Create quiz in Canvas course (one-step: generate + upload)
    Phase 2: Create quiz in Canvas course
    1. Generate quiz with Bonita AI
    2. Upload to Canvas
    3. Return preview URL
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()

        if not credentials:
            raise HTTPException(
                status_code=404,
                detail="Canvas not connected"
            )

        # Step 1: Generate quiz with Bonita AI
        print(f"🧠 Generating quiz on: {request.topic}")
        quiz_data = bonita.generate_quiz(
            week=1,
            topic=request.topic,
            description=request.description,
            num_questions=request.num_questions,
            difficulty=request.difficulty,
            language=request.language
        )

        # Step 2: Upload to Canvas
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        quiz_title = f"Quiz: {request.topic}"
        quiz_id = canvas_client.create_quiz(
            course_id=request.course_id,
            quiz_data={
                "title": quiz_title,
                "quiz_type": "assignment",
                "time_limit": 20,
                "allowed_attempts": 1,
                "points_possible": request.num_questions * 10,
                "due_at": request.due_date
            }
        )

        if not quiz_id:
            raise HTTPException(status_code=500, detail="Failed to create quiz in Canvas")

        # Step 3: Add questions
        for i, question in enumerate(quiz_data.get("questions", []), 1):
            canvas_client.add_quiz_question(
                course_id=request.course_id,
                quiz_id=quiz_id,
                question_data={
                    "name": f"Question {i}",
                    "text": question["question_text"],
                    "type": "multiple_choice_question",
                    "points": 10,
                    "answers": [
                        {
                            "answer_text": ans["text"],
                            "answer_weight": 100 if ans.get("correct") else 0
                        }
                        for ans in question["answers"]
                    ]
                }
            )

        # Return success with preview URL
        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/quizzes/{quiz_id}"

        return {
            "status": "success",
            "quiz_id": quiz_id,
            "quiz_title": quiz_title,
            "questions_added": len(quiz_data.get("questions", [])),
            "preview_url": preview_url,
            "message": "Quiz created successfully! Review and publish in Canvas."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AIAssignmentRequest(BaseModel):
    topic: str
    assignment_type: str
    requirements: str
    points: int = 100
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh


class AnnouncementRequest(BaseModel):
    course_id: int
    topic: str
    details: Optional[str] = None
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh


class AIPageRequest(BaseModel):
    title: str
    page_type: str
    description: str
    objectives: Optional[str] = None
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh


class PageRequest(BaseModel):
    course_id: int
    title: str
    content: str


class AssignmentRequest(BaseModel):
    course_id: int
    title: str
    description: str
    points: int = 100
    due_date: Optional[str] = None


class ModuleRequest(BaseModel):
    course_id: int
    name: str
    position: Optional[int] = None


class DiscussionRequest(BaseModel):
    course_id: int
    topic: str
    prompt: str


# Grading Setup Models
class GradingCategoryRules(BaseModel):
    drop_lowest: Optional[Dict[str, Any]] = None
    drop_highest: Optional[Dict[str, Any]] = None
    extra_credit: Optional[Dict[str, Any]] = None


class GradingCategory(BaseModel):
    name: str
    weight: float
    rules: Optional[Dict[str, Any]] = {}


class GradingSetupRequest(BaseModel):
    course_id: int
    grading_method: str  # "total_points" or "weighted"
    categories: List[GradingCategory]
    global_rules: Optional[Dict[str, Any]] = None


class GradingFixRequest(BaseModel):
    course_id: int
    fix_type: str = "auto"  # "auto" or "reset"


@app.post("/api/v2/canvas/announcement")
async def create_announcement_v2(
    request: AnnouncementRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Create an announcement in Canvas course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        # Generate announcement with AI
        print(f"📢 Generating announcement on: {request.topic}")

        # Get language name
        language_name = LANGUAGE_MAP.get(request.language, "English")

        system = "You are Bonita, helping professors create course announcements."
        prompt = f"""Create a professional course announcement about: {request.topic}

IMPORTANT: Generate ALL content in {language_name}.
The entire announcement must be in {language_name}.

{f'Additional details: {request.details}' if request.details else ''}

Make it:
- Professional but friendly
- Clear and concise (2-3 paragraphs max)
- Action-oriented if needed
- Formatted in HTML for Canvas

Return just the HTML content, no markdown code blocks."""

        announcement_html, _ = bonita.call_claude(prompt, system)

        # Upload to Canvas
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.create_announcement(
            course_id=request.course_id,
            announcement_data={
                "title": request.topic,
                "message": announcement_html
            }
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to create announcement")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/discussion_topics/{result['id']}"

        return {
            "status": "success",
            "announcement_id": result["id"],
            "title": request.topic,
            "preview_url": preview_url,
            "message": "Announcement posted successfully!"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/generate-page")
async def generate_ai_page(
    request: AIPageRequest,
    db: Session = Depends(get_db)
):
    """
    Generate AI-enhanced course page content using Groq/OpenAI
    Returns professional page content with proper structure
    """
    try:
        print(f"🤖 Generating AI page: {request.title}")

        # Map page types to better descriptions
        type_descriptions = {
            "overview": "course or unit overview",
            "resource_list": "resource list with links and descriptions",
            "study_guide": "study guide with key concepts",
            "tutorial": "tutorial or how-to guide",
            "reading": "reading material or article",
            "reference": "reference material",
            "other": "informational page"
        }

        page_type_desc = type_descriptions.get(request.page_type, "course page")

        # Get language name
        language_name = LANGUAGE_MAP.get(request.language, "English")

        system = """You are Bonita, an AI assistant helping college professors create course pages.
Your output should be well-formatted HTML suitable for Canvas LMS.
Use clear structure, headers, lists, and proper formatting."""

        prompt = f"""Create a professional course page titled: {request.title}

IMPORTANT: Generate ALL content in {language_name}.
The entire page must be in {language_name}, including all sections, headings, and content.

Page Type: {page_type_desc}
Description: {request.description}
{f'Learning Objectives: {request.objectives}' if request.objectives else ''}

Generate comprehensive page content with:

1. **Introduction** (2-3 paragraphs explaining the topic and its importance)

2. **Main Content Sections** (organized with clear headings)
   - Break down the content logically
   - Use bullet points and numbered lists where appropriate
   - Include examples or explanations

3. **Key Takeaways** (3-5 bullet points summarizing main points)

4. **Resources** (optional but recommended)
   - Suggested readings
   - Helpful links
   - Additional materials

Format the output in clean HTML suitable for Canvas LMS. Use:
- <h3> for section headings
- <h4> for subsection headings
- <p> for paragraphs
- <ul> and <li> for bullet lists
- <ol> and <li> for numbered lists
- <strong> for emphasis
- <a href="..."> for links (if suggesting real resources)

Do NOT include the page title as an <h1> or <h2> (Canvas will add that).
Make it educational, engaging, and well-organized."""

        # Generate with AI
        generated_content, cost = bonita.call_ai(prompt, system)

        print(f"✅ Page generated (cost: ${cost:.4f})")

        return {
            "status": "success",
            "generated_content": generated_content,
            "cost": cost
        }

    except Exception as e:
        print(f"❌ Error generating page: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/page")
async def create_page_v2(
    request: PageRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Create a course page in Canvas"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        # Upload page to Canvas (content already provided)
        print(f"📄 Creating page: {request.title}")

        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.create_page(
            course_id=request.course_id,
            page_data={
                "title": request.title,
                "content": request.content
            }
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to create page")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/pages/{result['url']}"

        return {
            "status": "success",
            "page_url": result["url"],
            "title": request.title,
            "preview_url": preview_url,
            "message": "Page created successfully! Review and publish in Canvas."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/generate-assignment")
async def generate_ai_assignment(
    request: AIAssignmentRequest,
    db: Session = Depends(get_db)
):
    """
    Generate AI-enhanced assignment content using Bonita
    Returns professional assignment description with instructions, objectives, rubric
    """
    try:
        print(f"🤖 Generating AI assignment: {request.topic}")

        # Map assignment types to better descriptions
        type_descriptions = {
            "essay": "essay or written paper",
            "discussion": "discussion post or forum response",
            "project": "project or presentation",
            "research": "research assignment",
            "case_study": "case study analysis",
            "lab": "lab or practical work",
            "reflection": "reflection assignment",
            "group": "group collaborative assignment",
            "other": "assignment"
        }

        assignment_type_desc = type_descriptions.get(request.assignment_type, "assignment")

        # Get language name
        language_name = LANGUAGE_MAP.get(request.language, "English")

        system = """You are Bonita, an AI assistant helping college professors create high-quality assignments.
Your output should be professional, clear, and properly formatted for Canvas LMS.
Use HTML formatting with headers, lists, and proper structure."""

        prompt = f"""Create a professional college assignment on: {request.topic}

IMPORTANT: Generate ALL content in {language_name}.
The entire response must be in {language_name}, including title, description, objectives, instructions, deliverables, and rubric.

Assignment Type: {assignment_type_desc}
Points: {request.points}
Professor's Requirements:
{request.requirements}

Generate a complete assignment description with the following sections:

1. **Assignment Overview** (2-3 paragraphs explaining what students will do and why it's important)

2. **Learning Objectives** (3-5 specific, measurable objectives students will achieve)

3. **Instructions** (Step-by-step directions for completing the assignment)

4. **Deliverables** (Specific list of what students must submit)

5. **Grading Rubric** (Clear criteria for how the assignment will be evaluated)

6. **Resources** (Suggested materials, readings, or tools students can use)

Format the output in clean HTML suitable for Canvas LMS. Use:
- <h3> for section headings
- <p> for paragraphs
- <ul> and <li> for lists
- <strong> for emphasis
- <table> for rubric (if applicable)

Keep it professional but engaging. Make instructions clear and actionable.
Do NOT include the assignment title as a heading (it will be added separately).
Focus on creating content that helps students succeed."""

        # Generate with Bonita AI
        generated_content, cost = bonita.call_claude(prompt, system)

        print(f"✅ Assignment generated (cost: ${cost:.4f})")

        return {
            "status": "success",
            "generated_content": generated_content,
            "cost": cost
        }

    except Exception as e:
        print(f"❌ Error generating assignment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/assignment")
async def create_assignment_v2(
    request: AssignmentRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Create an assignment in Canvas course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        # Generate assignment description with AI if needed
        description = request.description
        if len(description) < 50:  # If description is too short, expand it with AI
            print(f"📝 Enhancing assignment description with AI")

            system = "You are Bonita, creating assignment descriptions for professors."
            prompt = f"""Create a detailed assignment description for: {request.title}

Brief description: {description}

Include:
- Assignment overview (what students will do)
- Learning objectives
- Submission requirements
- Grading criteria

Format in HTML for Canvas."""

            description, _ = bonita.call_claude(prompt, system)

        # Upload to Canvas
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.create_assignment(
            course_id=request.course_id,
            assignment_data={
                "title": request.title,
                "description": description,
                "points": request.points,
                "due_date": request.due_date
            }
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to create assignment")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/assignments/{result['id']}"

        return {
            "status": "success",
            "assignment_id": result["id"],
            "title": request.title,
            "points": request.points,
            "preview_url": preview_url,
            "message": "Assignment created successfully! Review and publish in Canvas."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/canvas/modules/{course_id}")
async def get_modules_v2(
    course_id: int,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Get all modules in a course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        modules = canvas_client.get_modules(course_id)

        return {
            "modules": modules,
            "total": len(modules)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/module")
async def create_module_v2(
    request: ModuleRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Create a module in Canvas course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.create_module(
            course_id=request.course_id,
            module_data={
                "name": request.name,
                "position": request.position
            }
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to create module")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/modules"

        return {
            "status": "success",
            "module_id": result["id"],
            "name": request.name,
            "preview_url": preview_url,
            "message": f"Module '{request.name}' created successfully!"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/discussion")
async def create_discussion_v2(
    request: DiscussionRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Create a discussion topic in Canvas course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        # Generate discussion content with AI
        print(f"💬 Generating discussion: {request.topic}")

        system = "You are Bonita, creating discussion prompts for courses."
        prompt = f"""Create a discussion topic titled: {request.topic}

Prompt: {request.prompt}

Create an engaging discussion post that:
- Provides context for the discussion
- Asks thought-provoking questions (3-4 questions)
- Encourages student participation
- Is formatted in HTML for Canvas

Keep it concise but meaningful."""

        discussion_html, _ = bonita.call_claude(prompt, system)

        # Upload to Canvas
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.create_discussion(
            course_id=request.course_id,
            discussion_data={
                "title": request.topic,
                "message": discussion_html
            }
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to create discussion")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/discussion_topics/{result['id']}"

        return {
            "status": "success",
            "discussion_id": result["id"],
            "title": request.topic,
            "preview_url": preview_url,
            "message": "Discussion created successfully! Review and publish in Canvas."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# REFERENCE MATERIALS (SYLLABUS UPLOAD & AI STYLE MATCHING)
# ============================================================================

from fastapi import UploadFile, File
from database import ReferenceMaterial
import PyPDF2
from docx import Document
import io


@app.post("/api/v2/reference-materials/upload")
async def upload_reference_material(
    file: UploadFile = File(...),
    course_name: str = None,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Upload reference material (syllabus, document) for AI style matching
    Accepts: PDF, DOCX, TXT
    """
    try:
        user_id = current_user['user_id']

        # Validate file type
        file_ext = file.filename.split('.')[-1].lower()
        if file_ext not in ['pdf', 'docx', 'txt']:
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Accepted: PDF, DOCX, TXT"
            )

        # Read file content
        file_content = await file.read()

        # Extract text based on file type
        extracted_text = ""

        if file_ext == 'pdf':
            # Extract text from PDF
            try:
                pdf_file = io.BytesIO(file_content)
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                for page in pdf_reader.pages:
                    extracted_text += page.extract_text() + "\n"
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to extract text from PDF: {str(e)}"
                )

        elif file_ext == 'docx':
            # Extract text from DOCX
            try:
                docx_file = io.BytesIO(file_content)
                doc = Document(docx_file)
                for paragraph in doc.paragraphs:
                    extracted_text += paragraph.text + "\n"
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to extract text from DOCX: {str(e)}"
                )

        elif file_ext == 'txt':
            # Extract text from TXT
            try:
                extracted_text = file_content.decode('utf-8')
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to read TXT file: {str(e)}"
                )

        # Validate extracted text
        if not extracted_text or len(extracted_text.strip()) < 100:
            raise HTTPException(
                status_code=400,
                detail="File appears to be empty or too short. Please upload a valid syllabus."
            )

        # Save to database
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        reference_material = ReferenceMaterial(
            user_id=user_id,
            file_name=file.filename,
            file_type=file_ext,
            extracted_text=extracted_text,
            course_name=course_name
        )

        db.add(reference_material)
        db.commit()
        db.refresh(reference_material)

        print(f"✅ Reference material uploaded: {file.filename} ({len(extracted_text)} chars)")

        return {
            "status": "success",
            "message": "Reference material uploaded successfully",
            "material_id": reference_material.id,
            "file_name": file.filename,
            "extracted_length": len(extracted_text),
            "course_name": course_name
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error uploading reference material: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/reference-materials")
async def get_reference_materials(current_user=Depends(get_current_user_from_token), db: Session = Depends(get_db)):
    """
    Get all reference materials for the current user
    """
    try:
        user_id = current_user['user_id']

        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        materials = db.query(ReferenceMaterial).filter_by(user_id=user_id).order_by(
            ReferenceMaterial.upload_date.desc()
        ).all()

        return {
            "status": "success",
            "materials": [
                {
                    "id": m.id,
                    "file_name": m.file_name,
                    "file_type": m.file_type,
                    "course_name": m.course_name,
                    "upload_date": m.upload_date.isoformat(),
                    "text_length": len(m.extracted_text)
                }
                for m in materials
            ],
            "total": len(materials)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching reference materials: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v2/reference-materials/{material_id}")
async def delete_reference_material(material_id: int, current_user=Depends(get_current_user_from_token), db: Session = Depends(get_db)):
    """
    Delete a reference material
    """
    try:
        user_id = current_user['user_id']

        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        material = db.query(ReferenceMaterial).filter_by(
            id=material_id,
            user_id=user_id
        ).first()

        if not material:
            raise HTTPException(
                status_code=404,
                detail="Reference material not found"
            )

        file_name = material.file_name
        db.delete(material)
        db.commit()

        print(f"✅ Reference material deleted: {file_name}")

        return {
            "status": "success",
            "message": f"Deleted {file_name}"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting reference material: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

class AIDiscussionRequest(BaseModel):
    topic: str
    discussion_type: str
    goals: str
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh


class AISyllabusRequest(BaseModel):
    course_name: str
    description: str
    objectives: str
    grading: str
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh


class SyllabusRequest(BaseModel):
    course_id: int
    syllabus_body: str


@app.post("/api/v2/canvas/generate-discussion")
async def generate_ai_discussion(request: AIDiscussionRequest):
    """Generate AI-enhanced discussion topic"""
    try:
        print(f"🤖 Generating AI discussion: {request.topic}")

        # Get language name
        language_name = LANGUAGE_MAP.get(request.language, "English")

        system = "You are Bonita, helping professors create engaging class discussions."
        prompt = f"""Create a discussion topic on: {request.topic}

IMPORTANT: Generate ALL content in {language_name}.
The entire discussion topic must be in {language_name}, including the prompt, questions, guidelines, and outcomes.

Discussion Type: {request.discussion_type}
Learning Goals: {request.goals}

Generate an engaging discussion post that includes:

1. **Opening Prompt** (2-3 paragraphs that provide context and spark interest)

2. **Discussion Questions** (3-5 thought-provoking questions that encourage critical thinking)

3. **Participation Guidelines** (How students should engage - length, citations, peer responses)

4. **Expected Outcomes** (What students should gain from this discussion)

Format in HTML for Canvas. Make it engaging and encourage meaningful dialogue."""

        content, cost = bonita.call_ai(prompt, system)
        print(f"✅ Discussion generated (cost: ${cost:.4f})")
        return {"status": "success", "generated_content": content, "cost": cost}
    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/generate-syllabus")
async def generate_ai_syllabus(request: AISyllabusRequest):
    """Generate AI-enhanced course syllabus"""
    try:
        print(f"🤖 Generating AI syllabus: {request.course_name}")

        # Get language name
        language_name = LANGUAGE_MAP.get(request.language, "English")

        system = "You are Bonita, helping professors create comprehensive course syllabi."
        prompt = f"""Create a professional course syllabus for: {request.course_name}

IMPORTANT: Generate ALL content in {language_name}.
The entire syllabus must be in {language_name}, including all sections, policies, and schedule.

Course Description: {request.description}
Learning Objectives: {request.objectives}
Grading Policy: {request.grading}

Generate a complete syllabus with:

1. **Course Overview** (2-3 engaging paragraphs about the course and its value)

2. **Learning Objectives** (Clear, measurable objectives formatted as a list)

3. **Course Structure** (How the course is organized - weeks, units, topics)

4. **Grading Breakdown** (Based on the professor's grading policy, formatted as a table)

5. **Attendance & Participation Policy** (Professional but fair expectations)

6. **Academic Integrity Statement** (Clear guidelines on plagiarism and cheating)

7. **Course Materials** (Required textbooks, software, or resources if applicable)

8. **Important Policies** (Late work, extensions, communication expectations)

9. **Weekly Schedule Overview** (High-level breakdown of topics by week)

Format in clean HTML suitable for Canvas. Use:
- <h3> for major sections
- <h4> for subsections
- <ul> and <li> for lists
- <table> for grading breakdown
- <p> for paragraphs
- <strong> for emphasis

Make it comprehensive, professional, and student-friendly."""

        content, cost = bonita.call_ai(prompt, system)
        print(f"✅ Syllabus generated (cost: ${cost:.4f})")
        return {"status": "success", "generated_content": content, "cost": cost}
    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v2/canvas/syllabus")
async def upload_syllabus(
    request: SyllabusRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Upload syllabus to Canvas course"""
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        if not db:
            raise HTTPException(status_code=500, detail="Database not available")

        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        print(f"📋 Uploading syllabus to course {request.course_id}")

        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        canvas_client = CanvasClient(credentials.canvas_url, decrypted_token)

        result = canvas_client.update_syllabus(
            course_id=request.course_id,
            syllabus_body=request.syllabus_body
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to upload syllabus")

        preview_url = f"{credentials.canvas_url}/courses/{request.course_id}/assignments/syllabus"

        return {
            "status": "success",
            "course_id": request.course_id,
            "preview_url": preview_url,
            "message": "Syllabus uploaded successfully!"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error uploading syllabus: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# GRADING SETUP WIZARD
# ============================================================================
# Strategy by: Sunni
# Implementation by: Q-Tip
# Purpose: Reduce Canvas grading setup from 45 minutes to 2 minutes


@app.get("/api/grading/templates")
async def get_grading_templates():
    """
    Get available grading templates by subject

    Returns:
        {
            "Mass Communications": [{name, weight, rules}, ...],
            "Mathematics": [...],
            ...
        }
    """
    return {"templates": GRADING_TEMPLATES}


@app.get("/api/grading/template/{subject}")
async def get_subject_template(subject: str):
    """
    Get grading template for a specific subject

    Args:
        subject: Subject name (e.g., "Mass Communications", "Mathematics")

    Returns:
        List of categories with weights and rules
    """
    template = get_template(subject)

    if not template and subject != "Custom":
        raise HTTPException(status_code=404, detail=f"Template not found for subject: {subject}")

    return {
        "subject": subject,
        "categories": template
    }


@app.post("/api/grading/setup")
async def setup_grading(
    request: GradingSetupRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Complete grading setup for a Canvas course

    Creates assignment groups, enables weighted grading, applies rules.
    Reduces manual 45-minute process to 2-minute wizard.

    Request:
        {
            "course_id": 6355,
            "grading_method": "weighted",
            "categories": [
                {"name": "Quizzes", "weight": 30, "rules": {"drop_lowest": {"enabled": true, "count": 1}}},
                {"name": "Assignments", "weight": 40, "rules": {}},
                {"name": "Exams", "weight": 30, "rules": {}}
            ],
            "global_rules": {"late_penalty": {"enabled": true, "percent_per_day": 10}}
        }

    Returns:
        {
            "status": "success",
            "groups_created": 3,
            "weighted_grading_enabled": true,
            "assignment_groups": [...]
        }
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected. Please connect Canvas first.")

        print(f"📊 Setting up grading for course {request.course_id}")
        print(f"   Method: {request.grading_method}")
        print(f"   Categories: {len(request.categories)}")

        # Initialize grading service
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        grading_service = GradingSetupService(
            canvas_url=credentials.canvas_url,
            canvas_token=decrypted_token
        )

        # Validate grading method
        if request.grading_method == "total_points":
            # Total points doesn't need categories
            return {
                "status": "success",
                "message": "Total points grading enabled. No categories needed.",
                "grading_method": "total_points"
            }

        elif request.grading_method == "weighted":
            # Convert Pydantic models to dicts
            categories = [
                {
                    "name": cat.name,
                    "weight": cat.weight,
                    "rules": cat.rules or {}
                }
                for cat in request.categories
            ]

            # Setup weighted grading
            result = await grading_service.setup_weighted_grading(
                course_id=request.course_id,
                categories=categories,
                rules=request.global_rules
            )

            if result.get("status") == "error":
                raise HTTPException(status_code=400, detail=result.get("message"))

            print(f"✅ Grading setup complete!")
            print(f"   Groups created: {result.get('groups_created')}")

            return result

        else:
            raise HTTPException(status_code=400, detail=f"Invalid grading method: {request.grading_method}")

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error setting up grading: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/grading/analyze/{course_id}")
async def analyze_grading_setup(
    course_id: int,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Analyze existing Canvas grading setup

    Detects issues like:
    - Weights don't add to 100%
    - Assignments not in any category
    - Weighted grading not enabled
    - Empty categories

    Returns:
        {
            "has_groups": true,
            "groups": [...],
            "weighted_grading_enabled": true,
            "total_weight": 97,
            "orphan_assignments": 3,
            "issues": ["Weights don't add to 100%", ...],
            "suggestions": ["Adjust weights to total 100%", ...],
            "health": "needs_attention"
        }
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        print(f"🔍 Analyzing grading setup for course {course_id}")

        # Initialize grading service
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        grading_service = GradingSetupService(
            canvas_url=credentials.canvas_url,
            canvas_token=decrypted_token
        )

        # Analyze setup
        analysis = await grading_service.analyze_existing_setup(course_id)

        if analysis.get("status") == "error":
            raise HTTPException(status_code=500, detail=analysis.get("message"))

        print(f"   Health: {analysis.get('health')}")
        print(f"   Issues found: {len(analysis.get('issues', []))}")

        return analysis

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error analyzing grading: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/grading/fix")
async def fix_grading_setup(
    request: GradingFixRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Automatically fix common grading setup issues

    Fix types:
    - "auto": Fix issues while preserving structure (adjust weights, enable weighted grading)
    - "reset": Delete all groups and start fresh

    Request:
        {
            "course_id": 6355,
            "fix_type": "auto"
        }

    Returns:
        {
            "status": "success",
            "message": "Grading setup fixed automatically",
            "groups_adjusted": 3
        }
    """
    try:
        user_id = current_user['user_id']

        # Get Canvas credentials
        credentials = db.query(CanvasCredentials).filter_by(user_id=user_id).first()
        if not credentials:
            raise HTTPException(status_code=404, detail="Canvas not connected")

        print(f"🔧 Fixing grading setup for course {request.course_id} (mode: {request.fix_type})")

        # Initialize grading service
        decrypted_token = decrypt_token(credentials.access_token_encrypted)
        grading_service = GradingSetupService(
            canvas_url=credentials.canvas_url,
            canvas_token=decrypted_token
        )

        # Fix setup
        result = await grading_service.fix_existing_setup(
            course_id=request.course_id,
            fix_type=request.fix_type
        )

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))

        print(f"✅ Grading fixed: {result.get('message')}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fixing grading: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# DEMO ACCOUNT SYSTEM
# ============================================================================
# Allows visitors to create isolated demo accounts for testing

import secrets

def generate_demo_email():
    """Generate unique demo email"""
    random_id = ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(8))
    return f"demo-{random_id}@readysetclass.com"

@app.post("/api/demo/create")
async def create_demo_account(db: Session = Depends(get_db)):
    """
    Create a temporary demo account

    Each visitor gets their own isolated demo account that expires in 24 hours.
    No signup required - instant access!

    Returns:
        {
            "email": "demo-abc123@readysetclass.com",
            "password": "demo2026",
            "token": "jwt_token_here",
            "expires_in_hours": 24
        }
    """
    try:
        # Generate unique email
        email = generate_demo_email()
        password = "demo2026"  # Simple password for all demos

        # Hash password
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_bytes, salt).decode('utf-8')

        # Calculate expiration (24 hours from now)
        expires_at = datetime.utcnow() + timedelta(hours=24)

        # Get database connection
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create user
        cursor.execute("""
            INSERT INTO users
            (email, password_hash, full_name, role, institution, notes, is_active, is_demo, demo_expires_at)
            VALUES (%s, %s, %s, 'demo', %s, %s, TRUE, TRUE, %s)
            RETURNING id
        """, (
            email,
            password_hash,
            "Demo User",
            "Demo University",
            f"Auto-demo expires {expires_at.strftime('%Y-%m-%d %H:%M')}",
            expires_at
        ))

        user_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        print(f"✅ Created demo account: {email} (expires in 24h)")

        # Generate auth token
        token_data = {
            "user_id": user_id,
            "email": email,
            "exp": expires_at
        }
        token = jwt.encode(token_data, JWT_SECRET, algorithm=JWT_ALGORITHM)

        return {
            "email": email,
            "password": password,
            "token": token,
            "expires_in_hours": 24
        }

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error creating demo: {e}")
        print(f"Full traceback:\n{error_trace}")
        raise HTTPException(status_code=500, detail=f"Failed to create demo: {str(e)}")


@app.delete("/api/demo/cleanup")
async def cleanup_expired_demos(current_user=Depends(get_current_user_from_token)):
    """
    Cleanup expired demo accounts (admin only)
    Deletes demo accounts older than 24 hours
    """
    # Only allow admin users
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM users
            WHERE is_demo = TRUE
            AND demo_expires_at < NOW()
            RETURNING id, email
        """)

        deleted = cursor.fetchall()
        conn.commit()
        cursor.close()
        conn.close()

        return {
            "deleted_count": len(deleted),
            "message": f"Cleaned up {len(deleted)} expired demo accounts"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.get("/api/admin/users")
async def get_all_users(current_user=Depends(get_current_user_from_token)):
    """Get all users (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, email, full_name, role, institution, is_active, is_demo,
                   demo_expires_at, created_at, last_active_at
            FROM users
            ORDER BY created_at DESC
        """)

        users = []
        for row in cursor.fetchall():
            users.append({
                "id": row[0],
                "email": row[1],
                "full_name": row[2],
                "role": row[3],
                "institution": row[4],
                "is_active": row[5],
                "is_demo": row[6],
                "demo_expires_at": row[7].isoformat() if row[7] else None,
                "created_at": row[8].isoformat() if row[8] else None,
                "last_active_at": row[9].isoformat() if row[9] else None
            })

        cursor.close()
        conn.close()

        return {"users": users}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    request: dict,
    current_user=Depends(get_current_user_from_token)
):
    """Update user role (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    new_role = request.get('role')
    if new_role not in ['user', 'admin', 'demo']:
        raise HTTPException(status_code=400, detail="Invalid role")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE users SET role = %s WHERE id = %s
            RETURNING email
        """, (new_role, user_id))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": f"Updated role to {new_role}", "email": result[0]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    request: dict,
    current_user=Depends(get_current_user_from_token)
):
    """Enable/disable user account (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    is_active = request.get('is_active')
    if not isinstance(is_active, bool):
        raise HTTPException(status_code=400, detail="is_active must be boolean")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE users SET is_active = %s WHERE id = %s
            RETURNING email
        """, (is_active, user_id))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")

        conn.commit()
        cursor.close()
        conn.close()

        status = "enabled" if is_active else "disabled"
        return {"message": f"Account {status}", "email": result[0]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/stats")
async def get_system_stats(current_user=Depends(get_current_user_from_token)):
    """Get system statistics (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Total users
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_demo = FALSE")
        total_users = cursor.fetchone()[0]

        # Active users (last 7 days)
        cursor.execute("""
            SELECT COUNT(*) FROM users
            WHERE last_active_at > NOW() - INTERVAL '7 days' AND is_demo = FALSE
        """)
        active_users = cursor.fetchone()[0]

        # Demo accounts
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_demo = TRUE")
        demo_count = cursor.fetchone()[0]

        # Active sessions
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > NOW()")
        active_sessions = cursor.fetchone()[0]

        # Canvas connections
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM canvas_credentials")
        canvas_connected = cursor.fetchone()[0]

        # Total content created (rough estimate from activity log)
        cursor.execute("""
            SELECT COUNT(*) FROM activity_log
            WHERE action IN ('assignment_created', 'quiz_created', 'discussion_created')
        """)
        content_created = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return {
            "total_users": total_users,
            "active_users_7d": active_users,
            "demo_accounts": demo_count,
            "active_sessions": active_sessions,
            "canvas_connected": canvas_connected,
            "content_created": content_created
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# USER ACCOUNT ROUTES
# ============================================================================

@app.post("/api/feedback")
async def submit_feedback(request: dict, current_user=Depends(get_current_user_from_token)):
    """Submit user feedback"""
    message = request.get('message', '').strip()

    if not message:
        raise HTTPException(status_code=400, detail="Feedback message is required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Store feedback in activity log for now
        # TODO: Create dedicated feedback table
        cursor.execute("""
            INSERT INTO activity_log (user_id, action, details)
            VALUES (%s, 'feedback_submitted', %s)
        """, (current_user['user_id'], f'{{"message": "{message}", "timestamp": "{datetime.now().isoformat()}"}}'))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": "Thank you for your feedback!"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/billing/customer-portal")
async def create_customer_portal_session(current_user=Depends(get_current_user_from_token)):
    """Create Stripe Customer Portal session for subscription management"""
    try:
        # Get user's Stripe customer ID from database
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT stripe_customer_id FROM users WHERE id = %s
        """, (current_user['user_id'],))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result or not result[0]:
            raise HTTPException(status_code=404, detail="No active subscription found")

        stripe_customer_id = result[0]

        # Create Stripe Customer Portal session
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url="https://www.readysetclass.app/account.html"
        )

        return {"url": session.url}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# ANALYTICS & TRACKING ROUTES
# ============================================================================

@app.post("/api/analytics/track")
async def track_event(request: dict, current_user=Depends(get_current_user_from_token)):
    """Track user activity and feature usage"""
    event_type = request.get('event_type')
    feature = request.get('feature')
    duration = request.get('duration')  # time in seconds
    metadata = request.get('metadata', {})

    if not event_type:
        raise HTTPException(status_code=400, detail="event_type is required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create analytics table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_analytics (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                feature VARCHAR(100),
                duration INTEGER,
                metadata JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Insert tracking event
        cursor.execute("""
            INSERT INTO user_analytics (user_id, event_type, feature, duration, metadata)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            current_user['user_id'],
            event_type,
            feature,
            duration,
            str(metadata) if metadata else None
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return {"status": "tracked"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics")
async def get_analytics_data(current_user=Depends(get_current_user_from_token)):
    """Get analytics data (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Most used features (last 30 days)
        cursor.execute("""
            SELECT feature, COUNT(*) as count
            FROM user_analytics
            WHERE feature IS NOT NULL
            AND created_at > NOW() - INTERVAL '30 days'
            GROUP BY feature
            ORDER BY count DESC
            LIMIT 10
        """)
        top_features = [{"feature": row[0], "count": row[1]} for row in cursor.fetchall()]

        # Average session duration
        cursor.execute("""
            SELECT AVG(duration) as avg_duration
            FROM user_analytics
            WHERE event_type = 'session_end' AND duration IS NOT NULL
            AND created_at > NOW() - INTERVAL '30 days'
        """)
        avg_duration_result = cursor.fetchone()
        avg_session_duration = int(avg_duration_result[0]) if avg_duration_result and avg_duration_result[0] else 0

        # Daily active users (last 30 days)
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(DISTINCT user_id) as users
            FROM user_analytics
            WHERE created_at > NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        daily_active = [{"date": str(row[0]), "users": row[1]} for row in cursor.fetchall()]

        # Total events tracked
        cursor.execute("SELECT COUNT(*) FROM user_analytics")
        total_events = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return {
            "top_features": top_features,
            "avg_session_duration_seconds": avg_session_duration,
            "daily_active_users": daily_active,
            "total_events": total_events
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# REFERRAL / AFFILIATE PROGRAM
# ============================================================================

import re as _re
import random as _random


class ReferralApplyRequest(BaseModel):
    code: str


class ReferralTierUpdateRequest(BaseModel):
    tier: str
    commission_rate: float


def _generate_referral_code(email: str) -> str:
    """Generate a referral code from email: RSCE- + first 5 alphanumeric chars uppercased"""
    clean = _re.sub(r'[^a-zA-Z0-9]', '', email.split('@')[0]).upper()
    base = clean[:5].ljust(5, 'X')
    return f"RSCE-{base}"


@app.get("/api/referral/my-code")
async def get_my_referral_code(current_user=Depends(get_current_user_from_token)):
    """Get or generate the current user's referral code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']
        email = current_user['email']

        cursor.execute("""
            SELECT code, tier, commission_rate, total_referrals,
                   successful_referrals, total_earnings
            FROM referral_codes
            WHERE user_id = %s AND is_active = TRUE
        """, (user_id,))
        existing = cursor.fetchone()

        if existing:
            code, tier, rate, total, successful, earnings = existing
            return {
                "code": code, "tier": tier,
                "commission_rate": float(rate),
                "total_referrals": total,
                "successful_referrals": successful,
                "total_earnings": float(earnings),
                "shareable_link": f"https://readysetclass.app/r/{code}"
            }

        # Generate new code
        code = _generate_referral_code(email)
        cursor.execute("SELECT id FROM referral_codes WHERE code = %s", (code,))
        if cursor.fetchone():
            code = f"{code}{_random.randint(10, 99)}"

        cursor.execute("""
            INSERT INTO referral_codes (user_id, code, tier, commission_rate)
            VALUES (%s, %s, 'ambassador', 15.00)
            RETURNING code, tier, commission_rate, total_referrals, successful_referrals, total_earnings
        """, (user_id, code))
        row = cursor.fetchone()

        cursor.execute("UPDATE users SET referral_code = %s WHERE id = %s", (code, user_id))
        conn.commit()

        return {
            "code": row[0], "tier": row[1],
            "commission_rate": float(row[2]),
            "total_referrals": row[3],
            "successful_referrals": row[4],
            "total_earnings": float(row[5]),
            "shareable_link": f"https://readysetclass.app/r/{row[0]}"
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Referral code error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/referral/stats")
async def get_referral_stats(current_user=Depends(get_current_user_from_token)):
    """Get detailed referral stats for the current user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']

        cursor.execute("""
            SELECT code, tier, commission_rate, total_referrals,
                   successful_referrals, total_earnings
            FROM referral_codes
            WHERE user_id = %s AND is_active = TRUE
        """, (user_id,))
        code_row = cursor.fetchone()

        if not code_row:
            raise HTTPException(status_code=404, detail="No referral code found. Visit your account page first.")

        code, tier, rate, total, successful, earnings = code_row

        cursor.execute("""
            SELECT COUNT(*) FROM referrals
            WHERE referrer_id = %s AND status = 'pending'
        """, (user_id,))
        pending = cursor.fetchone()[0]

        cursor.execute("""
            SELECT u.email, r.status, r.created_at, r.converted_at
            FROM referrals r
            JOIN users u ON r.referred_user_id = u.id
            WHERE r.referrer_id = %s
            ORDER BY r.created_at DESC
        """, (user_id,))

        referrals_list = []
        for row in cursor.fetchall():
            email = row[0]
            at_idx = email.index('@')
            masked = email[:2] + '***' + email[at_idx:]
            referrals_list.append({
                "email": masked, "status": row[1],
                "created_at": row[2].isoformat() if row[2] else None,
                "converted_at": row[3].isoformat() if row[3] else None
            })

        return {
            "code": code, "tier": tier,
            "commission_rate": float(rate),
            "total_referrals": total,
            "successful_referrals": successful,
            "pending_referrals": pending,
            "total_earnings": float(earnings),
            "referrals": referrals_list
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Referral stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/referral/apply")
async def apply_referral_code(request: ReferralApplyRequest, current_user=Depends(get_current_user_from_token)):
    """Apply a referral code to the current user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']
        code = request.code.strip().upper()

        cursor.execute("SELECT referred_by FROM users WHERE id = %s", (user_id,))
        user_row = cursor.fetchone()
        if user_row and user_row[0]:
            raise HTTPException(status_code=400, detail="You have already applied a referral code")

        cursor.execute("""
            SELECT id, user_id, code
            FROM referral_codes
            WHERE code = %s AND is_active = TRUE
        """, (code,))
        code_row = cursor.fetchone()
        if not code_row:
            raise HTTPException(status_code=404, detail="Invalid referral code")

        referrer_code_id, referrer_user_id, referral_code = code_row

        if referrer_user_id == user_id:
            raise HTTPException(status_code=400, detail="You cannot use your own referral code")

        cursor.execute("""
            INSERT INTO referrals (referrer_id, referred_user_id, referral_code, status)
            VALUES (%s, %s, %s, 'pending')
        """, (referrer_user_id, user_id, referral_code))

        cursor.execute("""
            UPDATE referral_codes SET total_referrals = total_referrals + 1
            WHERE id = %s
        """, (referrer_code_id,))

        cursor.execute("UPDATE users SET referred_by = %s WHERE id = %s", (referral_code, user_id))
        conn.commit()

        return {"success": True, "message": "Referral code applied"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Referral apply error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/admin/referrals")
async def get_all_referrals(current_user=Depends(get_current_user_from_token)):
    """Get all referral codes with user info (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT u.id, u.email, rc.code, rc.tier, rc.commission_rate,
                   rc.total_referrals, rc.successful_referrals,
                   rc.total_earnings, rc.is_active, rc.created_at
            FROM referral_codes rc
            JOIN users u ON rc.user_id = u.id
            ORDER BY rc.created_at DESC
        """)

        referrals = []
        for row in cursor.fetchall():
            referrals.append({
                "user_id": row[0], "user_email": row[1],
                "code": row[2], "tier": row[3],
                "commission_rate": float(row[4]),
                "total_referrals": row[5],
                "successful_referrals": row[6],
                "total_earnings": float(row[7]),
                "is_active": row[8],
                "created_at": row[9].isoformat() if row[9] else None
            })

        return {"referrals": referrals}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Admin referrals error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.patch("/api/admin/referral/{user_id}/tier")
async def update_referral_tier(
    user_id: int,
    request: ReferralTierUpdateRequest,
    current_user=Depends(get_current_user_from_token)
):
    """Update a user's affiliate tier and commission rate (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    valid_tiers = {'ambassador', 'champion', 'partner'}
    if request.tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {', '.join(valid_tiers)}")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE referral_codes
            SET tier = %s, commission_rate = %s
            WHERE user_id = %s
            RETURNING code, tier, commission_rate, successful_referrals
        """, (request.tier, request.commission_rate, user_id))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Referral code not found for this user")

        code, tier, rate, successful = result

        # Auto-upgrade: if 5+ successful referrals and still ambassador, bump to champion
        if successful >= 5 and tier == 'ambassador':
            cursor.execute("""
                UPDATE referral_codes SET tier = 'champion', commission_rate = 25.00
                WHERE user_id = %s RETURNING tier, commission_rate
            """, (user_id,))
            upgraded = cursor.fetchone()
            tier, rate = upgraded

        conn.commit()
        return {"message": f"Tier updated to {tier}", "code": code, "tier": tier, "commission_rate": float(rate)}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Admin tier update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# AI GRADING ROUTES
# ============================================================================

# Import AI grading router
from routes_ai_grading import router as ai_grading_router

# Include AI grading routes
app.include_router(ai_grading_router)

# ============================================================================
# STUDENT ROUTES (Phife)
# ============================================================================

from routers.student import router as student_router
app.include_router(student_router)

