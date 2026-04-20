# Architecture — Database as Cognitive Foundation (personal edition)

> "The database is no longer a system of record. It's the agent's long-term memory." — Ziming Miao, Manus
>
> "The model is the language engine. The database is the brain." — Kavanagh, *The Database as Cognitive Foundation* (April 2026)

This skill implements the Cognitive Foundation pattern at personal scale.
Same architecture as the [EV charger platform](https://github.com/bernard-kavanagh/ev_charger_anomaly_detection).
Same architecture as Manus. Data plane stripped; context plane intact.

---

## The three tiers

```
┌───────────────────────────────────────────────────┐
│  Tier 1 — WORKING MEMORY                          │
│  Claude's context window. Ephemeral. Forgotten    │
│  the moment the session ends.                     │
└───────────────────────────────────────────────────┘
                        │ ephemeral
                        ▼
┌───────────────────────────────────────────────────┐
│  Tier 2 — SESSION MEMORY                          │
│  - session_state   (what THIS session is about)   │
│  - agent_reasoning (outcome records per session)  │
│  Persisted, but session-scoped. Becomes           │
│  searchable history.                              │
└───────────────────────────────────────────────────┘
                        │ promote on confirmation
                        ▼
┌───────────────────────────────────────────────────┐
│  Tier 3 — LONG-TERM MEMORY                        │
│  fleet_memory — durable knowledge, cross-session. │
│  Preferences, patterns, working-style rules,      │
│  confirmed decisions. Subject to all five duties. │
└───────────────────────────────────────────────────┘
```

Writes flow down. Retrievals flow up: context assembly pulls from all
three into the working memory at session start.

---

## The five custodial duties

Memory that isn't maintained decays into noise within weeks. Storing is not
remembering. These five duties distinguish a *cognitive foundation* from a
vector store.

### 1. Write control — *only confirmed outcomes persist*

- Enforced inline via the `resolution` ENUM on `agent_reasoning`.
- Allowed values: `confirmed | dismissed | escalated | promoted`.
- No row exists without a resolution. Intermediate reasoning stays in the
  context window and in `session_state.investigation_summary`.
- Implementation: `scripts/write_outcome.py` takes `--resolution` as a
  required argument. LLMs generate enormous volumes of text — storing all
  of it is building a junk drawer, not a memory system.

### 2. Deduplication — *one strong memory, not ten weak ones*

- Before INSERT to `fleet_memory`, vector-search the same scope.
- If cosine distance < **0.15**, UPDATE the existing row instead:
  `supporting_evidence_count += 1`, merge `source_refs`, nudge confidence.
- The winner gets stronger each time it's reinforced.
- Implementation: `scripts/write_memory.py` runs the check before every
  insert. Same threshold used in the EV platform.

### 3. Reconciliation — *new evidence supersedes the old*

- When a new memory contradicts an existing one in the same scope:
  insert the new, mark the old `status='superseded'`, set
  `superseded_by = new.id`.
- Future retrievals filter `status = 'active'` and skip the stale row.
- No deletion — provenance is preserved.
- Implementation: `scripts/maintenance.py --reconcile` runs daily and
  catches rows that were marked `superseded_by` but not flipped to
  `status='superseded'`.

### 4. Confidence decay — *what should a machine forget?*

- Active memories lose **5%** confidence per month without reinforcement.
- Reinforcement = being accessed (incremented on recall) or having a new
  supporting outcome promoted into it.
- Memories below **0.30** are auto-deprecated.
- Implementation: `scripts/maintenance.py --decay` runs weekly.
- Knowledge that was true six months ago but hasn't been validated since
  should fade, not persist to poison future retrievals.

### 5. Compaction — *merge drifted memories*

- Weekly: find pairs of active memories in the same scope within cosine
  distance < **0.15**.
- Merge: winner = higher `supporting_evidence_count`. Winner absorbs the
  loser's evidence count + provenance. Loser → `status='superseded'`,
  `superseded_by = winner.id`.
- Keeps the store lean as preferences restate themselves over time.
- Implementation: `scripts/maintenance.py --compact` runs weekly.

All five run **inside TiDB**. No external scheduler. No sync jobs.
`maintenance.py` is just SQL over SQLAlchemy against the same cluster.

---

## Context assembly — platform-side, not model-side

Quote from the article:

> *"The platform assembles context before the agent reasons. The model
>  doesn't decide what to retrieve. The platform ensures the right
>  knowledge is present."*

`load_context.py` is **deterministic**. Same priority order every time.
Claude does not compose this SQL — it calls the script and reads the
output.

### Priority order

| P | Source | Budget | Why this priority |
|---|--------|--------|-------------------|
| 1 | `session_state` (focus, summary) | ~50 tok | Cheapest and most specific |
| 2 | `project_registry` (active, top-5) | ~200 tok | Orientation — what's alive |
| 3 | `agent_reasoning` (last 7d, confirmed/promoted) | ~400 tok | What happened recently |
| 4 | `fleet_memory` (semantic hits on focus) | ~600 tok | What's relevant to THIS topic |
| 5 | `fleet_memory` (top-confidence global) | ~400 tok | Always-on preferences |

Default total budget: **4000 tokens**. Dropped once exhausted — lower
priorities don't push higher ones out.

---

## Why this isn't RAG

RAG brings external knowledge into the prompt. It's stateless — no
awareness of prior interactions, no accumulation across sessions, no
learning from outcomes.

Cognitive Foundation compounds across sessions. Each confirmed outcome
makes the next session better. Memories reinforce or fade based on
reuse. Contradictions resolve. Duplicates merge.

RAG optimises for retrieval quality. Cognitive Foundation optimises for
**knowledge integrity** — accurate writes, lifecycle management,
consistency. The bottleneck isn't finding the right document. It's
ensuring what you retrieve is still true.

---

## Why one cluster

All of this — project registry, session state, reasoning outcomes, fleet
memory, and the vectors on all of them — lives in **one TiDB Essentials
cluster**. One transaction boundary. One consistency model. No sync job
between a relational store and a vector store.

When `write_memory.py` runs its dedup vector search and then UPDATEs
the winner row, that's a single ACID transaction against a single
database. That's the point.
