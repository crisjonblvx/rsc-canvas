# Student API Deployment Guide

## What Just Shipped

Phase 1 student API endpoints are live on branch `crisjonblvx/RSC-student`.

### New Endpoints
- `POST /api/auth/register` - Student registration
- `POST /api/v1/student/canvas/connect` - Connect Canvas account
- `GET /api/v1/student/courses` - Get enrolled courses
- `GET /api/v1/student/courses/{course_id}/assignments` - Get course assignments
- `POST /api/v1/student/assignments/sync` - Sync all assignments from Canvas

### What's Included
- ✅ Student router (`backend/routers/student.py`) - 373 lines
- ✅ Student Canvas API methods (`canvas_client.py`)
- ✅ Migration 003 for `student_assignments` table
- ✅ CORS config updated for `student.readysetclass.app`
- ✅ Student role added to auth system

---

## Deployment Steps

### 1. Run Database Migration on Railway

**Option A: Via Railway Dashboard**
1. Go to Railway dashboard → Your project → Database
2. Click "Query" tab
3. Copy/paste the contents of `backend/migrations/003_add_student_tables.sql`
4. Execute

**Option B: Via Railway CLI (if linked)**
```bash
railway run python backend/migrate.py --migration 003
```

**Option C: SSH into Railway container**
```bash
# After deployment, exec into the container
railway run bash
cd backend
python migrate.py --migration 003
```

### 2. Verify Migration Success

Check that the migration ran:
```sql
-- In Railway DB query tab
SELECT * FROM student_assignments LIMIT 1;  -- Should exist
SELECT role FROM users WHERE role = 'student' LIMIT 1;  -- Should not error
```

### 3. Deploy to Railway

The code is already pushed to `crisjonblvx/RSC-student`. Railway should auto-deploy when you merge to `main` or if you've configured it to deploy this branch.

Check deployment status:
- Railway Dashboard → Deployments
- Look for commit `e8f106b`
- Wait for "Success" status

### 4. Test the Endpoints

#### Test 1: Health Check
```bash
curl https://facultyflow-production.up.railway.app/api/health
# Should return: {"status": "healthy", "bonita": "online"}
```

#### Test 2: Student Registration
```bash
curl -X POST https://facultyflow-production.up.railway.app/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test-student@vuu.edu",
    "password": "test123",
    "full_name": "Test Student",
    "institution": "Virginia Union University"
  }'
# Should return: {"token": "...", "user": {...}}
```

Save the token from the response — you'll need it for the next tests.

#### Test 3: Connect Canvas (with token from above)
```bash
curl -X POST https://facultyflow-production.up.railway.app/api/v1/student/canvas/connect \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -d '{
    "canvas_url": "https://vuu.instructure.com",
    "access_token": "YOUR_CANVAS_TOKEN"
  }'
# Should return: {"status": "connected", "canvas_url": "...", "user_name": "..."}
```

#### Test 4: Get Student Courses
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/courses \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
# Should return: {"courses": [...], "total": N}
```

#### Test 5: Get Assignments for a Course
```bash
curl https://facultyflow-production.up.railway.app/api/v1/student/courses/COURSE_ID/assignments \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
# Should return: {"assignments": [...], "total": N}
```

---

## Frontend Integration Test

Once the backend is deployed and tested, test with the live frontend:

1. Open `student.readysetclass.app` in browser
2. Open DevTools → Network tab
3. Try to register/login
4. Check for 404s — should be gone now
5. Verify API calls succeed (200 responses)

---

## Rollback Plan

If something breaks:

### Quick Rollback (Railway)
1. Railway Dashboard → Deployments
2. Find the previous working deployment
3. Click "Redeploy" on that commit

### Database Rollback (if migration causes issues)
```sql
-- Drop the student assignments table
DROP TABLE IF EXISTS student_assignments;

-- Revert role constraint
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('admin', 'demo', 'customer'));
```

---

## Troubleshooting

### 404 on `/api/auth/register`
- Check: Is the student router mounted in `main.py`?
- Check: Did Railway deploy the new code?
- Check: Is the import path correct? (`from routers.student import router`)

### 400 "Canvas not connected"
- Student needs to call `/api/v1/student/canvas/connect` first
- Canvas credentials must be valid (test with Canvas API directly)

### 401 "Invalid or expired session"
- Token from `/api/auth/register` expires after 24 hours
- Register a new student or login again

### Migration fails on Railway
- Check if migration 001 and 002 already ran
- Migrations are idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`)
- Safe to re-run if they fail

---

## Next Steps After Deployment

### Phase 2 Features (Deadline Dashboard + Grade Calculator)
1. Add `/api/v1/student/assignments/upcoming` endpoint
2. Add `/api/v1/student/dashboard/deadlines` endpoint
3. Add Canvas gradebook integration for grade calculator
4. Build frontend components

### Phase 3 Features (AI Tools)
1. The Lab (AI assignment reviewer) - highest priority
2. Calendar Sync (Google Calendar OAuth)
3. Grade Predictor (AI trend analysis)
4. Email Assistant (professor communication)

---

## Files Changed in This Deployment

```
backend/migrations/003_add_student_tables.sql    NEW
backend/routers/__init__.py                      NEW
backend/routers/student.py                       NEW  (373 lines)
backend/canvas_client.py                         MODIFIED (+70 lines)
backend/main.py                                  MODIFIED (+8 lines)
backend/migrate.py                               NEW
backend/run_all_migrations.py                    NEW
```

Total: +488 lines added

---

## Support

If deployment fails or endpoints don't work:
1. Check Railway logs (Dashboard → Deployments → Logs)
2. Check Railway database connectivity
3. Verify environment variables are set (JWT_SECRET, DATABASE_URL, etc.)
4. Test endpoints with curl (examples above)

Built by Phife (Claude Opus 4.6) for CJ 🎯
