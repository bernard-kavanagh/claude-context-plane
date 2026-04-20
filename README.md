# claude-context-plane
> **Status:** built in one session on 2026-04-20 as a working proof of the Cognitive Foundation pattern at personal scale. Used daily by the author. Probably has rough edges. Issues/PRs welcome.
A personal-scale implementation of the **Database as Cognitive Foundation**
pattern — the same architecture Manus uses at consumer scale and that the
[EV charger platform](https://github.com/bernard-kavanagh/ev_charger_anomaly_detection)
uses at industrial IoT scale, applied to a single human's working
relationship with Claude.

> *The model is the language engine. The database is the brain.*
>
> — Bernard Kavanagh, [*The Database as Cognitive Foundation*](https://medium.com/@bernardpkavanagh/the-database-as-cognitive-foundation-when-two-production-systems-arrive-at-the-same-answer-a755bd21c8aa)

---

## Why this exists

Claude is stateless. Every new conversation starts from zero — no memory of
what was decided last week, no knowledge of the projects in flight, no
recollection of preferences that have been stated twenty times. The chat
history workaround (re-read the last transcript, re-explain everything) is
the memory-wall problem on a personal scale.

The fix isn't a bigger context window or cleverer prompts. It's the same
fix Manus arrived at independently from EV fault detection: **put the
memory in the database**. Maintain it. Let the stateless model reach in,
reason, write back, and disappear.

This repo is the personal edition. One TiDB Essentials cluster. Five
tables. Seven scripts. A Claude Code skill that orchestrates them.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent layer                             │
│            Claude (stateless, disposable)                   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            Skill: ~/.claude/skills/context-plane/           │
│                                                             │
│  SKILL.md tells Claude WHEN to call each script             │
│  scripts/ are the deterministic platform-side operations    │
│    load_context.py   — assemble context (P1..P5, budgeted)  │
│    write_outcome.py  — duty 1 (write control)               │
│    write_memory.py   — duty 2 (deduplication)               │
│    promote.py        — tier 2 → tier 3                      │
│    recall.py         — semantic retrieval                   │
│    session.py        — session lifecycle                    │
│    maintenance.py    — duties 3, 4, 5                       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              TiDB Essentials — single cluster               │
│                                                             │
│  Tier 2 session memory:                                     │
│    session_state      — per-chat metadata                   │
│    agent_reasoning    — outcome records (resolution ENUM)   │
│                                                             │
│  Tier 3 long-term memory:                                   │
│    fleet_memory       — durable cross-session knowledge     │
│                                                             │
│  Project orientation:                                       │
│    project_registry   — active projects                     │
│                                                             │
│  Vector search: auto-embedded via EMBED_TEXT(), HNSW index  │
│  One transaction boundary. One consistency model.           │
└─────────────────────────────────────────────────────────────┘
```

**Tier 1 (working memory)** lives in Claude's context window and is never
persisted. Tier 2 writes only happen on **confirmed outcomes**. Tier 3 is
deduplicated, decayed, and compacted by `maintenance.py`.

---

## The five custodial duties

| # | Duty | Where enforced |
|---|------|----------------|
| 1 | **Write control** — only confirmed outcomes persist | `resolution` ENUM on `agent_reasoning`; `write_outcome.py` |
| 2 | **Deduplication** — cosine < 0.15 merges | `write_memory.py` (inline) |
| 3 | **Reconciliation** — new evidence supersedes old | `write_memory.py --supersedes <id>` (inline); `maintenance.py --reconcile` (daily sweep) |
| 4 | **Confidence decay** — 5% monthly, floor at 0.30 | `maintenance.py --decay` (weekly) |
| 5 | **Compaction** — merge drifted near-duplicates | `maintenance.py --compact` (weekly) |

All five run **inside TiDB**. No external schedulers, no sync jobs.
`maintenance.py` is just SQL over SQLAlchemy.

---

## Deliberate reductions from the EV-platform reference

This repo mirrors the context-plane architecture of
[`ev_charger_anomaly_detection`](https://github.com/bernard-kavanagh/ev_charger_anomaly_detection)
with three deliberate reductions. They're scale-appropriate for personal
use, not bugs:

**1. No data plane.** The EV repo has `charger_telemetry`, `charger_windows`,
and `outage_catalog` — tables that ingest ~112M IoT rows per day. There
is no equivalent here. Personal knowledge work has no telemetry stream
to reason *about*. Only the context plane matters, so only the context
plane exists.

**2. No TTL policies.** The EV schema has commented-out `ALTER TABLE ...
TTL = ...` blocks that cap steady-state storage at ~960M rows. Personal
scale produces tens of rows per week, so unbounded growth is a non-issue
for years. Easy to add later if the memory store ever gets unwieldy.

**3. Synchronous embedding, not async.** The EV repo pipes
`charger_windows` writes through TiCDC → Kafka → an embedding service,
then UPDATEs the vector column asynchronously. We use TiDB's native
`EMBED_TEXT()` in a `GENERATED ALWAYS AS ... STORED` column — vectors
are computed server-side on INSERT. Simpler, and works because we don't
need the throughput the EV pipeline was designed for. The trade-off: we
depend on TiDB Cloud's embedding providers (default is
`tidbcloud_free/amazon/titan-embed-text-v2`), whereas the EV repo can
swap embedding models freely.

These reductions are why this is ~500 lines of Python and ~300 lines of
SQL rather than ~2000 lines of Python, PyFlink, and TiCDC config.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/bernard-kavanagh/claude-context-plane.git
cd claude-context-plane

# 2. Configure
cp .env.example .env
$EDITOR .env   # paste TiDB Essentials connection params

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install the skill to ~/.claude/skills/
./install.sh

# 5. Symlink .env so scripts can find it
ln -sf "$PWD/.env" "$HOME/.claude/skills/context-plane/.env"

# 6. Smoke test — creates the tables on first use
python ~/.claude/skills/context-plane/scripts/load_context.py \
       --focus "first run — install smoke test"
```

Next time you open Claude Code and start a conversation, the skill will
auto-trigger on continuity phrases like "catch me up" or "where did we
leave off". See [INSTALL.md](INSTALL.md) for the full walkthrough.

---

## What's in the repo

```
claude-context-plane/
├── skill/                              ← gets copied to ~/.claude/skills/context-plane/
│   ├── SKILL.md                        ← Claude's instructions
│   ├── references/
│   │   ├── architecture.md             ← three tiers + five duties
│   │   ├── schema.md                   ← table-by-table reference
│   │   └── sql-patterns.md             ← ad-hoc reads via tidb-mcp-server
│   └── scripts/
│       ├── _models.py                  ← pytidb TableModels (source of truth)
│       ├── load_context.py             ← deterministic context assembly
│       ├── session.py                  ← session lifecycle
│       ├── write_outcome.py            ← duty 1: write control
│       ├── write_memory.py             ← duty 2: dedup
│       ├── promote.py                  ← tier 2 → tier 3
│       ├── recall.py                   ← semantic retrieval
│       └── maintenance.py              ← duties 3, 4, 5
├── sql/
│   ├── 001_schema.sql                  ← reference DDL
│   └── 002_maintenance.sql             ← reference maintenance SQL
├── install.sh                          ← copies skill/ into ~/.claude/skills/
├── .env.example
├── .gitignore
├── LICENSE                             ← Apache-2.0
├── README.md                           ← this file
├── INSTALL.md                          ← step-by-step walkthrough
└── requirements.txt                    ← pytidb + python-dotenv
```

---

## Not RAG

RAG brings external knowledge into the prompt. It's stateless — no
awareness of prior interactions, no accumulation across sessions, no
learning from outcomes.

Cognitive Foundation compounds across sessions. Each confirmed outcome
makes the next session better. Memories reinforce or fade based on reuse.
Contradictions resolve. Duplicates merge. The bottleneck isn't retrieval
quality — it's **knowledge integrity**.

If your AI memory layer has nobody running reconciliation, decay, or
compaction, you have storage, not memory.

---

## Prior art

- [**The Database as Cognitive Foundation**](https://medium.com/@bernardpkavanagh/the-database-as-cognitive-foundation-when-two-production-systems-arrive-at-the-same-answer-a755bd21c8aa) — Bernard Kavanagh, April 2026
- [**The Memory Wall**](https://medium.com/@bernardpkavanagh/the-memory-wall-your-ai-agents-arent-failing-because-they-re-dumb-db535dfb423a) — Bernard Kavanagh (the five custodial duties in technical depth)
- [**EV charger anomaly detection**](https://github.com/bernard-kavanagh/ev_charger_anomaly_detection) — industrial-scale reference implementation
- [**Manus case study**](https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/) — consumer-scale convergence
- [**Context Engineering for AI Agents — Lessons from Building Manus**](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)

---

## License

Apache-2.0 — same as the EV charger platform. See [LICENSE](LICENSE).
