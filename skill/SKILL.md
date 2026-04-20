---
name: context-plane
description: >
  Personal cognitive foundation for Bernard Kavanagh — the "Database as
  Cognitive Foundation" pattern (Manus + EV charger convergence) applied to
  our working relationship. TiDB Cloud Essentials stores project state,
  confirmed decisions, working-style memories, and session outcomes with
  vector search and five custodial duties (write control, deduplication,
  reconciliation, confidence decay, compaction). Use this skill at the
  START of every conversation to load context, whenever Bernard confirms
  a decision or preference, and at the END of every conversation to
  persist outcomes. Trigger on phrases like "load context", "what were
  we working on", "catch me up", "where did we leave off", "remember
  this", "I prefer X", "confirmed", "we decided", or any greeting that
  implies continuity from a previous session.
---

# context-plane

Personal-scale implementation of the *Database as Cognitive Foundation*
pattern. Same architecture as Bernard's EV charger platform
(`github.com/bernard-kavanagh/ev_charger_anomaly_detection`) and Manus —
one TiDB cluster, stateless agent, five custodial duties maintained
inside the cluster. No external scheduler. No sync jobs.

**The stateless model forgets. The platform remembers.**

---

## What this skill is for

This skill is how Claude reaches into Bernard's TiDB Essentials cluster
to:

1. Load relevant context at the start of a conversation — deterministic,
   priority-ordered, token-budgeted. The platform decides what to retrieve,
   not the model.
2. Write confirmed outcomes back to `agent_reasoning` at natural break
   points and at session end.
3. Promote durable knowledge (preferences, working-style rules, patterns)
   to `fleet_memory` when Bernard explicitly confirms it.
4. Recall specific memories via semantic search when Bernard asks
   "what do you remember about X".

It is **not** a chat-log archiver. See §"What NOT to write" below.

---

## Three tiers of memory

| Tier | Lives in | Purpose |
|------|----------|---------|
| 1. Working | Claude's context window | Ephemeral reasoning, never persisted |
| 2. Session | `agent_reasoning` (outcome records), `session_state` | Per-conversation outcomes |
| 3. Long-term | `fleet_memory` | Durable cross-session knowledge |

---

## Five custodial duties

| # | Duty | Where |
|---|------|-------|
| 1 | Write control — only outcomes persist | `write_outcome.py` (resolution ENUM gate) |
| 2 | Deduplication — cosine < 0.15 merges | `write_memory.py` (inline check) |
| 3 | Reconciliation — supersede contradictions | `maintenance.py --reconcile` (daily) |
| 4 | Confidence decay — 5% monthly | `maintenance.py --decay` (weekly) |
| 5 | Compaction — merge drifted duplicates | `maintenance.py --compact` (weekly) |

---

## How Claude should use this skill

### At session start — ALWAYS

When Bernard starts a conversation, or says "load context" / "catch me up" /
"where did we leave off" / any greeting that implies continuity, run:

```bash
python ~/.claude/skills/context-plane/scripts/load_context.py \
    --focus "<first user message or inferred focus>"
```

The script returns a markdown context block plus a JSON trailer with the
`session_id`. **Remember the `session_id`** — every subsequent write in
this conversation passes it.

Read the returned context. Do not dump it raw to Bernard — synthesise a
brief "here's where we are" in natural language (1-3 sentences).

### During the conversation

**Write an outcome** when Bernard explicitly confirms a conclusion, decision,
or direction. Use:

```bash
python ~/.claude/skills/context-plane/scripts/write_outcome.py \
    --session-id <sess_id> \
    --resolution confirmed \
    --observation "<what was observed or concluded>" \
    --hypothesis "<optional conclusion>" \
    --project-id <if relevant> \
    --confidence 0.9 \
    --tags tag1,tag2
```

`--resolution` MUST be one of `confirmed | dismissed | escalated | promoted`.
If there's no clear resolution, **do not write**. The row will be rejected.

**Promote to fleet_memory** when the outcome is durable — a preference, a
working-style rule, a decision that should influence every future session:

```bash
python ~/.claude/skills/context-plane/scripts/promote.py \
    --reasoning-id <id from write_outcome> \
    --category preference \
    --scope global
```

Valid categories: `pattern | preference | working_style | project_note |
person_note | tool_note | operational_rule | decision`

Valid scopes: `global`, `project:<id>`, `person:<name>`, `tool:<name>`

**Supersede a contradicted memory** when new evidence directly contradicts
an existing one (duty 3 — inline reconciliation). First recall the old one
to get its id, then:

```bash
python ~/.claude/skills/context-plane/scripts/write_memory.py \
    --category <cat> \
    --scope <scope> \
    --content "<new knowledge that replaces the old>" \
    --supersedes <old_memory_id>
```

The old row gets `status='superseded'` + `superseded_by=<new_id>` in one
transaction. Dedup is skipped — the whole point is that this IS different.
Use this when Bernard explicitly corrects something: *"actually we're going
with Path B now, not Path A"*. Do NOT use this for mere restatements —
those should merge via normal dedup.

**Recall memories** when Bernard asks "what do you remember about X" or
when you need to check whether a preference exists:

```bash
python ~/.claude/skills/context-plane/scripts/recall.py \
    --query "<natural-language query>" \
    [--scope <scope>] [--category <cat>] [--limit 5]
```

### At session end

When the conversation wraps up — Bernard says goodbye, switches topic
decisively, or the session is clearly done — call:

```bash
python ~/.claude/skills/context-plane/scripts/session.py end <sess_id> \
    --summary "<one-paragraph wrap-up of what happened>"
```

This writes a final `investigation_summary` to `session_state`. The next
session loads it via P3/P4 of context assembly.

### Periodically (Bernard runs, not Claude)

Duties 3, 4, 5 are scheduled, not per-turn. Bernard runs them via:

```bash
python ~/.claude/skills/context-plane/scripts/maintenance.py --all
```

Recommended: daily `--reconcile`, weekly `--all`. Claude can *suggest*
running this if memory feels stale or contradictory, but should not run
it unprompted.

---

## Write-control rules — IMPORTANT

**What to write (and when):**

- ✅ Bernard confirms an architectural decision → `write_outcome` + `promote`
- ✅ Bernard states a preference or working-style rule → `write_outcome` (`confirmed`) + `promote` (`preference` or `working_style`)
- ✅ A hypothesis is explicitly dismissed → `write_outcome` (`dismissed`)
- ✅ Session wrap-up → `session end --summary`

**What NOT to write:**

- ❌ Intermediate reasoning steps or exploratory thinking
- ❌ Questions Bernard asked (those aren't outcomes)
- ❌ Tool outputs or search results (those aren't memories)
- ❌ Anything without a clear `resolution` value
- ❌ Restatements of something already in `fleet_memory` — the dedup gate
     will merge, but don't trigger it for trivially-restated facts

If in doubt: **do not write**. The store stays bounded at O(outcomes), not
O(turns). This is the "junk drawer vs memory system" distinction.

---

## SQL shortcuts via the TiDB MCP server

If Bernard has the official `tidb-mcp-server` configured in Claude Code,
use `db_query` for ad-hoc reads when a script isn't enough:

```sql
-- Project state overview
SELECT project_id, name, status, last_touched
  FROM project_registry
 WHERE status = 'active'
 ORDER BY last_touched DESC;

-- Fleet memory by category, ordered by confidence
SELECT category, scope, content, confidence, supporting_evidence_count
  FROM fleet_memory
 WHERE status = 'active'
 ORDER BY confidence DESC
 LIMIT 20;
```

**Never use `db_execute` to write to `agent_reasoning` or `fleet_memory`
directly.** That bypasses write control (duty 1) and deduplication (duty 2).
Always go through the scripts.

---

## Natural-language tone

The memory system is plumbing, not performance. When using context Claude
loaded from the platform, speak as if the knowledge is naturally continuous
— don't narrate "I'm loading your context" or "According to my memory".
Just be informed.

Example:
- ❌ "Let me load your context... OK, I can see we were working on Stockholm"
- ✅ "Picking up where we left off on the Stockholm deck — you wanted to revisit slide 12."

---

## Reference files

- `references/architecture.md` — three tiers + five duties, deeper treatment
- `references/schema.md` — table-by-table reference
- `references/sql-patterns.md` — canonical SQL for ad-hoc reads
- `scripts/_models.py` — pytidb `TableModel` definitions (source of truth)

---

## Failure modes

If a script errors (e.g. TiDB unreachable, API key missing):
- Report the error to Bernard concisely
- Continue the conversation without the context plane — it's augmentation,
  not a dependency
- Do NOT try to "rebuild" context from chat history in place of the platform
