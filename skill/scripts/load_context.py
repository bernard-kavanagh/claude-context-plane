#!/usr/bin/env python3
"""
load_context.py — deterministic platform-side context assembly.

This is the single entry point Claude calls at the start of every session.
The skill does NOT decide what to retrieve. The platform does.

Priority-ordered, token-budgeted, same pattern as assemble_context() in the
EV charger platform's tool_handlers.py.

PRIORITY ORDER (highest first; dropped once budget is exhausted):

    P1. Active session state          (focus, summary)       ~  50 tok
    P2. Active projects               (registry, top 5)      ~ 200 tok
    P3. Recent outcomes               (last 7d, top 10)      ~ 400 tok
    P4. Semantic hits on focus text   (fleet_memory)         ~ 600 tok
    P5. Top-confidence global memories (fleet_memory)        ~ 400 tok

Default budget: 4000 tokens. Output: plain-text context block, ready to
paste into a conversation, plus a JSON metadata trailer.

Usage:
    python load_context.py                        # fresh session
    python load_context.py --session-id sess_abc  # existing session
    python load_context.py --focus "stockholm deck"
    python load_context.py --budget 2000
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

from _models import (
    DEFAULT_TOKEN_BUDGET,
    SessionState,
    get_client,
    get_tables,
    update_row,
)


# Cheap token estimate — no tiktoken dependency, ~4 chars = 1 token is close
# enough for budgeting decisions. Swap for tiktoken if you want precision.
def tok(text: str) -> int:
    return max(1, len(text) // 4)


def section(title: str, body: str) -> str:
    return f"## {title}\n{body}\n"


def _result_to_dicts(result) -> list[dict]:
    """
    Coerce pytidb's QueryResult into a list of plain dicts regardless of
    which shape the installed version returns (SQLModel rows, pydantic
    models, or raw dicts).
    """
    try:
        # Most pytidb versions: QueryResult.to_list() returns list[dict]
        return list(result.to_list())
    except AttributeError:
        pass
    try:
        # Some versions: iterable of rows
        return [
            r.model_dump() if hasattr(r, "model_dump")
            else dict(r) if hasattr(r, "keys")
            else {k: v for k, v in vars(r).items() if not k.startswith("_")}
            for r in result
        ]
    except Exception:
        return []


def load_p1_session(tables, session_id: str | None) -> tuple[str, int, str]:
    """Active session state: focus, running summary. Returns (text, tokens, session_id)."""
    if session_id:
        row = tables.sessions.get(session_id)
    else:
        # Start a fresh session
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        row = SessionState(
            session_id=session_id,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            last_active=datetime.now(timezone.utc).replace(tzinfo=None),
            token_budget=DEFAULT_TOKEN_BUDGET,
            tokens_used=0,
        )
        tables.sessions.insert(row)

    if row is None:
        return ("", 0, session_id)

    bits = [f"Session: `{row.session_id}`"]
    if row.focus_summary:
        bits.append(f"Focus: {row.focus_summary}")
    if row.focus_projects:
        bits.append(f"Projects in focus: {', '.join(row.focus_projects)}")
    if row.investigation_summary:
        bits.append(f"Running summary: {row.investigation_summary}")

    body = "\n".join(bits)
    text = section("SESSION", body)
    return (text, tok(text), session_id)


def load_p2_projects(tables, limit: int = 5) -> tuple[str, int]:
    """Active projects ordered by last_touched DESC."""
    try:
        result = tables.projects.query(
            filters={"status": "active"},
            order_by={"last_touched": "desc"},
            limit=limit,
        )
        rows = _result_to_dicts(result)
    except Exception:
        rows = []

    if not rows:
        return ("", 0)

    bits = []
    for r in rows:
        name = r.get("name") or r.get("project_id")
        pid = r.get("project_id")
        cat = r.get("category")
        desc = r.get("description") or ""
        if len(desc) > 160:
            desc = desc[:157] + "..."
        bits.append(f"- **{name}** (`{pid}`, {cat}): {desc}")

    text = section("ACTIVE PROJECTS", "\n".join(bits))
    return (text, tok(text))


def load_p3_recent_outcomes(tables, days: int = 7, limit: int = 10) -> tuple[str, int]:
    """Recent confirmed/promoted outcomes from agent_reasoning."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # pytidb's filters dict supports simple equality. For the date predicate
    # we'd need SQLAlchemy column syntax; pull a wider window and filter in
    # Python. For personal scale (tens of rows/week) this is fine.
    try:
        result = tables.reasoning.query(
            filters={"resolution": "confirmed"},
            order_by={"created_at": "desc"},
            limit=limit * 3,
        )
        confirmed = _result_to_dicts(result)

        result = tables.reasoning.query(
            filters={"resolution": "promoted"},
            order_by={"created_at": "desc"},
            limit=limit * 3,
        )
        promoted = _result_to_dicts(result)

        rows = confirmed + promoted
        rows = [r for r in rows if r.get("created_at") and r["created_at"] >= since]
        rows.sort(key=lambda r: r.get("created_at") or datetime.min, reverse=True)
        rows = rows[:limit]
    except Exception:
        rows = []

    if not rows:
        return ("", 0)

    bits = []
    for r in rows:
        obs = r.get("observation") or ""
        hyp = r.get("hypothesis") or ""
        if len(obs) > 140:
            obs = obs[:137] + "..."
        if len(hyp) > 140:
            hyp = hyp[:137] + "..."
        proj = r.get("project_id") or "—"
        res = r.get("resolution")
        line = f"- [{res}] ({proj}) {obs}"
        if hyp:
            line += f" → {hyp}"
        bits.append(line)

    text = section("RECENT OUTCOMES (last 7 days)", "\n".join(bits))
    return (text, tok(text))


def load_p4_semantic_hits(tables, focus_text: str | None, limit: int = 6) -> tuple[str, int]:
    """Semantic search of fleet_memory using focus text."""
    if not focus_text:
        return ("", 0)

    try:
        hits = (
            tables.memory
            .search(focus_text)
            .filter({"status": "active"})
            .limit(limit)
            .to_list()
        )
    except Exception:
        hits = []

    if not hits:
        return ("", 0)

    bits = []
    for h in hits:
        content = h.get("content") or ""
        if len(content) > 180:
            content = content[:177] + "..."
        cat = h.get("category")
        scope = h.get("scope")
        bits.append(f"- [{cat} / {scope}] {content}")

    text = section("RELEVANT MEMORIES", "\n".join(bits))
    return (text, tok(text))


def load_p5_top_confidence(tables, limit: int = 8) -> tuple[str, int]:
    """Top-confidence active global memories — always-on context."""
    try:
        result = tables.memory.query(
            filters={"status": "active", "scope": "global"},
            order_by={"confidence": "desc"},
            limit=limit,
        )
        rows = _result_to_dicts(result)
    except Exception:
        rows = []

    if not rows:
        return ("", 0)

    bits = []
    for r in rows:
        content = r.get("content") or ""
        if len(content) > 160:
            content = content[:157] + "..."
        conf = r.get("confidence")
        cat = r.get("category")
        bits.append(f"- [{cat}, conf={conf}] {content}")

    text = section("HIGH-CONFIDENCE GLOBAL MEMORIES", "\n".join(bits))
    return (text, tok(text))


def main() -> int:
    p = argparse.ArgumentParser(
        description="Deterministic context assembly from the cognitive foundation."
    )
    p.add_argument("--session-id", help="Existing session id; if omitted, starts one.")
    p.add_argument(
        "--focus",
        help=(
            "Free-text focus of this session — drives the semantic-hits "
            "step. Usually the user's opening message or a short summary."
        ),
    )
    p.add_argument("--budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the JSON trailer; emit only the markdown context.",
    )

    args = p.parse_args()

    client = get_client()
    tables = get_tables(client)

    parts: list[tuple[str, int]] = []     # (text, tokens) in priority order
    spent = 0
    budget = args.budget

    # P1 — session
    text, n, session_id = load_p1_session(tables, args.session_id)
    if text and spent + n <= budget:
        parts.append((text, n))
        spent += n

    # P2 — projects
    text, n = load_p2_projects(tables)
    if text and spent + n <= budget:
        parts.append((text, n))
        spent += n

    # P3 — recent outcomes
    text, n = load_p3_recent_outcomes(tables)
    if text and spent + n <= budget:
        parts.append((text, n))
        spent += n

    # P4 — semantic hits on focus text
    text, n = load_p4_semantic_hits(tables, args.focus)
    if text and spent + n <= budget:
        parts.append((text, n))
        spent += n

    # P5 — always-on high-confidence memories
    text, n = load_p5_top_confidence(tables)
    if text and spent + n <= budget:
        parts.append((text, n))
        spent += n

    # Update session token usage for observability
    try:
        row = tables.sessions.get(session_id)
        if row is not None:
            row.tokens_used = spent
            row.last_active = datetime.now(timezone.utc).replace(tzinfo=None)
            if args.focus:
                row.focus_summary = args.focus[:256]
            update_row(tables.sessions, row, pk_field="session_id")
    except Exception:
        pass

    header = (
        "# Cognitive foundation — loaded context\n"
        f"_session={session_id} · tokens={spent}/{budget} · "
        f"generated_at={datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}Z_\n"
    )
    body = "\n".join(text for text, _ in parts)
    output = header + "\n" + body

    print(output)

    if not args.quiet:
        print(
            "\n<!-- meta: "
            + json.dumps(
                {
                    "session_id": session_id,
                    "tokens_used": spent,
                    "budget": budget,
                    "sections": len(parts),
                }
            )
            + " -->"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
