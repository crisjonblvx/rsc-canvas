"""
RSC Model Router — Revised Architecture (Addendum §1 v1)
Routes generation tasks to the correct model based on task type.

Architecture:
  - Groq / Llama 3.3 70B        → routing, classification, announcements, short MCQ
  - Gemini 2.5 Flash-Lite       → date calculations, time-saved copy, starter pack matching
  - Gemini 2.5 Flash            → discussions, accessibility checks, enhance suggestions
  - Claude Sonnet 4.6           → all professor-facing outputs (assignments, syllabi, pages,
                                   mirror mode, long-form quiz, Bonita PII review)
  - Gemini 2.5 Flash Image      → image generation (credit pack required — check balance first)
  - Claude Haiku 4.5            → EMERGENCY FALLBACK ONLY if Google AI is unavailable

Fallback chain:
  Groq unavailable      → Gemini 2.5 Flash-Lite
  Google AI unavailable → Haiku 4.5
  Anthropic unavailable → queue request, show human-readable hold message
"""

import os
from typing import Optional

# ─────────────────────────────────────────────────
# Route definitions
# ─────────────────────────────────────────────────

GROQ_ROUTES = {
    'intent_classification',
    'announcement_generation',
    'short_quiz_mcq',
    'short_quiz_tf',
    'short_quiz_fitb',
    'resource_link_formatting',
    'feature_routing',
}

# Lightweight mid-layer: date calcs, copy, matching
GEMINI_FLASH_LITE_ROUTES = {
    'date_refresh_calculation',
    'time_saved_copy',
    'starter_pack_matching',
    'clone_refresh_date_only',
}

# Mid-depth: discussions, accessibility, enhance suggestions
GEMINI_FLASH_ROUTES = {
    'discussion_standard',
    'accessibility_check',
    'enhance_mode_suggestion',
    'short_form_support',
}

# Quality layer: all professor-facing outputs
SONNET_ROUTES = {
    'assignment_full',
    'syllabus_full',
    'page_full',
    'mirror_mode_generation',
    'quiz_long_form',
    'clone_refresh_light',
    'clone_refresh_full',
    'bonita_pii_review',
}

IMAGE_ROUTE = {'image_generation'}

# Legacy: tasks that previously routed to Haiku — now Gemini Flash
# Keep for backwards compatibility
HAIKU_ROUTES = GEMINI_FLASH_ROUTES  # alias — same task set, now Gemini-routed


# ─────────────────────────────────────────────────
# Per-task default max_tokens
# ─────────────────────────────────────────────────

MAX_TOKENS = {
    'announcement_generation':    800,
    'short_quiz_mcq':            2048,
    'short_quiz_tf':             1024,
    'short_quiz_fitb':           1024,
    'discussion_standard':       1500,
    'accessibility_check':       1000,
    'enhance_mode_suggestion':    600,
    'assignment_full':           4000,
    'syllabus_full':             6000,
    'page_full':                 4000,
    'mirror_mode_generation':    4000,
    'quiz_long_form':            4000,
    'bonita_pii_review':         2048,
    'resource_link_formatting':   800,
}

# Per-task temperature
TEMPERATURE = {
    'intent_classification':     0.1,
    'feature_routing':           0.1,
    'accessibility_check':       0.1,
    'enhance_mode_suggestion':   0.4,
    'short_quiz_mcq':            0.5,
    'short_quiz_tf':             0.5,
    'announcement_generation':   0.7,
    'discussion_standard':       0.7,
    'assignment_full':           0.7,
    'syllabus_full':             0.65,
    'page_full':                 0.7,
    'mirror_mode_generation':    0.65,
    'quiz_long_form':            0.6,
}


def _gemini_available() -> bool:
    """Returns True if GEMINI_API_KEY is configured."""
    return bool(os.getenv("GEMINI_API_KEY"))


def _groq_available() -> bool:
    """Returns True if GROQ_API_KEY is configured."""
    return bool(os.getenv("GROQ_API_KEY"))


def get_model_config(task_type: str, complexity_hint: Optional[str] = None,
                     groq_available: bool = True) -> dict:
    """
    Return the model config dict for a given task_type.

    Returns:
        {
            "provider": "groq" | "anthropic" | "gemini",
            "model": "<model-id>",
            "max_tokens": int,
            "temperature": float,
            "task_type": str,
        }
    """
    max_tok = MAX_TOKENS.get(task_type, 2048)
    temp = TEMPERATURE.get(task_type, 0.7)
    gemini_ok = _gemini_available()

    # ── Image generation (credit-gated) ──────────────────────────
    if task_type in IMAGE_ROUTE:
        if gemini_ok:
            return {
                "provider": "gemini",
                "model": "gemini-2.5-flash-preview-04-17",
                "max_tokens": 0,
                "temperature": 1.0,
                "task_type": task_type,
            }
        # No Gemini key → block image generation (never use Haiku for images)
        return {
            "provider": "blocked",
            "model": None,
            "max_tokens": 0,
            "temperature": 0,
            "task_type": task_type,
            "error": "GEMINI_API_KEY required for image generation",
        }

    # ── Sonnet: all professor-facing outputs ─────────────────────
    if task_type in SONNET_ROUTES:
        return {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tok,
            "temperature": temp,
            "task_type": task_type,
        }

    # ── Gemini Flash: discussions, accessibility, enhance ────────
    if task_type in GEMINI_FLASH_ROUTES:
        if gemini_ok:
            return {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "max_tokens": max_tok,
                "temperature": temp,
                "task_type": task_type,
            }
        else:
            # Fallback: Google AI unavailable → Haiku 4.5
            return {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tok,
                "temperature": temp,
                "task_type": task_type,
                "fallback": True,
                "fallback_reason": "gemini_unavailable",
            }

    # ── Gemini Flash-Lite: date calcs, copy, matching ────────────
    if task_type in GEMINI_FLASH_LITE_ROUTES:
        if gemini_ok:
            return {
                "provider": "gemini",
                "model": "gemini-2.5-flash-lite",
                "max_tokens": max_tok,
                "temperature": temp,
                "task_type": task_type,
            }
        else:
            return {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tok,
                "temperature": temp,
                "task_type": task_type,
                "fallback": True,
                "fallback_reason": "gemini_unavailable",
            }

    # ── Groq: routing, short structured output ───────────────────
    if task_type in GROQ_ROUTES:
        groq_key = _groq_available()
        if groq_available and groq_key:
            return {
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "max_tokens": max_tok,
                "temperature": temp,
                "task_type": task_type,
            }
        else:
            # Groq unavailable → Gemini Flash-Lite fallback
            if gemini_ok:
                return {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash-lite",
                    "max_tokens": max_tok,
                    "temperature": temp,
                    "task_type": task_type,
                    "fallback": True,
                    "fallback_reason": "groq_unavailable",
                }
            else:
                # Both Groq and Gemini down → Haiku (emergency)
                return {
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": max_tok,
                    "temperature": temp,
                    "task_type": task_type,
                    "fallback": True,
                    "fallback_reason": "groq_and_gemini_unavailable",
                }

    # Unknown task type → Sonnet (safest for quality)
    return {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tok,
        "temperature": temp,
        "task_type": task_type,
        "fallback_unknown": True,
    }


# ─────────────────────────────────────────────────
# Cost calculation helpers
# ─────────────────────────────────────────────────

COST_PER_MILLION = {
    # (input_cost_per_million, output_cost_per_million)
    "claude-sonnet-4-6":                   (3.00,  15.00),
    "claude-haiku-4-5-20251001":           (1.00,   5.00),
    "llama-3.3-70b-versatile":             (0.59,   0.79),
    "gemini-2.5-flash":                    (0.30,   2.50),
    "gemini-2.5-flash-lite":               (0.10,   0.40),
    "gemini-2.5-flash-preview-04-17":      (0.30,   2.50),
}

# Image generation: flat per-image cost
# Note: evaluate Imagen 4 Fast ($0.020/img) vs gemini-2.5-flash-image ($0.039/img)
IMAGE_COST_PER_CREDIT = 0.039


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD for a model call."""
    rates = COST_PER_MILLION.get(model, (3.00, 15.00))
    return (input_tokens / 1_000_000 * rates[0]) + (output_tokens / 1_000_000 * rates[1])


# ─────────────────────────────────────────────────
# Tier → generation limits
# ─────────────────────────────────────────────────

TIER_LIMITS = {
    "demo":          {"slots": 1,  "monthly_gens": 5,   "image_credits": 0},
    "trial":         {"slots": 1,  "monthly_gens": 5,   "image_credits": 0},
    "monthly":       {"slots": 2,  "monthly_gens": 25,  "image_credits": 0},
    "educator":      {"slots": 2,  "monthly_gens": 25,  "image_credits": 0},  # legacy alias
    "pro_monthly":   {"slots": 5,  "monthly_gens": 75,  "image_credits": 0},
    "pro":           {"slots": 5,  "monthly_gens": 75,  "image_credits": 0},  # legacy alias
    "annual":        {"slots": 99, "monthly_gens": 150, "image_credits": 0},
    "institutional": {"slots": 99, "monthly_gens": 100, "image_credits": 0},
    "team":          {"slots": 99, "monthly_gens": 100, "image_credits": 0},  # legacy alias
    "enterprise":    {"slots": 99, "monthly_gens": 150, "image_credits": 0},  # legacy alias
}


def get_tier_limits(tier: str) -> dict:
    """Return limits dict for a subscription tier."""
    return TIER_LIMITS.get(tier, TIER_LIMITS["demo"])
