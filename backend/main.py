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
from model_router import get_model_config, calculate_cost, get_tier_limits

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
    try:
        init_db()
        print("✅ Database initialized successfully")
    except Exception as e:
        print(f"⚠️  Database initialization failed: {e}")
        print("   App will continue but some features may not work")

# CORS middleware - Allow readysetclass.app domains only
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

# Initialize Groq (primary - FREE!)
if os.getenv("GROQ_API_KEY"):
    try:
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        print("✅ Groq client initialized (Llama 3.3 70B - FREE! - PRIMARY)")
    except Exception as e:
        print(f"⚠️  Groq initialization failed: {e}")
        print("   Using OpenAI or Anthropic instead")

# Initialize OpenAI (fallback - paid)
if os.getenv("OPENAI_API_KEY"):
    try:
        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print("✅ OpenAI client initialized (GPT-4o-mini - fallback)")
    except Exception as e:
        print(f"⚠️  OpenAI initialization failed: {e}")

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
    Supports: Groq (FREE!), OpenAI (fallback), Anthropic (last resort)
    Provider priority: Groq > OpenAI > Anthropic
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
        Priority: Groq (FREE!) > OpenAI > Anthropic
        Returns: (response_text, cost)
        """
        # Try Groq first (FREE! - primary for all course building)
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
                print(f"⚠️  Groq failed: {e}, trying OpenAI...")

        # Try OpenAI second (paid fallback)
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
                print(f"⚠️  OpenAI failed: {e}, falling back to Claude...")

        # Last resort: Claude Sonnet (highest quality)
        if self.anthropic_client:
            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-6",
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

    def call_haiku(self, prompt: str, system: str = "", max_tokens: int = 2048) -> tuple[str, float]:
        """Use Claude Haiku 4.5 for high-quality, natural content generation."""
        if self.anthropic_client:
            try:
                response = self.anthropic_client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}]
                )
                cost = (response.usage.input_tokens / 1_000_000 * 0.80) + (response.usage.output_tokens / 1_000_000 * 4.0)
                print(f"✅ Haiku 4.5 response (cost: ${cost:.4f})")
                return response.content[0].text, cost
            except Exception as e:
                print(f"⚠️  Haiku failed: {e}, falling back to Groq...")
        return self.call_ai(prompt, system)
    
    def call_sonnet(self, prompt: str, system: str = "", max_tokens: int = 4000) -> tuple[str, float]:
        """Use Claude Sonnet 4.6 — primary model for all professor-facing outputs."""
        if self.anthropic_client:
            try:
                response = self.anthropic_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}]
                )
                cost = calculate_cost("claude-sonnet-4-6", response.usage.input_tokens, response.usage.output_tokens)
                print(f"✅ Sonnet 4.6 response (cost: ${cost:.4f})")
                return response.content[0].text, cost
            except Exception as e:
                print(f"⚠️  Sonnet failed: {e}, falling back to Haiku...")
        return self.call_haiku(prompt, system, max_tokens)

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
        language: str = "en",
        tone: int = 3
    ) -> Dict:
        """Generate quiz questions with detailed context and grade-appropriate language (Groq - FREE!)"""

        # Get reading level instructions
        level_info = get_reading_level_instructions(grade_level)

        # Get language name
        language_name = LANGUAGE_MAP.get(language, "English")

        # Get tone description
        tone_desc = AI_TONE_MAP.get(tone, AI_TONE_MAP[3])

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

Tone: {tone_desc}

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

@app.get("/api/diagnostics")
async def diagnostics():
    """Check DB and Stripe configuration"""
    db_ok = False
    db_error = None
    try:
        DATABASE_URL = os.getenv('DATABASE_URL')
        if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        db_ok = True
    except Exception as e:
        db_error = f"{type(e).__name__}: {str(e)}"
    return {
        "database": {"connected": db_ok, "error": db_error},
        "database_url_set": bool(os.getenv('DATABASE_URL')),
        "stripe_configured": bool(os.getenv('STRIPE_SECRET_KEY'))
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
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    course_name: Optional[str] = None  # Selected course name for filtering reference materials

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
# AUTH & INSTITUTION HELPERS
# ============================================================================

def get_db_connection():
    """Get direct database connection"""
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(DATABASE_URL)


def resolve_institution_for_user(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Resolve the institution record for a given user using the institutions table.

    Strategy:
      1. Read users.institution (free-text) from the user dict
      2. Try to match institutions.name exactly
      3. If no match and institution name present, create minimal institutions record lazily
      4. Return the institutions row as a dict, or None if user has no institution

    This keeps existing users working as-is while enabling per-institution flags
    like qm_mode_enabled. Data cleanup / enrichment happens in separate passes.
    """
    institution_name = user.get("institution")
    if not institution_name:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Step 1: try to find an existing institution by exact name
        cursor.execute(
            """
            SELECT id, name, domain, qm_mode_enabled, seat_limit, stripe_customer_id, created_at
            FROM institutions
            WHERE name = %s
            """,
            (institution_name,),
        )
        row = cursor.fetchone()

        if not row:
            # Step 2: lazily create a minimal institution record
            cursor.execute(
                """
                INSERT INTO institutions (name)
                VALUES (%s)
                ON CONFLICT (name) DO NOTHING
                RETURNING id, name, domain, qm_mode_enabled, seat_limit, stripe_customer_id, created_at
                """,
                (institution_name,),
            )
            created = cursor.fetchone()

            # If another process created it first, read it back
            if not created:
                cursor.execute(
                    """
                    SELECT id, name, domain, qm_mode_enabled, seat_limit, stripe_customer_id, created_at
                    FROM institutions
                    WHERE name = %s
                    """,
                    (institution_name,),
                )
                row = cursor.fetchone()
            else:
                row = created

            conn.commit()

        if not row:
            return None

        return {
            "id": row[0],
            "name": row[1],
            "domain": row[2],
            "qm_mode_enabled": row[3],
            "seat_limit": row[4],
            "stripe_customer_id": row[5],
            "created_at": row[6],
        }
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# GENERATION LIMIT HELPERS
# ============================================================================

def check_and_increment_generation(user_id: int) -> dict:
    """
    Check generation limits and increment counter.
    Returns: {"allowed": bool, "demo_count": int, "tier": str, "used": int, "limit": int}
    Raises HTTPException with user-friendly message if blocked.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT subscription_tier, generations_used_this_cycle, monthly_generation_limit,
                   billing_cycle_start, total_demo_generations, is_demo
            FROM users WHERE id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        tier, used, limit, cycle_start, demo_gens, is_demo = row
        tier = tier or "demo"

        # Reset monthly counter if billing cycle rolled over
        if cycle_start:
            now = datetime.utcnow()
            cycle_dt = cycle_start if isinstance(cycle_start, datetime) else datetime.fromisoformat(str(cycle_start))
            if (now - cycle_dt).days >= 30:
                cursor.execute("""
                    UPDATE users SET generations_used_this_cycle = 0, billing_cycle_start = NOW()
                    WHERE id = %s
                """, (user_id,))
                used = 0

        tier_info = get_tier_limits(tier)
        monthly_limit = tier_info["monthly_gens"]

        # Demo / trial users: enforce total_demo_generations limit of 5
        if tier in ("demo", "trial") or is_demo:
            demo_count = demo_gens or 0
            if demo_count >= 5:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "DEMO_LIMIT_REACHED",
                        "message": "You've used all 5 free generations.",
                        "demo_count": demo_count
                    }
                )
            cursor.execute(
                "UPDATE users SET total_demo_generations = total_demo_generations + 1 WHERE id = %s",
                (user_id,)
            )
            conn.commit()
            return {"allowed": True, "demo_count": demo_count + 1, "tier": tier, "used": demo_count + 1, "limit": 5}

        # Paid users: enforce monthly generation limit
        if monthly_limit and used >= monthly_limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "MONTHLY_LIMIT_REACHED",
                    "message": f"You've used all {monthly_limit} generations this month. Upgrade your plan to continue.",
                    "used": used,
                    "limit": monthly_limit,
                    "tier": tier
                }
            )

        cursor.execute("""
            UPDATE users
            SET generations_used_this_cycle = generations_used_this_cycle + 1,
                ai_generations_count = ai_generations_count + 1,
                billing_cycle_start = COALESCE(billing_cycle_start, NOW())
            WHERE id = %s
        """, (user_id,))
        conn.commit()
        return {"allowed": True, "tier": tier, "used": (used or 0) + 1, "limit": monthly_limit}

    finally:
        cursor.close()
        conn.close()


def save_asset(user_id: int, asset_type: str, title: str, content: str,
               course_id: int = None, course_name: str = None,
               week_number: int = None, semester_tag: str = None,
               generation_params: dict = None, is_published: bool = False) -> int:
    """Auto-save generated content to assets table. Returns asset_id."""
    import json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO assets
                (user_id, course_id, course_name, asset_type, title, content,
                 week_number, semester_tag, generation_params, is_published)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            user_id, course_id, course_name, asset_type, title, content,
            week_number, semester_tag,
            json.dumps(generation_params) if generation_params else None,
            is_published
        ))
        asset_id = cursor.fetchone()[0]
        conn.commit()
        return asset_id
    except Exception as e:
        print(f"⚠️  Asset save failed: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def mark_asset_published(asset_id: int):
    """Mark an asset as published to Canvas."""
    if not asset_id:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE assets SET is_published = TRUE, updated_at = NOW() WHERE id = %s", (asset_id,))
        conn.commit()
    except Exception as e:
        print(f"⚠️  Asset publish mark failed: {e}")
    finally:
        cursor.close()
        conn.close()


def log_model_usage(user_id: int, task_type: str, model: str, provider: str,
                    input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0):
    """Log a model usage event to model_usage_log."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO model_usage_log (user_id, task_type, model_used, provider, input_tokens, output_tokens, cost_usd)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (user_id, task_type, model, provider, input_tokens, output_tokens, cost_usd))
        conn.commit()
    except Exception as e:
        print(f"⚠️  Model usage log failed: {e}")
    finally:
        cursor.close()
        conn.close()


def record_time_saved(user_id: int, asset_type: str, asset_id: int = None, semester_tag: str = None):
    """Log time saved after a Canvas push."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM app_config WHERE key = 'time_savings_minutes'")
        row = cursor.fetchone()
        if row:
            import json
            config = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            minutes = config.get(asset_type, 20)
        else:
            minutes = {"assignment": 45, "quiz": 30, "discussion": 20,
                       "announcement": 10, "page": 60, "syllabus": 60}.get(asset_type, 20)

        cursor.execute("""
            INSERT INTO time_savings (user_id, asset_id, asset_type, minutes_saved, semester_tag)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, asset_id, asset_type, minutes, semester_tag))
        conn.commit()
        return minutes
    except Exception as e:
        print(f"⚠️  Time savings log failed: {e}")
        return 0
    finally:
        cursor.close()
        conn.close()

async def get_current_user_from_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from session token"""
    token = credentials.credentials
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

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

        # Fetch preferred_language safely (column may not exist before migration 008)
        preferred_language = 'en'
        try:
            cursor.execute("SELECT preferred_language FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                preferred_language = row[0]
        except Exception:
            pass  # column doesn't exist yet — use default

        return {
            "user_id": user_id,
            "email": email,
            "role": role,
            "is_demo": is_demo,
            "preferred_language": preferred_language
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Auth error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Database connection error. Please try again.")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Login endpoint"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
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
        print(f"Login error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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


class LanguageUpdateRequest(BaseModel):
    preferred_language: str

_SUPPORTED_LANGS = {'en', 'es', 'fr', 'pt', 'ar', 'zh'}

@app.patch("/api/v2/user/language")
async def update_preferred_language(
    request: LanguageUpdateRequest,
    current_user=Depends(get_current_user_from_token)
):
    """Persist the user's preferred UI language."""
    if request.preferred_language not in _SUPPORTED_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported language code. Supported: {', '.join(sorted(_SUPPORTED_LANGS))}")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET preferred_language = %s WHERE id = %s",
            (request.preferred_language, current_user['user_id'])
        )
        conn.commit()
        return {"preferred_language": request.preferred_language}
    except Exception:
        conn.rollback()
        # Column may not exist before migration 008 — still return success
        # so the frontend doesn't show errors
        return {"preferred_language": request.preferred_language}
    finally:
        cursor.close()
        conn.close()

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

        def ensure_stripe_customer(email, user_id, existing_customer_id):
            """Get or create a Stripe customer, handling mode mismatches (test vs live)."""
            if existing_customer_id:
                try:
                    # Verify the stored customer still works in the current Stripe mode
                    stripe.Customer.retrieve(existing_customer_id)
                    return existing_customer_id
                except stripe.InvalidRequestError:
                    print(f"⚠️  Stored customer {existing_customer_id} invalid (mode mismatch?), creating new one")

            # Create new Stripe customer
            print(f"Creating Stripe customer for {email}")
            customer = stripe.Customer.create(
                email=email,
                metadata={"user_id": str(user_id)}
            )
            # Save new customer ID
            cursor.execute(
                "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
                (customer.id, user_id)
            )
            conn.commit()
            return customer.id

        stripe_customer_id = ensure_stripe_customer(email, current_user['user_id'], stripe_customer_id)

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
                'user_id': str(current_user['user_id']),
                'price_id': request.price_id
            }
        )

        cursor.close()
        conn.close()

        print(f"Checkout session created successfully: {checkout_session.id}")
        return {"checkout_url": checkout_session.url}

    except HTTPException:
        raise
    except Exception as e:
        error_type = type(e).__name__
        print(f"Checkout error: {error_type}: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try: conn.close()
            except Exception: pass
        # Surface the actual error for debugging
        if 'InvalidRequestError' in error_type:
            raise HTTPException(status_code=400, detail=f"Invalid payment request: {str(e)}")
        elif 'AuthenticationError' in error_type:
            raise HTTPException(status_code=500, detail="Payment system authentication failed. Please contact support.")
        raise HTTPException(status_code=500, detail=f"Checkout error: {str(e)}")


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    # Map Stripe price IDs to subscription tiers
    PRICE_TIER_MAP = {
        'price_1T0FFFGKcotGCnJDC4jShxqY': 'educator',   # educator-monthly
        'price_1T0FJnGKcotGCnJDGuf6bhAv': 'educator',   # educator-yearly
        'price_1T0FLtGKcotGCnJDTKbwtYHu': 'pro',        # pro-monthly
        'price_1T0FQXGKcotGCnJDIRJygEV0': 'pro',        # pro-yearly
        'price_1T0FrdGKcotGCnJDLCLHZxhK': 'school',     # school
        'price_1T0FbKGKcotGCnJDVrqbHUHu': 'district',   # district
    }

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

            # Determine tier from price_id stored in metadata
            price_id = session['metadata'].get('price_id', '')
            tier = PRICE_TIER_MAP.get(price_id, 'educator')  # default to educator if unknown
            print(f"✅ Checkout completed: user={user_id}, price={price_id}, tier={tier}")

            # Update user subscription and clear demo status if they were a demo user
            cursor.execute("""
                UPDATE users
                SET subscription_status = 'active',
                    subscription_tier = %s,
                    stripe_subscription_id = %s,
                    trial_ends_at = NULL,
                    is_demo = FALSE,
                    demo_expires_at = NULL
                WHERE id = %s
            """, (tier, session['subscription'], user_id))

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
async def generate_quiz_questions(
    request: QuizGenerateRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Generate quiz questions with AI (preview only, no Canvas upload)
    Returns questions for user to review before uploading
    """
    try:
        user_id = current_user['user_id']
        check_and_increment_generation(user_id)
        print(f"🧠 Generating {request.grade_level} quiz questions: {request.topic}")

        # Enrich description with user's reference materials (filtered to selected course)
        reference_context = get_user_reference_context(user_id, db, course_name=request.course_name)
        enriched_description = request.description
        if reference_context:
            enriched_description += f"\n\nCourse reference materials (use specific topics and concepts from here):\n{reference_context}"

        quiz_data = bonita.generate_quiz(
            week=1,
            topic=request.topic,
            description=enriched_description,
            num_questions=request.num_questions,
            difficulty=request.difficulty,
            grade_level=request.grade_level,
            language=request.language,
            tone=request.tone
        )

        questions = quiz_data.get("questions", [])
        import json as _json
        asset_id = save_asset(
            user_id, 'quiz', f"Quiz: {request.topic}",
            _json.dumps(questions, indent=2),
            course_name=request.course_name
        )

        return {
            "status": "success",
            "topic": request.topic,
            "questions": questions,
            "num_questions": len(questions),
            "asset_id": asset_id,
            "message": "Quiz questions generated! Review and upload to Canvas."
        }

    except HTTPException:
        raise
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
        # Calculate total points from individual questions, fallback to num_questions * 10
        calc_points = sum(q.get("points", 10) for q in request.questions) if request.questions else request.num_questions * 10
        quiz_id = canvas_client.create_quiz(
            course_id=request.course_id,
            quiz_data={
                "title": quiz_title,
                "quiz_type": "assignment",
                "time_limit": 20,
                "allowed_attempts": 1,
                "points_possible": calc_points,
                "due_at": request.due_date
            }
        )

        if not quiz_id:
            raise HTTPException(status_code=500, detail="Failed to create quiz in Canvas")

        # Add questions to Canvas quiz (supports MCQ, T/F, essay, matching, short answer)
        total_points = 0
        for i, question in enumerate(request.questions, 1):
            q_type = question.get("question_type", "multiple_choice_question")
            q_points = question.get("points", 10)
            total_points += q_points

            # Build answers based on question type
            q_answers = []
            if q_type == "matching_question":
                q_answers = [
                    {"answer_match_left": ans.get("match_left", ans.get("text", "")),
                     "answer_match_right": ans.get("match_right", "")}
                    for ans in question.get("answers", [])
                ]
            elif q_type == "essay_question":
                q_answers = []  # no answers for essay
            else:
                q_answers = [
                    {"answer_text": ans["text"],
                     "answer_weight": 100 if ans.get("correct") else 0}
                    for ans in question.get("answers", [])
                ]

            canvas_client.add_quiz_question(
                course_id=request.course_id,
                quiz_id=quiz_id,
                question_data={
                    "name": f"Question {i}",
                    "text": question["question_text"],
                    "type": q_type,
                    "points": q_points,
                    "answers": q_answers
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
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    course_name: Optional[str] = None  # Selected course name for filtering reference materials
    use_qm_alignment: bool = False  # Per-request QM toggle (server-enforced)


AI_TONE_MAP = {
    1: "very formal and academic — use professional language, avoid contractions, maintain scholarly distance",
    2: "professional and clear — well-organized, polished, approachable",
    3: "balanced — professional yet warm, friendly tone",
    4: "friendly and conversational — warm, encouraging, use contractions naturally",
    5: "casual and personable — like a colleague talking to students, relaxed and approachable"
}

# Keep ANNOUNCEMENT_TONE_MAP as alias for backwards compatibility
ANNOUNCEMENT_TONE_MAP = AI_TONE_MAP


def get_user_reference_context(user_id: int, db: Session, max_materials: int = 3, max_chars: int = 3000, course_name: str = None) -> str:
    """Fetch a user's uploaded reference materials and return as a formatted context string.

    If course_name is provided, tries multiple strategies to filter materials to the correct course:
    1. Extract course code (e.g. 'MCM 200') and match against stored course_name / file_name
    2. If no code match, match by significant words from the canvas course display name
    Falls back to all materials if nothing matches.
    """
    import re
    try:
        all_materials = db.query(ReferenceMaterial).filter_by(user_id=user_id).order_by(
            ReferenceMaterial.upload_date.desc()
        ).all()
        if not all_materials:
            return ""

        filtered = all_materials
        matched_label = None  # human-readable label for what was matched

        if course_name:
            # Strategy 1: extract course code (e.g. "MCM 200") and match against stored metadata
            code_match = re.search(r'\b([A-Z]{2,5})\s*(\d{3,4})\b', course_name)
            if code_match:
                course_code = f"{code_match.group(1)} {code_match.group(2)}"
                matched = [
                    m for m in all_materials
                    if (m.course_name and course_code.lower() in m.course_name.lower())
                    or (m.file_name and course_code.lower() in m.file_name.lower())
                ]
                if matched:
                    filtered = matched
                    matched_label = course_code
                    print(f"📚 Filtered to {len(filtered)} materials matching course code '{course_code}'")

            # Strategy 2: if code match found nothing, try significant words from the display name
            # Strip Canvas semester codes like (2025;20;MCM 200 1001) and use the course title words
            if filtered is all_materials and course_name:
                clean_name = re.sub(r'\(.*?\)', '', course_name).strip()  # remove parenthetical codes
                words = [w for w in re.split(r'\W+', clean_name) if len(w) >= 5]  # meaningful words only
                if words:
                    matched = [
                        m for m in all_materials
                        if m.course_name and any(w.lower() in m.course_name.lower() for w in words)
                        or m.file_name and any(w.lower() in m.file_name.lower() for w in words)
                    ]
                    if matched:
                        filtered = matched
                        matched_label = clean_name
                        print(f"📚 Filtered to {len(filtered)} materials matching course name words")

            if filtered is all_materials:
                print(f"📚 No filter match for '{course_name}', using all {len(all_materials)} materials")

        materials = filtered[:max_materials]
        parts = []
        for m in materials:
            label = f"{m.file_name}" + (f" (Course: {m.course_name})" if m.course_name else "")
            parts.append(f"[{label}]\n{m.extracted_text[:max_chars]}")
        return "\n\n".join(parts)
    except Exception:
        return ""


QM_SYSTEM_PROMPT_FULL = """You are generating educational content with Quality Matters (QM) alignment.
Apply the following principles to every piece of content you produce.

LEARNING OBJECTIVES (QM General Standard 2)
  - All learning objectives must be MEASURABLE. Use action verbs from
    Bloom's Taxonomy that describe observable, assessable behavior.
  - BANNED verbs (unmeasurable): understand, know, learn, appreciate,
    be aware of, realize, recognize the importance of, become familiar with
  - REQUIRED: Replace banned verbs with measurable alternatives.
    understand → explain, analyze, describe, compare
    know → identify, define, list, state
    appreciate → evaluate, reflect on, argue, assess
  - Match verb cognitive level to course level:
    Introductory courses: define, identify, describe, explain, classify
    Intermediate courses: apply, demonstrate, calculate, solve, construct
    Advanced courses: analyze, evaluate, synthesize, design, critique
  - Write objectives in learner-centered language:
    "Upon completion, learners will be able to [verb] [specific outcome]"
    NOT: "This module covers [topic]" or "Students will learn [topic]"
  - Course-level objectives must connect logically to module-level objectives.
    Do not generate module objectives that cannot trace back to a course goal.

ALIGNMENT (QM Core Principle — runs through SRS 2.1, 3.1, 4.1, 5.1, 6.1)
  - Every assessment you generate must measure one or more stated objectives.
    Name the objective(s) each assessment addresses.
  - Every activity you generate must help learners prepare for an assessment.
    Explain how the activity connects to the objective and assessment.
  - Do not generate assessments that are misaligned with their objectives.
    Example of misalignment: objective says "deliver a speech" but assessment
    asks learners to "write about public speaking." Flag this if it occurs.
  - When generating a syllabus, include an alignment note for each major
    assessment showing which course objectives it measures.

ASSESSMENTS (QM General Standard 3)
  - Include multiple assessment types when generating a full course structure:
    formative (low-stakes, feedback-focused) + summative (graded, higher-stakes)
  - Sequence assessments so learners build on earlier work.
  - Grading criteria must be specific. Rubrics should describe performance
    levels, not just point values. Vague criteria ("good analysis") fail QM.
  - Academic integrity guidance should be included in assessment instructions.

INSTRUCTIONAL MATERIALS (QM General Standard 4)
  - When referencing materials, explain HOW they connect to the objective.
    Not: "Read Chapter 3."
    Yes: "Read Chapter 3 to prepare for the Module 2 discussion on X,
         which connects to Course Objective 2."
  - Suggest variety of material types when appropriate: text, video, audio,
    interactive. Do not default to text-only.

COURSE OVERVIEW / SYLLABUS (QM General Standard 1)
  - Syllabi must include: how to get started, course purpose and structure,
    communication guidelines, grading policy, technology requirements,
    required prior knowledge, and instructor introduction placeholder.
  - Grading policy must be internally consistent. If points are used,
    use points throughout. Do not mix points and percentages without
    explaining the relationship.

ACCESSIBILITY (QM General Standard 8)
  - Flag any generated content that may create accessibility issues.
  - Learning objectives and instructions should avoid unnecessary jargon.
  - When generating headings, maintain logical hierarchy (H1 → H2 → H3).

TRANSPARENCY
  - When generating learning objectives, briefly note which Bloom's level
    each verb represents. This helps faculty understand the cognitive level
    of their course.
  - When alignment is strong, note it. When a potential alignment gap exists,
    flag it constructively — never critically. Tone: colleague, not auditor.
"""


def apply_qm_prompt(content_type: str, use_qm_alignment: bool, base_system_prompt: str) -> str:
    """
    Inject QM system prompt content based on content_type and toggle.

    content_type: one of "syllabus", "assignment", "quiz", "discussion",
                  "lesson_plan", "study_guide", "study_pack", "exam",
                  "announcement", "rubric", "image"
    use_qm_alignment: already-enforced flag (institution + request)
    base_system_prompt: existing system prompt string
    """
    if not use_qm_alignment:
        return base_system_prompt

    # Map content types to QM behavior (FULL / PARTIAL / NONE)
    full_types = {"syllabus", "assignment", "lesson_plan", "rubric"}
    partial_types = {"quiz", "discussion", "study_guide", "study_pack", "exam"}

    if content_type in full_types:
        qm_block = QM_SYSTEM_PROMPT_FULL
    elif content_type in partial_types:
        # TODO: For PARTIAL types, trim this block down per content type.
        # For now we reuse the full block so behavior is safely QM-forward.
        qm_block = QM_SYSTEM_PROMPT_FULL
    else:
        # NONE — no QM injection
        return base_system_prompt

    return f"{qm_block}\n\n{base_system_prompt}"

class AnnouncementRequest(BaseModel):
    course_id: int
    topic: str
    details: Optional[str] = None
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    custom_message: Optional[str] = None  # If set, use this text instead of generating
    enhance: bool = False  # If True + custom_message, AI polishes the custom message


class AIPageRequest(BaseModel):
    title: str
    page_type: str
    description: str
    objectives: Optional[str] = None
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    course_name: Optional[str] = None  # Selected course name for filtering reference materials
    study_pack_sections: Optional[List[str]] = None  # hook, cases, critical, glossary, resources, closing, assignment, model_example, rubric
    use_qm_alignment: bool = False  # Per-request QM toggle (server-enforced)


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

        # Get language name and tone description
        language_name = LANGUAGE_MAP.get(request.language, "English")
        tone_desc = ANNOUNCEMENT_TONE_MAP.get(request.tone, ANNOUNCEMENT_TONE_MAP[3])

        if request.custom_message and not request.enhance:
            # Quick Input: Post as-is, just wrap in HTML paragraphs
            print(f"📢 Posting announcement as-is (no AI)")
            paragraphs = [f"<p>{p.strip()}</p>" for p in request.custom_message.strip().split('\n\n') if p.strip()]
            announcement_html = '\n'.join(paragraphs) if paragraphs else f"<p>{request.custom_message}</p>"
        elif request.custom_message and request.enhance:
            # Quick Input + AI Polish: lightly improve what they wrote
            print(f"📢 Polishing announcement with AI (tone={request.tone})")
            system = "You are Bonita, helping professors refine course announcements."
            prompt = f"""Polish and lightly improve this course announcement while keeping the author's voice and intent.

Tone: {tone_desc}
Language: {language_name} — ALL content must be in {language_name}.

Original announcement:
{request.custom_message}

Instructions:
- Keep the same meaning and key points
- Improve clarity, flow, and grammar
- Apply the requested tone — do not over-formalize or over-casualize
- Keep it concise (2-3 paragraphs max)
- Format in HTML for Canvas

Return just the HTML content, no markdown code blocks."""
            announcement_html, _ = bonita.call_haiku(prompt, system)
        else:
            # AI Generate: write the full announcement from topic
            print(f"📢 Generating announcement on: {request.topic}")
            system = "You are Bonita, helping professors create course announcements."
            prompt = f"""Create a course announcement about: {request.topic}

Tone: {tone_desc}
IMPORTANT: Generate ALL content in {language_name}. The entire announcement must be in {language_name}.

{f'Additional details: {request.details}' if request.details else ''}

Make it:
- {tone_desc}
- Clear and concise (2-3 paragraphs max)
- Action-oriented if needed
- Formatted in HTML for Canvas

Return just the HTML content, no markdown code blocks."""
            announcement_html, _ = bonita.call_haiku(prompt, system)

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

        # Auto-save to asset bank + record time saved
        asset_id = save_asset(
            user_id=current_user['user_id'], asset_type="announcement",
            title=request.topic, content=announcement_html,
            course_id=request.course_id,
            generation_params={"topic": request.topic},
            is_published=True
        )
        record_time_saved(current_user['user_id'], "announcement", asset_id)

        return {
            "status": "success",
            "announcement_id": result["id"],
            "asset_id": asset_id,
            "title": request.topic,
            "preview_url": preview_url,
            "message": "Announcement posted successfully!",
            "minutes_saved": 10
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/generate-page")
async def generate_ai_page(
    request: AIPageRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Generate AI-enhanced course page content using Groq/OpenAI
    Returns professional page content with proper structure
    """
    try:
        user_id = current_user['user_id']
        check_and_increment_generation(user_id)

        # Resolve institution and enforce QM toggle before building prompts
        institution = resolve_institution_for_user(current_user)
        institution_qm_enabled = bool(institution and institution.get("qm_mode_enabled"))
        qm_active = bool(institution_qm_enabled and request.use_qm_alignment)

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

        # Get language name and tone description
        language_name = LANGUAGE_MAP.get(request.language, "English")
        tone_desc = AI_TONE_MAP.get(request.tone, AI_TONE_MAP[3])

        # Study guides need more material to avoid truncating sections
        ref_max_chars = 8000 if request.page_type == "study_guide" else 3000

        # Fetch reference materials for context (filtered to selected course)
        reference_context = get_user_reference_context(current_user['user_id'], db, course_name=request.course_name, max_chars=ref_max_chars)
        course_label = request.course_name or "this course"

        base_system = """You are Bonita, an AI assistant helping college professors create course pages.
Your output should be well-formatted HTML suitable for Canvas LMS."""

        # Study guides map to QM content type "study_guide", everything else is "page" (NONE)
        content_type = "study_guide" if request.page_type == "study_guide" else "page"
        system = apply_qm_prompt(content_type, qm_active, base_system)

        sections = set(request.study_pack_sections or [])

        # CASE 1: Study guide — convert professor's uploaded source material faithfully
        if request.page_type == "study_guide" and reference_context:
            prompt = f"""Convert this professor's study guide into polished HTML for Canvas LMS.

SELECTED COURSE: {course_label}
Tone: {tone_desc}
Language: {language_name} — ALL content must be in {language_name}.

SOURCE MATERIAL (convert ALL of this — do NOT skip or summarize any section):
{reference_context}

Professor's additional instructions:
{request.description}

STRICT RULES:
1. Include EVERY section from the source — do not drop any people, topics, or sections.
2. Preserve ALL links exactly as written. Convert each URL to: <a href="URL" target="_blank">Link Text</a>
3. Do NOT remove or summarize sections — convert each one fully.
4. Maintain the order and structure of the original.
5. Only add extra sections (glossary, key takeaways, etc.) if explicitly requested above.

Format in HTML for Canvas:
- <h3> for numbered section headings, <p> for paragraphs, <ul>/<li> for bullet lists
- <strong> for key terms, <a href="..." target="_blank"> for all links

Do NOT include the page title as a heading (Canvas adds it automatically)."""

        # CASE 2: Study guide — generate fresh from scratch using professor's format
        elif request.page_type == "study_guide":
            # Build section instructions based on checkboxes
            section_instructions = []
            num = 1

            if 'hook' in sections:
                section_instructions.append(f"{num}. HOOK / OPENING SCENARIO — Open with a compelling real-world story or question (a specific person, dollar amount, date, platform). End with a sharp question that reframes the issue. Use short punchy paragraphs.")
                num += 1
            if 'cases' in sections:
                section_instructions.append(f"{num}. CASE STUDIES (2-3) — Each with: a scene-setting intro, the problem, key facts/timeline, what happened after, and the lesson. Use real names, dates, and numbers. Keep it tight and engaging.")
                num += 1
            if 'critical' in sections:
                section_instructions.append(f"{num}. CULTURAL/CRITICAL ANALYSIS — Examine race, power, systemic dynamics, or double standards related to the topic. Ask hard questions. Don't just describe — analyze.")
                num += 1
            if 'glossary' in sections:
                section_instructions.append(f"{num}. KEY TERMS / GLOSSARY — 5-8 terms with clear, direct definitions. Format as a definition list.")
                num += 1
            if 'resources' in sections:
                section_instructions.append(f"{num}. RESOURCES WITH LINKS — 2-3 resources per major section. Use REAL URLs from stable sources: FTC.gov, FCC.gov, history.com, Britannica, NPR, PBS, Pew Research, Rolling Stone, YouTube, Netflix, academic journals, major newspapers. Format each as: <a href='URL' target='_blank'>Source Name – Article/Resource Title</a>. Add a note to verify links before posting.")
                num += 1
            if 'closing' in sections:
                section_instructions.append(f"{num}. CLOSING FRAME — A tight 2-3 line paragraph that connects back to the course's bigger theme and teases what comes next. Conversational and punchy.")
                num += 1
            if 'assignment' in sections:
                section_instructions.append(f"{num}. ASSIGNMENT — Title, due date placeholder, point value, word count range, and a clear mission statement. Break it into numbered steps (Find, Document, Analyze, Conclude). Each step should tell students exactly what to do.")
                num += 1
            if 'model_example' in sections:
                section_instructions.append(f"{num}. MODEL EXAMPLE (WEAK vs STRONG) — Show students the difference between a C-level response and an A-level response on the assignment. The weak example should be vague and surface-level. The strong example should be specific, analytical, cite sources, and demonstrate critical thinking.")
                num += 1
            if 'rubric' in sections:
                section_instructions.append(f"{num}. GRADING RUBRIC — HTML table with 4 criteria columns (A: 90-100, B: 80-89, C/D: 60-79) and a points column. Make each level description specific and actionable, not generic.")
                num += 1

            sections_block = "\n\n".join(section_instructions) if section_instructions else "Create a complete study guide with all standard sections."

            prompt = f"""You are creating a college-level study pack for a professor. Write it with the energy and editorial voice of a professor who is direct, culturally aware, and knows how to engage students.

TOPIC: {request.title}
COURSE: {course_label}
Tone: {tone_desc}
Language: {language_name} — ALL content must be in {language_name}.
Professor's notes: {request.description}
{f'Learning objectives: {request.objectives}' if request.objectives else ''}

STYLE GUIDE:
- Short, punchy paragraphs — never more than 3-4 sentences in a row
- Use em-dashes (—) for emphasis and rhythm
- Ask rhetorical questions to pull students in
- Be specific: use real names, real dollar amounts, real dates, real cases
- Don't hedge or qualify everything — take a stance
- Cultural and racial dynamics are fair game when relevant to the topic
- Connect ideas to previous or upcoming course themes when natural

SECTIONS TO INCLUDE (in this order):
{sections_block}

FORMAT IN HTML FOR CANVAS:
- <h3> for major section headings
- <h4> for subsections within a section
- <p> for paragraphs, <ul>/<li> for bullet lists, <ol>/<li> for numbered steps
- <strong> for key terms and emphasis
- <a href="URL" target="_blank">Link text</a> for all resources
- <table> for the rubric if included
- Do NOT wrap everything in a div — just the HTML elements directly

Do NOT include the page title as a heading (Canvas adds it automatically).
Do NOT add a generic introduction paragraph before the first section."""

        # CASE 3: All other page types
        else:
            ref_section = f"\n\nCOURSE REFERENCE MATERIALS for {course_label} (use ONLY these — ignore content from any other courses):\n{reference_context}" if reference_context else ""
            prompt = f"""Create a course page titled: {request.title}

SELECTED COURSE: {course_label}
Language: {language_name} — ALL content must be in {language_name}.
Tone: {tone_desc}
Page Type: {page_type_desc}
Description: {request.description}
{f'Learning Objectives: {request.objectives}' if request.objectives else ''}{ref_section}

IMPORTANT: Generate content SPECIFICALLY for {course_label}. Use ONLY reference materials that belong to this course.

Write content that is specific to this course — use actual topics, concepts, and terminology from the reference materials above. Avoid generic filler.

Structure the page logically for the page type. Include an introduction, well-organized main content with clear headings, and key takeaways. Length should match the complexity of the topic — don't pad.

Format in HTML for Canvas:
- <h3> for section headings, <h4> for subsections
- <p> for paragraphs, <ul>/<li> for lists, <strong> for emphasis

Do NOT include the page title as a heading (Canvas adds it automatically)."""

        # Study guides need more output tokens to render all sections fully
        sonnet_max_tokens = 4096 if request.page_type == "study_guide" else 4000

        # Generate with Claude Sonnet 4.6 (page_full route per model router)
        generated_content, cost = bonita.call_sonnet(prompt, system, max_tokens=sonnet_max_tokens)

        asset_id = save_asset(
            user_id, 'page', request.title or "Course Page",
            generated_content, course_name=request.course_name,
            generation_params={"qm_mode_used": qm_active}
        )

        print(f"✅ Page generated (cost: ${cost:.4f})")

        return {
            "status": "success",
            "generated_content": generated_content,
            "asset_id": asset_id,
            "cost": cost,
            "qm_mode_used": qm_active
        }

    except HTTPException:
        raise
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
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Generate AI-enhanced assignment content using Bonita
    Returns professional assignment description with instructions, objectives, rubric
    """
    try:
        check_and_increment_generation(current_user['user_id'])

        # Resolve institution and enforce QM toggle before building prompts
        institution = resolve_institution_for_user(current_user)
        institution_qm_enabled = bool(institution and institution.get("qm_mode_enabled"))
        qm_active = bool(institution_qm_enabled and request.use_qm_alignment)

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

        # Get language name and tone description
        language_name = LANGUAGE_MAP.get(request.language, "English")
        tone_desc = AI_TONE_MAP.get(request.tone, AI_TONE_MAP[3])

        # Fetch reference materials for course-specific context (filtered to selected course)
        reference_context = get_user_reference_context(current_user['user_id'], db, course_name=request.course_name)
        course_label = request.course_name or "this course"
        ref_section = f"\n\nCOURSE REFERENCE MATERIALS for {course_label} (use ONLY these — ignore content from any other courses):\n{reference_context}" if reference_context else ""

        base_system = """You are Bonita, an AI assistant helping college professors create assignments.
Write in HTML formatted for Canvas LMS."""
        system = apply_qm_prompt("assignment", qm_active, base_system)

        prompt = f"""Help a professor create a {assignment_type_desc} for their course.

SELECTED COURSE: {course_label}
Tone: {tone_desc}
Assignment Title: {request.topic}
Points: {request.points}
Language: {language_name} — ALL content must be in {language_name}.

Professor's instructions:
{request.requirements}{ref_section}

IMPORTANT: Generate content SPECIFICALLY for {course_label}. If reference materials are provided above, use ONLY the ones that belong to this course. Do NOT pull in concepts from other courses.

Write a focused, course-specific assignment. Use the actual topics, concepts, and terminology from this course — do NOT write generic academic boilerplate.

Include:
1. **Overview** — what students will do and why (1-2 direct paragraphs, no filler)
2. **Instructions** — clear numbered steps
3. **Deliverables** — exact list of what to submit
4. **Grading Rubric** — HTML table with criteria, point values, and clear expectations

Format in HTML for Canvas:
- <h3> for section headings, <p> for paragraphs, <ul>/<li> for lists
- <table><thead><tbody> for the rubric

Do NOT include the assignment title as a heading. Be practical, specific, and actionable."""

        # Generate with Claude Haiku 4.5 (natural, course-specific content)
        generated_content, cost = bonita.call_haiku(prompt, system)

        print(f"✅ Assignment generated (cost: ${cost:.4f})")

        return {
            "status": "success",
            "generated_content": generated_content,
            "cost": cost,
            "qm_mode_used": qm_active
        }

    # KNOWN GAP — Assignment QM logging
    # generate_ai_assignment returns HTML but does not call save_asset.
    # Persistence happens at /api/v2/canvas/assignment (upload path).
    # qm_mode_used is not currently threaded through to that write.
    # Resolution: future brief will either (a) add a lightweight generation
    # log table, or (b) pass qm_mode_used as a hidden field through the
    # upload payload. Do not patch this inline — it needs a design decision.

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

            description, _ = bonita.call_sonnet(prompt, system, max_tokens=4000)

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

        # Auto-save to asset bank + record time saved
        asset_id = save_asset(
            user_id=current_user['user_id'], asset_type="assignment",
            title=request.title, content=description,
            course_id=request.course_id,
            generation_params={"topic": request.title, "assignment_type": getattr(request, 'assignment_type', None)},
            is_published=True
        )
        record_time_saved(current_user['user_id'], "assignment", asset_id)

        return {
            "status": "success",
            "assignment_id": result["id"],
            "asset_id": asset_id,
            "title": request.title,
            "points": request.points,
            "preview_url": preview_url,
            "message": "Assignment created successfully! Review and publish in Canvas.",
            "minutes_saved": 45
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
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    course_name: Optional[str] = None  # Selected course name for filtering reference materials
    use_qm_alignment: bool = False  # Per-request QM toggle (server-enforced)


class AISyllabusRequest(BaseModel):
    course_name: str
    description: str
    objectives: str
    grading: str
    language: str = "en"  # Language code: en, es, fr, pt, ar, zh
    tone: int = 3  # 1=Formal, 2=Professional, 3=Balanced, 4=Friendly, 5=Casual
    selected_course_name: Optional[str] = None  # Selected Canvas course for filtering reference materials
    use_qm_alignment: bool = False  # Per-request QM toggle (server-enforced)


class SyllabusRequest(BaseModel):
    course_id: int
    syllabus_body: str


@app.post("/api/v2/canvas/generate-discussion")
async def generate_ai_discussion(
    request: AIDiscussionRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Generate AI-enhanced discussion topic"""
    try:
        user_id = current_user['user_id']
        check_and_increment_generation(user_id)

        # Resolve institution and enforce QM toggle before building prompts
        institution = resolve_institution_for_user(current_user)
        institution_qm_enabled = bool(institution and institution.get("qm_mode_enabled"))
        qm_active = bool(institution_qm_enabled and request.use_qm_alignment)

        language_name = LANGUAGE_MAP.get(request.language, "English")
        tone_desc = AI_TONE_MAP.get(request.tone, AI_TONE_MAP[3])

        reference_context = get_user_reference_context(current_user['user_id'], db, course_name=request.course_name)
        course_label = request.course_name or "this course"
        ref_section = f"\n\nCOURSE REFERENCE MATERIALS for {course_label} (use ONLY these — ignore content from any other courses):\n{reference_context}" if reference_context else ""

        base_system = "You are Bonita, helping professors create engaging class discussions."
        system = apply_qm_prompt("discussion", qm_active, base_system)
        prompt = f"""Create a discussion topic for a college course.

SELECTED COURSE: {course_label}
Tone: {tone_desc}
Topic: {request.topic}
Discussion Type: {request.discussion_type}
Learning Goals: {request.goals}
Language: {language_name} — ALL content must be in {language_name}.{ref_section}

IMPORTANT: Generate content SPECIFICALLY for {course_label}. Use ONLY reference materials that belong to this course.

Write a discussion post that is specific to this course — reference actual concepts and readings from the course materials. Avoid generic prompts.

Include:
1. **Opening Prompt** — context and a compelling hook (1-2 focused paragraphs)
2. **Discussion Questions** — 3-4 specific, thought-provoking questions tied to this course
3. **Participation Guidelines** — what's expected (length, peer responses, citations if relevant)

Format in HTML for Canvas. Be direct and engaging — students should know exactly what to discuss."""

        content, cost = bonita.call_haiku(prompt, system)

        asset_id = save_asset(
            user_id, 'discussion', f"Discussion: {request.topic}",
            content, course_name=request.course_name,
            generation_params={"qm_mode_used": qm_active}
        )

        print(f"✅ Discussion generated (cost: ${cost:.4f})")
        return {
            "status": "success",
            "generated_content": content,
            "asset_id": asset_id,
            "cost": cost,
            "qm_mode_used": qm_active
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/canvas/generate-syllabus")
async def generate_ai_syllabus(
    request: AISyllabusRequest,
    current_user=Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """Generate AI-enhanced course syllabus"""
    try:
        user_id = current_user['user_id']
        check_and_increment_generation(user_id)

        # Resolve institution and enforce QM toggle before building prompts
        institution = resolve_institution_for_user(current_user)
        institution_qm_enabled = bool(institution and institution.get("qm_mode_enabled"))
        qm_active = bool(institution_qm_enabled and request.use_qm_alignment)

        language_name = LANGUAGE_MAP.get(request.language, "English")
        tone_desc = AI_TONE_MAP.get(request.tone, AI_TONE_MAP[3])

        selected_cn = request.selected_course_name or request.course_name
        reference_context = get_user_reference_context(current_user['user_id'], db, course_name=selected_cn)
        course_label = selected_cn or request.course_name or "this course"
        ref_section = f"\n\nEXISTING COURSE MATERIALS for {course_label} (use ONLY these — ignore content from any other courses):\n{reference_context}" if reference_context else ""

        base_system = "You are Bonita, helping professors create course syllabi."
        system = apply_qm_prompt("syllabus", qm_active, base_system)
        prompt = f"""Create a course syllabus for: {request.course_name}

SELECTED COURSE: {course_label}
Language: {language_name} — ALL content must be in {language_name}.
Tone: {tone_desc}
Course Description: {request.description}
Learning Objectives: {request.objectives}
Grading Policy: {request.grading}{ref_section}

IMPORTANT: Generate content SPECIFICALLY for {course_label}. Use ONLY reference materials that belong to this course.

Write a complete syllabus that is specific to this course. If existing materials are provided above, reflect the actual topics, readings, and structure from them.

Include: Course Overview, Learning Objectives, Grading Breakdown (table), Course Policies (attendance, late work, academic integrity), and Weekly Schedule.

Format in HTML for Canvas:
- <h3> for major sections, <h4> for subsections
- <ul>/<li> for lists, <table> for grading
- <p> for paragraphs, <strong> for emphasis

Be specific, practical, and student-friendly. No filler."""

        # Syllabus uses Sonnet (syllabus_full route per model router)
        content, cost = bonita.call_sonnet(prompt, system, max_tokens=6000)

        asset_id = save_asset(
            user_id, 'syllabus', f"Syllabus: {request.course_name}",
            content, course_name=request.course_name,
            generation_params={"qm_mode_used": qm_active}
        )

        print(f"✅ Syllabus generated (cost: ${cost:.4f})")
        return {
            "status": "success",
            "generated_content": content,
            "asset_id": asset_id,
            "cost": cost,
            "qm_mode_used": qm_active
        }
    except HTTPException:
        raise
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
from collections import defaultdict

# Simple in-memory rate limiter: {ip: [timestamp, ...]}
_demo_rate_limit: dict = defaultdict(list)
_DEMO_RATE_LIMIT_MAX = 5    # max demo accounts per IP
_DEMO_RATE_LIMIT_WINDOW = 3600  # per hour (seconds)

def generate_demo_email():
    """Generate unique demo email"""
    random_id = ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(8))
    return f"demo-{random_id}@readysetclass.com"

def generate_demo_password() -> str:
    """Generate a unique random password for each demo account."""
    chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(chars) for _ in range(10))


# ============================================================================
# INSTITUTION ADMIN APIs (QM mode)
# ============================================================================


@app.get("/api/v2/admin/institutions")
async def get_institutions(current_user=Depends(get_current_user_from_token)):
    """List institutions with QM mode flag (admin only)."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, name, domain, qm_mode_enabled, seat_limit, stripe_customer_id, created_at
            FROM institutions
            ORDER BY name ASC
            """
        )
        rows = cursor.fetchall()
        institutions = [
            {
                "id": r[0],
                "name": r[1],
                "domain": r[2],
                "qm_mode_enabled": r[3],
                # seat_limit and stripe_customer_id are intentionally not exposed to UI yet
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
        return {"institutions": institutions}
    finally:
        cursor.close()
        conn.close()


@app.patch("/api/v2/admin/institutions/{institution_id}")
async def update_institution_qm_mode(
    institution_id: int,
    payload: Dict[str, Any],
    current_user=Depends(get_current_user_from_token),
):
    """Update qm_mode_enabled for an institution (admin only)."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if "qm_mode_enabled" not in payload:
        raise HTTPException(status_code=400, detail="qm_mode_enabled is required")

    qm_mode_enabled = bool(payload["qm_mode_enabled"])

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE institutions
            SET qm_mode_enabled = %s
            WHERE id = %s
            RETURNING id, name, qm_mode_enabled
            """,
            (qm_mode_enabled, institution_id),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Institution not found")

        conn.commit()
        return {
            "id": row[0],
            "name": row[1],
            "qm_mode_enabled": row[2],
        }
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v2/canvas/qm-status")
async def get_qm_status(current_user=Depends(get_current_user_from_token)):
    """
    Return whether QM mode is available for the current user's institution.

    Response:
        { "qm_mode_available": bool }
    """
    institution = resolve_institution_for_user(current_user)
    qm_mode_available = bool(institution and institution.get("qm_mode_enabled"))
    return {"qm_mode_available": qm_mode_available}

@app.post("/api/demo/create")
async def create_demo_account(request: Request, db: Session = Depends(get_db)):
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
    # IP-based rate limiting
    import time
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    now = time.time()
    # Prune old timestamps
    _demo_rate_limit[client_ip] = [t for t in _demo_rate_limit[client_ip] if now - t < _DEMO_RATE_LIMIT_WINDOW]
    if len(_demo_rate_limit[client_ip]) >= _DEMO_RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many demo accounts created from this IP. Please try again later.")
    _demo_rate_limit[client_ip].append(now)

    try:
        # Generate unique email and password per demo account
        email = generate_demo_email()
        password = generate_demo_password()

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


@app.patch("/api/admin/users/{user_id}/extend-demo")
async def extend_demo_account(
    user_id: int,
    request: dict,
    current_user=Depends(get_current_user_from_token)
):
    """Extend a demo account's expiration (admin only)"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    hours = request.get('hours', 24)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Always reset to exactly N hours from NOW (not stacked on top of current expiry)
        cursor.execute("""
            UPDATE users
            SET demo_expires_at = NOW() + INTERVAL '%s hours',
                is_active = TRUE
            WHERE id = %s AND is_demo = TRUE
            RETURNING email, demo_expires_at
        """, (hours, user_id))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Demo user not found")

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "message": f"Demo extended by {hours} hours",
            "email": result[0],
            "new_expiry": result[1].isoformat()
        }

    except HTTPException:
        raise
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


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
# NEW RSC FEATURE ENDPOINTS
# ============================================================================

# --- Onboarding ---

@app.post("/api/v2/onboarding/complete")
async def complete_onboarding(current_user=Depends(get_current_user_from_token)):
    """Mark onboarding as complete for the current user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET onboarding_completed = TRUE WHERE id = %s", (current_user['user_id'],))
        conn.commit()
        return {"status": "ok"}
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v2/onboarding/status")
async def get_onboarding_status(current_user=Depends(get_current_user_from_token)):
    """Return onboarding completion status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT onboarding_completed FROM users WHERE id = %s", (current_user['user_id'],))
        row = cursor.fetchone()
        completed = bool(row[0]) if row else False
        return {"onboarding_completed": completed}
    finally:
        cursor.close()
        conn.close()


# --- Generation Status / Limits ---

@app.get("/api/v2/generation/status")
async def get_generation_status(current_user=Depends(get_current_user_from_token)):
    """Return current generation usage for the dashboard counter widget."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT subscription_tier, generations_used_this_cycle,
                   monthly_generation_limit, total_demo_generations,
                   is_demo, image_credits_balance
            FROM users WHERE id = %s
        """, (current_user['user_id'],))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        tier, used, limit, demo_gens, is_demo, img_credits = row
        tier = tier or "demo"
        tier_info = get_tier_limits(tier)
        monthly_limit = tier_info["monthly_gens"]
        if tier in ("demo", "trial") or is_demo:
            return {
                "tier": tier, "used": demo_gens or 0, "limit": 5,
                "image_credits": 0, "is_demo": True
            }
        return {
            "tier": tier,
            "used": used or 0,
            "limit": monthly_limit,
            "image_credits": img_credits or 0,
            "is_demo": False,
            "percent": round((used or 0) / monthly_limit * 100) if monthly_limit else 0
        }
    finally:
        cursor.close()
        conn.close()


# --- Content Asset Bank (D2) ---

class AssetSearchParams(BaseModel):
    query: Optional[str] = None
    asset_type: Optional[str] = None
    course_id: Optional[int] = None
    is_published: Optional[bool] = None
    limit: int = 50
    offset: int = 0


@app.get("/api/v2/assets")
async def list_assets(
    query: Optional[str] = None,
    asset_type: Optional[str] = None,
    course_id: Optional[int] = None,
    is_published: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user_from_token)
):
    """List assets for the current user with optional filtering and search."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        import json as _json
        conditions = ["user_id = %s"]
        params = [current_user['user_id']]

        if asset_type:
            conditions.append("asset_type = %s")
            params.append(asset_type)
        if course_id:
            conditions.append("course_id = %s")
            params.append(course_id)
        if is_published is not None:
            conditions.append("is_published = %s")
            params.append(is_published)
        if query:
            conditions.append("to_tsvector('english', title || ' ' || content) @@ plainto_tsquery('english', %s)")
            params.append(query)

        where = " AND ".join(conditions)
        params += [limit, offset]

        cursor.execute(f"""
            SELECT id, asset_type, title, course_name, week_number, semester_tag,
                   is_published, reuse_count, created_at, updated_at
            FROM assets
            WHERE {where}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """, params)

        assets = []
        for row in cursor.fetchall():
            assets.append({
                "id": row[0], "asset_type": row[1], "title": row[2],
                "course_name": row[3], "week_number": row[4], "semester_tag": row[5],
                "is_published": row[6], "reuse_count": row[7],
                "created_at": row[8].isoformat() if row[8] else None,
                "updated_at": row[9].isoformat() if row[9] else None
            })

        return {"assets": assets, "total": len(assets)}
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v2/assets/{asset_id}")
async def get_asset(asset_id: int, current_user=Depends(get_current_user_from_token)):
    """Get full asset content."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, asset_type, title, content, course_id, course_name,
                   week_number, semester_tag, is_published, reuse_count,
                   bonita_opt_in, created_at
            FROM assets WHERE id = %s AND user_id = %s
        """, (asset_id, current_user['user_id']))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Asset not found")
        return {
            "id": row[0], "asset_type": row[1], "title": row[2], "content": row[3],
            "course_id": row[4], "course_name": row[5], "week_number": row[6],
            "semester_tag": row[7], "is_published": row[8], "reuse_count": row[9],
            "bonita_opt_in": row[10],
            "created_at": row[11].isoformat() if row[11] else None
        }
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/v2/assets/{asset_id}")
async def delete_asset(asset_id: int, current_user=Depends(get_current_user_from_token)):
    """Delete an asset (soft — removes from user's library)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM assets WHERE id = %s AND user_id = %s RETURNING id", (asset_id, current_user['user_id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Asset not found")
        conn.commit()
        return {"status": "deleted"}
    finally:
        cursor.close()
        conn.close()


# --- Time Saved Counter (D1) ---

@app.get("/api/v2/time-savings")
async def get_time_savings(current_user=Depends(get_current_user_from_token)):
    """Return total and semester-to-date time saved."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT hourly_rate_preference FROM users WHERE id = %s", (current_user['user_id'],))
        rate_row = cursor.fetchone()
        hourly_rate = float(rate_row[0]) if rate_row and rate_row[0] else 50.0

        cursor.execute("""
            SELECT SUM(minutes_saved) FROM time_savings WHERE user_id = %s
        """, (current_user['user_id'],))
        total_minutes = cursor.fetchone()[0] or 0

        # Current semester = last 6 months approximation
        cursor.execute("""
            SELECT SUM(minutes_saved) FROM time_savings
            WHERE user_id = %s AND created_at >= NOW() - INTERVAL '6 months'
        """, (current_user['user_id'],))
        semester_minutes = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT asset_type, SUM(minutes_saved) FROM time_savings
            WHERE user_id = %s GROUP BY asset_type
        """, (current_user['user_id'],))
        by_type = {row[0]: int(row[1]) for row in cursor.fetchall()}

        total_hours = round(total_minutes / 60, 1)
        semester_hours = round(semester_minutes / 60, 1)
        dollar_value = round(total_hours * hourly_rate, 0)

        return {
            "total_minutes": int(total_minutes),
            "total_hours": total_hours,
            "semester_hours": semester_hours,
            "dollar_value": dollar_value,
            "hourly_rate": hourly_rate,
            "by_type": by_type
        }
    finally:
        cursor.close()
        conn.close()


# --- Enhance Mode (C3) ---

class EnhanceSuggestionRequest(BaseModel):
    generated_content: str
    asset_type: str
    reference_context: Optional[str] = None


@app.post("/api/v2/enhance-suggestion")
async def get_enhance_suggestion(
    request: EnhanceSuggestionRequest,
    current_user=Depends(get_current_user_from_token)
):
    """
    Secondary Haiku 4.5 call — returns 1-2 specific enhancement suggestions
    for already-generated content. Returns null if no strong suggestions.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT enhance_mode_enabled FROM users WHERE id = %s", (current_user['user_id'],))
        row = cursor.fetchone()
        if row and row[0] is False:
            return {"suggestion": None, "reason": "enhance_mode_disabled"}
    finally:
        cursor.close()
        conn.close()

    bonita = BonitaEngine()
    ref_section = f"\n\nReference materials from this instructor:\n{request.reference_context[:2000]}" if request.reference_context else ""

    system = "You are Bonita, reviewing AI-generated course content for potential improvements."
    prompt = f"""Review this {request.asset_type} and suggest 1-2 specific improvements.{ref_section}

GENERATED CONTENT:
{request.generated_content[:3000]}

Instructions:
- Only suggest meaningful improvements that would genuinely strengthen the content
- Be specific and brief — name exactly what to add or change
- If no strong suggestions exist, return exactly: null
- If you have suggestions, format as 1-2 bullet points
- Do not rewrite the content — just suggest what could be added or improved

Return ONLY the bullet points or the word null. No preamble."""

    suggestion_text, cost = bonita.call_haiku(prompt, system, max_tokens=400)
    suggestion_text = suggestion_text.strip()

    if suggestion_text.lower() in ("null", "none", ""):
        return {"suggestion": None}

    log_model_usage(current_user['user_id'], "enhance_mode_suggestion",
                    "claude-haiku-4-5-20251001", "anthropic", cost_usd=cost)

    return {"suggestion": suggestion_text}


# --- Bonita Opt-In (F1) ---

class BonitaOptInRequest(BaseModel):
    opt_in: bool  # True = consent, False = withdraw


@app.post("/api/v2/bonita/consent")
async def set_bonita_consent(request: BonitaOptInRequest, current_user=Depends(get_current_user_from_token)):
    """Grant or revoke Bonita data pipeline consent."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']
        if request.opt_in:
            cursor.execute("""
                UPDATE users SET bonita_consent_granted_at = NOW(), bonita_consent_revoked_at = NULL
                WHERE id = %s
            """, (user_id,))
        else:
            cursor.execute("""
                UPDATE users SET bonita_consent_revoked_at = NOW()
                WHERE id = %s
            """, (user_id,))
            # Set all assets bonita_opt_in = false
            cursor.execute("UPDATE assets SET bonita_opt_in = FALSE WHERE user_id = %s", (user_id,))
            # Queue deletion requests for previously exported assets
            cursor.execute("""
                INSERT INTO deletion_requests (asset_id)
                SELECT bpe.asset_id FROM bonita_pipeline_exports bpe
                JOIN assets a ON bpe.asset_id = a.id
                WHERE a.user_id = %s AND bpe.fulfilled_at IS NULL
                ON CONFLICT DO NOTHING
            """, (user_id,))
        conn.commit()
        return {"status": "ok", "opted_in": request.opt_in}
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v2/bonita/consent")
async def get_bonita_consent(current_user=Depends(get_current_user_from_token)):
    """Get current Bonita consent status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT bonita_consent_granted_at, bonita_consent_revoked_at,
                   COUNT(a.id) FILTER (WHERE a.bonita_opt_in = TRUE)
            FROM users u
            LEFT JOIN assets a ON a.user_id = u.id
            WHERE u.id = %s
            GROUP BY u.bonita_consent_granted_at, u.bonita_consent_revoked_at
        """, (current_user['user_id'],))
        row = cursor.fetchone()
        if not row:
            return {"opted_in": False, "granted_at": None, "asset_count": 0}
        granted, revoked, count = row
        opted_in = granted is not None and (revoked is None or granted > revoked)
        return {
            "opted_in": opted_in,
            "granted_at": granted.isoformat() if granted else None,
            "asset_count": count or 0
        }
    finally:
        cursor.close()
        conn.close()


# --- Course Activations (B1 Dual Limiter) ---

class CourseActivationRequest(BaseModel):
    course_id: int
    course_name: Optional[str] = None


@app.post("/api/v2/courses/activate")
async def activate_course(request: CourseActivationRequest, current_user=Depends(get_current_user_from_token)):
    """Activate a course slot for the current user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']

        # Get tier limits
        cursor.execute("SELECT subscription_tier, active_course_slots FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        tier = (row[0] if row else "demo") or "demo"
        tier_info = get_tier_limits(tier)
        slot_limit = tier_info["slots"]

        # Count currently active slots
        cursor.execute("""
            SELECT COUNT(*) FROM course_activations
            WHERE user_id = %s AND deactivated_at IS NULL
        """, (user_id,))
        active_count = cursor.fetchone()[0]

        if active_count >= slot_limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "SLOT_LIMIT_REACHED",
                    "message": f"Your {tier} plan allows {slot_limit} active course{'s' if slot_limit != 1 else ''}. Deactivate a course or upgrade to add more.",
                    "active_count": active_count,
                    "limit": slot_limit
                }
            )

        # Check if already active
        cursor.execute("""
            SELECT id FROM course_activations
            WHERE user_id = %s AND course_id = %s AND deactivated_at IS NULL
        """, (user_id, request.course_id))
        if cursor.fetchone():
            return {"status": "already_active"}

        cursor.execute("""
            INSERT INTO course_activations (user_id, course_id, course_name)
            VALUES (%s, %s, %s) RETURNING id
        """, (user_id, request.course_id, request.course_name))
        conn.commit()
        return {"status": "activated", "course_id": request.course_id}
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v2/courses/deactivate")
async def deactivate_course(request: CourseActivationRequest, current_user=Depends(get_current_user_from_token)):
    """Deactivate a course slot (swap out)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE course_activations SET deactivated_at = NOW()
            WHERE user_id = %s AND course_id = %s AND deactivated_at IS NULL
        """, (current_user['user_id'], request.course_id))
        conn.commit()
        return {"status": "deactivated"}
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v2/courses/active")
async def get_active_courses(current_user=Depends(get_current_user_from_token)):
    """Return list of currently active course IDs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT course_id, course_name, activated_at FROM course_activations
            WHERE user_id = %s AND deactivated_at IS NULL
            ORDER BY activated_at DESC
        """, (current_user['user_id'],))
        active = [{"course_id": r[0], "course_name": r[1], "activated_at": r[2].isoformat() if r[2] else None}
                  for r in cursor.fetchall()]

        cursor.execute("SELECT subscription_tier FROM users WHERE id = %s", (current_user['user_id'],))
        row = cursor.fetchone()
        tier = (row[0] if row else "demo") or "demo"
        tier_info = get_tier_limits(tier)

        return {"active_courses": active, "slot_limit": tier_info["slots"], "slots_used": len(active)}
    finally:
        cursor.close()
        conn.close()


# --- Referral Invite (E3 UI support) ---

@app.get("/api/v2/referral/invite-info")
async def get_referral_invite_info(current_user=Depends(get_current_user_from_token)):
    """Get referral link + pre-written invite copy for the "Invite a Colleague" modal."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = current_user['user_id']
        cursor.execute("SELECT referral_code, full_name FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        code = row[0] if row else None
        name = row[1] if row else "A colleague"

        # Generate code if none exists
        if not code:
            import re
            nm = (row[1] or "RSC").upper()
            letters = re.sub(r'[^A-Z]', '', nm)[:3].ljust(3, 'X')
            import random
            code = letters + ''.join(random.choices('ABCDEFGHJKMNPQRSTUVWXYZ23456789', k=6))
            cursor.execute("UPDATE users SET referral_code = %s WHERE id = %s", (code, user_id))
            conn.commit()

        link = f"https://readysetclass.app/join?ref={code}"
        subject = "Save hours every semester — try ReadySetClass"
        body = f"""{name} thought you'd find this useful.

ReadySetClass is an AI assistant built specifically for faculty — not generic AI. It connects to your Canvas account and generates assignments, quizzes, discussions, syllabi, and pages in minutes. Built for professors, at HBCUs and MSIs in particular.

Your free trial link (no credit card): {link}

Not hype. Just help."""

        return {
            "code": code,
            "link": link,
            "invite_subject": subject,
            "invite_body": body
        }
    finally:
        cursor.close()
        conn.close()


# --- Admin: Model Usage Dashboard ---

@app.get("/api/admin/model-usage")
async def get_model_usage_stats(
    days: int = 7,
    current_user=Depends(get_current_user_from_token)
):
    """Admin view of model usage costs by model and task type."""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT model_used, provider, task_type,
                   COUNT(*) as calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cost_usd) as total_cost
            FROM model_usage_log
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY model_used, provider, task_type
            ORDER BY total_cost DESC
        """, (days,))
        rows = cursor.fetchall()
        return {
            "period_days": days,
            "rows": [
                {"model": r[0], "provider": r[1], "task_type": r[2],
                 "calls": r[3], "input_tokens": r[4], "output_tokens": r[5],
                 "total_cost_usd": float(r[6] or 0)}
                for r in rows
            ]
        }
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

