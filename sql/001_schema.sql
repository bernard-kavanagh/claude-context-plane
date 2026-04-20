-- ============================================================================
-- claude-context-plane — REFERENCE DDL
-- ============================================================================
-- This file documents the schema. In normal use, you DO NOT run this —
-- the pytidb scripts create tables on first use via TableModel classes.
-- This file exists for:
--   (a) people who want to inspect the schema in SQL form
--   (b) the Stockholm audience, who want to see what unified storage looks like
--   (c) manual creation if you want tables to exist before the scripts run
--
-- THREE TIERS OF MEMORY
--   Tier 1 (working)   — the model's context window. Not persisted.
--   Tier 2 (session)   — session_state + agent_reasoning (outcome records).
--   Tier 3 (long-term) — fleet_memory (durable cross-session knowledge).
--
-- FIVE CUSTODIAL DUTIES
--   1. Write control    — enforced inline via the resolution ENUM
--   2. Deduplication    — cosine < 0.15 merge in write_memory.py
--   3. Reconciliation   — superseded_by column + maintenance.py
--   4. Confidence decay — maintenance.py --decay
--   5. Compaction       — maintenance.py --compact
--
-- EMBEDDING MODEL
--   Default: tidbcloud_free/amazon/titan-embed-text-v2 (1024-dim, no API key)
--   Alt:     huggingface/sentence-transformers/all-MiniLM-L6-v2 (384-dim, BYOK)
--   To switch, change EMBEDDING_MODEL in .env and adjust VECTOR(...) dims.
--
-- The generated VECTOR column uses EMBED_TEXT() which runs server-side —
-- no Python embedding service needed for the write path.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS claude_context;
USE claude_context;

-- ============================================================================
-- PROJECT REGISTRY — static / slowly-changing project metadata
-- Analogue of charger_registry in the EV platform.
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_registry (
    project_id          VARCHAR(64)  PRIMARY KEY,
    name                VARCHAR(128) NOT NULL,
    category            ENUM('demo','partnership','talk','blog','infra','ops','other')
                        NOT NULL DEFAULT 'other',
    status              ENUM('active','paused','archived','done')
                        NOT NULL DEFAULT 'active',

    description         TEXT,
    repo_url            VARCHAR(256),
    stakeholders        JSON,
    tags                JSON,

    started_at          DATE,
    last_touched        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_status (status, last_touched DESC),
    INDEX idx_category (category, status)
);

-- ============================================================================
-- AGENT REASONING — outcome records only (Tier 2)
-- WRITE CONTROL enforced by the resolution ENUM: no row without a resolution.
-- Mirrors agent_reasoning in the EV platform.
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_reasoning (
    id                  BIGINT       AUTO_RANDOM PRIMARY KEY,
    project_id          VARCHAR(64),
    session_id          VARCHAR(64)  NOT NULL,
    created_at          TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),

    observation         TEXT         NOT NULL,
    hypothesis          TEXT,
    evidence_refs       JSON,

    confidence          DECIMAL(3,2) DEFAULT 0.70,

    resolution          ENUM('confirmed','dismissed','escalated','promoted') NOT NULL,
    resolved_at         TIMESTAMP    NULL,

    tags                JSON,

    -- Auto-embedded: EMBED_TEXT runs server-side on INSERT
    -- 1024 dims for tidbcloud_free/amazon/titan-embed-text-v2
    -- Change to VECTOR(384) if switching to all-MiniLM-L6-v2
    reasoning_vec       VECTOR(1024) GENERATED ALWAYS AS (EMBED_TEXT(
                            "tidbcloud_free/amazon/titan-embed-text-v2",
                            CONCAT_WS(' | ',
                                COALESCE(observation,''),
                                COALESCE(hypothesis,''))
                        )) STORED,

    INDEX idx_project_reasoning (project_id, created_at DESC),
    INDEX idx_session (session_id, created_at),
    INDEX idx_resolution (resolution, created_at DESC),
    VECTOR INDEX idx_reasoning_vec ((VEC_COSINE_DISTANCE(reasoning_vec)))
);

-- ============================================================================
-- FLEET MEMORY — long-term cross-session knowledge (Tier 3)
-- Mirrors fleet_memory in the EV platform.
-- ============================================================================

CREATE TABLE IF NOT EXISTS fleet_memory (
    id                        BIGINT       AUTO_RANDOM PRIMARY KEY,
    created_at                TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    category                  ENUM('pattern','preference','working_style',
                                   'project_note','person_note','tool_note',
                                   'operational_rule','decision') NOT NULL,
    scope                     VARCHAR(128) NOT NULL DEFAULT 'global',
    content                   TEXT         NOT NULL,

    source_refs               JSON,

    confidence                DECIMAL(3,2) DEFAULT 0.70,
    supporting_evidence_count INT          DEFAULT 1,

    access_count              INT          DEFAULT 0,
    last_accessed             TIMESTAMP    NULL,

    status                    ENUM('active','deprecated','superseded') DEFAULT 'active',
    superseded_by             BIGINT       NULL,

    memory_vec                VECTOR(1024) GENERATED ALWAYS AS (EMBED_TEXT(
                                  "tidbcloud_free/amazon/titan-embed-text-v2",
                                  content
                              )) STORED,

    INDEX idx_scope_status (scope, status),
    INDEX idx_category (category, status),
    INDEX idx_confidence (confidence, status),
    VECTOR INDEX idx_memory_vec ((VEC_COSINE_DISTANCE(memory_vec)))
);

-- ============================================================================
-- SESSION STATE — per-chat-session working metadata
-- ============================================================================

CREATE TABLE IF NOT EXISTS session_state (
    session_id              VARCHAR(64)  PRIMARY KEY,
    started_at              TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    last_active             TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    focus_projects          JSON,
    focus_summary           VARCHAR(256),

    investigation_summary   TEXT,

    token_budget            INT          DEFAULT 4000,
    tokens_used             INT          DEFAULT 0,

    last_context_hash       VARCHAR(64),

    INDEX idx_last_active (last_active DESC)
);

-- ============================================================================
-- CONTEXT SNAPSHOTS — cached pre-assembled prompt fragments
-- ============================================================================

CREATE TABLE IF NOT EXISTS context_snapshots (
    id                  BIGINT       AUTO_RANDOM PRIMARY KEY,

    entity_type         ENUM('project','person','global') NOT NULL,
    entity_id           VARCHAR(64)  NOT NULL,
    snapshot_type       ENUM('profile','recent_outcomes','open_threads',
                             'preferences','summary') NOT NULL,

    content             TEXT         NOT NULL,
    token_count         INT          NOT NULL DEFAULT 0,

    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    expires_at          TIMESTAMP    NOT NULL,
    is_stale            BOOLEAN      DEFAULT FALSE,

    INDEX idx_entity (entity_type, entity_id, snapshot_type),
    INDEX idx_expires (expires_at),
    UNIQUE INDEX idx_unique_snap (entity_type, entity_id, snapshot_type)
);
