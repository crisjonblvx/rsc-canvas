# Phase 2 Complete: Deadline Dashboard + Grade Calculator

**Branch:** `crisjonblvx/RSC-student`
**Commit:** `c40ffb9`

---

## What's New

### Deadline Dashboard (2.1)

Visual timeline of upcoming assignments with urgency-based grouping.

#### Endpoints

**GET `/api/v1/student/assignments/upcoming`**
- Query param: `days` (default: 7)
- Returns assignments due in the next N days, sorted by due date
- Response:
```json
{
  "assignments": [
    {
      "assignment_id": "123",
      "title": "Essay on Cognitive Biases",
      "course_name": "Psychology 101",
      "due_at": "2026-02-20T23:59:59",
      "points_possible": 100,
      "score": null,
      "submitted": false,
      "workflow_state": "published"
    }
  ],
  "total": 5,
  "days": 7
}
```

**GET `/api/v1/student/dashboard/deadlines`**
- Groups all assignments by urgency
- Response:
```json
{
  "this_week": [...],        // Due in next 7 days
  "next_week": [...],        // Due in 7-14 days
  "overdue": [...],          // Past due and not submitted
  "no_due_date": [...],      // No due date set
  "summary": {
    "total": 15,
    "this_week_count": 3,
    "next_week_count": 5,
    "overdue_count": 2,
    "no_due_date_count": 5
  }
}
```

**Assignment object includes:**
- `urgency` field: "this_week", "next_week", or "overdue"
- `submitted` boolean
- `score` if graded
- `course_name` for filtering

---

### Grade Calculator (2.2)

Calculate current grades and "what you need" scenarios.

#### Endpoints

**GET `/api/v1/student/courses/{course_id}/grade-calculator`**
- Shows current grade with letter grade
- Shows points earned vs possible
- Calculates "what you need" scenarios for target grades (A, B, C, D)
- Response:
```json
{
  "current_grade": {
    "score": 85.5,
    "final_score": 85.5,
    "letter": "B"
  },
  "points": {
    "earned": 342,
    "possible": 400,
    "remaining": 58
  },
  "scenarios": [
    {
      "target_grade": "A",
      "target_percentage": 90,
      "points_needed": 18,
      "percentage_needed_on_remaining": 31.03,
      "is_achievable": true
    },
    {
      "target_grade": "B",
      "target_percentage": 80,
      "points_needed": -22,
      "percentage_needed_on_remaining": -37.93,
      "is_achievable": true
    }
  ],
  "grading_scheme": {...}
}
```

**GET `/api/v1/student/courses/{course_id}/grade-breakdown`**
- Shows grade by assignment group (category)
- Handles weighted and non-weighted grading
- Response:
```json
{
  "is_weighted": true,
  "groups": [
    {
      "name": "Quizzes",
      "weight": 30,
      "earned": 85,
      "possible": 100,
      "percentage": 85.0,
      "assignments": [
        {
          "name": "Quiz 1: Classical Conditioning",
          "points_possible": 50,
          "score": 45,
          "percentage": 90.0
        }
      ]
    }
  ]
}
```

---

## Canvas Client Extensions

New methods added to `canvas_client.py`:

### `get_student_grades(course_id)`
- Fetches student enrollment with grade data
- Includes current score, final score
- Returns enrollment object

### `get_assignment_groups(course_id)`
- Fetches assignment categories with assignments
- Includes group weights (if weighted grading)
- Returns list of groups

### `get_course_grading_scheme(course_id)`
- Fetches grading standard (A/B/C cutoffs)
- Returns course info with grading scheme

---

## Frontend Integration Guide

### Deadline Dashboard UI

**Color coding for urgency:**
- 🔴 Overdue: Red (past due, not submitted)
- 🟠 This week: Orange (due in 0-7 days)
- 🟡 Next week: Yellow (due in 7-14 days)
- ⚪ No due date: Gray

**Visual timeline:**
- X-axis: Time (today → 2 weeks out)
- Y-axis: Assignments (dots/cards)
- "Today" marker with vertical line
- Tap assignment → see details

**Features:**
- Filter by course
- Sort by due date or points
- Swipe to mark as complete
- "Add to Calendar" button per assignment

### Grade Calculator UI

**Current Grade Display:**
- Big number: "85.5%"
- Letter grade: "B"
- Visual bar: earned/possible points

**"What Do I Need?" Section:**
- Slider: "I want to get a ___" (A/B/C/D)
- Shows: "You need XX% on remaining work"
- Color-coded achievability:
  - Green: < 90% needed (easy)
  - Yellow: 90-100% needed (possible)
  - Red: > 100% needed (impossible)

**Grade Breakdown:**
- List of assignment groups
- Show weight if weighted grading
- Expandable: tap to see assignments
- Percentage per group with bar chart

---

## Testing the Endpoints

### 1. Sync assignments first
```bash
curl -X POST https://facultyflow-production.up.railway.app/api/v1/student/assignments/sync \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 2. Get upcoming assignments
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/assignments/upcoming?days=14 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Get deadline dashboard
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/dashboard/deadlines \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 4. Get grade calculator
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/courses/6355/grade-calculator \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 5. Get grade breakdown
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/courses/6355/grade-breakdown \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Known Limitations

### Canvas API Permissions
- Student tokens may not have access to all grade data
- Some courses hide grades until a certain date
- Grading schemes may be course-specific

### Grade Calculator Accuracy
- Assumes equal weighting if not explicitly weighted
- Doesn't account for drop rules (e.g., "drop lowest 2 quizzes")
- Doesn't handle extra credit assignments
- Letter grade thresholds use standard scale (90/80/70/60)

### Deadline Dashboard
- Requires `/assignments/sync` to be called first
- Sync should be run daily or on-demand
- No real-time updates (relies on local DB)

---

## Next Steps

### Phase 3 - AI Features

**Priority Order (from Sunni's doc):**

1. **The Lab (#7)** - AI Assignment Reviewer
   - Upload assignment before submitting
   - AI analyzes against rubric
   - Shows checklist of criteria met/missing
   - Estimated grade with confidence
   - Suggestions for improvement

2. **Calendar Sync (#8)** - Smart Scheduling
   - Google Calendar OAuth integration
   - Find free time blocks
   - AI suggests study sessions
   - Auto-schedule based on deadlines

3. **Grade Predictor (#4)** - AI Trend Analysis
   - Predict final grade based on current trajectory
   - Confidence meter (high/medium/low)
   - Trend indicator (improving/stable/declining)
   - Cached for 24 hours

4. **Email Assistant (#10)** - Professor Communication
   - Draft professional emails to professors
   - Templates: extension request, clarification, office hours
   - Tone selector (formal/friendly/apologetic)
   - Copy to clipboard or send via Gmail

---

## Files Changed

```
backend/canvas_client.py      +60 lines (grade methods)
backend/routers/student.py    +292 lines (deadline + grade endpoints)
```

**Total:** student router now 665 lines

---

Ready for Phase 3 🚀
