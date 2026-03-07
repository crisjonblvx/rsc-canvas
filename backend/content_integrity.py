"""
content_integrity.py — G1 Content Integrity + Accountability System

Three functions used across all content flows:
  check_input_quality(text, question_context) — Zone 1, interview guardrail
  check_content_safety(text, content_type)    — Zones 2+3, generation + upload
  log_content_approval(...)                   — Writes to content_approvals table

Keyword pre-filter: runs first, skips Claude if content is clean.
This keeps safety screening cost near-zero for normal usage.
"""

from __future__ import annotations
import os
import re
import json
import hashlib
from typing import Optional

# ── Hard-block keywords ─────────────────────────────────────────────────────────
# If ANY of these appear (case-insensitive), skip to Claude for full evaluation.
# Keep this list minimal — false positives hurt legitimate academic content.

HARD_BLOCK_KEYWORDS = [
    # Slurs (abbreviated patterns — full list maintained in env or DB in production)
    r'\bn[*i]gg', r'\bf[*a]gg', r'\bc[*u]nt', r'\bk[*i]ke', r'\bsp[*i]c',
    r'\btr[*a]nny', r'\bret[*a]rd',
    # Sexual content
    r'\bporn', r'\bsex(?:ual)?\s+(?:act|content|material)', r'\bnude\s+photo',
    r'\bsexually\s+explicit',
    # Targeted harassment
    r'\bi\s+will\s+(?:kill|hurt|harm|assault)',
    r'\bthreat(?:en)?(?:ing)?\s+(?:student|faculty|staff)',
    # Personally identifying (student SSNs, DOBs, full addresses)
    r'\b\d{3}-\d{2}-\d{4}\b',  # SSN pattern
]

# ── Soft-flag patterns ──────────────────────────────────────────────────────────
SOFT_FLAG_PATTERNS = {
    "placeholder_text": [
        r'\[INSERT\b', r'\bTODO\b', r'\bTBD\b', r'\bPLACEHOLDER\b',
        r'\bDRAFT\b', r'\bEXAMPLE ONLY\b', r'\bDO NOT USE\b',
        r'\[YOUR NAME\]', r'\[DATE\]', r'\[COURSE NAME\]',
    ],
    "personal_contact": [
        r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',  # phone numbers
    ],
}


def _keyword_hard_block_check(text: str) -> bool:
    """Returns True if any hard-block keyword is found."""
    lower = text.lower()
    for pattern in HARD_BLOCK_KEYWORDS:
        if re.search(pattern, lower):
            return True
    return False


def _keyword_soft_flag_check(text: str) -> Optional[str]:
    """Returns the first soft-flag reason found, or None."""
    for reason, patterns in SOFT_FLAG_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return reason
    return None


# ── Zone 1: Input Quality Check ────────────────────────────────────────────────

async def check_input_quality(
    text: str,
    question_context: str,
    groq_client=None,
) -> dict:
    """
    Evaluate the quality of a faculty message during the Bonita interview.
    Uses Groq (fast, cheap) for the actual AI check.

    Returns:
        {
          "quality": "good" | "low" | "garbage",
          "issue": "too_short" | "nonsense" | "hostile" | "joke_input" | "off_topic" | None,
          "recoverable": True | False
        }
    """
    if not text or len(text.strip()) < 2:
        return {"quality": "garbage", "issue": "too_short", "recoverable": False}

    stripped = text.strip()

    # Rule-based pre-checks (no AI cost)
    if len(stripped) < 5:
        return {"quality": "garbage", "issue": "too_short", "recoverable": True}

    # Check for pure noise (repeated characters, keyboard mashing)
    if len(set(stripped.lower().replace(' ', ''))) < 3 and len(stripped) > 4:
        return {"quality": "garbage", "issue": "nonsense", "recoverable": True}

    # Check for hostile patterns
    hostile_patterns = [
        r'\bstupid\b', r'\bwaste of time\b', r'\bdoesn.t work\b',
        r'\bthis is dumb\b', r'\bi\s+(?:hate|don.t\s+care)',
        r'\bscrew\s+(?:this|you|it)\b',
    ]
    for pat in hostile_patterns:
        if re.search(pat, stripped, re.IGNORECASE):
            return {"quality": "low", "issue": "hostile", "recoverable": True}

    # If Groq is available and text is borderline, do a fast AI check
    if groq_client and len(stripped) < 30:
        try:
            prompt = (
                f"A faculty member was asked: '{question_context}'\n"
                f"Their response was: '{stripped}'\n\n"
                "Rate the quality of this response as input for a faculty AI profile.\n"
                "Reply ONLY with one of: good | low | garbage\n"
                "good = genuine answer that provides useful information\n"
                "low = vague or very short but possibly real\n"
                "garbage = nonsense, joke, hostile, or clearly not a real answer"
            )
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a content quality classifier. Reply with only the label: good, low, or garbage."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=10,
                temperature=0.1,
            )
            label = response.choices[0].message.content.strip().lower()
            if label in ("good", "low", "garbage"):
                issue = None
                if label == "low":
                    issue = "too_short"
                elif label == "garbage":
                    issue = "joke_input"
                return {
                    "quality": label,
                    "issue": issue,
                    "recoverable": label != "garbage",
                }
        except Exception:
            pass  # Groq unavailable — fall through to default

    return {"quality": "good", "issue": None, "recoverable": True}


# ── Zones 2+3: Content Safety Check ───────────────────────────────────────────

async def check_content_safety(
    text: str,
    content_type: str = "content",
    anthropic_client=None,
    word_count_min: Optional[int] = None,
    word_count_max: Optional[int] = None,
) -> dict:
    """
    Screen content before it reaches students (AI-generated or manual input).

    Returns:
        {
          "passed": True | False,
          "level": "clean" | "soft_flag" | "hard_block",
          "reason": str | None,
          "display_message": str | None   # shown to faculty on soft flags
        }
    """
    if not text or not text.strip():
        return {"passed": True, "level": "clean", "reason": None, "display_message": None}

    # ── Step 1: Hard-block keyword check (free) ──
    if _keyword_hard_block_check(text):
        # Send to Claude for confirmation before hard-blocking (reduce false positives)
        if anthropic_client:
            try:
                hard_confirm = await _claude_safety_check(text, content_type, anthropic_client)
                if hard_confirm.get("level") == "hard_block":
                    return hard_confirm
                # Claude says it's OK — keyword was likely context (e.g., academic discussion)
                return {"passed": True, "level": "clean", "reason": None, "display_message": None}
            except Exception:
                pass
        return {
            "passed": False,
            "level": "hard_block",
            "reason": "policy_violation",
            "display_message": None,
        }

    # ── Step 2: Soft-flag pattern check (free) ──
    soft_reason = _keyword_soft_flag_check(text)
    if soft_reason:
        msg = _soft_flag_message(soft_reason, text)
        return {
            "passed": False,
            "level": "soft_flag",
            "reason": soft_reason,
            "display_message": msg,
        }

    # ── Step 3: Word count checks ──
    word_count = len(text.split())
    if word_count_min and word_count < word_count_min:
        return {
            "passed": False,
            "level": "soft_flag",
            "reason": "too_short",
            "display_message": f"This content is quite short ({word_count} words). Is that intentional?",
        }
    if word_count_max and word_count > word_count_max:
        return {
            "passed": False,
            "level": "soft_flag",
            "reason": "too_long",
            "display_message": f"This content is unusually long ({word_count:,} words) for a {content_type}. Worth a quick review.",
        }

    # All checks passed — no Claude call needed for normal content
    return {"passed": True, "level": "clean", "reason": None, "display_message": None}


async def _claude_safety_check(text: str, content_type: str, anthropic_client) -> dict:
    """Full Claude safety evaluation — only called when keywords flag suspicious content."""
    system = (
        "You are a content safety classifier for an academic platform. "
        "Evaluate the following content and respond with JSON only, no explanation.\n\n"
        "Hard block: slurs, explicit sexual content, targeted harassment, direct threats, student PII\n"
        "Soft flag: profanity, politically charged language, placeholder text, suspicious patterns\n"
        "Clean: normal academic content (including discussions of difficult topics in scholarly context)"
    )
    prompt = (
        f"Content type: {content_type}\n\n"
        f"Content:\n{text[:3000]}\n\n"
        'Respond ONLY with: {"level": "clean"|"soft_flag"|"hard_block", "reason": "..." or null}'
    )
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        temperature=0.0,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    result = json.loads(raw)
    level = result.get("level", "clean")
    reason = result.get("reason")
    if level == "hard_block":
        return {
            "passed": False, "level": "hard_block",
            "reason": reason or "policy_violation", "display_message": None,
        }
    if level == "soft_flag":
        return {
            "passed": False, "level": "soft_flag",
            "reason": reason or "flagged_content",
            "display_message": f"Bonita noticed something worth a second look: {reason or 'potential content issue'}.",
        }
    return {"passed": True, "level": "clean", "reason": None, "display_message": None}


def _soft_flag_message(reason: str, text: str) -> str:
    """Generate a faculty-facing message for a soft flag."""
    if reason == "placeholder_text":
        # Find the first placeholder token for the message
        import re as _re
        m = _re.search(r'\[[\w\s]+\]|TODO|TBD|PLACEHOLDER|DRAFT|EXAMPLE ONLY|DO NOT USE', text, _re.IGNORECASE)
        found = f" — '{m.group(0)}' is still in the text" if m else ""
        return f"This looks like it might still have a placeholder{found}. Want to fix that first?"
    if reason == "personal_contact":
        return "This content appears to contain a phone number or personal contact information. Confirm it's intended for students."
    return "Bonita noticed something worth a quick review before this goes to students."


# ── Content Approval Log ───────────────────────────────────────────────────────

def log_content_approval(
    conn,
    user_id: int,
    content_type: str,
    generation_method: str,
    content: str,
    faculty_approved: bool,
    course_id: Optional[str] = None,
    bonita_generated: bool = False,
    safety_checked: bool = False,
    safety_passed: bool = True,
    safety_flags: Optional[dict] = None,
) -> int:
    """
    Write an approval record to content_approvals.
    Returns the new record ID.
    Always runs — whether AI or manual, approved or not.
    """
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    snapshot = content[:500] if content else None
    approved_at = "NOW()" if faculty_approved else "NULL"

    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO content_approvals
                (user_id, course_id, content_type, generation_method, content_hash,
                 bonita_generated, faculty_reviewed, faculty_approved, approved_at,
                 content_snapshot, safety_checked, safety_passed, safety_flags)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            user_id, course_id, content_type, generation_method, content_hash,
            bonita_generated, faculty_approved, faculty_approved,
            None if not faculty_approved else "NOW()",
            snapshot,
            safety_checked, safety_passed,
            json.dumps(safety_flags) if safety_flags else None,
        ))
        record_id = cursor.fetchone()[0]
        conn.commit()
        return record_id
    finally:
        cursor.close()


# ── Strike Tracking ────────────────────────────────────────────────────────────

def record_quality_strike(
    conn,
    user_id: int,
    issue_type: str,
) -> dict:
    """
    Record a bad-faith input strike for this user today.
    Returns current strike counts. Auto-flags if garbage_count >= 3.
    """
    is_hostile = issue_type == "hostile"
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO onboarding_quality_flags (user_id, session_date, garbage_count, hostile_count)
            VALUES (%s, CURRENT_DATE, %s, %s)
            ON CONFLICT (user_id, session_date) DO UPDATE SET
                garbage_count = onboarding_quality_flags.garbage_count + %s,
                hostile_count = onboarding_quality_flags.hostile_count + %s,
                flagged_for_review = CASE
                    WHEN (onboarding_quality_flags.garbage_count + %s) >= 3 THEN TRUE
                    ELSE onboarding_quality_flags.flagged_for_review
                END
            RETURNING garbage_count, hostile_count, flagged_for_review
        """, (
            user_id, 0 if is_hostile else 1, 1 if is_hostile else 0,
            0 if is_hostile else 1, 1 if is_hostile else 0,
            0 if is_hostile else 1,
        ))
        row = cursor.fetchone()
        conn.commit()
        return {
            "garbage_count": row[0],
            "hostile_count": row[1],
            "flagged_for_review": row[2],
        }
    finally:
        cursor.close()


# ── Bonita Recovery Responses ──────────────────────────────────────────────────
# Used when quality check returns "low" or "garbage".
# Warm, direct — not scolding.

_BONITA_RECOVERY_RESPONSES = {
    "too_short": (
        "I want to make sure I get this right — can you tell me a little more? "
        "Even a sentence helps me understand how to build content that works for you."
    ),
    "nonsense": (
        "I want to make sure I get this right — can you tell me a little more? "
        "Even a sentence helps me understand how to build content that works for you."
    ),
    "joke_input": (
        "Ha — okay. When you're ready to give me something I can actually work with, "
        "I'm here. This part matters for how well I can support you all semester."
    ),
    "hostile": (
        "Noted. If you'd rather skip this and use Bonita with general defaults, that's "
        "an option — just know the content won't be personalized to you or your students. "
        "Your call."
    ),
    "off_topic": (
        "I think I might have asked this in a confusing way — let me try again: "
        "can you share a bit about what specifically matters to you here?"
    ),
    "low": (
        "I want to make sure I get this right — can you tell me a little more? "
        "Even a sentence helps me understand how to build content that works for you."
    ),
    "garbage": (
        "Ha — okay. When you're ready to give me something I can actually work with, "
        "I'm here. This part matters for how well I can support you all semester."
    ),
}


def mark_hostile_skip(conn, user_id: int) -> None:
    """Mark that the user chose the hostile skip path (sets onboarding_phase = 'skipped_hostile')."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO onboarding_quality_flags (user_id, session_date, skipped_hostile, flagged_for_review)
            VALUES (%s, CURRENT_DATE, TRUE, TRUE)
            ON CONFLICT (user_id, session_date) DO UPDATE SET
                skipped_hostile = TRUE,
                flagged_for_review = TRUE
        """, (user_id,))
        cursor.execute("""
            INSERT INTO faculty_profiles (user_id, onboarding_phase)
            VALUES (%s, 'skipped_hostile')
            ON CONFLICT (user_id) DO UPDATE SET
                onboarding_phase = 'skipped_hostile',
                last_updated = NOW()
        """, (user_id,))
        conn.commit()
    finally:
        cursor.close()
