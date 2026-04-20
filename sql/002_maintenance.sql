-- ============================================================================
-- claude-context-plane — MAINTENANCE SQL
-- ============================================================================
-- Reference SQL for custodial duties 3, 4, 5. These are what the platform
-- does AFTER writes to keep memory integrity.
--
-- In normal use, run these via:  python -m scripts.maintenance
-- This file documents the exact SQL statements that scripts/maintenance.py
-- executes, so you can audit, modify, or run them manually if you prefer.
--
-- Recommended schedule:
--   Daily   — DUTY 3 (reconciliation pass)
--   Weekly  — DUTY 4 (confidence decay), DUTY 5 (compaction)
-- ============================================================================

USE claude_context;

-- ============================================================================
-- DUTY 3 — RECONCILIATION
-- ----------------------------------------------------------------------------
-- Mark active memories as 'deprecated' when they have been superseded
-- (superseded_by IS NOT NULL) but their status is still 'active'.
-- This catches cases where a supersede write didn't also update status.
-- ============================================================================

UPDATE fleet_memory
   SET status = 'superseded'
 WHERE status = 'active'
   AND superseded_by IS NOT NULL;


-- ============================================================================
-- DUTY 4 — CONFIDENCE DECAY
-- ----------------------------------------------------------------------------
-- 5% monthly decay on active memories that have not been reinforced in the
-- last 30 days. Memories that drop below 0.30 are auto-deprecated.
--
-- Reinforcement signal: access_count increased OR supporting_evidence_count
-- increased, both of which update updated_at.
-- ============================================================================

-- Decay step: reduce confidence by 5% for memories older than 30 days
UPDATE fleet_memory
   SET confidence = ROUND(confidence * 0.95, 2)
 WHERE status      = 'active'
   AND updated_at  < NOW() - INTERVAL 30 DAY
   AND confidence  > 0.30;

-- Auto-deprecate anything that fell below the 0.30 floor
UPDATE fleet_memory
   SET status = 'deprecated'
 WHERE status     = 'active'
   AND confidence < 0.30;


-- ============================================================================
-- DUTY 5 — COMPACTION
-- ----------------------------------------------------------------------------
-- Weekly re-clustering. Memories that have drifted close together (cosine
-- distance < 0.15) are merged into the older (more-evidenced) one.
--
-- This is a READ query to find the candidates. Actual merging is done by
-- scripts/maintenance.py --compact because it needs:
--   - transactional UPDATE of the winner (accumulate evidence + provenance)
--   - UPDATE the loser to status='superseded', superseded_by=<winner.id>
--
-- Running this query alone shows what compaction WOULD do.
-- ============================================================================

-- Find compaction candidates: pairs of active memories in the same scope
-- whose memory_vecs are within cosine 0.15 of each other.
WITH a AS (
    SELECT id, scope, memory_vec, supporting_evidence_count
      FROM fleet_memory
     WHERE status = 'active'
)
SELECT
    a1.id                              AS winner_id,
    a1.supporting_evidence_count       AS winner_evidence,
    a2.id                              AS loser_id,
    a2.supporting_evidence_count       AS loser_evidence,
    a1.scope                           AS scope,
    VEC_COSINE_DISTANCE(a1.memory_vec, a2.memory_vec) AS distance
  FROM a AS a1
  JOIN a AS a2
    ON a1.scope = a2.scope
   AND a1.id    < a2.id
 WHERE VEC_COSINE_DISTANCE(a1.memory_vec, a2.memory_vec) < 0.15
 ORDER BY distance ASC;


-- ============================================================================
-- CONTEXT SNAPSHOT CLEANUP (housekeeping, not one of the five duties)
-- Removes expired snapshot rows. These are a cache, not canonical state.
-- ============================================================================

DELETE FROM context_snapshots
 WHERE expires_at < NOW();
