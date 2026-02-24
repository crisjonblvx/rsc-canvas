-- ReadySetClass Feature Migrations
-- Migration 007: Assets, Dual Limiter, Model Router, Time Savings, Demo Tier, Onboarding, Credits

-- ============================================================================
-- USERS TABLE ADDITIONS
-- ============================================================================

-- Onboarding
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT FALSE;

-- Demo tier output limiter
ALTER TABLE users ADD COLUMN IF NOT EXISTS total_demo_generations INTEGER DEFAULT 0;

-- Subscription dual limiter (new spec tiers)
ALTER TABLE users ADD COLUMN IF NOT EXISTS active_course_slots INTEGER DEFAULT 1;
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_generation_limit INTEGER DEFAULT 5;
ALTER TABLE users ADD COLUMN IF NOT EXISTS generations_used_this_cycle INTEGER DEFAULT 0;

-- Image credits
ALTER TABLE users ADD COLUMN IF NOT EXISTS image_credits_balance INTEGER DEFAULT 0;

-- Referral rewards cap
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_rewards_this_semester INTEGER DEFAULT 0;

-- Time saved preference
ALTER TABLE users ADD COLUMN IF NOT EXISTS hourly_rate_preference DECIMAL(8,2) DEFAULT 50.00;

-- heyBonita.AI consent
ALTER TABLE users ADD COLUMN IF NOT EXISTS bonita_consent_granted_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS bonita_consent_revoked_at TIMESTAMP;

-- Language preference
ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(5) DEFAULT 'en';

-- Enhance Mode toggle
ALTER TABLE users ADD COLUMN IF NOT EXISTS enhance_mode_enabled BOOLEAN DEFAULT TRUE;

-- Update subscription_tier CHECK to reflect new tier names
-- (Keep old values valid too for rollback safety)
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_subscription_tier_check;
ALTER TABLE users ADD CONSTRAINT users_subscription_tier_check
    CHECK (subscription_tier IN ('demo','trial','monthly','pro_monthly','annual','institutional','educator','pro','team','enterprise'));

-- ============================================================================
-- COURSE ACTIVATIONS (B1 Dual Limiter)
-- ============================================================================

CREATE TABLE IF NOT EXISTS course_activations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL,       -- Canvas course ID
    course_name VARCHAR(255),
    activated_at TIMESTAMP DEFAULT NOW(),
    deactivated_at TIMESTAMP          -- NULL = currently active
);

CREATE INDEX IF NOT EXISTS idx_course_activations_user ON course_activations(user_id);
CREATE INDEX IF NOT EXISTS idx_course_activations_active ON course_activations(user_id, deactivated_at);

-- ============================================================================
-- ASSETS TABLE (D2 Content Asset Bank)
-- ============================================================================

CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER,                -- Canvas course ID (nullable — asset may outlive course)
    course_name VARCHAR(255),
    asset_type VARCHAR(20) NOT NULL CHECK (asset_type IN ('assignment','quiz','discussion','announcement','page','syllabus')),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    subject_tag VARCHAR(100),
    week_number INTEGER,
    semester_tag VARCHAR(50),
    generation_params JSONB,          -- Stored so Clone & Refresh can regenerate
    bonita_opt_in BOOLEAN DEFAULT FALSE,
    accessibility_score JSONB,
    is_published BOOLEAN DEFAULT FALSE,
    reuse_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Full-text search index
CREATE INDEX IF NOT EXISTS idx_assets_user ON assets(user_id);
CREATE INDEX IF NOT EXISTS idx_assets_course ON assets(course_id);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_published ON assets(is_published);
CREATE INDEX IF NOT EXISTS idx_assets_fts ON assets USING gin(to_tsvector('english', title || ' ' || content));

-- ============================================================================
-- REUSE EVENTS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS reuse_events (
    id SERIAL PRIMARY KEY,
    original_asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    new_asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    reused_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- MODEL USAGE LOG (Addendum §1)
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_usage_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    task_type VARCHAR(50) NOT NULL,
    model_used VARCHAR(50) NOT NULL,
    provider VARCHAR(20) NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd DECIMAL(8,6) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_usage_user ON model_usage_log(user_id);
CREATE INDEX IF NOT EXISTS idx_model_usage_created ON model_usage_log(created_at);
CREATE INDEX IF NOT EXISTS idx_model_usage_model ON model_usage_log(model_used);

-- ============================================================================
-- TIME SAVINGS (D1)
-- ============================================================================

CREATE TABLE IF NOT EXISTS time_savings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    asset_type VARCHAR(20) NOT NULL,
    minutes_saved INTEGER NOT NULL,
    semester_tag VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_time_savings_user ON time_savings(user_id);
CREATE INDEX IF NOT EXISTS idx_time_savings_semester ON time_savings(user_id, semester_tag);

-- ============================================================================
-- APP CONFIG (D1 — time saved config values, adjustable without deploys)
-- ============================================================================

CREATE TABLE IF NOT EXISTS app_config (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO app_config (key, value) VALUES
    ('time_savings_minutes', '{"assignment": 45, "quiz": 30, "discussion": 20, "announcement": 10, "page": 60, "syllabus": 60}')
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- IMAGE CREDIT PACKS (B3)
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_pack_purchases (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    pack_type VARCHAR(20) NOT NULL CHECK (pack_type IN ('starter','standard','power','institutional')),
    credits_purchased INTEGER NOT NULL,
    amount_paid DECIMAL(10,2) NOT NULL,
    stripe_payment_intent_id VARCHAR(255),
    purchased_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credit_usage_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    credits_used INTEGER NOT NULL DEFAULT 1,
    image_url VARCHAR(500),
    prompt_used TEXT,
    used_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- MEDIA LIBRARY (C1 Image Generation)
-- ============================================================================

CREATE TABLE IF NOT EXISTS media_library (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    prompt_used TEXT NOT NULL,
    style_preset VARCHAR(50),
    image_url VARCHAR(500) NOT NULL,
    alt_text TEXT,
    subject_tag VARCHAR(100),
    credit_cost INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_library_user ON media_library(user_id);

-- ============================================================================
-- BONITA PIPELINE (F1)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bonita_pipeline_exports (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    exported_at TIMESTAMP DEFAULT NOW(),
    export_batch_id VARCHAR(50),
    anonymized_payload JSONB
);

CREATE TABLE IF NOT EXISTS deletion_requests (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    requested_at TIMESTAMP DEFAULT NOW(),
    fulfilled_at TIMESTAMP
);

-- ============================================================================
-- STARTER PACKS (E2)
-- ============================================================================

CREATE TABLE IF NOT EXISTS starter_packs (
    id SERIAL PRIMARY KEY,
    discipline VARCHAR(100) NOT NULL,
    course_level VARCHAR(20) CHECK (course_level IN ('100','200','300','400','graduate')),
    asset_type VARCHAR(20) NOT NULL CHECK (asset_type IN ('assignment','quiz','discussion','announcement','page','syllabus')),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    template_content TEXT NOT NULL,
    tags TEXT[],
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_starter_packs_discipline ON starter_packs(discipline);

-- ============================================================================
-- REFERRAL SYSTEM UPDATES (E3 spec alignment)
-- ============================================================================

-- Add referral_code to users (may already exist)
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(12);

-- Ensure referrals table has spec-required fields
CREATE TABLE IF NOT EXISTS referrals_v2 (
    id SERIAL PRIMARY KEY,
    referrer_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    referred_email VARCHAR(255),
    referral_code VARCHAR(12) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending','signed_up','converted','rewarded')),
    created_at TIMESTAMP DEFAULT NOW(),
    converted_at TIMESTAMP,
    rewarded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_referrals_v2_referrer ON referrals_v2(referrer_user_id);
CREATE INDEX IF NOT EXISTS idx_referrals_v2_code ON referrals_v2(referral_code);

SELECT '007 migration complete' as message;
