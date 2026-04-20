# Schema — table-by-table reference

Five tables. Everything fits in one TiDB cluster. pytidb `TableModel`
classes in `scripts/_models.py` are the source of truth; this file is
human documentation.

See also:
- `sql/001_schema.sql` — canonical DDL in SQL form
- `sql/002_maintenance.sql` — the three scheduled duties

---

## `project_registry`

Static / slowly-changing project metadata. Analogue of `charger_registry`
in the EV platform.

| Column | Type | Notes |
|--------|------|-------|
| `project_id` | `VARCHAR(64) PK` | e.g. `ev_charger_demo`, `stockholm_talk` |
| `name` | `VARCHAR(128)` | Display name |
| `category` | ENUM | `demo`, `partnership`, `talk`, `blog`, `infra`, `ops`, `other` |
| `status` | ENUM | `active`, `paused`, `archived`, `done` |
| `description` | `TEXT` | Free text |
| `repo_url` | `VARCHAR(256)` | GitHub or similar |
| `stakeholders` | `JSON` | e.g. `["Stephen", "Ververica"]` |
| `tags` | `JSON` | Free-form tags |
| `started_at` | `DATE` | When the project kicked off |
| `last_touched` | `TIMESTAMP` | Auto-updated on write |

No vector column — this table is look-up, not semantic search.

---

## `agent_reasoning` — **Tier 2, outcome records only**

One row per outcome. The `resolution` ENUM is the gatekeeper.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT AUTO_RANDOM PK` | |
| `project_id` | `VARCHAR(64)` | FK-shaped, not enforced |
| `session_id` | `VARCHAR(64) NOT NULL` | Every outcome belongs to a session |
| `created_at` | `TIMESTAMP(3)` | ms-precision for ordering |
| `observation` | `TEXT NOT NULL` | What was observed. **This is the vector source.** |
| `hypothesis` | `TEXT` | What was concluded |
| `evidence_refs` | `JSON` | URLs, file paths, prior reasoning IDs |
| `confidence` | `DECIMAL(3,2)` | 0.00 – 1.00, default 0.70 |
| `resolution` | ENUM | `confirmed`, `dismissed`, `escalated`, `promoted` |
| `resolved_at` | `TIMESTAMP` | When the resolution was reached |
| `tags` | `JSON` | For filtering |
| `reasoning_vec` | `VECTOR(1024)` | Auto-embedded from `observation` |

**Write control (duty 1):** `resolution` is NOT NULL. No row without it.

Indexes: `(project_id, created_at DESC)`, `(session_id, created_at)`,
`(resolution, created_at DESC)`, HNSW cosine on `reasoning_vec`.

---

## `fleet_memory` — **Tier 3, durable knowledge**

Long-term cross-session memory.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT AUTO_RANDOM PK` | |
| `created_at` | `TIMESTAMP` | |
| `updated_at` | `TIMESTAMP ON UPDATE` | Reinforcement signal for decay (duty 4) |
| `category` | ENUM | `pattern`, `preference`, `working_style`, `project_note`, `person_note`, `tool_note`, `operational_rule`, `decision` |
| `scope` | `VARCHAR(128)` | `global`, `project:<id>`, `person:<name>`, `tool:<name>` |
| `content` | `TEXT NOT NULL` | The knowledge itself. **Vector source.** |
| `source_refs` | `JSON` | Provenance: `agent_reasoning.id` values |
| `confidence` | `DECIMAL(3,2)` | Subject to decay (duty 4) |
| `supporting_evidence_count` | `INT` | Incremented on merge (duty 2) |
| `access_count` | `INT` | Incremented on recall |
| `last_accessed` | `TIMESTAMP` | Reinforcement signal |
| `status` | ENUM | `active`, `deprecated`, `superseded` |
| `superseded_by` | `BIGINT` | FK-shaped, points to the newer row |
| `memory_vec` | `VECTOR(1024)` | Auto-embedded from `content` |

Indexes: `(scope, status)`, `(category, status)`, `(confidence, status)`,
HNSW cosine on `memory_vec`.

---

## `session_state` — per-chat-session metadata

One row per conversation session. Not deleted at session end — serves as
an audit trail and feeds P1 of context assembly on the next session.

| Column | Type | Notes |
|--------|------|-------|
| `session_id` | `VARCHAR(64) PK` | `sess_<12 hex chars>` |
| `started_at` | `TIMESTAMP` | |
| `last_active` | `TIMESTAMP ON UPDATE` | |
| `focus_projects` | `JSON` | Array of `project_id` |
| `focus_summary` | `VARCHAR(256)` | Human-readable focus |
| `investigation_summary` | `TEXT` | Running summary, overwritten each turn |
| `token_budget` | `INT` | Default 4000 |
| `tokens_used` | `INT` | Updated by `load_context.py` |
| `last_context_hash` | `VARCHAR(64)` | For cache invalidation (reserved) |

---

## `context_snapshots` — cached prompt fragments (optional)

Pre-rendered project briefs, keyed by `(entity_type, entity_id, snapshot_type)`.
Not required — reserved for future use if assembly gets expensive.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT AUTO_RANDOM PK` | |
| `entity_type` | ENUM | `project`, `person`, `global` |
| `entity_id` | `VARCHAR(64)` | e.g. `ev_charger_demo`, `Stephen`, `_` |
| `snapshot_type` | ENUM | `profile`, `recent_outcomes`, `open_threads`, `preferences`, `summary` |
| `content` | `TEXT` | The rendered fragment |
| `token_count` | `INT` | Pre-computed for budgeting |
| `expires_at` | `TIMESTAMP` | TTL-driven invalidation |
| `is_stale` | `BOOLEAN` | Manual invalidation flag |

Not currently used by the scripts — reserved for Stockholm-demo scale.
