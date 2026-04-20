# SQL patterns — ad-hoc reads via `tidb-mcp-server`

If Bernard has the official `tidb-mcp-server` configured, Claude can
issue SQL directly via its `db_query` tool. Use these canonical patterns
for common inspections.

**Always read-only.** All writes go through the scripts (this preserves
duties 1 and 2). If you find yourself wanting to INSERT or UPDATE via
`db_execute`, stop and use the appropriate script instead.

---

## Orientation queries

### What's active right now
```sql
SELECT project_id, name, category, last_touched
  FROM project_registry
 WHERE status = 'active'
 ORDER BY last_touched DESC
 LIMIT 10;
```

### This session's history
```sql
SELECT created_at, project_id, resolution, observation
  FROM agent_reasoning
 WHERE session_id = '<session_id>'
 ORDER BY created_at;
```

### Recent outcomes across all sessions (last 7 days)
```sql
SELECT created_at, project_id, resolution,
       LEFT(observation, 80) AS obs
  FROM agent_reasoning
 WHERE created_at >= NOW() - INTERVAL 7 DAY
   AND resolution IN ('confirmed', 'promoted')
 ORDER BY created_at DESC
 LIMIT 20;
```

---

## Memory introspection

### Top-confidence active memories
```sql
SELECT category, scope, confidence,
       supporting_evidence_count AS ev,
       LEFT(content, 120) AS content
  FROM fleet_memory
 WHERE status = 'active'
 ORDER BY confidence DESC, supporting_evidence_count DESC
 LIMIT 20;
```

### Memories by category
```sql
SELECT category, COUNT(*) AS n, AVG(confidence) AS avg_conf
  FROM fleet_memory
 WHERE status = 'active'
 GROUP BY category
 ORDER BY n DESC;
```

### Decay candidates (stale memories at risk)
```sql
SELECT id, category, scope, confidence, updated_at,
       LEFT(content, 100) AS content
  FROM fleet_memory
 WHERE status = 'active'
   AND updated_at < NOW() - INTERVAL 30 DAY
   AND confidence > 0.30
 ORDER BY confidence ASC, updated_at ASC
 LIMIT 20;
```

### Recently deprecated (for audit)
```sql
SELECT id, category, scope, confidence, content
  FROM fleet_memory
 WHERE status = 'deprecated'
 ORDER BY updated_at DESC
 LIMIT 20;
```

---

## Semantic retrieval (vector search)

pytidb's `.search()` is the idiomatic path, but raw SQL works too:

### Find memories similar to a query
```sql
SELECT id, category, scope, content,
       VEC_EMBED_COSINE_DISTANCE(
           memory_vec,
           'your natural language query here'
       ) AS distance
  FROM fleet_memory
 WHERE status = 'active'
 ORDER BY distance ASC
 LIMIT 5;
```

### Find reasoning outcomes similar to a query
```sql
SELECT id, project_id, resolution, observation,
       VEC_EMBED_COSINE_DISTANCE(
           reasoning_vec,
           'your query'
       ) AS distance
  FROM agent_reasoning
 WHERE resolution IN ('confirmed','promoted')
 ORDER BY distance ASC
 LIMIT 5;
```

---

## Audit & lineage

### Supersede chain for a memory
```sql
WITH RECURSIVE chain AS (
    SELECT id, superseded_by, content, status, 0 AS depth
      FROM fleet_memory
     WHERE id = <root_id>
    UNION ALL
    SELECT m.id, m.superseded_by, m.content, m.status, c.depth + 1
      FROM fleet_memory m
      JOIN chain c ON m.id = c.superseded_by
)
SELECT * FROM chain ORDER BY depth;
```

### Provenance: which outcomes produced this memory
```sql
SELECT
    m.id AS memory_id,
    m.content AS memory,
    r.id AS reasoning_id,
    r.observation AS source_observation,
    r.resolution AS source_resolution
  FROM fleet_memory m
  JOIN agent_reasoning r
    ON JSON_CONTAINS(m.source_refs, CAST(r.id AS JSON))
 WHERE m.id = <memory_id>;
```

---

## Health checks

### Counts per table
```sql
SELECT 'project_registry' AS t, COUNT(*) AS n FROM project_registry
UNION ALL SELECT 'agent_reasoning',   COUNT(*) FROM agent_reasoning
UNION ALL SELECT 'fleet_memory',      COUNT(*) FROM fleet_memory
UNION ALL SELECT 'session_state',     COUNT(*) FROM session_state;
```

### Embedding backlog (rows with NULL vector)
```sql
SELECT 'agent_reasoning' AS t, COUNT(*) AS null_vec
  FROM agent_reasoning WHERE reasoning_vec IS NULL
UNION ALL
SELECT 'fleet_memory', COUNT(*) FROM fleet_memory WHERE memory_vec IS NULL;
```

If auto-embedding is working (GENERATED ALWAYS AS ...), these should
stay near zero. Sustained backlog = embedding provider trouble.
