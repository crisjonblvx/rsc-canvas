-- ReadySetClass Institutions Migrations
-- Migration 009: Institutions table for QM mode and future institutional features

-- ============================================================================
-- INSTITUTIONS TABLE (minimal first pass)
-- ============================================================================

CREATE TABLE IF NOT EXISTS institutions (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(255) UNIQUE NOT NULL,
    domain              VARCHAR(255),          -- .edu domain for auto-matching (e.g. 'nsu.edu')
    qm_mode_enabled     BOOLEAN DEFAULT FALSE,
    seat_limit          INTEGER DEFAULT NULL,  -- NULL = unlimited / individual plan
    stripe_customer_id  VARCHAR(255),
    created_at          TIMESTAMP DEFAULT NOW()
);

-- Index for domain lookups (used during signup auto-match)
CREATE INDEX IF NOT EXISTS idx_institutions_domain ON institutions(domain)
    WHERE domain IS NOT NULL;

-- Index for name lookups (used when resolving users.institution VARCHAR)
CREATE INDEX IF NOT EXISTS idx_institutions_name ON institutions(name);

