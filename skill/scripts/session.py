#!/usr/bin/env python3
"""
session.py — manage the per-chat-session row in session_state.

Usage:
    python session.py start [--focus "short summary"] [--projects id1,id2]
    python session.py update <session_id> [--focus "..."] [--projects ...]
                             [--tokens-used N] [--summary "..."]
    python session.py end <session_id> [--summary "..."]
    python session.py get <session_id>

The skill calls this at the start and end of every conversation. It returns
JSON for easy consumption.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

from _models import get_client, get_tables, SessionState, update_row


def _session_to_dict(s: SessionState) -> dict:
    return {
        "session_id": s.session_id,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "last_active": s.last_active.isoformat() if s.last_active else None,
        "focus_projects": s.focus_projects,
        "focus_summary": s.focus_summary,
        "investigation_summary": s.investigation_summary,
        "token_budget": s.token_budget,
        "tokens_used": s.tokens_used,
    }


def cmd_start(args) -> dict:
    client = get_client()
    tables = get_tables(client)

    session_id = args.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    projects = args.projects.split(",") if args.projects else None

    row = SessionState(
        session_id=session_id,
        started_at=datetime.utcnow(),
        last_active=datetime.utcnow(),
        focus_projects=projects,
        focus_summary=args.focus,
        investigation_summary=None,
        token_budget=args.budget,
        tokens_used=0,
    )
    tables.sessions.insert(row)

    return {"ok": True, "session_id": session_id, "action": "started"}


def cmd_update(args) -> dict:
    client = get_client()
    tables = get_tables(client)

    existing = tables.sessions.get(args.session_id)
    if existing is None:
        return {"ok": False, "error": f"session not found: {args.session_id}"}

    if args.focus is not None:
        existing.focus_summary = args.focus
    if args.projects is not None:
        existing.focus_projects = args.projects.split(",")
    if args.tokens_used is not None:
        existing.tokens_used = args.tokens_used
    if args.summary is not None:
        existing.investigation_summary = args.summary

    existing.last_active = datetime.utcnow()
    update_row(tables.sessions, existing, pk_field="session_id")

    return {"ok": True, "session_id": args.session_id, "action": "updated"}


def cmd_end(args) -> dict:
    """
    End a session. We don't delete the row — it stays as an audit trail and
    can be pruned later by TTL if enabled. We just set last_active and
    optionally write a final investigation_summary.
    """
    client = get_client()
    tables = get_tables(client)

    existing = tables.sessions.get(args.session_id)
    if existing is None:
        return {"ok": False, "error": f"session not found: {args.session_id}"}

    if args.summary is not None:
        existing.investigation_summary = args.summary
    existing.last_active = datetime.utcnow()
    update_row(tables.sessions, existing, pk_field="session_id")

    return {
        "ok": True,
        "session_id": args.session_id,
        "action": "ended",
        "session": _session_to_dict(existing),
    }


def cmd_get(args) -> dict:
    client = get_client()
    tables = get_tables(client)
    existing = tables.sessions.get(args.session_id)
    if existing is None:
        return {"ok": False, "error": f"session not found: {args.session_id}"}
    return {"ok": True, "session": _session_to_dict(existing)}


def main() -> int:
    p = argparse.ArgumentParser(description="Manage session_state rows.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--session-id", help="override autogen session id")
    p_start.add_argument("--focus", help="short description of focus")
    p_start.add_argument("--projects", help="comma-separated project_ids")
    p_start.add_argument("--budget", type=int, default=4000)
    p_start.set_defaults(func=cmd_start)

    p_update = sub.add_parser("update")
    p_update.add_argument("session_id")
    p_update.add_argument("--focus")
    p_update.add_argument("--projects")
    p_update.add_argument("--tokens-used", type=int)
    p_update.add_argument("--summary")
    p_update.set_defaults(func=cmd_update)

    p_end = sub.add_parser("end")
    p_end.add_argument("session_id")
    p_end.add_argument("--summary")
    p_end.set_defaults(func=cmd_end)

    p_get = sub.add_parser("get")
    p_get.add_argument("session_id")
    p_get.set_defaults(func=cmd_get)

    args = p.parse_args()
    result = args.func(args)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
